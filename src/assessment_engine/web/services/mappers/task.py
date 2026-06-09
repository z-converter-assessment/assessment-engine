"""Task 표시 mapper — TaskRow → TaskSummaryItem/TaskDetailItem (P2)."""

from datetime import datetime

from assessment_engine.db.dtos.outbound import TaskRow
from assessment_engine.web.view_models.task import TaskDetailItem, TaskSummaryItem

# status -> (badge_class, badge_label). 미지 값은 unknown으로 매핑.
_TASK_STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "pending": ("rec-pending", "진행 중"),
    "in_progress": ("rec-pending", "진행 중"),
    "success": ("rec-success", "성공"),
    "failure": ("rec-failure", "실패"),
    "failed": ("rec-failure", "실패"),  # 레거시 row 호환
}
_TASK_STATUS_UNKNOWN: tuple[str, str] = ("rec-unknown", "—")

# failure_reason -> 한글 라벨. agent.md task.result 절 단일 진실. "timeout" 은 engine deadline 만료 (agent 미발행).
_FAILURE_REASON_LABEL: dict[str, str] = {
    "url_not_allowed": "URL 화이트리스트 위반",
    "download_failed": "다운로드 실패",
    "sha256_mismatch": "체크섬 불일치",
    "extract_failed": "압축 해제 실패",
    "script_not_found": "스크립트 없음",
    "script_failed": "스크립트 비정상 종료",
    "script_timeout": "스크립트 timeout",
    "insufficient_disk": "디스크 공간 부족",
    "internal_error": "내부 오류",
    "already_done": "중복 배달",
    "unsupported_install_type": "agent 가 처리 못 하는 install type",
    "timeout": "응답 시간 초과(미수신)",
}


def _task_status_display(status: str | None) -> tuple[str, str]:
    if status is None:
        return _TASK_STATUS_UNKNOWN
    return _TASK_STATUS_DISPLAY.get(status, _TASK_STATUS_UNKNOWN)


def _failure_label(reason: str | None) -> str | None:
    if reason is None:
        return None
    return _FAILURE_REASON_LABEL.get(reason, reason)


def _effective_display(row: TaskRow, now: datetime) -> tuple[str, str | None, tuple[str, str], str | None]:
    """표시 파생 — (status, failure_reason, (badge_class, badge_label), failure_label).

    deadline 경과 pending 은 DB raw 가 pending 이어도 표시상 failure(timeout) 로 파생 — 폴링 JS(status='failure')
    가 자동 종료하고, 재발행/재조회 전이를 기다리지 않고 즉시 실패로 노출. 발행 시 expire 전이된 실제
    DB timeout(reason='timeout') 과 동일. badge 는 성공/진행중/실패 3종으로만 — timeout 사유는 failure_reason/
    failure_label(상세 '실패 사유'·목록 hover)로 확인.
    """
    is_timeout = row.failure_reason == "timeout" or (
        row.status == "pending" and row.deadline_at is not None and now > row.deadline_at
    )
    if is_timeout:
        return "failure", "timeout", _task_status_display("failure"), _failure_label("timeout")
    return row.status, row.failure_reason, _task_status_display(row.status), _failure_label(row.failure_reason)


def to_task_summary(row: TaskRow, now: datetime) -> TaskSummaryItem:
    status, _reason, (badge_class, badge_label), failure_label = _effective_display(row, now)
    return TaskSummaryItem(
        task_id=row.public_id,
        task_type=row.task_type,
        status=status,
        badge_class=badge_class,
        badge_label=badge_label,
        failure_label=failure_label,
        created_at=row.created_at,
        completed_at=row.completed_at,
        duration_ms=row.duration_ms,
    )


def to_task_detail(row: TaskRow, now: datetime) -> TaskDetailItem:
    status, failure_reason, (badge_class, badge_label), failure_label = _effective_display(row, now)
    return TaskDetailItem(
        task_id=row.public_id,
        target_public_id=row.target_public_id,
        target_hostname=row.target_hostname,
        task_type=row.task_type,
        status=status,
        badge_class=badge_class,
        badge_label=badge_label,
        failure_reason=failure_reason,
        failure_label=failure_label,
        exit_code=row.exit_code,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
        completed_at=row.completed_at,
        stdout_tail=row.stdout_tail,
        stderr_tail=row.stderr_tail,
        params=row.params,
    )
