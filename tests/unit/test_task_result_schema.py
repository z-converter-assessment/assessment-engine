import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from assessment_engine.consumer.schemas import TaskResultInput
from tests.factories import make_task_result_payload

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


def _validate(payload: JsonObject) -> TaskResultInput:
    return TaskResultInput.model_validate_json(json.dumps(payload))


def test_success_payload_parses() -> None:
    payload = make_task_result_payload(status="success", exit_code=0, duration_ms=30)
    data = _validate(payload)
    assert data.message_type == "task.result"
    assert data.status == "success"
    assert data.failure_reason is None
    assert data.exit_code == 0
    assert data.duration_ms == 30
    assert data.boot_time is None
    assert data.agent_started_at is None


def test_failure_payload_with_known_reason() -> None:
    payload = make_task_result_payload(
        status="failure",
        failure_reason="sha256_mismatch",
        exit_code=None,
        duration_ms=8000,
        stdout_tail="",
        stderr_tail="hash mismatch",
    )
    data = _validate(payload)
    assert data.status == "failure"
    assert data.failure_reason == "sha256_mismatch"
    assert data.exit_code is None


def test_unknown_failure_reason_silent_pass() -> None:
    payload = make_task_result_payload(status="failure", failure_reason="future_new_reason")
    data = _validate(payload)
    assert data.failure_reason == "future_new_reason"


def test_os_version_received() -> None:
    payload = make_task_result_payload(
        status="failure", failure_reason="script_failed", exit_code=2, os_version="20348"
    )
    data = _validate(payload)
    assert data.os_version == "20348"


def test_os_version_defaults_null() -> None:
    data = _validate(make_task_result_payload(status="success", exit_code=0))
    assert data.os_version is None


def test_signal_no_captured_on_signal_death() -> None:
    payload = make_task_result_payload(status="failure", failure_reason="script_failed", exit_code=None, signal_no=9)
    data = _validate(payload)
    assert data.exit_code is None
    assert data.signal_no == 9


def test_signal_no_null_on_normal_exit() -> None:
    data = _validate(make_task_result_payload(status="success", exit_code=0))
    assert data.exit_code == 0
    assert data.signal_no is None


def test_composite_id_empty_string_normalized_to_none() -> None:
    payload = make_task_result_payload()
    payload["composite_id"] = ""
    data = _validate(payload)
    assert data.composite_id is None


def test_failure_reason_max_length() -> None:
    payload = make_task_result_payload(status="failure", failure_reason="x" * 33)
    with pytest.raises(ValidationError):
        _validate(payload)


def test_boot_time_nullable_allowed() -> None:
    payload = make_task_result_payload(boot_time=None, agent_started_at=None)
    data = _validate(payload)
    assert data.boot_time is None
    assert data.agent_started_at is None


def test_boot_time_value_also_allowed() -> None:
    payload = make_task_result_payload(
        boot_time=datetime(2026, 5, 14, 10, 0, tzinfo=UTC),
        agent_started_at=datetime(2026, 5, 14, 10, 1, tzinfo=UTC),
    )
    data = _validate(payload)
    assert data.boot_time is not None
    assert data.agent_started_at is not None


def test_composite_id_nullable_for_task_result() -> None:
    payload = make_task_result_payload()
    del payload["composite_id"]
    data = _validate(payload)
    assert data.composite_id is None
    assert data.status == "success"


def test_agent_id_nullable_for_task_result() -> None:
    payload = make_task_result_payload()
    del payload["agent_id"]
    assert "agent_id" not in payload
    data = _validate(payload)
    assert data.agent_id is None
    assert data.status == "success"


def test_agent_id_value_accepted_for_task_result() -> None:
    payload = make_task_result_payload()
    payload["agent_id"] = "00000000-0000-4000-8000-0000000000c1"
    data = _validate(payload)
    assert str(data.agent_id) == "00000000-0000-4000-8000-0000000000c1"


def test_legacy_message_type_underscore_rejected() -> None:
    payload = make_task_result_payload()
    payload["message_type"] = "task_result"
    with pytest.raises(ValidationError):
        _validate(payload)


def test_arbitrary_status_silent_pass() -> None:
    payload = make_task_result_payload(status="failure")
    payload["status"] = "failed"
    data = _validate(payload)
    assert data.status == "failed"


def test_task_public_id_field_not_required() -> None:
    payload = make_task_result_payload()
    del payload["task_id"]
    payload["task_public_id"] = "00000000-0000-4000-8000-000000000099"
    with pytest.raises(ValidationError) as exc:
        _validate(payload)
    errors = exc.value.errors()
    assert any(e["loc"] == ("task_id",) for e in errors)


def test_duration_ms_non_negative() -> None:
    payload = make_task_result_payload(duration_ms=-1)
    with pytest.raises(ValidationError):
        _validate(payload)


def test_tail_max_length() -> None:
    payload = make_task_result_payload(stdout_tail="x" * 8193)
    with pytest.raises(ValidationError):
        _validate(payload)
