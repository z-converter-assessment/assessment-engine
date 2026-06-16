"""5 도메인 추상 인터페이스 결합 — service 가 본 interface 1개 의존 (F4).

새 메서드 추가 시 적절한 sub-base (base_server / base_metric / ...) 갱신 의무 —
본 모듈은 결합만 표현, 메서드 정의 0.
"""

from assessment_engine.db.repositories.query.base_attention import BaseAttentionQueryRepository
from assessment_engine.db.repositories.query.base_metric import BaseMetricQueryRepository
from assessment_engine.db.repositories.query.base_report import BaseReportQueryRepository
from assessment_engine.db.repositories.query.base_server import BaseServerQueryRepository
from assessment_engine.db.repositories.query.base_task import BaseTaskQueryRepository


class BaseQueryRepository(
    BaseServerQueryRepository,
    BaseMetricQueryRepository,
    BaseReportQueryRepository,
    BaseAttentionQueryRepository,
    BaseTaskQueryRepository,
):
    """Web service 가 의존하는 단일 query interface — 5 도메인 sub-base ABC 결합."""
