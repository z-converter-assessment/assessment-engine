"""Query sub-repository 공통 mixin — session 보유 + 다중 도메인 공유 helper.

5 concrete sub-repository (server / metric / report / attention / task) 가 본 mixin 을 상속.
QueryRepository facade 가 multiple inheritance 로 5 concrete 결합 시 본 mixin __init__ 한 번만 호출.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class _BaseQueryMixin:
    """`__init__(session)` + `_latest_per_dimension` 공통 helper.

    `self.session` 은 모든 sub-repository 가 사용. `_latest_per_dimension` 은 server / metric 양쪽 사용
    (server: get_storage·get_network, metric: latest_dashboard).
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _latest_per_dimension(
        self,
        table: str,
        dim_col: str,
        server_id: int,
        n: int,
    ) -> list[Any]:
        """{table}에서 (server_id 한정) {dim_col}별 최신 n행 반환.

        n=1: DISTINCT ON (가장 단순), n>=2: PARTITION BY + ROW_NUMBER.
        table·dim_col은 ORM 모델의 정적 attribute로 whitelisted — SQL에 직접 포맷
        (C5 예외 — dispatch table whitelist만).

        C5: hypertable partition pruning 의무. 30d 윈도우 — 30d 이상 오프라인 서버는
        metrics 조회 의미 약함 + 7d chunk 기준 4~5 chunk만 스캔.
        """
        if n == 1:
            sql = text(f"""
                SELECT *
                FROM (
                    SELECT DISTINCT ON ({dim_col}) *
                    FROM {table}
                    WHERE server_id = :sid AND collected_at >= now() - interval '30 days'
                    ORDER BY {dim_col}, collected_at DESC
                ) s
                ORDER BY {dim_col}
            """)
            params: dict[str, Any] = {"sid": server_id}
        else:
            sql = text(f"""
                SELECT *
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY {dim_col} ORDER BY collected_at DESC) AS rn
                    FROM {table}
                    WHERE server_id = :sid AND collected_at >= now() - interval '30 days'
                ) t
                WHERE rn <= :n
                ORDER BY {dim_col}, collected_at DESC
            """)
            params = {"sid": server_id, "n": n}
        result = await self.session.execute(sql, params)
        return result.all()
