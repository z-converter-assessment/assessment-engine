"""Query sub-repository 공통 mixin — session 보유 + 다중 도메인 공유 helper.

SqlQueryRepository facade 가 5 concrete sub-repository 를 multiple inheritance 로 결합해도 `__init__` 은
본 mixin 것 하나만 돈다.
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

# agent 시계가 어긋나 collected_at 이 미래로 발행되면(예: Windows RTC 를 local TZ 로 해석해 UTC 오프셋만큼


_FUTURE_SKEW_SQL = "now() + interval '2 minutes'"

_LATEST_WINDOW_SQL = "now() - interval '30 days'"


class _BaseQueryMixin:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _latest_per_dimension[M: Base](
        self,
        model: type[M],
        dim: InstrumentedAttribute[Any],
        server_id: int,
        n: int,
    ) -> Sequence[M]:
        columns = model.__table__.c
        collected_at = columns["collected_at"]
        window = select(model).where(
            columns["server_id"] == server_id,
            collected_at >= text(_LATEST_WINDOW_SQL),
            collected_at <= text(_FUTURE_SKEW_SQL),
        )
        if n == 1:
            return (await self.session.scalars(window.distinct(dim).order_by(dim, collected_at.desc()))).all()

        ranked = window.add_columns(
            func.row_number().over(partition_by=dim, order_by=collected_at.desc()).label("rn")
        ).subquery()
        entity = aliased(model, ranked)
        stmt = select(entity).where(ranked.c["rn"] <= n).order_by(ranked.c[dim.key], ranked.c["collected_at"].desc())
        return (await self.session.scalars(stmt)).all()

    async def _latest_link_speed(self, server_ids: list[int], since: datetime) -> dict[int, dict[str, int]]:
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
