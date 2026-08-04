"""보고서 발행 result JSONB 구조 + 발행 helper — web 발행(snapshot 저장) 단일 진실.

web `report_serializer` (ViewModel <-> JsonObject) 가 본 모듈의 키·dict 조립 helper 를 참조.
report_serializer 는 web view_models 에 의존하므로, 순수 계약(키·dict 조립)만 본 모듈에 분리한다.

result JSONB 구조 (job_type customer_report/engineer_report 공통):
  {
    "kind": "env_report",   # 모든 보고서 (selection N대 + 환경 + 단일서버 공통 양식)
    "snapshot": {...},      # ViewModel asdict (datetime ISO str)
    "view": "customer" | "engineer",
    "aux": {...},           # ViewModel 밖 부가 (운영신호 등 정적 보관)
  }

input_hash/anchor helper — 라우터 발행 시 anchor 정규화·같은 분 더블클릭 dedup.
"""

import hashlib
import json
from datetime import UTC, datetime

from assessment_engine.json_types import JsonObject

REPORT_KIND_ENV = "env_report"  # 전 보고서 공통 양식 (EnvironmentReportSummary)


def build_report_result(*, kind: str, snapshot: JsonObject, view: str, aux: JsonObject | None = None) -> JsonObject:
    """발행 시점 보고서 스냅샷 + 부가 정적 데이터를 result JSONB dict 로 묶음."""
    return {
        "kind": kind,
        "snapshot": snapshot,
        "view": view,
        "aux": aux or {},
    }


def _compute_hash(scope: str, input_params: JsonObject) -> str:
    canonical = json.dumps(input_params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{scope}|{canonical}".encode()).hexdigest()


def _normalize_anchor(at: datetime | None) -> datetime:
    """anchor 분 단위 truncate — 같은 분 발행은 같은 input_hash (더블클릭 dedup).

    None이면 now() UTC 분 단위. 명시 시 timezone-aware 후 UTC 변환 + 분 단위.
    """
    if at is None:
        at = datetime.now(UTC)
    elif at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).replace(second=0, microsecond=0)
