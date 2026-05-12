"""진단 스케줄러 — cron 발화 → 활성 서버 N대 + environment job enqueue + retention DELETE (ADR 0004).

기동: `python -m assessment_engine.diagnostic.scheduler`.
기존 `src/assessment_engine/scheduler/` 폐기 (ADR 0004 결정 — 본 모듈로 대체).
broker 연결은 web/main.py·diagnostic/main.py와 동일 인자로 declare (#B3).
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import aio_pika
from croniter import croniter
from loguru import logger
from sqlalchemy import func, select, text

from assessment_engine.config import diagnostic_settings
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.redis import close_pool, get_redis
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository
from assessment_engine.db.repositories.query_repository import QueryRepository
from assessment_engine.db.session import AsyncSessionLocal
from assessment_engine.web.services.diagnostic_service import (
    DiagnosticService,
    _NotFound,
    _RaceMiss,
)


async def main() -> None:
    logger.info("diagnostic scheduler starting cron={} retention_days={}",
                diagnostic_settings.diagnostic_schedule_cron,
                diagnostic_settings.diagnostic_retention_days)

    redis = get_redis()
    dlx_name = f"{diagnostic_settings.rabbitmq_exchange}.dlx"
    routing_key = diagnostic_settings.diagnostic_routing_key

    try:
        conn = await aio_pika.connect_robust(diagnostic_settings.broker_url, timeout=10)
        async with conn, conn.channel() as channel:
            # broker 인자 일치 의무 (#B3) — web/worker와 동일 declare
            dlx = await channel.declare_exchange(
                dlx_name, aio_pika.ExchangeType.DIRECT, durable=True,
            )
            exchange = await channel.declare_exchange(
                diagnostic_settings.rabbitmq_exchange,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )
            dlq = await channel.declare_queue(f"{routing_key}.dead", durable=True)
            await dlq.bind(dlx, routing_key=routing_key)
            queue = await channel.declare_queue(
                routing_key,
                durable=True,
                arguments={
                    "x-dead-letter-exchange":    dlx_name,
                    "x-dead-letter-routing-key": routing_key,
                    "x-message-ttl":             diagnostic_settings.diagnostic_queue_ttl_ms,
                    "x-max-length":              diagnostic_settings.diagnostic_queue_max_len,
                },
            )
            await queue.bind(exchange, routing_key=routing_key)

            await _run_loop(channel, redis)
    finally:
        await close_pool()


_KST = ZoneInfo("Asia/Seoul")


async def _run_loop(broker_channel, redis) -> None:
    """cron 다음 발화 시각까지 sleep → _run_once. 영구 루프.

    cron 표현식은 KST 가정 (운영자 직관·ADR 0004 default `0 3 * * *` = 매일 03시 KST).
    croniter는 입력 datetime의 timezone을 그대로 따른다 — KST datetime을 넘겨 일관 유지.
    """
    cron = croniter(diagnostic_settings.diagnostic_schedule_cron, datetime.now(_KST))
    while True:
        next_run: datetime = cron.get_next(datetime)
        now = datetime.now(_KST)
        wait = max(0.0, (next_run - now).total_seconds())
        logger.info("scheduler next run at {} (wait {:.0f}s)", next_run.isoformat(), wait)
        await asyncio.sleep(wait)

        try:
            await _run_once(broker_channel, redis)
        except Exception:
            # 발화 1회 실패가 루프 자체를 중단시키지 않게 격리 (#F10 fail-close는 메시지 처리에만)
            logger.exception("scheduler run_once failed")


async def _run_once(broker_channel, redis) -> None:
    time_range = "14d"  # ADR 0003 WINDOW_DAYS 동일 (recommendation.WINDOW_DAYS와 단일 진실)

    # 활성 서버 조회 (last_seen_at > now() - N hours)
    async with AsyncSessionLocal() as session:
        active_public_ids = await _list_active_server_public_ids(
            session, diagnostic_settings.diagnostic_active_server_window_hours,
        )
    logger.info("scheduler tick — active servers={}", len(active_public_ids))

    # server scope — 1대씩 service.submit (부분 실패 격리)
    enqueued = 0
    for public_id in active_public_ids:
        async with AsyncSessionLocal() as session:
            service = _build_service(session, broker_channel, redis)
            try:
                ids = await service.submit(
                    "server", [public_id], time_range, anchor_at=None, requested_by="scheduler",
                )
                enqueued += len(ids)
            except _NotFound:
                # 스케줄러 SQL과 service.resolve 사이 race — 서버가 사라짐. silent skip.
                logger.debug("scheduled server diagnostic — server disappeared pid={}", public_id)
            except _RaceMiss:
                logger.debug("scheduled server diagnostic — race miss pid={}", public_id)
            except Exception:
                logger.exception("scheduled server diagnostic failed pid={}", public_id)
    logger.info("scheduled server diagnostics enqueued={}", enqueued)

    # environment scope — 1건
    async with AsyncSessionLocal() as session:
        service = _build_service(session, broker_channel, redis)
        try:
            env_ids = await service.submit(
                "environment", None, time_range, anchor_at=None, requested_by="scheduler",
            )
            logger.info("scheduled environment diagnostic enqueued count={}", len(env_ids))
        except Exception:
            logger.exception("scheduled environment diagnostic failed")

    # retention DELETE (90일) — 별도 cron 인프라 없이 진단 사이클에 묶음
    async with AsyncSessionLocal() as session:
        repo = DiagnosticRepository(session)
        deleted = await repo.delete_retention(diagnostic_settings.diagnostic_retention_days)
        await session.commit()
        if deleted > 0:
            logger.info("retention purged jobs={}", deleted)


def _build_service(session, broker_channel, redis) -> DiagnosticService:
    return DiagnosticService(
        query_repo=QueryRepository(session),
        session_factory=AsyncSessionLocal,
        diagnostic_repo_factory=DiagnosticRepository,
        broker_channel=broker_channel,
        redis=redis,
    )


async def _list_active_server_public_ids(session, since_hours: int) -> list[str]:
    """활성 서버 = last_seen_at이 N시간 이내. 단순 SELECT 1회.

    BaseQueryRepository에 옮길 가치 = 향후 web도 동일 필터 필요해진 시점. 1차는 본 모듈에 캡슐화.
    """
    stmt = (
        select(ServerInventory.public_id)
        .where(ServerInventory.last_seen_at > func.now() - text(f"interval '{since_hours} hours'"))
        .order_by(ServerInventory.last_seen_at.desc())
        .limit(10000)
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


if __name__ == "__main__":
    asyncio.run(main())
