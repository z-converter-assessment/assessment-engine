"""보고서 발행 result JSONB 구조 — web 발행(snapshot 저장) <-> worker(narrative 채움) 공유 계약.

web `report_serializer` (ViewModel <-> dict) 와 worker `handler` (narrative merge) 양쪽이 본 모듈의
키·dict 조립 helper 를 단일 진실로 참조. 의존 방향: web -> diagnostic 허용, diagnostic -> web 금지.
report_serializer 는 web view_models 에 의존해 worker 가 import 못 하므로, 순수 계약(키·dict 조립)만
본 모듈에 분리한다. (ADR 0014 분리 본질 — submitter 와 동일 사유로 diagnostic 패키지 안.)

result JSONB 구조 (job_type customer_report/engineer_report 공통):
  {
    "kind": "report_summary" | "env_report",   # 역직렬화 분기 (server N대 vs 환경/단일서버)
    "snapshot": {...},                          # ViewModel asdict (datetime ISO str)
    "view": "customer" | "engineer",
    "narrative_status": "none"|"pending"|"succeeded"|"failed",  # 발행 시점 worker 진행 신호
    "narratives": { key: {...narrative entry...} },  # engineer per-key (key=public_id | "environment")
    "aux": {...},                               # ViewModel 밖 부가 (운영신호 등 정적 보관)
  }

narrative entry (engineer 보고서 worker 가 채움 — key 별 1건):
  {
    "narrative": str | None,
    "classification": str | None,                 # server scope 단일 분류 (environment 는 None)
    "classification_label_kr": str | None,
    "recommendation_action": str | None,
    "status": "succeeded" | "failed",             # key 별 narrative 결과 (보고서 자체는 항상 표시)
    "error": str | None,
  }
"""

REPORT_KIND_SUMMARY = "report_summary"  # server scope N대 (ReportSummary)
REPORT_KIND_ENV = "env_report"  # 환경 + 단일서버 (EnvironmentReportSummary)

# narrative dict 키 — server scope 는 public_id 별, environment scope 는 본 sentinel 단일 키.
ENV_NARRATIVE_KEY = "environment"

# narrative_status — engineer per-report 진행 신호. customer 는 항상 none (narrative 없음).
# pending: worker 미처리 / succeeded: worker 완료(개별 narrative 실패 여부는 entry.status) / failed: worker 자체 중단.
NARRATIVE_STATUS_NONE = "none"
NARRATIVE_STATUS_PENDING = "pending"
NARRATIVE_STATUS_SUCCEEDED = "succeeded"
NARRATIVE_STATUS_FAILED = "failed"


def build_report_result(
    *,
    kind: str,
    snapshot: dict,
    view: str,
    narrative_status: str = NARRATIVE_STATUS_NONE,
    narratives: dict | None = None,
    aux: dict | None = None,
) -> dict:
    """발행 시점 보고서 스냅샷 + narrative 상태/부가 데이터를 result JSONB dict 로 묶음."""
    return {
        "kind": kind,
        "snapshot": snapshot,
        "view": view,
        "narrative_status": narrative_status,
        "narratives": narratives or {},
        "aux": aux or {},
    }


def build_narrative_entry(
    *,
    status: str,
    narrative: str | None = None,
    classification: str | None = None,
    classification_label_kr: str | None = None,
    recommendation_action: str | None = None,
    error: str | None = None,
) -> dict:
    """narrative dict 의 key 별 1건 — worker 가 LLM 합성 성공/실패 후 채움."""
    return {
        "narrative": narrative,
        "classification": classification,
        "classification_label_kr": classification_label_kr,
        "recommendation_action": recommendation_action,
        "status": status,
        "error": error,
    }
