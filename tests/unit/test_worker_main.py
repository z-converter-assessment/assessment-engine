"""worker 프로세스 종료 경로 — 정리 순서와 exit 신호 (#F11).

여기가 검증하는 것은 "무엇을 어떤 순서로 닫는가" 다. 순서가 틀리면 남은 asyncpg 커넥션이 루프 종료 후
GC 되면서 "Event loop is closed" 가 stdout 으로 새고 `LOG_FORMAT=json` 계약이 깨진다. 자식 루프가 죽었는데
0 으로 나가면 `restart: unless-stopped` 가 재기동하지 않아 컨테이너만 살아 있는 상태가 된다.

두 자식 루프는 대역으로 바꾼다 — 실제 DB·broker 없이 종료 경로만 보는 것이 목적이다.
"""

import asyncio
import os
import signal
from typing import Any, cast

import pytest

from assessment_engine.worker import main as worker_main


def _fake_diag_service(**_kw: Any) -> Any:
    return object()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """자식 루프·외부 자원 해제를 대역으로 바꾸고 호출 순서를 기록한다."""
    order: list[str] = []

    async def report_loop(**kw: Any) -> None:
        await kw["stop_event"].wait()
        order.append("report drained")

    async def reaper_loop(**kw: Any) -> None:
        await kw["stop_event"].wait()
        order.append("reaper drained")

    async def dispose() -> None:
        order.append("engine disposed")

    async def close() -> None:
        order.append("redis closed")

    monkeypatch.setattr(worker_main, "run_report_loop", report_loop)
    monkeypatch.setattr(worker_main, "run_task_reaper", reaper_loop)
    monkeypatch.setattr(worker_main, "dispose_engine", dispose)
    monkeypatch.setattr(worker_main, "close_pool", close)
    monkeypatch.setattr(worker_main, "get_session_factory", lambda: cast("Any", object()))
    monkeypatch.setattr(worker_main, "DiagnosticService", _fake_diag_service)
    return order


async def test_shutdown_closes_resources_in_order(wired: list[str]):
    """SIGTERM -> 두 루프 drain -> 엔진 dispose -> redis close. 외부 자원 해제가 마지막이다."""
    task = asyncio.create_task(worker_main.main())
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)
    await task

    assert wired == ["report drained", "reaper drained", "engine disposed", "redis closed"]


async def test_child_failure_still_closes_resources_then_propagates(monkeypatch: pytest.MonkeyPatch, wired: list[str]):
    """자식 루프가 죽어도 정리는 끝까지 돌고, 그 뒤 예외가 그대로 나간다 (exit code != 0).

    정리를 건너뛰면 커넥션이 남고, 예외를 삼키면 컨테이너가 재기동 없이 살아 있는 상태가 된다.
    """

    async def dying_loop(**_kw: Any) -> None:
        await asyncio.sleep(0)
        raise RuntimeError("report loop died")

    monkeypatch.setattr(worker_main, "run_report_loop", dying_loop)

    with pytest.raises(RuntimeError, match="report loop died"):
        await worker_main.main()

    assert wired[-2:] == ["engine disposed", "redis closed"]


async def test_signal_handlers_are_asyncio_native(wired: list[str], monkeypatch: pytest.MonkeyPatch):
    """`loop.add_signal_handler` 로 건다 — `signal.signal` 은 종료 race 를 만든다 (#F11 금지)."""
    registered: list[int] = []
    loop = asyncio.get_running_loop()
    original = loop.add_signal_handler

    def record(sig: int, cb: Any, *args: Any) -> None:
        registered.append(sig)
        original(sig, cb, *args)

    monkeypatch.setattr(loop, "add_signal_handler", record)
    task = asyncio.create_task(worker_main.main())
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)
    await task

    assert set(registered) == {signal.SIGTERM, signal.SIGINT}
