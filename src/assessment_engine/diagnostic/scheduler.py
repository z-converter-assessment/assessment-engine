"""진단 스케줄러 — cron 발화 → 활성 서버 N대 + environment job enqueue + retention DELETE (ADR 0004).

기동: `python -m assessment_engine.diagnostic.scheduler`.
기존 `src/assessment_engine/scheduler/` 폐기 (ADR 0004 결정 — 본 모듈로 대체).
broker 연결은 web/main.py·diagnostic/main.py와 동일 인자로 declare (#B3).
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aio_pika
from aio_pika.exceptions import AMQPError
from croniter import croniter
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.redis import close_pool
from assessment_engine.db.repositories.base_diagnostic_repository import (
    DIAGNOSTIC_DEFAULT_TIME_RANGE,
)
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository
from assessment_engine.db.repositories.query_repository import QueryRepository
from assessment_engine.db.session import AsyncSessionLocal
from assessment_engine.diagnostic.settings import diagnostic_settings
from assessment_engine.diagnostic.submitter import (
    DiagnosticNotFound,
    DiagnosticRaceMiss,
    DiagnosticSubmitter,
)
from assessment_engine.log_config import setup_logging


async def main() -> None:
    setup_logging(diagnostic_settings.log_format)

    logger.info("diagnostic scheduler starting cron={}",
                diagnostic_settings.diagnostic_schedule_cron)

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

            await _run_loop(channel)
    finally:
        await close_pool()


_KST = ZoneInfo("Asia/Seoul")


async def _run_loop(broker_channel) -> None:
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
            await _run_once(broker_channel)
        except Exception:
            # 발화 1회 실패가 루프 자체를 중단시키지 않게 격리 (#F6 fail-close는 메시지 처리에만)
            logger.exception("scheduler run_once failed")


async def _run_once(broker_channel) -> None:
    # feature flag — diagnostic_enabled=False 시 cron 발화 no-op. publish/active server 조회 모두 skip.
    if not diagnostic_settings.diagnostic_enabled:
        logger.info("scheduler tick — diagnostic disabled (DIAGNOSTIC_ENABLED=false), skip")
        return

    time_range = DIAGNOSTIC_DEFAULT_TIME_RANGE  # F10 단일 진실

    # 활성 서버 조회 (last_seen_at > now() - N hours)
    async with AsyncSessionLocal() as session:
        active_public_ids = await _list_active_server_public_ids(
            session, diagnostic_settings.diagnostic_active_server_window_hours,
        )
    logger.info("scheduler tick — active servers={}", len(active_public_ids))

    # server scope — 1대씩 submitter.submit (부분 실패 격리)
    enqueued = 0
    for public_id in active_public_ids:
        async with AsyncSessionLocal() as session:
            submitter = _build_submitter(session, broker_channel)
            try:
                ids = await submitter.submit(
                    "server", [public_id], time_range, anchor_at=None, requested_by="scheduler",
                )
                enqueued += len(ids)
            except DiagnosticNotFound:
                # 스케줄러 SQL과 submitter.resolve 사이 race — 서버가 사라짐. silent skip.
                logger.debug("scheduled server diagnostic — server disappeared pid={}", public_id)
            except DiagnosticRaceMiss:
                logger.debug("scheduled server diagnostic — race miss pid={}", public_id)
            except (OperationalError, AMQPError):
                # DB/broker 일시 장애 — 운영 cron 보호용 silent skip (다음 발화에서 재시도).
                logger.exception("scheduled server diagnostic infrastructure error pid={}", public_id)
    logger.info("scheduled server diagnostics enqueued={}", enqueued)

    # environment scope — 1건
    async with AsyncSessionLocal() as session:
        submitter = _build_submitter(session, broker_channel)
        try:
            env_ids = await submitter.submit(
                "environment", None, time_range, anchor_at=None, requested_by="scheduler",
            )
            logger.info("scheduled environment diagnostic enqueued count={}", len(env_ids))
        except DiagnosticRaceMiss:
            logger.debug("scheduled environment diagnostic — race miss")
        except (OperationalError, AMQPError):
            logger.exception("scheduled environment diagnostic infrastructure error")

    # retention DELETE — 임시 비활성. 복원 시 delete_retention 호출 + diagnostic_retention_days 사용.


def _build_submitter(session, broker_channel) -> DiagnosticSubmitter:
    """scheduler 노드 composition root — web.services 의존 없이 submitter 단독 인스턴스화 (#F4)."""
    return DiagnosticSubmitter(
        query_repo=QueryRepository(session),
        session_factory=AsyncSessionLocal,
        diagnostic_repo_factory=DiagnosticRepository,
        broker_channel=broker_channel,
    )


async def _list_active_server_public_ids(session, since_hours: int) -> list[str]:
    """활성 서버 = last_seen_at이 N시간 이내. 단순 SELECT 1회.

    BaseQueryRepository에 옮길 가치 = 향후 web도 동일 필터 필요해진 시점. 1차는 본 모듈에 캡슐화.
    """
    stmt = (
        select(ServerInventory.public_id)
        .where(ServerInventory.last_seen_at > func.now() - timedelta(hours=since_hours))
        .order_by(ServerInventory.last_seen_at.desc())
        .limit(10000)
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


if __name__ == "__main__":
    asyncio.run(main())
