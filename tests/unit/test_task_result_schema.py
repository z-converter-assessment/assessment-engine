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


def test_os_version_received() -> None:
    """os_version 수신 — Windows worker 의 CurrentBuildNumber (성공 보정 정책 키)."""
    payload = make_task_result_payload(
        status="failure", failure_reason="script_failed", exit_code=2, os_version="20348"
    )
    data = _validate(payload)
    assert data.os_version == "20348"


def test_os_version_defaults_null() -> None:
    """os_version 미발행(Linux worker) 시 null (task.result 한정 nullable)."""
    data = _validate(make_task_result_payload(status="success", exit_code=0))
    assert data.os_version is None


def test_signal_no_captured_on_signal_death() -> None:
    """시그널 사망 — exit_code null + signal_no 값 (POSIX wait status 상호배타)."""
    payload = make_task_result_payload(status="failure", failure_reason="script_failed", exit_code=None, signal_no=9)
    data = _validate(payload)
    assert data.exit_code is None
    assert data.signal_no == 9


def test_signal_no_null_on_normal_exit() -> None:
    """정상 종료 — exit_code 값 + signal_no null (미발행 시 default None)."""
    data = _validate(make_task_result_payload(status="success", exit_code=0))
    assert data.exit_code == 0
    assert data.signal_no is None


def test_composite_id_empty_string_normalized_to_none() -> None:
    """agent digest 실패 시 composite_id='' 발행 (wire 허용) — MessageBase validator 가 None 으로 정규화."""
    payload = make_task_result_payload()
    payload["composite_id"] = ""
    data = _validate(payload)
    assert data.composite_id is None


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


def test_composite_id_nullable_for_task_result() -> None:
    """ADR 0027 — worker 컨텍스트는 composite hash 미산출. task.result 한정 composite_id nullable override.

    결과 매칭은 task_id 로 하므로 composite_id 불필요 — agent worker 가 키 자체를 안 보내도 수용.
    """
    payload = make_task_result_payload()
    del payload["composite_id"]
    data = _validate(payload)
    assert data.composite_id is None
    assert data.status == "success"


def test_agent_id_nullable_for_task_result() -> None:
    """ADR 0049 — worker 컨텍스트는 식별자 미산출. task.result 한정 agent_id nullable override.

    다른 메시지 타입은 agent_id required. worker 는 결과를 task_id 로 매칭하므로 agent_id 생략 수용.
    """
    payload = make_task_result_payload()  # agent_id 미포함 (worker 발행 현실)
    assert "agent_id" not in payload
    data = _validate(payload)
    assert data.agent_id is None
    assert data.status == "success"


def test_agent_id_value_accepted_for_task_result() -> None:
    """agent_id 를 실어 보내도 UUID 로 파싱·수용 (nullable override 는 필수 완화일 뿐 값 배제 아님)."""
    payload = make_task_result_payload()
    payload["agent_id"] = "00000000-0000-4000-8000-0000000000c1"
    data = _validate(payload)
    assert str(data.agent_id) == "00000000-0000-4000-8000-0000000000c1"


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
