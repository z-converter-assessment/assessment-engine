"""Task ViewModel — list / detail / API 응답 표시."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TaskSummaryItem:
    """list / detail row 단위 task 요약 — 한 줄 표시.

    상태 표현은 mapper 단일 결정 (P2):
    - badge_class: rec-{key} CSS class. success / failure / pending / unknown
    - badge_label: 한글 표시 ("성공" / "실패" / "진행 중" / "—")
    - failure_label: failure_reason 한글 (실패 시만)
    """

    task_id: str
    task_type: str
    status: str
    badge_class: str
    badge_label: str
    failure_label: str | None
    created_at: datetime
    completed_at: datetime | None
    duration_ms: int | None


@dataclass
class TaskDetailItem:
    """단일 task 상세 — modal / API 응답. 디버깅 데이터(tail) 포함."""

    task_id: str
    target_public_id: str | None
    target_hostname: str | None
    task_type: str
    status: str
    badge_class: str
    badge_label: str
    failure_reason: str | None
    failure_label: str | None
    exit_code: int | None
    signal_no: int | None
    signal_label: str | None  # "SIGKILL (9)" precompute (mapper). None 이면 표시 계층에서 시그널 행 생략
    duration_ms: int | None
    created_at: datetime
    completed_at: datetime | None
    stdout_tail: str | None
    stderr_tail: str | None
    params: dict | None = None  # install task 발행 파라미터 ({zdm_ip, zdm_user} 등) — modal 노출
