"""TaskResultInput Pydantic 단위 테스트 (ADR 0007).

검증 항목:
- success / failure 경로 양쪽 Literal 통과
- boot_time / agent_started_at 가 null 이라도 검증 통과 (task.result 한정 nullable override)
- 페이로드 wire JSON -> model_validate_json -> field 매핑
- 알려지지 않은 failure_reason 도 max_length 만 강제 (silent pass)
- 4 ERROR 회귀 (D1·D2·D4·D5) 가드 — 옛 형식("task_result" / "failed" / "task_public_id" / boot_time required) 거부
"""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from assessment_engine.consumer.schemas import TaskResultInput
from tests.factories import make_task_result_payload


def _validate(payload: dict) -> TaskResultInput:
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
    """알려지지 않은 failure_reason 도 max_length만 강제 — agent 진화 silent 호환."""
    payload = make_task_result_payload(status="failure", failure_reason="future_new_reason")
    data = _validate(payload)
    assert data.failure_reason == "future_new_reason"


def test_failure_reason_max_length() -> None:
    payload = make_task_result_payload(status="failure", failure_reason="x" * 33)
    with pytest.raises(ValidationError):
        _validate(payload)


def test_boot_time_nullable_allowed() -> None:
    """ADR 0007 D1 — worker 컨텍스트 분리로 task.result 는 boot_time/agent_started_at null 발행."""
    payload = make_task_result_payload(boot_time=None, agent_started_at=None)
    data = _validate(payload)
    assert data.boot_time is None
    assert data.agent_started_at is None


def test_boot_time_value_also_allowed() -> None:
    """nullable override 라도 값이 있으면 그대로 datetime 으로 파싱."""
    payload = make_task_result_payload(
        boot_time=datetime(2026, 5, 14, 10, 0, tzinfo=UTC),
        agent_started_at=datetime(2026, 5, 14, 10, 1, tzinfo=UTC),
    )
    data = _validate(payload)
    assert data.boot_time is not None
    assert data.agent_started_at is not None


# ─── ADR 0007 4 ERROR 회귀 가드 ────────────────────────────────────────────


def test_legacy_message_type_underscore_rejected() -> None:
    """D2 — 옛 'task_result' (underscore) 페이로드는 거부."""
    payload = make_task_result_payload()
    payload["message_type"] = "task_result"
    with pytest.raises(ValidationError):
        _validate(payload)


def test_legacy_status_failed_rejected() -> None:
    """D5 — 옛 'failed' 페이로드는 거부 (Literal 'failure' 만)."""
    payload = make_task_result_payload(status="failure")
    payload["status"] = "failed"
    with pytest.raises(ValidationError):
        _validate(payload)


def test_task_public_id_field_not_required() -> None:
    """D4 — 옛 'task_public_id' 키는 schema 가 안 봄 (extra=ignore). task_id 누락이 실패 원인이어야."""
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
