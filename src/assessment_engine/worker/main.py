"""전용 백그라운드 워커 — 비동기 보고서 생성 + install task reaper.

web(HTTP 전담)에서 분리한 별도 컨테이너. 두 루프를 공유 stop_event 로 병행 구동하고 SIGTERM 시 함께
graceful 종료한다 — 미완 보고서는 running 으로 남았다가 다음 기동의 recover_stale 이 회수한다.
"""

import asyncio
import signal
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

from loguru import logger

from assessment_engine.cache.redis import close_pool, get_redis
from assessment_engine.db.repositories.collect_sql import SqlCollectRepository
from assessment_engine.db.repositories.diagnostic_sql import SqlDiagnosticRepository
from assessment_engine.db.repositories.query.repository_sql import SqlQueryRepository
from assessment_engine.db.session import dispose_engine, get_session_factory
from assessment_engine.log_config import setup_logging
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.query import QueryService
from assessment_engine.worker.lifecycle import graceful_drain
from assessment_engine.worker.report_loop import run_report_loop
from assessment_engine.worker.settings import get_worker_settings
from assessment_engine.worker.task_reaper import run_task_reaper

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def _query_service_factory() -> AsyncGenerator[QueryService]:
    """job 마다 독립 세션의 QueryService — 생성 쿼리 트랜잭션 분리."""
    async with get_session_factory()() as session:
        yield QueryService(SqlQueryRepository(session), get_redis())


async def main() -> None:
    setup_logging(get_worker_settings().log_format, get_worker_settings().log_level)
    logger.info("worker starting — report generation + install task reaper")

    # asyncio.run 은 SIGTERM(docker stop)을 취소로 바꾸지 않는다 — add_signal_handler 로 공유 stop_event 를
    # set 한다(signal.signal 은 이벤트 루프 밖이라 쓰지 않는다).
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):  # 비 POSIX 방어
            loop.add_signal_handler(sig, stop_event.set)

    diag_service = DiagnosticService(
        session_factory=get_session_factory(),
        diagnostic_repo_factory=SqlDiagnosticRepository,
    )
    report_task = asyncio.create_task(
        run_report_loop(
            diag_service=diag_service,
            query_service_factory=_query_service_factory,
            poll_interval_sec=get_worker_settings().report_worker_poll_interval_sec,
            stale_seconds=get_worker_settings().report_worker_stale_seconds,
            stop_event=stop_event,
        )
    )
    reaper_task = asyncio.create_task(
        run_task_reaper(
            session_factory=get_session_factory(),
            collect_repo_factory=SqlCollectRepository,
            interval_sec=get_worker_settings().install_reaper_interval_sec,
            stop_event=stop_event,
        )
    )
    # 신호와 자식 둘 중 먼저 끝나는 것을 기다린다. stop_event 만 기다리면 자식이 예외로 죽어도 프로세스가
    # 계속 살아 있고, 그 예외는 아무도 회수하지 않은 채 종료 시점까지 잠긴다.
    stop_task = asyncio.create_task(stop_event.wait())
    failures: list[BaseException] = []
    try:
        await asyncio.wait({report_task, reaper_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        logger.info("worker stopping")
    finally:
        # 남은 stop_task 를 정리하지 않으면 "Task was destroyed but it is pending" 이 stdout 으로 나간다.
        stop_task.cancel()
        with suppress(asyncio.CancelledError):
            await stop_task
        # 한 drain 이 자식 예외를 올려도 나머지 정리는 계속돼야 한다 — 각각 격리하고 예외는 모아 둔다.
        try:
            failures = [
                failure
                for failure in (
                    await _drain_logged(
                        report_task,
                        stop_event,
                        get_worker_settings().report_worker_shutdown_timeout_sec,
                        "report worker shutdown timeout — in-flight job left running (stale recovery will requeue)",
                    ),
                    await _drain_logged(
                        reaper_task,
                        stop_event,
                        get_worker_settings().install_reaper_shutdown_timeout_sec,
                        "install task reaper shutdown timeout — cancelled",
                    ),
                )
                if failure is not None
            ]
        finally:
            await dispose_engine()
            await close_pool()

    if failures:
        # 0 으로 나가면 restart 정책이 재기동하지 않아 컨테이너만 살아 있고 두 루프는 죽은 채로 남는다.
        raise failures[0]


async def _drain_logged(
    task: asyncio.Task[None],
    stop_event: asyncio.Event,
    shutdown_timeout_sec: float,
    timeout_warning: str,
) -> BaseException | None:
    """`graceful_drain` + 자식 예외 회수. 삼키는 것이 아니라 뒤 정리를 살리려고 잡았다가 호출자에게 돌려준다."""
    try:
        await graceful_drain(task, stop_event, shutdown_timeout_sec, timeout_warning)
    except Exception as e:  # noqa: BLE001  루프가 무엇으로 죽었든 나머지 정리는 돌아야 한다
        logger.exception("worker loop failed during shutdown")
        return e
    return None
