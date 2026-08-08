import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from assessment_engine.consumer.handlers._common import _format_validation_error, _sanitize_log_text
from assessment_engine.consumer.handlers.error import make_error_handler
from assessment_engine.consumer.handlers.inventory import make_inventory_handler
from assessment_engine.consumer.handlers.metrics import make_metrics_handler
from assessment_engine.consumer.handlers.task_result import make_task_result_handler
from assessment_engine.consumer.schemas import MetricsInput
from assessment_engine.consumer.settings import get_consumer_settings
from tests.fakes import FakeMessage, FakeRedis, FakeSessionFactory, InMemoryCollectRepository

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject

_EXAMPLES: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/reference/contracts/wire-examples.json").read_text()
)

_SUCCESS_EXIT_CODES = {"linux": [0], "windows": [0, 3010]}

_MATCHABLE_TASK_ID = "00000000-0000-4000-8000-000000000001"


def _body(name: str, **overrides: object) -> bytes:
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


async def test_metrics_known_server_stores_and_marks_online():
    repo = InMemoryCollectRepository(known_agents={_agent_id("linux_metrics"): 42})
    redis = FakeRedis()
    message = FakeMessage(_body("linux_metrics"))

    await _metrics_handler(repo, redis)(cast("Any", message))

    assert repo.call_names() == ["ensure_server_id", "record_metrics"]
    assert redis.store[get_consumer_settings().redis_key_online.format(42)] == "1"
    assert get_consumer_settings().redis_key_cache_metrics.format(42) not in redis.store


async def test_metrics_unknown_server_auto_registers(captured_logs: list[str]):
    repo = InMemoryCollectRepository(next_server_id=7)
    redis = FakeRedis()

    await _metrics_handler(repo, redis)(cast("Any", FakeMessage(_body("linux_metrics"))))

    assert repo.call_names() == ["ensure_server_id", "upsert_server", "record_metrics"]
    assert any("auto-registered server from metrics" in line for line in captured_logs)


async def test_metrics_duplicate_message_skips_db(captured_logs: list[str]):
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
    repo = InMemoryCollectRepository(known_agents={_agent_id("windows_metrics"): 8})

    await _metrics_handler(repo, FakeRedis())(cast("Any", FakeMessage(_body("windows_metrics"))))

    assert repo.call_names() == ["ensure_server_id", "record_metrics"]


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
    repo = InMemoryCollectRepository()
    marker = "supersecret-hostname-value"
    body = json.dumps({"message_type": "inventory", "hostname": {"nested": marker}}).encode()

    with pytest.raises(ValueError, match="inventory validation failed"):
        await _inventory_handler(repo, FakeRedis())(cast("Any", FakeMessage(body)))

    assert not any(marker in line for line in captured_logs)


async def test_error_message_logged_as_warning(captured_logs: list[str]):
    await make_error_handler(cast("Any", FakeRedis()))(cast("Any", FakeMessage(_body("error"))))

    assert any("agent error agent_id=" in line for line in captured_logs)


async def test_error_message_sanitized_before_logging(captured_logs: list[str]):
    forged = "boom\nlevel=CRITICAL fake line"
    body = _body("error", error_message=forged)

    await make_error_handler(cast("Any", FakeRedis()))(cast("Any", FakeMessage(body)))

    logged = next(line for line in captured_logs if "agent error agent_id=" in line).rstrip("\n")
    assert "\n" not in logged
    assert "boomlevel=CRITICAL fake line" in logged


async def test_error_message_empty_renders_placeholder(captured_logs: list[str]):
    await make_error_handler(cast("Any", FakeRedis()))(cast("Any", FakeMessage(_body("error", error_message="\x00"))))

    assert any("msg=(empty)" in line for line in captured_logs)


async def test_task_result_completes_task(captured_logs: list[str]):
    repo = InMemoryCollectRepository()

    body = _body("task_result", task_id=_MATCHABLE_TASK_ID)

    await _task_result_handler(repo, FakeRedis())(cast("Any", FakeMessage(body)))

    assert repo.call_names() == ["complete_task"]
    assert any("task_result stored" in line for line in captured_logs)


async def test_task_result_non_uuid_task_id_silently_acks(captured_logs: list[str]):
    repo = InMemoryCollectRepository()

    await _task_result_handler(repo, FakeRedis())(cast("Any", FakeMessage(_body("task_result"))))

    assert repo.calls == []
    assert any("task_id not uuid" in line for line in captured_logs)


async def test_task_result_unknown_task_silently_acks(captured_logs: list[str]):
    repo = InMemoryCollectRepository(complete_task_ok=False)
    body = _body("task_result", task_id=_MATCHABLE_TASK_ID)

    await _task_result_handler(repo, FakeRedis())(cast("Any", FakeMessage(body)))

    assert repo.call_names() == ["complete_task"]
    assert any("for unknown task_id" in line for line in captured_logs)


async def test_task_result_status_remap_logged(captured_logs: list[str]):
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


def test_sanitize_log_text_drops_control_chars_and_truncates():
    assert _sanitize_log_text("a\nb\tc", 100) == "abc"
    assert _sanitize_log_text("x" * 10, 4) == "xxxx~"


def test_format_validation_err_keeps_paths_not_values():
    marker = "leaked-uuid-value"
    with pytest.raises(ValidationError) as excinfo:
        MetricsInput.model_validate({"message_id": marker})

    detail = _format_validation_error(excinfo.value)

    assert marker not in detail
    assert "message_id=" in detail
    assert detail.startswith("count=")


def test_format_validation_err_caps_field_count():
    with pytest.raises(ValidationError) as excinfo:
        MetricsInput.model_validate({})

    detail = _format_validation_error(excinfo.value, limit=2)

    assert "more" in detail
    assert detail.count("=") <= 4
