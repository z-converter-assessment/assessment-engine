"""5 도메인 protocol 결합 — service 는 이 protocol 하나만 의존한다.

새 메서드는 해당 도메인 protocol 에 추가한다. 본 모듈은 결합만 표현하고 메서드를 정의하지 않는다.
"""

from typing import Protocol

from assessment_engine.db.repositories.query.attention import AttentionQueryRepository
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
    Protocol,
):
    """Web service 가 의존하는 단일 query interface — 5 도메인 protocol 결합."""
