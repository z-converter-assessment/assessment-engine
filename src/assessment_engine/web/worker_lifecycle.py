"""web 프로세스 내 백그라운드 워커 공용 lifecycle 유틸 (F11 graceful shutdown 단일 진실).

report_worker(보고서 생성)·task_reaper(install 마감 정리) 등 lifespan 워커가 공유 — sleep_or_stop(대기 단축)·
graceful_drain(SIGTERM 시 진행 중 1건 drain, 초과 시 cancel)을 한 곳에서 정의해 두 워커의 종료 동작이
구조적으로 일치. `signal.signal`·`os._exit` 미사용(F11) — asyncio-native stop_event + wait_for.
"""

import asyncio

from loguru import logger


async def sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    """poll/tick 대기 — stop_event set 되면 즉시 깸(graceful shutdown 시 대기 단축)."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def graceful_drain(
    task: asyncio.Task,
    stop_event: asyncio.Event,
    shutdown_timeout_sec: float,
    timeout_warning: str,
) -> None:
    """graceful shutdown — stop_event set 으로 새 작업 중단 + 진행 중 1건 timeout 안 완료 시도. 초과 시 cancel.

    timeout_warning: 초과 cancel 시 남길 경고 문구(워커별 후속 복구 정책 명시 — 예: stale 회수/requeue).
    """
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=shutdown_timeout_sec)
    except TimeoutError:
        task.cancel()
        logger.warning(timeout_warning)
    except asyncio.CancelledError:
        pass
