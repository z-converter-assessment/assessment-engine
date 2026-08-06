"""consumer 가 broker 에 선언하는 토폴로지 — exchange·큐·DLX 바인딩·큐 정책 (#B · rabbitmq.md).

큐 정책(TTL·max-length)은 broker 에 한 번 선언되면 인자가 바뀔 때 재선언이 PRECONDITION_FAILED 로 실패한다.
즉 이 값들은 코드 상수가 아니라 운영 중 broker 상태와 맞물린 계약이고, 바뀌면 큐를 수동 삭제해야 한다.
그래서 "무엇을 어떤 인자로 선언하는가" 를 값으로 고정한다.

대역은 aio-pika 표면 중 `main()` 이 실제로 부르는 것만 갖는다 — 실 broker 접속 0.
"""

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
    """`main()` 을 한 바퀴 돌려 선언된 토폴로지를 회수한다. 기동 직후 종료 신호로 루프를 빠져나온다."""
    channel = _FakeChannel()

    async def fake_connect(url: str, timeout: int = 10) -> _FakeConnection:  # noqa: ASYNC109  aio-pika 시그니처
        return _FakeConnection(channel)

    monkeypatch.setattr(consumer_main.aio_pika, "connect_robust", fake_connect)
    monkeypatch.setattr(consumer_main, "get_redis", lambda: cast("Any", object()))
    monkeypatch.setattr(consumer_main, "get_session_factory", lambda: cast("Any", object()))
    monkeypatch.setattr(consumer_main, "close_pool", _noop)
    monkeypatch.setattr(consumer_main, "dispose_engine", _noop)

    task = asyncio.create_task(consumer_main.main())
    for _ in range(200):  # 선언이 끝나고 stop_event 대기에 들어갈 때까지
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
    """수집·작업 2계열 x (본 exchange + DLX) — 전부 DIRECT·durable."""
    names = [e.name for e in declared.exchanges]

    assert len(names) == 4
    assert all(e.durable for e in declared.exchanges)
    assert all(e.kind is aio_pika.ExchangeType.DIRECT for e in declared.exchanges)
    assert [n for n in names if n.endswith(".dlx")] == sorted(n for n in names if n.endswith(".dlx"))


async def test_every_queue_has_a_dead_letter_pair(declared: _FakeChannel):
    """본 큐마다 `{queue}.dead` 가 하나씩 — DLQ 없는 큐가 생기면 실패 메시지가 사라진다."""
    live = {q.name for q in declared.queues if not q.name.endswith(".dead")}
    dead = {q.name.removesuffix(".dead") for q in declared.queues if q.name.endswith(".dead")}

    assert live == dead
    assert len(live) == 4


async def test_live_queues_route_failures_to_their_dlx(declared: _FakeChannel):
    """본 큐 인자에 DLX·routing key 가 박혀 있어야 nack 이 DLQ 로 간다."""
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
    """큐 정책은 모듈 상수와 1:1 — 값을 바꾸면 broker 큐를 수동 삭제해야 하므로 조용히 바뀌면 안 된다."""
    q = next(q for q in declared.queues if q.name.endswith(queue_suffix) and not q.name.endswith(".dead"))

    assert q.arguments["x-message-ttl"] == ttl_ms
    assert q.arguments.get("x-max-length") == max_len


async def test_inventory_queue_has_no_expiry(declared: _FakeChannel):
    """인벤토리는 TTL·max-length 를 걸지 않는다 — 정적 정보라 오래된 메시지도 버릴 이유가 없다."""
    q = next(q for q in declared.queues if q.name.endswith("inventory") and not q.name.endswith(".dead"))

    assert "x-message-ttl" not in q.arguments
    assert "x-max-length" not in q.arguments


async def test_prefetch_is_set_before_consuming(declared: _FakeChannel):
    """prefetch 미설정이면 broker 가 한 컨슈머에 메시지를 무제한 밀어 넣는다."""
    assert declared.prefetch == 10
