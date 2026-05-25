"""5 도메인 concrete repository 결합 — composition root 에서 단일 instance 생성.

`QueryRepository(session)` 호출은 `_BaseQueryMixin.__init__(session)` 한 번 — MRO C3 linearization
으로 5 concrete 모두 동일 session 공유. 새 메서드 추가 시 sub-module (server / metric / ...) 의
concrete class 갱신 의무 — 본 모듈은 결합만 표현.
"""

from assessment_engine.db.repositories.query.attention import AttentionQueryRepository
from assessment_engine.db.repositories.query.base_query_repository import BaseQueryRepository
from assessment_engine.db.repositories.query.metric import MetricQueryRepository
from assessment_engine.db.repositories.query.report import ReportQueryRepository
from assessment_engine.db.repositories.query.server import ServerQueryRepository
from assessment_engine.db.repositories.query.task import TaskQueryRepository


class QueryRepository(
    ServerQueryRepository,
    MetricQueryRepository,
    ReportQueryRepository,
    AttentionQueryRepository,
    TaskQueryRepository,
    BaseQueryRepository,
):
    """Web service 가 사용하는 단일 query concrete — 5 도메인 sub-repository multiple inheritance 결합."""
