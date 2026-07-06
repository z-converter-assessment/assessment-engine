"""Server 도메인 concrete — inventory · storage · network · collection status."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from assessment_engine.db.dtos.outbound import (
    CollectionStatus,
    MountUsageRaw,
    NetIoRaw,
    NetworkWithIo,
    ServerDetail,
    ServerSummary,
    StorageWithUsage,
)
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_mount_usage import ServerMountUsage
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.repositories.query._base import _BaseQueryMixin
from assessment_engine.db.repositories.query.base_server import BaseServerQueryRepository

# 수집 상태 조회 윈도우 — "이 기간 내 metric 無 = 수집 끊김(None 표시)" 기준 + C5 hypertable pruning 술어.
# 수집 생존 신호용 운영 윈도우로 right-sizing 평가 윈도우(recommendation.WINDOW_DAYS)와 독립 — 연동 금지.
_COLLECTION_STATUS_WINDOW = timedelta(days=7)


class ServerQueryRepository(_BaseQueryMixin, BaseServerQueryRepository):
    async def resolve_server_id(self, public_id: str) -> int | None:
        result = await self.session.execute(select(ServerInventory.id).where(ServerInventory.public_id == public_id))
        return result.scalar_one_or_none()

    async def resolve_server_ids(self, public_ids: list[str]) -> dict[str, int]:
        if not public_ids:
            return {}
        result = await self.session.execute(
            select(ServerInventory.public_id, ServerInventory.id).where(ServerInventory.public_id.in_(public_ids))
        )
        return {str(r.public_id): r.id for r in result.all()}

    async def list_servers(
        self,
        page: int,
        limit: int,
        search: str | None,
    ) -> list[ServerSummary]:
        # 명시 SELECT — mounts/listen_ports/services 등 큰 JSONB 는 list 화면 미사용.
        # 뱃지는 ingest 사전계산 service_categories(text[]) 소비 — services JSONB 역직렬화 불요(경량).
        stmt = select(
            ServerInventory.id,
            ServerInventory.public_id,
            ServerInventory.composite_id,
            ServerInventory.hostname,
            ServerInventory.os_id,
            ServerInventory.os_version,
            ServerInventory.kernel_version,
            ServerInventory.cpu_cores,
            ServerInventory.mem_total_kb,
            ServerInventory.ip_external,
            ServerInventory.disks,
            ServerInventory.service_categories,
            ServerInventory.last_seen_at,
        ).order_by(ServerInventory.hostname.asc())
        if search:
            stmt = stmt.where(ServerInventory.hostname.ilike(f"%{search}%"))
        stmt = stmt.offset((page - 1) * limit).limit(limit)

        result = await self.session.execute(stmt)
        return [
            ServerSummary(
                id=r.id,
                public_id=r.public_id,
                composite_id=r.composite_id,
                hostname=r.hostname,
                os_id=r.os_id,
                os_version=r.os_version,
                kernel_version=r.kernel_version,
                cpu_cores=r.cpu_cores,
                mem_total_kb=r.mem_total_kb,
                ip_external=r.ip_external,
                disks=r.disks or [],
                service_categories=r.service_categories or [],
                last_seen_at=r.last_seen_at,
            )
            for r in result.all()
        ]

    @staticmethod
    def _row_to_server_detail(r: ServerInventory) -> ServerDetail:
        return ServerDetail(
            id=r.id,
            public_id=r.public_id,
            agent_id=r.agent_id,
            composite_id=r.composite_id,
            machine_id=r.machine_id,
            hostname=r.hostname,
            agent_version=r.agent_version,
            os_family=r.os_family,
            os_id=r.os_id,
            os_version=r.os_version,
            os_codename=r.os_codename,
            kernel_version=r.kernel_version,
            cpu_cores=r.cpu_cores,
            cpu_model=r.cpu_model,
            mem_total_kb=r.mem_total_kb,
            swap_total_kb=r.swap_total_kb,
            boot_time=r.boot_time,
            agent_started_at=r.agent_started_at,
            interfaces=r.interfaces or [],
            ip_external=r.ip_external,
            disks=r.disks or [],
            mounts=r.mounts or [],
            services=r.services,
            listen_ports=r.listen_ports or [],
            last_seen_at=r.last_seen_at,
            service_categories=r.service_categories or [],
        )

    async def get_server(self, server_id: int) -> ServerDetail | None:
        result = await self.session.execute(select(ServerInventory).where(ServerInventory.id == server_id))
        r = result.scalars().one_or_none()
        return self._row_to_server_detail(r) if r is not None else None

    async def get_servers(self, server_ids: list[int]) -> list[ServerDetail]:
        if not server_ids:
            return []
        result = await self.session.execute(select(ServerInventory).where(ServerInventory.id.in_(server_ids)))
        return [self._row_to_server_detail(r) for r in result.scalars().all()]

    async def list_all_server_public_ids(self) -> list[str]:
        result = await self.session.execute(select(ServerInventory.public_id).order_by(ServerInventory.id))
        return list(result.scalars().all())

    async def get_storage(self, server_id: int) -> StorageWithUsage | None:
        inv_result = await self.session.execute(
            select(
                ServerInventory.id,
                ServerInventory.public_id,
                ServerInventory.hostname,
                ServerInventory.disks,
                ServerInventory.mounts,
                ServerInventory.last_seen_at,
            ).where(ServerInventory.id == server_id)
        )
        r = inv_result.one_or_none()
        if not r:
            return None

        rows = await self._latest_per_dimension(ServerMountUsage.__tablename__, "mount", server_id, n=1)
        mount_usage = [
            MountUsageRaw(
                mount=row.mount,
                total_bytes=row.total_bytes,
                avail_bytes=row.avail_bytes,
                free_bytes=row.free_bytes,
                collected_at=row.collected_at,
                boot_time=row.boot_time,
                agent_started_at=row.agent_started_at,
                kind=row.kind,
            )
            for row in rows
        ]
        return StorageWithUsage(
            server_id=r.id,
            public_id=r.public_id,
            hostname=r.hostname,
            disks=r.disks or [],
            inventory_mounts=r.mounts or [],
            mount_usage=mount_usage,
            inventory_at=r.last_seen_at,
        )

    async def get_network(self, server_id: int) -> NetworkWithIo | None:
        inv_result = await self.session.execute(
            select(
                ServerInventory.id,
                ServerInventory.public_id,
                ServerInventory.hostname,
                ServerInventory.interfaces,
                ServerInventory.ip_external,
                ServerInventory.last_seen_at,
            ).where(ServerInventory.id == server_id)
        )
        r = inv_result.one_or_none()
        if not r:
            return None

        rows = await self._latest_per_dimension(ServerNetIo.__tablename__, "interface", server_id, n=2)
        net_io = [
            NetIoRaw(
                interface=row.interface,
                collected_at=row.collected_at,
                rx_bytes=row.rx_bytes,
                tx_bytes=row.tx_bytes,
                rx_packets=row.rx_packets,
                tx_packets=row.tx_packets,
                rx_errors=row.rx_errors,
                tx_errors=row.tx_errors,
                kind=row.kind,
            )
            for row in rows
        ]
        return NetworkWithIo(
            server_id=r.id,
            public_id=r.public_id,
            hostname=r.hostname,
            interfaces=r.interfaces or [],
            ip_external=r.ip_external,
            net_io=net_io,
            inventory_at=r.last_seen_at,
        )

    async def get_collection_status(self, server_id: int) -> CollectionStatus | None:
        inv_result = await self.session.execute(
            select(ServerInventory.last_seen_at).where(ServerInventory.id == server_id)
        )
        row = inv_result.scalar_one_or_none()
        if row is None:
            return None
        metric_window_start = datetime.now(UTC) - _COLLECTION_STATUS_WINDOW
        metric_result = await self.session.execute(
            select(func.max(ServerMetrics.collected_at)).where(
                ServerMetrics.server_id == server_id,
                ServerMetrics.collected_at >= metric_window_start,
            )
        )
        return CollectionStatus(
            last_metric_at=metric_result.scalar_one_or_none(),
            last_inventory_at=row,
        )

    async def list_server_ids(self, limit: int = 1000) -> list[int]:
        result = await self.session.execute(select(ServerInventory.id).order_by(ServerInventory.id.asc()).limit(limit))
        return [r for r in result.scalars().all()]
