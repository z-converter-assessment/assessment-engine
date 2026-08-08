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
    task = asyncio.create_task(worker_main.main())
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)
    await task

    assert wired == ["report drained", "reaper drained", "engine disposed", "redis closed"]


async def test_child_failure_still_closes_resources_then_propagates(monkeypatch: pytest.MonkeyPatch, wired: list[str]):

    async def dying_loop(**_kw: Any) -> None:
        await asyncio.sleep(0)
        raise RuntimeError("report loop died")

    monkeypatch.setattr(worker_main, "run_report_loop", dying_loop)

    with pytest.raises(RuntimeError, match="report loop died"):
        await worker_main.main()

    assert wired[-2:] == ["engine disposed", "redis closed"]


async def test_signal_handlers_are_asyncio_native(wired: list[str], monkeypatch: pytest.MonkeyPatch):
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
