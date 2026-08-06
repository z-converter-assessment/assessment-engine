"""consumer 종료 경로 — `_run_logged` / `_track_inflight` / `_drain` (#F11).

여기가 데이터 손실 창의 관문이다. 배달을 끊기 전에 채널이 닫히면 진행 중 핸들러가 커밋 직전에
취소되고, 재전송된 메시지는 멱등성 1단(#D2)에 중복으로 걸려 조용히 사라진다.

drain 예산(`_SHUTDOWN_DRAIN_SEC`)은 5초라 그대로 두면 초과 분기 하나가 테스트를 5초 붙잡는다.
모듈 자기 상수를 짧게 덮어 그 분기를 실제로 실행한다.
"""

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
    """aio-pika 가 콜백을 띄우는 방식 그대로 — `_track_inflight` 는 `current_task()` 로 자신을 찾는다."""

    async def _run() -> None:
        await handler(_message())

    await asyncio.create_task(_run())


# --- _run_logged -------------------------------------------------------------


async def test_run_logged_swallows_handler_exception(captured_logs: list[str]):
    """핸들러 예외를 여기서 회수하지 않으면 asyncio 기본 핸들러가 평문 traceback 을 낸다 (#F7).

    ack/nack 은 핸들러 안 `message.process` 가 이미 끝냈으므로 삼켜도 배달 처리에 영향이 없다.
    """

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


# --- _track_inflight ---------------------------------------------------------


async def test_track_inflight_registers_during_and_clears_after():
    """진행 중에는 집합에 있고 끝나면 빠진다 — `_drain` 이 기다릴 대상 목록이다."""
    inflight: set[asyncio.Task[Any]] = set()
    seen: list[int] = []

    async def handler(_message: Any) -> None:
        seen.append(len(inflight))

    await _consume_as_task(_track_inflight(handler, inflight))

    assert seen == [1]
    assert inflight == set()


async def test_track_inflight_clears_even_when_handler_raises():
    """예외 경로에서 등록이 남으면 다음 종료가 영원히 그 유령을 기다린다."""
    inflight: set[asyncio.Task[Any]] = set()

    async def boom(_message: Any) -> None:
        raise RuntimeError("boom")

    # _run_logged 가 안쪽에서 회수하므로 여기까지 예외가 오지 않는다.
    await _consume_as_task(_track_inflight(boom, inflight))

    assert inflight == set()


# --- _drain ------------------------------------------------------------------


async def test_drain_cancels_every_consumer_before_waiting():
    """배달을 먼저 끊는다. basic.cancel 은 이미 배달된 메시지의 ack 를 막지 않는다."""
    queues = [FakeQueue(), FakeQueue()]
    consumers = [(cast("Any", q), f"tag-{i}") for i, q in enumerate(queues)]

    await _drain(consumers, set())

    assert [q.cancelled for q in queues] == [["tag-0"], ["tag-1"]]


async def test_drain_waits_for_inflight_to_finish():
    """진행 중 핸들러가 ack/nack 을 마칠 때까지 기다린다 — 예산 안이면 전부 완주한다."""
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
    """예산을 넘기면 경고를 남기고 포기한다 — 미완 메시지는 unack 이라 재전송된다."""
    monkeypatch.setattr(consumer_main, "_SHUTDOWN_DRAIN_SEC", 0.05)
    stuck = asyncio.create_task(asyncio.Event().wait())

    try:
        await _drain([(cast("Any", FakeQueue()), "tag")], {stuck})
    finally:
        stuck.cancel()

    assert any("drain timeout inflight=1" in line for line in captured_logs)


async def test_drain_survives_broker_already_gone():
    """broker 가 이미 끊긴 상태의 cancel 실패는 종료를 막지 않는다 — 남은 메시지는 재전송된다."""
    queues = [FakeQueue(error=AMQPError("connection lost")), FakeQueue()]
    consumers = [(cast("Any", q), f"tag-{i}") for i, q in enumerate(queues)]

    await _drain(consumers, set())

    assert queues[1].cancelled == ["tag-1"]


async def test_drain_does_not_hang_on_unresponsive_cancel(monkeypatch: pytest.MonkeyPatch):
    """robust 채널의 cancel 은 재연결 완료를 무기한 기다린다 — 호출 전체가 예산에 묶여 있어야 한다."""
    monkeypatch.setattr(consumer_main, "_SHUTDOWN_DRAIN_SEC", 0.05)
    hanging = FakeQueue(hang=True)

    await _drain([(cast("Any", hanging), "tag")], set())

    assert hanging.cancelled == ["tag"]
