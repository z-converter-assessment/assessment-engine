import asyncio
import contextlib
import re
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import pytest
from loguru import logger

from assessment_engine.config import WorkerSettings
from assessment_engine.db.session import dispose_engine, get_engine
from assessment_engine.web.services.report import ReportGenerationError
from assessment_engine.web.services.task_service import (
    TaskNotConfiguredError,
    _resolve_install_dispatch,
)
from assessment_engine.web.settings import get_web_settings
from assessment_engine.worker import report_loop
from assessment_engine.worker.lifecycle import graceful_drain
from assessment_engine.worker.main import _drain_logged
from assessment_engine.worker.report_loop import run_report_loop
from assessment_engine.worker.task_reaper import run_task_reaper

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from assessment_engine.db.repositories.collect import CollectRepository
    from assessment_engine.web.services.diagnostic_service import DiagnosticService
    from assessment_engine.web.services.query import QueryService


def _no_query_service() -> AbstractAsyncContextManager[QueryService]:
    raise AssertionError("query_service_factory 가 호출되면 안 되는 경로다")


class _FakeDiag:
    def __init__(self, *, recover_raises: bool = False, claim_raises: bool = False) -> None:
        self.recover_raises = recover_raises
        self.claim_raises = claim_raises
        self.recover_calls = 0
        self.claim_calls = 0

    async def recover_stale(self, stale_seconds: int) -> int:
        self.recover_calls += 1
        if self.recover_raises:
            raise RuntimeError("db down at startup")
        return 0

    async def claim_pending(self):
        self.claim_calls += 1
        if self.claim_raises:
            raise RuntimeError("claim failed")


def test_worker_settings_exposes_worker_fields():
    s = WorkerSettings()  # pyright: ignore[reportCallIssue]
    assert s.report_worker_poll_interval_sec > 0
    assert s.report_worker_stale_seconds > 0
    assert s.report_worker_shutdown_timeout_sec > 0
    assert s.install_reaper_interval_sec > 0
    assert s.install_reaper_shutdown_timeout_sec > 0
    assert s.database_url.startswith("postgresql+asyncpg://")


async def test_report_loop_survives_startup_recover_failure():
    stop = asyncio.Event()
    stop.set()
    diag = _FakeDiag(recover_raises=True)
    await run_report_loop(
        diag_service=cast("DiagnosticService", diag),
        query_service_factory=_no_query_service,
        poll_interval_sec=0.01,
        stale_seconds=1,
        stop_event=stop,
    )
    assert diag.recover_calls == 1
    assert diag.claim_calls == 0


async def test_report_loop_stops_on_event():
    stop = asyncio.Event()
    diag = _FakeDiag()
    task = asyncio.create_task(
        run_report_loop(
            diag_service=cast("DiagnosticService", diag),
            query_service_factory=_no_query_service,
            poll_interval_sec=0.01,
            stale_seconds=1,
            stop_event=stop,
        )
    )
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert diag.recover_calls == 1
    assert diag.claim_calls >= 1


async def test_report_loop_survives_claim_failure():
    stop = asyncio.Event()
    diag = _FakeDiag(claim_raises=True)
    task = asyncio.create_task(
        run_report_loop(
            diag_service=cast("DiagnosticService", diag),
            query_service_factory=_no_query_service,
            poll_interval_sec=0.01,
            stale_seconds=1,
            stop_event=stop,
        )
    )
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert diag.claim_calls >= 1


class _FakeRepo:
    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.calls = 0

    async def expire_all_overdue_tasks(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("db down")
        return 0


class _FakeSession:
    def __init__(self):
        self.committed = 0

    async def commit(self):
        self.committed += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _collect(messages: list[str]) -> Callable[[Any], None]:

    def _sink(message: Any) -> None:
        messages.append(str(message.record["message"]))

    return _sink


def _repo_factory(repo: object) -> Callable[[AsyncSession], CollectRepository]:

    def _factory(_s: AsyncSession) -> CollectRepository:
        return cast("CollectRepository", repo)

    return _factory


def _session_factory() -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    @asynccontextmanager
    async def _factory() -> AsyncGenerator[AsyncSession]:
        yield cast("AsyncSession", _FakeSession())

    return _factory


async def test_task_reaper_survives_tick_failure():
    stop = asyncio.Event()
    repo = _FakeRepo(raises=True)
    task = asyncio.create_task(
        run_task_reaper(
            session_factory=_session_factory(),
            collect_repo_factory=_repo_factory(repo),
            interval_sec=0.01,
            stop_event=stop,
        )
    )
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert repo.calls >= 1


async def test_task_reaper_stops_on_event():
    stop = asyncio.Event()
    repo = _FakeRepo()
    sess_factory = _session_factory()
    task = asyncio.create_task(
        run_task_reaper(
            session_factory=sess_factory,
            collect_repo_factory=_repo_factory(repo),
            interval_sec=0.01,
            stop_event=stop,
        )
    )
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert repo.calls >= 1


async def test_graceful_drain_completes_within_timeout():
    stop = asyncio.Event()
    done = asyncio.Event()

    async def quick():
        await asyncio.sleep(0.01)
        done.set()
        return "ok"

    task = asyncio.create_task(quick())
    messages: list[str] = []
    sink_id = logger.add(_collect(messages), level="WARNING")
    try:
        await graceful_drain(
            task,
            stop,
            shutdown_timeout_sec=1.0,
            timeout_warning="should-not-fire",
        )
    finally:
        logger.remove(sink_id)

    assert stop.is_set()
    assert done.is_set()
    assert not task.cancelled()
    assert task.result() == "ok"
    assert messages == []


async def test_graceful_drain_cancels_on_timeout():
    stop = asyncio.Event()
    never = asyncio.Event()

    async def stuck():
        await never.wait()

    task = asyncio.create_task(stuck())
    messages: list[str] = []
    sink_id = logger.add(_collect(messages), level="WARNING")
    try:
        await graceful_drain(
            task,
            stop,
            shutdown_timeout_sec=0.02,
            timeout_warning="drain overrun requeue",
        )
    finally:
        logger.remove(sink_id)

    assert stop.is_set()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert any("drain overrun requeue" in m for m in messages)


async def test_graceful_drain_swallows_already_cancelled():
    stop = asyncio.Event()
    never = asyncio.Event()

    async def stuck():
        await never.wait()

    task = asyncio.create_task(stuck())
    await asyncio.sleep(0)
    task.cancel()
    await graceful_drain(
        task,
        stop,
        shutdown_timeout_sec=1.0,
        timeout_warning="w",
    )
    assert stop.is_set()


def test_resolve_install_dispatch_linux():
    package_path, install_type, install_script = _resolve_install_dispatch("linux")
    assert package_path == get_web_settings().zdm_package_path
    assert install_type == "shell"
    assert install_script == get_web_settings().zdm_package_script
    assert install_script is not None


def test_resolve_install_dispatch_windows():
    package_path, install_type, install_script = _resolve_install_dispatch("windows")
    assert package_path == get_web_settings().zdm_package_path_windows
    assert install_type == "direct_exec"
    assert install_script is None


def test_resolve_install_dispatch_linux_windows_differ():
    linux = _resolve_install_dispatch("linux")
    windows = _resolve_install_dispatch("windows")
    assert linux[0] != windows[0]
    assert linux[1] != windows[1]


def test_resolve_install_dispatch_unsupported_raises():
    for bad in ("macos", "aix", "", "Linux", "darwin"):
        with pytest.raises(TaskNotConfiguredError, match=re.escape(repr(bad))):
            _resolve_install_dispatch(bad)


async def test_drain_logged_returns_none_on_clean_shutdown():
    stop = asyncio.Event()

    async def quick() -> None:
        await stop.wait()

    failure = await _drain_logged(asyncio.create_task(quick()), stop, 1.0, "w")

    assert failure is None


async def test_drain_logged_returns_child_exception(captured_logs: list[str]):
    stop = asyncio.Event()

    async def boom() -> None:
        raise RuntimeError("loop died")

    task = asyncio.create_task(boom())
    await asyncio.sleep(0)

    failure = await _drain_logged(task, stop, 1.0, "w")

    assert isinstance(failure, RuntimeError)
    assert any("worker loop failed during shutdown" in line for line in captured_logs)


async def test_dispose_engine_noop_without_engine():
    get_engine.cache_clear()

    await dispose_engine()

    assert get_engine.cache_info().currsize == 0


class _ProcessDiag:
    def __init__(self) -> None:
        self.succeeded: list[tuple[str, object]] = []
        self.failed: list[tuple[str, str]] = []

    async def finish_succeeded(self, job_id: str, result: object) -> None:
        self.succeeded.append((job_id, result))

    async def finish_failed(self, job_id: str, message: str) -> None:
        self.failed.append((job_id, message))


class _Job:
    id = "job-1"
    scope = "environment"


@asynccontextmanager
async def _query_service() -> AsyncGenerator[Any]:
    yield cast("Any", object())


async def _run_process_one(monkeypatch: pytest.MonkeyPatch, build: Any) -> _ProcessDiag:
    monkeypatch.setattr(report_loop, "build_report_result_for_job", build)
    diag = _ProcessDiag()
    await report_loop._process_one(cast("Any", diag), _query_service, cast("Any", _Job()))
    return diag


async def test_process_one_stores_generated_report(monkeypatch: pytest.MonkeyPatch):

    async def build(*_args: Any) -> dict[str, str]:
        return {"kind": "env_report"}

    diag = await _run_process_one(monkeypatch, build)

    assert diag.succeeded == [("job-1", {"kind": "env_report"})]
    assert diag.failed == []


async def test_process_one_surfaces_domain_reason(monkeypatch: pytest.MonkeyPatch, captured_logs: list[str]):

    async def build(*_args: Any) -> dict[str, str]:
        raise ReportGenerationError("등록된 서버가 없다")

    diag = await _run_process_one(monkeypatch, build)

    assert diag.failed == [("job-1", "등록된 서버가 없다")]
    assert any("report job failed (generation)" in line for line in captured_logs)


async def test_process_one_hides_internal_error_detail(monkeypatch: pytest.MonkeyPatch, captured_logs: list[str]):
    leaked = "postgres://user:pw@host/db"

    async def build(*_args: Any) -> dict[str, str]:
        raise RuntimeError(leaked)

    diag = await _run_process_one(monkeypatch, build)

    assert diag.failed == [("job-1", "internal error")]
    assert leaked not in diag.failed[0][1]
    assert any("report job failed (internal)" in line for line in captured_logs)
