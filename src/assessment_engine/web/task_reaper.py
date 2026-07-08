"""install task reaper — web 프로세스 내 백그라운드 루프.

deadline_at 경과 pending(install) task 를 능동적으로 failure(timeout) 로 전이한다. 발행 경로(TaskService)도
emit 직전 만료분을 정리하지만(`expire_overdue_tasks`), 그건 lazy — 해당 서버에 다음 emit 이 없으면 pending 이
DB 에 영구 잔류한다. 본 reaper 가 emit 과 무관하게 주기적으로 전역 정리해 "미배달·무회신 pending" 이 terminal
상태에 도달하게 한다 (오프라인 호스트가 배달 창 안에 안 돌아온 경우 등, F6 관측성).

큐 메시지 자체는 broker x-message-ttl(= install_task_deadline_sec) 로 만료되므로, 창을 넘긴 task 는 배달도
안 되고 여기서 timeout 으로 정리된다 — 두 시점이 같은 창이라 zombie 지연 실행 없음.

graceful(F11): stop_event 로 다음 tick 중단. 진행 중 UPDATE 1건은 짧아 drain 즉시 완료.
구체 인스턴스는 composition root(web/main.py lifespan)가 구성해 주입.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from loguru import logger

from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository


async def _sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    """tick 대기 — stop_event set 되면 즉시 깸(graceful shutdown 시 대기 단축)."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_task_reaper(
    *,
    session_factory: Callable[[], AbstractAsyncContextManager],
    collect_repo_factory: Callable[..., BaseCollectRepository],
    interval_sec: float,
    stop_event: asyncio.Event,
) -> None:
    """reaper 메인 루프 — interval 마다 deadline 경과 pending 전역 timeout 전이.

    session_factory: tick 마다 독립 세션(전이 UPDATE 트랜잭션 분리).
    stop_event: lifespan shutdown 시 set — 루프가 다음 점검에서 종료.
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
        await _sleep_or_stop(stop_event, interval_sec)
    logger.info("install task reaper stopped")


@asynccontextmanager
async def lifespan_task_reaper(
    *,
    session_factory: Callable[[], AbstractAsyncContextManager],
    collect_repo_factory: Callable[..., BaseCollectRepository],
    interval_sec: float,
    shutdown_timeout_sec: float,
) -> AsyncIterator[None]:
    """lifespan 안에서 `async with lifespan_task_reaper(...):` 로 reaper 기동/정리.

    진입 시 백그라운드 task 시작, 이탈 시 graceful drain(진행 중 tick 완료 대기, 초과 시 cancel).
    """
    stop_event = asyncio.Event()
    reaper_task = asyncio.create_task(
        run_task_reaper(
            session_factory=session_factory,
            collect_repo_factory=collect_repo_factory,
            interval_sec=interval_sec,
            stop_event=stop_event,
        )
    )
    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(reaper_task, timeout=shutdown_timeout_sec)
        except TimeoutError:
            reaper_task.cancel()
            logger.warning("install task reaper shutdown timeout — cancelled")
        except asyncio.CancelledError:
            pass
