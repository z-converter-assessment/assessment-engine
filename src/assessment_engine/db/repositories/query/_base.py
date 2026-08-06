"""Query sub-repository 공통 mixin — session 보유 + 다중 도메인 공유 helper.

5 concrete sub-repository (server / metric / report / attention / task) 가 본 mixin 을 상속.
SqlQueryRepository facade 가 multiple inheritance 로 5 concrete 결합 시 본 mixin __init__ 한 번만 호출.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import aliased

from assessment_engine.db.models.base import Base
from assessment_engine.db.models.server_net_io import ServerNetIo

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import InstrumentedAttribute

# "최신 행" 조회의 미래 timestamp 방어 — agent 시계가 어긋나 collected_at 이 미래로 발행되면(예: Windows
# RTC 가 local TZ 로 해석돼 UTC 오프셋만큼 튐) 그 행이 "가짜 최신"으로 잡혀 대시보드 CPU delta(최신 2행
# 연속성)를 깨뜨린다. now()+SKEW 보다 미래인 행은 시계 오류로 간주해 제외. SKEW 는 정상적인 서버-DB 간
# 미세 시계차(수 초~분)는 흡수하되 OS 타임존 오류(시간 단위)는 거른다.
_FUTURE_SKEW_SQL = "now() + interval '2 minutes'"
# "최신 행" 조회의 하한 — chunk pruning 용이라 값 자체가 의미를 갖지 않는다 (#C5).
_LATEST_WINDOW_SQL = "now() - interval '30 days'"


class _BaseQueryMixin:
    """`__init__(session)` + `_latest_per_dimension` 공통 helper (server / metric sub-repo 공유)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _latest_per_dimension[M: Base](
        self,
        model: type[M],
        dim: InstrumentedAttribute[Any],
        server_id: int,
        n: int,
    ) -> Sequence[M]:
        """`model` 에서 (server_id 한정) `dim` 별 최신 n행. n=1: DISTINCT ON, n>=2: ROW_NUMBER.

        시간 술어 둘만 `text()` 다 — `now()` 기준 상대 창은 DB 시각으로 계산해야 하고, 파라미터로
        올리면 기준이 앱 프로세스 시각으로 바뀐다. 나머지는 ORM 이 조립하므로 컬럼명 오타가 실행
        시점이 아니라 타입 검사에서 걸린다.

        C5 partition pruning: 30d 윈도우 — 30d 이상 오프라인 서버는 조회 의미 약함 + chunk 4~5개만 스캔.
        """
        columns = model.__table__.c
        collected_at = columns["collected_at"]
        window = select(model).where(
            columns["server_id"] == server_id,
            collected_at >= text(_LATEST_WINDOW_SQL),
            collected_at <= text(_FUTURE_SKEW_SQL),
        )
        if n == 1:
            # DISTINCT ON 은 ORDER BY 선두가 그 축이어야 한다 — 차원별 1행이 곧 차원 오름차순이다.
            return (await self.session.scalars(window.distinct(dim).order_by(dim, collected_at.desc()))).all()

        ranked = window.add_columns(
            func.row_number().over(partition_by=dim, order_by=collected_at.desc()).label("rn")
        ).subquery()
        entity = aliased(model, ranked)
        stmt = select(entity).where(ranked.c["rn"] <= n).order_by(ranked.c[dim.key], ranked.c["collected_at"].desc())
        return (await self.session.scalars(stmt)).all()

    async def _latest_link_speed(self, server_ids: list[int], since: datetime) -> dict[int, dict[str, int]]:
        """서버·iface 별 최신 link_speed_bps (bit/s gauge). 없으면 그 iface 키가 빠진다.

        server 도메인(서버 네트워크 탭)과 metric 도메인(환경 자원 평가)이 같은 질의를 서로 다른 창으로
        쓴다. `since` 를 여기서 고정하면 한쪽 화면의 인터페이스 속도가 조용히 다른 창 값으로 바뀐다.
        """
        if not server_ids:
            return {}
        stmt = (
            select(ServerNetIo.server_id, ServerNetIo.iface_id, ServerNetIo.link_speed_bps)
            .where(
                ServerNetIo.server_id.in_(server_ids),
                ServerNetIo.collected_at >= since,
                ServerNetIo.collected_at <= text(_FUTURE_SKEW_SQL),
                ServerNetIo.link_speed_bps.is_not(None),
            )
            .distinct(ServerNetIo.server_id, ServerNetIo.iface_id)
            .order_by(ServerNetIo.server_id, ServerNetIo.iface_id, ServerNetIo.collected_at.desc())
        )
        out: dict[int, dict[str, int]] = {}
        for r in (await self.session.execute(stmt)).all():
            out.setdefault(r.server_id, {})[r.iface_id] = int(r.link_speed_bps)
        return out
