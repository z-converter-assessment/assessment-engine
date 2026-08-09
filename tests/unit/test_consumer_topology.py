import asyncio
from typing import Any, cast

import aio_pika
import pytest

from assessment_engine.consumer import main as consumer_main


class _FakeQueue:
    def __init__(self, name: str, arguments: dict[str, Any] | None) -> None:
        self.name = name
        self.arguments = arguments or {}
        self.bindings: list[tuple[str, str]] = []

    async def bind(self, exchange: Any, routing_key: str) -> None:
        self.bindings.append((exchange.name, routing_key))

    async def consume(self, handler: Any) -> str:
        return f"tag-{self.name}"

    async def cancel(self, consumer_tag: str) -> None:
        return None


class _FakeExchange:
    def __init__(self, name: str, kind: Any, durable: bool) -> None:
        self.name = name
        self.kind = kind
        self.durable = durable


class _FakeChannel:
    def __init__(self) -> None:
        self.exchanges: list[_FakeExchange] = []
        self.queues: list[_FakeQueue] = []
        self.prefetch: int | None = None

    async def set_qos(self, prefetch_count: int) -> None:
        self.prefetch = prefetch_count

    async def declare_exchange(self, name: str, kind: Any, durable: bool = False) -> _FakeExchange:
        ex = _FakeExchange(name, kind, durable)
        self.exchanges.append(ex)
        return ex

    async def declare_queue(self, name: str, durable: bool = False, arguments: dict[str, Any] | None = None):
        q = _FakeQueue(name, arguments)
        self.queues.append(q)
        return q

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeConnection:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    def channel(self) -> _FakeChannel:
        return self._channel

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
async def declared(monkeypatch: pytest.MonkeyPatch) -> _FakeChannel:
    channel = _FakeChannel()

    async def fake_connect(url: str, timeout: int = 10) -> _FakeConnection:  # noqa: ASYNC109
        return _FakeConnection(channel)

    monkeypatch.setattr(consumer_main.aio_pika, "connect_robust", fake_connect)
    monkeypatch.setattr(consumer_main, "get_redis", lambda: cast("Any", object()))
    monkeypatch.setattr(consumer_main, "get_session_factory", lambda: cast("Any", object()))
    monkeypatch.setattr(consumer_main, "close_pool", _noop)
    monkeypatch.setattr(consumer_main, "dispose_engine", _noop)

    task = asyncio.create_task(consumer_main.main())
    for _ in range(200):
        await asyncio.sleep(0)
        if len(channel.queues) >= 8:
            break
    consumer_main.asyncio.get_running_loop().call_soon(lambda: None)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return channel


async def _noop() -> None:
    return None


async def test_declares_four_direct_durable_exchanges(declared: _FakeChannel):
    names = [e.name for e in declared.exchanges]

    assert len(names) == 4
    assert all(e.durable for e in declared.exchanges)
    assert all(e.kind is aio_pika.ExchangeType.DIRECT for e in declared.exchanges)
    assert [n for n in names if n.endswith(".dlx")] == sorted(n for n in names if n.endswith(".dlx"))


async def test_every_queue_has_a_dead_letter_pair(declared: _FakeChannel):
    live = {q.name for q in declared.queues if not q.name.endswith(".dead")}
    dead = {q.name.removesuffix(".dead") for q in declared.queues if q.name.endswith(".dead")}

    assert live == dead
    assert len(live) == 4


async def test_live_queues_route_failures_to_their_dlx(declared: _FakeChannel):
    for q in (q for q in declared.queues if not q.name.endswith(".dead")):
        assert q.arguments["x-dead-letter-exchange"].endswith(".dlx"), q.name
        assert q.arguments["x-dead-letter-routing-key"] == q.name


@pytest.mark.parametrize(
    ("queue_suffix", "ttl_ms", "max_len"),
    [
        ("metrics", consumer_main._METRICS_TTL_MS, consumer_main._METRICS_MAX_LEN),
        ("error", consumer_main._ERROR_TTL_MS, None),
        ("result", consumer_main._TASK_RESULT_TTL_MS, consumer_main._TASK_RESULT_MAX_LEN),
    ],
)
async def test_queue_policy_matches_declared_constants(
    declared: _FakeChannel, queue_suffix: str, ttl_ms: int, max_len: int | None
):
    q = next(q for q in declared.queues if q.name.endswith(queue_suffix) and not q.name.endswith(".dead"))

    assert q.arguments["x-message-ttl"] == ttl_ms
    assert q.arguments.get("x-max-length") == max_len


async def test_inventory_queue_has_no_expiry(declared: _FakeChannel):
    q = next(q for q in declared.queues if q.name.endswith("inventory") and not q.name.endswith(".dead"))

    assert "x-message-ttl" not in q.arguments
    assert "x-max-length" not in q.arguments


async def test_prefetch_is_set_before_consuming(declared: _FakeChannel):
    assert declared.prefetch == 10
