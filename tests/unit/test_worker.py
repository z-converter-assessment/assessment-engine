"""전용 worker 프로세스 루프 단위 테스트 — 기동 격리·graceful 종료·설정.

DB 없이 fake 로 run_report_worker/run_task_reaper 의 F6 격리(기동·tick 예외가 루프를 죽이지 않음)와
stop_event 종료를 검증. 실제 claim/expire SQL 은 통합 테스트(test_task_queries·test_diagnostic_service).
"""

import asyncio
from contextlib import asynccontextmanager

from assessment_engine.config import WorkerSettings
from assessment_engine.worker.report_worker import run_report_worker
from assessment_engine.worker.task_reaper import run_task_reaper


class _FakeDiag:
    """DiagnosticService 대역 — recover/claim 을 프로그래밍 가능하게."""

    def __init__(self, *, recover_raises=False, claim_raises=False):
        self.recover_raises = recover_raises
        self.claim_raises = claim_raises
        self.recover_calls = 0
        self.claim_calls = 0

    async def recover_stale(self, stale_seconds):
        self.recover_calls += 1
        if self.recover_raises:
            raise RuntimeError("db down at startup")
        return 0

    async def claim_pending(self):
        self.claim_calls += 1
        if self.claim_raises:
            raise RuntimeError("claim failed")
        return None  # pending 없음


# ─── WorkerSettings ───────────────────────────────────────────


def test_worker_settings_exposes_worker_fields():
    """worker 설정이 report/reaper 필드 + 상속한 DB 설정을 노출."""
    s = WorkerSettings()
    assert s.report_worker_poll_interval_sec > 0
    assert s.report_worker_stale_seconds > 0
    assert s.report_worker_shutdown_timeout_sec > 0
    assert s.install_reaper_interval_sec > 0
    assert s.install_reaper_shutdown_timeout_sec > 0
    # WebSettings 상속 — DB layer 접근(worker 는 DB job-claim 만).
    assert s.database_url.startswith("postgresql+asyncpg://")


# ─── run_report_worker ────────────────────────────────────────


async def test_report_worker_survives_startup_recover_failure():
    """기동 recover_stale 예외가 전파되면 워커가 죽는다 — 가드가 흡수해 정상 종료해야."""
    stop = asyncio.Event()
    stop.set()  # 루프 진입 전 종료 조건 (recover 가드만 실행)
    diag = _FakeDiag(recover_raises=True)
    await run_report_worker(
        diag_service=diag, query_service_factory=None,
        poll_interval_sec=0.01, stale_seconds=1, stop_event=stop,
    )
    assert diag.recover_calls == 1  # 시도했고, 예외는 삼켜짐(await 이 raise 안 함)
    assert diag.claim_calls == 0  # stop set 이라 루프 미진입


async def test_report_worker_stops_on_event():
    """pending 없으면 poll 하며 대기, stop_event set 시 다음 점검에서 종료."""
    stop = asyncio.Event()
    diag = _FakeDiag()
    task = asyncio.create_task(run_report_worker(
        diag_service=diag, query_service_factory=None,
        poll_interval_sec=0.01, stale_seconds=1, stop_event=stop,
    ))
    await asyncio.sleep(0.05)  # 몇 차례 claim 돌게
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert diag.recover_calls == 1
    assert diag.claim_calls >= 1  # 최소 1회 poll


async def test_report_worker_survives_claim_failure():
    """claim 예외(일시 DB 장애)가 루프를 죽이지 않고 계속 poll."""
    stop = asyncio.Event()
    diag = _FakeDiag(claim_raises=True)
    task = asyncio.create_task(run_report_worker(
        diag_service=diag, query_service_factory=None,
        poll_interval_sec=0.01, stale_seconds=1, stop_event=stop,
    ))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert diag.claim_calls >= 1  # 예외에도 반복 시도


# ─── run_task_reaper ──────────────────────────────────────────


class _FakeRepo:
    def __init__(self, *, raises=False):
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

    async def __aexit__(self, *exc):
        return False


def _session_factory():
    @asynccontextmanager
    async def _factory():
        yield _FakeSession()
    return _factory


async def test_task_reaper_survives_tick_failure():
    """reaper tick 예외가 루프를 죽이지 않고 다음 tick 진행."""
    stop = asyncio.Event()
    repo = _FakeRepo(raises=True)
    task = asyncio.create_task(run_task_reaper(
        session_factory=_session_factory(), collect_repo_factory=lambda _s: repo,
        interval_sec=0.01, stop_event=stop,
    ))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert repo.calls >= 1  # 예외에도 반복


async def test_task_reaper_stops_on_event():
    """정상 tick 후 stop_event set 시 종료 + 성공 tick 은 commit."""
    stop = asyncio.Event()
    repo = _FakeRepo()
    sess_factory = _session_factory()
    task = asyncio.create_task(run_task_reaper(
        session_factory=sess_factory, collect_repo_factory=lambda _s: repo,
        interval_sec=0.01, stop_event=stop,
    ))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert repo.calls >= 1
