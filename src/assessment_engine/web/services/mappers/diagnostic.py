"""진단 결과의 표시 파생 필드 단일 진실 (P2·P3·P5).

router·SSR·JSON API·결과 페이지·이력 페이지 모두 본 mapper의 view를 사용한다.
템플릿과 JS는 view 필드를 그대로 표시 — 분류 분기·badge 매핑·라벨 매핑을 클라/템플릿에 두지 않는다.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from assessment_engine.db.dtos.outbound import DiagnosticJobRecord
from assessment_engine.db.repositories.base_diagnostic_repository import (
    CLASSIFICATION_LABEL_KR,
    DIAGNOSTIC_RANGE_LABEL_KR,
)

# --- 분류·진행 stage badge 단일 진실 ---

_CLASSIFICATION_BADGE_CLASS: dict[str, str] = {
    "idle": "badge-warn",
    "shutdown": "badge-warn",
    "over_provisioned": "badge-warn",
    "under_provisioned": "badge-danger",
    "optimal": "badge-ok",
    "insufficient_data": "badge",
}

_STATUS_BADGE_CLASS: dict[str, str] = {
    "pending": "badge",
    "running": "badge-warn",
    "succeeded": "badge-ok",
    "failed": "badge-danger",
}

_PROGRESS_LABEL_KR: dict[str, str] = {
    "queued": "대기 중",
    "extracting_stats": "통계 추출 중",
    "applying_rules": "추천 판정 중",
    "retrieving_context": "관련 자료 검색 중",
    "generating_narrative": "AI 분석 작성 중",
}


@dataclass
class DiagnosticPanelView:
    """진단 1건의 표시용 view — JSON 직렬화 호환.

    raw `result` dict는 그대로 보존 (차트·표 렌더 입력). 분류·라벨·badge 같은 파생만 별도 필드.
    """

    job_id: str
    short_id: str  # job_id[:8]
    scope: str  # 'server' | 'environment'
    server_public_id: str | None

    status: str
    status_badge_class: str
    progress_stage: str | None
    progress_label_kr: str | None

    time_range: str
    window_label_kr: str
    anchor_at: datetime | None
    anchor_at_iso: str | None  # JSON 직렬화용

    classification: str | None
    classification_label_kr: str | None
    classification_badge_class: str | None
    recommendation_action: str | None

    result: dict | None
    error_message: str | None

    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    requested_by: str | None

    def to_dict(self) -> dict[str, Any]:
        """JSON 응답·SSR inline payload용 — datetime은 ISO 8601 UTC 문자열."""
        return {
            "job_id": self.job_id,
            "short_id": self.short_id,
            "scope": self.scope,
            "server_public_id": self.server_public_id,
            "status": self.status,
            "status_badge_class": self.status_badge_class,
            "progress_stage": self.progress_stage,
            "progress_label_kr": self.progress_label_kr,
            "time_range": self.time_range,
            "window_label_kr": self.window_label_kr,
            "anchor_at": self.anchor_at_iso,
            "classification": self.classification,
            "classification_label_kr": self.classification_label_kr,
            "classification_badge_class": self.classification_badge_class,
            "recommendation_action": self.recommendation_action,
            "result": self.result,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "requested_by": self.requested_by,
        }


def to_history_item(rec: DiagnosticJobRecord) -> dict[str, Any]:
    """AI 진단 이력 페이지 행 1개 — JSONB 키 추출·표시 fallback 단일 진실 (P2).

    history.html 템플릿이 attribute access만 하도록 dict 변환. time_range 누락 시 "—".
    KST 변환은 템플릿 kst 필터에 위임 (F2) — datetime을 그대로 전달.
    보고서 이력은 별도 mapper (`report_mapper.to_report_history_item`).
    """
    return {
        "job_id": rec.id,
        "scope": rec.scope,
        "server_public_id": rec.input_params.get("server_public_id"),
        "time_range": rec.input_params.get("time_range", "—"),
        "anchor_at": rec.input_params.get("anchor_at"),
        "status": rec.status,
        "status_badge_class": _STATUS_BADGE_CLASS.get(rec.status, "badge"),
        "created_at": rec.created_at,
        "finished_at": rec.finished_at,
        "requested_by": rec.requested_by,
    }


def to_view(rec: DiagnosticJobRecord | None) -> DiagnosticPanelView | None:
    """DiagnosticJobRecord → DiagnosticPanelView (P2 단일 변환)."""
    if rec is None:
        return None

    time_range = rec.input_params.get("time_range", "14d")
    anchor_at_str = rec.input_params.get("anchor_at")
    anchor_at = datetime.fromisoformat(anchor_at_str) if anchor_at_str else None

    # result에서 classification·recommendation 추출 — server scope는 단일 분류, environment는 dominant 또는 None
    classification: str | None = None
    recommendation_action: str | None = None
    if rec.result:
        cls_field = rec.result.get("classification")
        # server scope: classification = str. environment scope: classification = {idle:{count}, ...} dict
        if isinstance(cls_field, str):
            classification = cls_field
        recommendation_action = (rec.result.get("recommendation") or {}).get("action")

    return DiagnosticPanelView(
        job_id=rec.id,
        short_id=rec.id[:8],
        scope=rec.scope,
        server_public_id=rec.input_params.get("server_public_id"),
        status=rec.status,
        status_badge_class=_STATUS_BADGE_CLASS.get(rec.status, "badge"),
        progress_stage=rec.progress_stage,
        progress_label_kr=_PROGRESS_LABEL_KR.get(rec.progress_stage) if rec.progress_stage else None,
        time_range=time_range,
        window_label_kr=DIAGNOSTIC_RANGE_LABEL_KR.get(time_range, time_range),
        anchor_at=anchor_at,
        anchor_at_iso=anchor_at_str,
        classification=classification,
        classification_label_kr=CLASSIFICATION_LABEL_KR.get(classification) if classification else None,
        classification_badge_class=_CLASSIFICATION_BADGE_CLASS.get(classification) if classification else None,
        recommendation_action=recommendation_action,
        result=rec.result,
        error_message=rec.error_message,
        created_at=rec.created_at,
        started_at=rec.started_at,
        finished_at=rec.finished_at,
        requested_by=rec.requested_by,
    )
