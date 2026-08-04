"""전용 백그라운드 워커 프로세스 — 비동기 보고서 생성 + install task reaper.

web(HTTP 전담)에서 분리한 별도 컨테이너(command: assessment_engine.worker). 두 루프를 공유 stop_event 로
병행 구동하고, SIGTERM 시 asyncio-native 로 함께 graceful 종료한다(F11). job/task 상태는 DB 라 메모리
손실 0 — 미완 보고서는 running 잔류 후 다음 기동 recover_stale 가 회수.

Composition Root(F4): DiagnosticService·QueryService·Repository 구체 인스턴스를 여기서 구성해 루프에 주입.
"""

import asyncio
import signal
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

from loguru import logger

from assessment_engine.cache.redis import close_pool, get_redis
from assessment_engine.db.repositories.collect_repository import CollectRepository
from assessment_engine.db.repositories.diagnostic_repository import DiagnosticRepository
from assessment_engine.db.repositories.query.query_repository import QueryRepository
from assessment_engine.db.session import get_session_factory
from assessment_engine.log_config import setup_logging
from assessment_engine.web.services.diagnostic_service import DiagnosticService
from assessment_engine.web.services.query_service import QueryService
from assessment_engine.worker.report_worker import run_report_worker
from assessment_engine.worker.settings import get_worker_settings
from assessment_engine.worker.task_reaper import run_task_reaper
from assessment_engine.worker.worker_lifecycle import graceful_drain

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def _query_service_factory() -> AsyncGenerator[QueryService]:
    """job 마다 독립 세션의 QueryService — 생성 쿼리 트랜잭션 분리."""
    async with get_session_factory()() as session:
        yield QueryService(QueryRepository(session), get_redis())


async def main() -> None:
    setup_logging(get_worker_settings().log_format)
    logger.info("worker starting — report generation + install task reaper")

    # graceful shutdown (F11) — asyncio.run 은 SIGTERM(docker stop)을 취소로 변환하지 않으므로 asyncio-native
    # add_signal_handler 로 공유 stop_event 를 set(signal.signal 아님 — 이벤트 루프 안전). 두 루프가 동시에
    # 다음 점검에서 종료, graceful_drain 이 진행 중 1건 drain.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):  # 비 POSIX 방어
            loop.add_signal_handler(sig, stop_event.set)

    diag_service = DiagnosticService(
        session_factory=get_session_factory(),
        diagnostic_repo_factory=DiagnosticRepository,
    )
    report_task = asyncio.create_task(
        run_report_worker(
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
            collect_repo_factory=CollectRepository,
            interval_sec=get_worker_settings().install_reaper_interval_sec,
            stop_event=stop_event,
        )
    )
    try:
        await stop_event.wait()  # SIGTERM/SIGINT 까지 두 루프 병행 구동 (루프는 내부 격리라 자체 종료 없음)
        logger.info("worker stopping (signal received)")
    finally:
        # 공유 stop_event 로 두 루프 종료 신호 -> 각자 shutdown timeout 안 진행 중 1건 drain, 초과 시 cancel.
        # report 미완 job 은 running 잔류 -> 다음 기동 recover_stale 회수(in-flight 손실 0).
        await graceful_drain(
            report_task,
            stop_event,
            get_worker_settings().report_worker_shutdown_timeout_sec,
            "report worker shutdown timeout — in-flight job left running (stale recovery will requeue)",
        )
        await graceful_drain(
            reaper_task,
            stop_event,
            get_worker_settings().install_reaper_shutdown_timeout_sec,
            "install task reaper shutdown timeout — cancelled",
        )
        await close_pool()
