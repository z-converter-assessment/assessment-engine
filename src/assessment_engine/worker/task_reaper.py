"""install task reaper 루프 — 전용 워커 프로세스(assessment_engine.worker)가 구동.

deadline_at 경과 pending(install) task 를 능동적으로 failure(timeout) 로 전이한다. 발행 경로(TaskService)도
emit 직전 만료분을 정리하지만(`expire_overdue_tasks`), 그건 lazy — 해당 서버에 다음 emit 이 없으면 pending 이
DB 에 영구 잔류한다. 본 reaper 가 emit 과 무관하게 주기적으로 전역 정리해 "미배달·무회신 pending" 이 terminal
상태에 도달하게 한다 (오프라인 호스트가 배달 창 안에 안 돌아온 경우 등, F6 관측성).

큐 메시지 자체는 broker x-message-ttl(= install_task_deadline_sec) 로 만료되므로, 창을 넘긴 task 는 배달도
안 되고 여기서 timeout 으로 정리된다 — 두 시점이 같은 창이라 zombie 지연 실행 없음.

graceful(F11): stop_event 로 다음 tick 중단. 진행 중 UPDATE 1건은 짧아 drain 즉시 완료.
구체 인스턴스는 composition root(worker/main.py)가 구성해 주입.
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository
from assessment_engine.worker.worker_lifecycle import sleep_or_stop


async def run_task_reaper(
    *,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    collect_repo_factory: Callable[..., BaseCollectRepository],
    interval_sec: float,
    stop_event: asyncio.Event,
) -> None:
    """reaper 메인 루프 — interval 마다 deadline 경과 pending 전역 timeout 전이.

    session_factory: tick 마다 독립 세션(전이 UPDATE 트랜잭션 분리).
    stop_event: SIGTERM 시 set — 루프가 다음 점검에서 종료.
    """
    logger.info("install task reaper started interval={}s", interval_sec)
    while not stop_event.is_set():
        try:
            async with session_factory() as session:
                repo = collect_repo_factory(session)
                expired = await repo.expire_all_overdue_tasks()
                await session.commit()
            if expired:
                logger.info("install task reaper expired overdue pending n={}", expired)
        except Exception:
            # reaper 격리 — 일시 DB 장애 등이 루프를 죽이면 안 됨(F6 except Exception 예외: reraise 시 reaper 사망).
            logger.exception("install task reaper tick failed")
        await sleep_or_stop(stop_event, interval_sec)
    logger.info("install task reaper stopped")
