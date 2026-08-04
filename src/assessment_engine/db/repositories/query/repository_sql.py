"""5 도메인 구현 결합 — composition root 가 인스턴스 하나를 만든다.

`SqlQueryRepository(session)` 는 `_BaseQueryMixin.__init__` 를 한 번 부르고, MRO 를 따라 다섯 구현이 같은
session 을 공유한다. 새 메서드는 해당 도메인 구현 모듈에 추가한다.
"""

from assessment_engine.db.repositories.query.attention_sql import SqlAttentionQueryRepository
from assessment_engine.db.repositories.query.metric_sql import SqlMetricQueryRepository
from assessment_engine.db.repositories.query.report_sql import SqlReportQueryRepository
from assessment_engine.db.repositories.query.server_sql import SqlServerQueryRepository
from assessment_engine.db.repositories.query.task_sql import SqlTaskQueryRepository


class SqlQueryRepository(
    SqlServerQueryRepository,
    SqlMetricQueryRepository,
    SqlReportQueryRepository,
    SqlAttentionQueryRepository,
    SqlTaskQueryRepository,
):
    """Web service 가 쓰는 단일 query 구현 — 5 도메인 구현을 다중 상속으로 결합한다."""
