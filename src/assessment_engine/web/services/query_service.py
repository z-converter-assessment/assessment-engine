"""QueryService — 6 도메인 mixin 결합 조합 모듈 (composition root 에서 단일 instance 생성).

repo 계층 `db/repositories/query/query_repository.py` 와 동형 — 도메인 mixin 은 `web/services/query/` 하위.
외부(deps·라우터)는 본 모듈에서 QueryService·외부 노출 심볼(DASHBOARD_TIME_RANGE 등)을 import.
새 메서드 추가 시 해당 도메인 mixin 갱신 의무 — 본 조합 클래스는 결합만.
"""

# api.py 편의 re-export — 원 소속 types.py (라우터가 query_service 경유 import 유지).
from assessment_engine.db.repositories.query.types import AggFunc, BucketSize, MetricType, TimeRange
from assessment_engine.web.services.query.attention import AttentionQueryMixin
from assessment_engine.web.services.query.environment import (
    DASHBOARD_TIME_RANGE,
    DASHBOARD_WINDOW_DAYS,
    EnvironmentQueryMixin,
)
from assessment_engine.web.services.query.metric import MetricQueryMixin
from assessment_engine.web.services.query.report import ReportQueryMixin
from assessment_engine.web.services.query.server import ServerQueryMixin
from assessment_engine.web.services.query.task import TaskQueryMixin

__all__ = [
    "QueryService",
    "DASHBOARD_TIME_RANGE",
    "DASHBOARD_WINDOW_DAYS",
    "AggFunc",
    "BucketSize",
    "MetricType",
    "TimeRange",
]


class QueryService(
    ServerQueryMixin,
    MetricQueryMixin,
    AttentionQueryMixin,
    EnvironmentQueryMixin,
    ReportQueryMixin,
    TaskQueryMixin,
):
    """Web 라우터가 사용하는 단일 query service — 6 도메인 mixin multiple inheritance 결합.

    `__init__(repo, redis)` 는 `_BaseQueryServiceMixin` 단일 (MRO C3 linearization 으로 전 mixin 공유).
    도메인 간 메서드 상호 호출(예: report 가 self._assemble_overview·self.get_attention_signals)은
    본 결합으로 self 경유 해결 — repo 계층과 동일 패턴.
    """
