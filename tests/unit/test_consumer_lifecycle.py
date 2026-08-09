import asyncio
from typing import TYPE_CHECKING, Any, cast

from aio_pika.exceptions import AMQPError

from assessment_engine.consumer import main as consumer_main
from assessment_engine.consumer.main import _drain, _run_logged, _track_inflight
from tests.fakes import FakeMessage, FakeQueue

if TYPE_CHECKING:
    import pytest

    from assessment_engine.consumer.handlers._types import MessageHandler


def _message() -> Any:
    return cast("Any", FakeMessage(b"{}", routing_key="server.metrics", delivery_tag=7))


async def _consume_as_task(handler: MessageHandler) -> None:

    async def _run() -> None:
        await handler(_message())

    await asyncio.create_task(_run())


async def test_run_logged_swallows_handler_exception(captured_logs: list[str]):

    async def boom(_message: Any) -> None:
        raise RuntimeError("handler exploded")

    await _run_logged(boom, _message())

    logged = next(line for line in captured_logs if "handler failed" in line)
    assert "routing_key=server.metrics" in logged
    assert "delivery_tag=7" in logged


async def test_run_logged_quiet_on_success(captured_logs: list[str]):
    async def ok(_message: Any) -> None:
        return None

    await _run_logged(ok, _message())

    assert not any("handler failed" in line for line in captured_logs)


async def test_track_inflight_registers_during_and_clears_after():
    inflight: set[asyncio.Task[Any]] = set()
    seen: list[int] = []

    async def handler(_message: Any) -> None:
        seen.append(len(inflight))

    await _consume_as_task(_track_inflight(handler, inflight))

    assert seen == [1]
    assert inflight == set()


async def test_track_inflight_clears_even_when_handler_raises():
    inflight: set[asyncio.Task[Any]] = set()

    async def boom(_message: Any) -> None:
        raise RuntimeError("boom")

    await _consume_as_task(_track_inflight(boom, inflight))

    assert inflight == set()


async def test_drain_cancels_every_consumer_before_waiting():
    queues = [FakeQueue(), FakeQueue()]
    consumers = [(cast("Any", q), f"tag-{i}") for i, q in enumerate(queues)]

    await _drain(consumers, set())

    assert [q.cancelled for q in queues] == [["tag-0"], ["tag-1"]]


async def test_drain_waits_for_inflight_to_finish():
    done: list[str] = []

    async def work() -> None:
        await asyncio.sleep(0)
        done.append("committed")

    task = asyncio.create_task(work())
    inflight = {task}
    task.add_done_callback(inflight.discard)

    await _drain([(cast("Any", FakeQueue()), "tag")], inflight)

    assert done == ["committed"]


async def test_drain_gives_up_after_budget(monkeypatch: pytest.MonkeyPatch, captured_logs: list[str]):
    monkeypatch.setattr(consumer_main, "_SHUTDOWN_DRAIN_SEC", 0.05)
    stuck = asyncio.create_task(asyncio.Event().wait())

    try:
        await _drain([(cast("Any", FakeQueue()), "tag")], {stuck})
    finally:
        stuck.cancel()

    assert any("drain timeout inflight=1" in line for line in captured_logs)


async def test_drain_survives_broker_already_gone():
    queues = [FakeQueue(error=AMQPError("connection lost")), FakeQueue()]
    consumers = [(cast("Any", q), f"tag-{i}") for i, q in enumerate(queues)]

    await _drain(consumers, set())

    assert queues[1].cancelled == ["tag-1"]


async def test_drain_does_not_hang_on_unresponsive_cancel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(consumer_main, "_SHUTDOWN_DRAIN_SEC", 0.05)
    hanging = FakeQueue(hang=True)

    await _drain([(cast("Any", hanging), "tag")], set())

    assert hanging.cancelled == ["tag"]
