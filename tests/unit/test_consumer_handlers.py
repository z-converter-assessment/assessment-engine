"""4 routing key 핸들러 팩토리 characterization — 무엇을 저장하고 무엇을 로그로 내는가.

메시지 본문은 `docs/reference/contracts/wire-examples.json` 정본을 그대로 쓴다. 여기서 payload 를
손으로 다시 쓰면 계약이 바뀌어도 이 파일만 옛 형태로 남는다.

로그는 `captured_logs` 로 실제 렌더 문자열을 본다 — #F8 이 막는 것은 "찍히는 문자열에 원문이
들어가는 것" 이라 format string 과 인자를 따로 보는 방식으로는 검사가 되지 않는다.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from assessment_engine.consumer.handlers import (
    make_error_handler,
    make_inventory_handler,
    make_metrics_handler,
    make_task_result_handler,
)
from assessment_engine.consumer.handlers._common import _format_validation_error, _sanitize_log_text
from assessment_engine.consumer.schemas import MetricsInput
from assessment_engine.consumer.settings import get_consumer_settings
from tests.fakes import FakeMessage, FakeRedis, FakeSessionFactory, InMemoryCollectRepository

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject

_EXAMPLES: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/reference/contracts/wire-examples.json").read_text()
)

# 예시 4종의 agent_id 는 정본이 고정한 값이다 — 등록된 서버를 흉내내려면 그 값으로 대역을 채워야 한다.
_SUCCESS_EXIT_CODES = {"linux": [0], "windows": [0, 3010]}

# 정본 예시의 task_id 는 "task-abc123" 이다. wire 계약이 자유 문자열이라 그 값 자체가 유효하지만
# `tasks.public_id` 는 uuid 컬럼이라 매칭될 수 없다 — 매칭 경로를 보려면 UUID 로 덮어야 한다.
_MATCHABLE_TASK_ID = "00000000-0000-4000-8000-000000000001"


def _body(name: str, **overrides: object) -> bytes:
    """정본 예시 1건을 JSON bytes 로. message_id 는 매번 새로 뽑아 멱등성 1단에 걸리지 않게 한다."""
    payload = cast("JsonObject", dict(_EXAMPLES[name]))
    payload["message_id"] = str(uuid4())
    payload.update(overrides)
    return json.dumps(payload).encode()


def _agent_id(name: str) -> str:
    return cast("str", _EXAMPLES[name]["agent_id"])


def _metrics_handler(repo: InMemoryCollectRepository, redis: FakeRedis):
    return make_metrics_handler(cast("Any", FakeSessionFactory()), lambda _session: repo, cast("Any", redis))


def _inventory_handler(repo: InMemoryCollectRepository, redis: FakeRedis):
    return make_inventory_handler(cast("Any", FakeSessionFactory()), lambda _session: repo, cast("Any", redis))


def _task_result_handler(repo: InMemoryCollectRepository, redis: FakeRedis):
    return make_task_result_handler(
        cast("Any", FakeSessionFactory()),
        lambda _session: repo,
        cast("Any", redis),
        _SUCCESS_EXIT_CODES,
    )


# --- metrics -----------------------------------------------------------------


async def test_metrics_known_server_stores_and_marks_online():
    """등록된 서버: ensure_server_id -> record_metrics, online 키 SET, 상세 캐시 무효화."""
    repo = InMemoryCollectRepository(known_agents={_agent_id("linux_metrics"): 42})
    redis = FakeRedis()
    message = FakeMessage(_body("linux_metrics"))

    await _metrics_handler(repo, redis)(cast("Any", message))

    assert repo.call_names() == ["ensure_server_id", "record_metrics"]
    assert redis.store[get_consumer_settings().redis_key_online.format(42)] == "1"
    assert get_consumer_settings().redis_key_cache_metrics.format(42) not in redis.store


async def test_metrics_unknown_server_auto_registers(captured_logs: list[str]):
    """미등록 서버: placeholder 로 등록하고 그 사실을 INFO 로 남긴다 (다음 inventory 가 채운다)."""
    repo = InMemoryCollectRepository(next_server_id=7)
    redis = FakeRedis()

    await _metrics_handler(repo, redis)(cast("Any", FakeMessage(_body("linux_metrics"))))

    assert repo.call_names() == ["ensure_server_id", "upsert_server", "record_metrics"]
    assert any("auto-registered server from metrics" in line for line in captured_logs)


async def test_metrics_duplicate_message_skips_db(captured_logs: list[str]):
    """멱등성 1단에 걸린 재전송은 DB 를 건드리지 않는다 (#D2)."""
    repo = InMemoryCollectRepository(known_agents={_agent_id("linux_metrics"): 42})
    redis = FakeRedis()
    handler = _metrics_handler(repo, redis)
    body = _body("linux_metrics")

    await handler(cast("Any", FakeMessage(body)))
    repo.calls.clear()
    await handler(cast("Any", FakeMessage(body)))

    assert repo.calls == []
    assert any("metrics duplicate skipped" in line for line in captured_logs)


async def test_metrics_invalid_body_raises_inside_process_context(captured_logs: list[str]):
    """검증 실패는 `message.process` 컨텍스트 안에서 raise — 그래야 nack 이 성립한다 (#F11)."""
    repo = InMemoryCollectRepository()
    message = FakeMessage(b'{"message_type": "metrics"}')

    with pytest.raises(ValueError, match="metrics validation failed"):
        await _metrics_handler(repo, FakeRedis())(cast("Any", message))

    assert message.entered
    assert message.exited
    assert isinstance(message.raised, ValueError)
    assert repo.calls == []
    assert any("metrics parse error" in line for line in captured_logs)


async def test_metrics_windows_example_stores():
    """윈도우 예시도 같은 경로 — OS 분기는 mapper 안쪽이라 핸들러 흐름은 하나다."""
    repo = InMemoryCollectRepository(known_agents={_agent_id("windows_metrics"): 8})

    await _metrics_handler(repo, FakeRedis())(cast("Any", FakeMessage(_body("windows_metrics"))))

    assert repo.call_names() == ["ensure_server_id", "record_metrics"]


# --- inventory ---------------------------------------------------------------


async def test_inventory_upserts_and_invalidates_cache(captured_logs: list[str]):
    repo = InMemoryCollectRepository(next_server_id=5)
    redis = FakeRedis(
        {
            get_consumer_settings().redis_key_cache_inventory.format(5): "stale",
        }
    )

    await _inventory_handler(repo, redis)(cast("Any", FakeMessage(_body("linux_inventory"))))

    assert repo.call_names() == ["upsert_server"]
    assert redis.store[get_consumer_settings().redis_key_online.format(5)] == "1"
    assert get_consumer_settings().redis_key_cache_inventory.format(5) not in redis.store
    assert any("inventory stored" in line for line in captured_logs)


async def test_inventory_invalid_body_raises_without_payload_in_log(captured_logs: list[str]):
    """검증 오류 로그에 원문 조각이 실리지 않는다 (#F8) — 필드 경로와 오류 종류만."""
    repo = InMemoryCollectRepository()
    marker = "supersecret-hostname-value"
    body = json.dumps({"message_type": "inventory", "hostname": {"nested": marker}}).encode()

    with pytest.raises(ValueError, match="inventory validation failed"):
        await _inventory_handler(repo, FakeRedis())(cast("Any", FakeMessage(body)))

    assert not any(marker in line for line in captured_logs)


# --- error -------------------------------------------------------------------


async def test_error_message_logged_as_warning(captured_logs: list[str]):
    await make_error_handler(cast("Any", FakeRedis()))(cast("Any", FakeMessage(_body("error"))))

    assert any("agent error agent_id=" in line for line in captured_logs)


async def test_error_message_sanitized_before_logging(captured_logs: list[str]):
    """개행이 섞인 error_message 는 로그 줄을 위조할 수 있다 — 인쇄 가능 문자만 남긴다."""
    forged = "boom\nlevel=CRITICAL fake line"
    body = _body("error", error_message=forged)

    await make_error_handler(cast("Any", FakeRedis()))(cast("Any", FakeMessage(body)))

    logged = next(line for line in captured_logs if "agent error agent_id=" in line).rstrip("\n")
    assert "\n" not in logged
    assert "boomlevel=CRITICAL fake line" in logged


async def test_error_message_empty_renders_placeholder(captured_logs: list[str]):
    """정제 후 빈 문자열이면 자리표시자 — `msg=` 뒤가 비면 다음 필드와 붙어 읽힌다."""
    await make_error_handler(cast("Any", FakeRedis()))(cast("Any", FakeMessage(_body("error", error_message="\x00"))))

    assert any("msg=(empty)" in line for line in captured_logs)


# --- task.result -------------------------------------------------------------


async def test_task_result_completes_task(captured_logs: list[str]):
    repo = InMemoryCollectRepository()

    body = _body("task_result", task_id=_MATCHABLE_TASK_ID)

    await _task_result_handler(repo, FakeRedis())(cast("Any", FakeMessage(body)))

    assert repo.call_names() == ["complete_task"]
    assert any("task_result stored" in line for line in captured_logs)


async def test_task_result_non_uuid_task_id_silently_acks(captured_logs: list[str]):
    """비 UUID task_id 는 매칭될 수 없다 — DB 를 건드리지 않고 ack (DLQ 부적합).

    정본 예시가 이미 그 형태("task-abc123")다. wire 는 자유 문자열을 허용하고 매칭은 여기서 갈린다.
    """
    repo = InMemoryCollectRepository()

    await _task_result_handler(repo, FakeRedis())(cast("Any", FakeMessage(_body("task_result"))))

    assert repo.calls == []
    assert any("task_id not uuid" in line for line in captured_logs)


async def test_task_result_unknown_task_silently_acks(captured_logs: list[str]):
    """UPDATE 가 0행이면 운영자가 지운 task — 경고만 남기고 ack."""
    repo = InMemoryCollectRepository(complete_task_ok=False)
    body = _body("task_result", task_id=_MATCHABLE_TASK_ID)

    await _task_result_handler(repo, FakeRedis())(cast("Any", FakeMessage(body)))

    assert repo.call_names() == ["complete_task"]
    assert any("for unknown task_id" in line for line in captured_logs)


async def test_task_result_status_remap_logged(captured_logs: list[str]):
    """정책 보정으로 status 가 바뀌면 그 사실을 따로 남긴다 — 저장값과 발행값이 갈리는 유일한 지점."""
    repo = InMemoryCollectRepository()
    body = _body(
        "task_result",
        task_id=_MATCHABLE_TASK_ID,
        status="failure",
        failure_reason="exit_code",
        exit_code=0,
        task_policy=True,
    )

    await _task_result_handler(repo, FakeRedis())(cast("Any", FakeMessage(body)))

    assert any("task_result status remapped" in line for line in captured_logs)
    _, update = repo.calls[0]
    assert update.status == "success"


# --- 로그 정제 helper --------------------------------------------------------


def test_sanitize_log_text_drops_control_chars_and_truncates():
    assert _sanitize_log_text("a\nb\tc", 100) == "abc"
    assert _sanitize_log_text("x" * 10, 4) == "xxxx~"


def test_format_validation_err_keeps_paths_not_values():
    """`msg` 를 싣지 않는다 — pydantic 의 일부 오류 종류가 실패한 입력 문자를 그 안에 넣는다."""
    marker = "leaked-uuid-value"
    with pytest.raises(ValidationError) as excinfo:
        MetricsInput.model_validate({"message_id": marker})

    detail = _format_validation_error(excinfo.value)

    assert marker not in detail
    assert "message_id=" in detail
    assert detail.startswith("count=")


def test_format_validation_err_caps_field_count():
    """필드가 많은 메시지는 상위 몇 건만 — 레코드 하나가 임의 크기로 부풀지 않게 한다 (#F7)."""
    with pytest.raises(ValidationError) as excinfo:
        MetricsInput.model_validate({})

    detail = _format_validation_error(excinfo.value, limit=2)

    assert "more" in detail
    assert detail.count("=") <= 4  # count= + 2 필드 + more 접미
