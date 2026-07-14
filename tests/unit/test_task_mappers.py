"""task mapper 단위 테스트 (P2 단일 진실).

mapper 책임: TaskRow (raw outbound DTO) -> TaskSummaryItem / TaskDetailItem.
표시 파생(badge_class·badge_label·failure_label) 모두 mapper precompute.
"""

from datetime import UTC, datetime

from assessment_engine.web.services.mappers.task import to_task_detail, to_task_summary
from tests.factories import make_task_row

# deadline 판정 기준 — make_task_row 는 deadline_at=None(마감 없음) default 라 timeout 파생 없음.
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_success_summary_badge() -> None:
    row = make_task_row(status="success")
    summary = to_task_summary(row, _NOW)
    assert summary.badge_class == "rec-success"
    assert summary.badge_label == "성공"
    assert summary.failure_label is None


def test_failure_summary_badge_with_reason_label() -> None:
    row = make_task_row(
        status="failure",
        failure_reason="sha256_mismatch",
        exit_code=None,
        duration_ms=8000,
    )
    summary = to_task_summary(row, _NOW)
    assert summary.badge_class == "rec-failure"
    assert summary.badge_label == "실패"
    assert summary.failure_label == "체크섬 불일치"


def test_pending_summary_badge() -> None:
    row = make_task_row(status="pending", completed_at=None, exit_code=None, duration_ms=None)
    summary = to_task_summary(row, _NOW)
    assert summary.badge_class == "rec-pending"
    assert summary.badge_label == "진행 중"


def test_legacy_failed_status_maps_to_failure_badge() -> None:
    """레거시 row 호환 — 옛 'failed' 값도 rec-failure 로 매핑 (mapper _TASK_STATUS_DISPLAY)."""
    row = make_task_row(status="failed", failure_reason="script_failed", exit_code=1)
    summary = to_task_summary(row, _NOW)
    assert summary.badge_class == "rec-failure"
    assert summary.failure_label == "스크립트 비정상 종료"


def test_unknown_status_maps_to_unknown_badge() -> None:
    row = make_task_row(status="weird_state")
    summary = to_task_summary(row, _NOW)
    assert summary.badge_class == "rec-unknown"


def test_unknown_failure_reason_passes_through() -> None:
    """알려지지 않은 reason 은 카탈로그에 없으면 그대로 표시 (silent pass — schema 와 일관)."""
    row = make_task_row(status="failure", failure_reason="future_new_reason")
    summary = to_task_summary(row, _NOW)
    assert summary.failure_label == "future_new_reason"


def test_detail_signal_label_precomputed() -> None:
    """시그널 사망 — signal_no 로 SIG 이름 라벨 precompute (P2). exit_code 와 상호배타."""
    row = make_task_row(status="failure", failure_reason="script_failed", exit_code=None, signal_no=9)
    detail = to_task_detail(row, _NOW)
    assert detail.signal_no == 9
    assert detail.signal_label == "SIGKILL (9)"


def test_detail_signal_label_none_on_normal_exit() -> None:
    """정상 종료 — signal_no None 이면 signal_label None (표시 계층에서 시그널 행 생략)."""
    detail = to_task_detail(make_task_row(status="success", exit_code=0), _NOW)
    assert detail.signal_no is None
    assert detail.signal_label is None


def test_detail_unknown_signal_number_shows_number_only() -> None:
    """미지 시그널 번호는 이름 없이 숫자만."""
    detail = to_task_detail(make_task_row(status="failure", exit_code=None, signal_no=99), _NOW)
    assert detail.signal_label == "99"


def test_summary_task_type_known_label() -> None:
    """task_type 카탈로그 히트 — 사람이 읽는 라벨로 치환 (mapper _TASK_TYPE_LABEL)."""
    row = make_task_row(task_type="zconverter_install")
    summary = to_task_summary(row, _NOW)
    assert summary.task_type == "ZConverter Install"


def test_summary_task_type_unknown_passes_through() -> None:
    """알려지지 않은 task_type 은 raw 그대로 (표시 불가 방지 — failure_reason 과 동일 폴백 정책)."""
    row = make_task_row(task_type="future_new_task")
    summary = to_task_summary(row, _NOW)
    assert summary.task_type == "future_new_task"


def test_detail_includes_tails() -> None:
    row = make_task_row(
        status="success",
        exit_code=0,
        duration_ms=42,
        stdout_tail="installed foo-1.2\n",
        stderr_tail="",
    )
    detail = to_task_detail(row, _NOW)
    assert detail.task_id == row.public_id
    assert detail.stdout_tail == "installed foo-1.2\n"
    assert detail.stderr_tail == ""
    assert detail.exit_code == 0
    assert detail.duration_ms == 42
    assert detail.target_hostname == "test-host-01"
