"""보고서 생성 파이프라인 — 발행 시점 스냅샷을 만들고 JSONB 로 넣고 꺼낸다.

세 단계가 순서대로 물린다 — `generator`(ViewModel 조립) -> `result`(`diagnostic_jobs.result` 구조·해시)
-> `serializer`(ViewModel <-> dict). 함께 바뀌는 것들이라 한 패키지에 둔다.

공개 표면은 아래 재수출 목록이다. 그 밖의 심볼은 패키지 안에서 조립에만 쓴다.
"""

from assessment_engine.web.services.report.generator import (
    ReportGenerationError,
    attention_by_host,
    attention_for_host,
    build_report_result_for_job,
)
from assessment_engine.web.services.report.result import (
    REPORT_KIND_ENV,
    build_report_result,
    compute_hash,
    normalize_anchor,
)
from assessment_engine.web.services.report.serializer import env_report_from_dict, env_report_to_dict

__all__ = [
    "REPORT_KIND_ENV",
    "ReportGenerationError",
    "attention_by_host",
    "attention_for_host",
    "build_report_result",
    "build_report_result_for_job",
    "compute_hash",
    "env_report_from_dict",
    "env_report_to_dict",
    "normalize_anchor",
]
