"""`diagnostic_jobs.result` JSONB 구조 + 조립 helper — 발행 스냅샷 계약 단일 진실.

`report_serializer` 와 나눠 둔 이유는 의존 방향이다. serializer 는 ViewModel <-> dict 변환이라
`web/view_models` 를 import 하는데, 여기 있는 것은 키 이름과 dict 모양뿐이라 그 의존이 필요 없다.
같은 파일에 두면 키 상수를 읽는 쪽까지 ViewModel 을 끌고 온다.

`web/services/` 에 있는 이유는 소비자가 전부 여기이기 때문이다 — 라우터 3곳과 service 3곳.
다른 계층(consumer·worker)이 이 계약을 읽기 시작하면 그때 도메인 위치로 올린다.

result JSONB 구조 (job_type customer_report/engineer_report 공통):
  {
    "kind": "env_report",   # 모든 보고서 (selection N대 + 환경 + 단일서버 공통 양식)
    "snapshot": {...},      # ViewModel asdict (datetime ISO str)
    "view": "customer" | "engineer",
    "aux": {...},           # ViewModel 밖 부가 (운영신호 등 정적 보관)
  }
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def compute_hash(scope: str, input_params: JsonObject) -> str:
    canonical = json.dumps(input_params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{scope}|{canonical}".encode()).hexdigest()


def normalize_anchor(at: datetime | None) -> datetime:
    """anchor 분 단위 truncate — 같은 분 발행이 같은 input_hash 를 내야 더블클릭이 dedup 된다.

    None 이면 now(). naive 입력은 UTC 로 간주.
    """
    if at is None:
        at = datetime.now(UTC)
    elif at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).replace(second=0, microsecond=0)
