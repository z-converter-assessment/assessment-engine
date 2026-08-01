"""Server 도메인 concrete — inventory · storage · network · collection status."""

from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

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
from assessment_engine.db.models.server_filesystem import ServerFilesystem
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.repositories.query._base import _BaseQueryMixin
from assessment_engine.db.repositories.query.base_server import BaseServerQueryRepository

# 수집 상태 조회 윈도우 — "이 기간 내 metric 無 = 수집 끊김(None 표시)" 기준 + C5 hypertable pruning 술어.
# 수집 생존 신호용 운영 윈도우로 right-sizing 평가 윈도우(recommendation.WINDOW_DAYS)와 독립 — 연동 금지.
_COLLECTION_STATUS_WINDOW = timedelta(days=7)


class _LinkSpeedProvider(Protocol):
    """server 도메인이 sibling metric sub-repository 에 거는 계약 — `QueryRepository` MRO 결합에서 충족된다."""

    async def latest_link_speed(self, server_ids: list[int], since: datetime) -> dict[int, dict[str, int]]: ...


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
        # 명시 SELECT — listen_ports/services/net_interfaces 등 큰 JSONB 는 list 화면 미사용.
        # 뱃지는 ingest 사전계산 service_categories(text[]) 소비 — services JSONB 역직렬화 불요(경량).
        stmt = select(
            ServerInventory.id,
            ServerInventory.public_id,
            ServerInventory.composite_id,
            ServerInventory.hostname,
            ServerInventory.os_id,
            ServerInventory.os_version,
            ServerInventory.kernel_version,
            ServerInventory.product_name,
            ServerInventory.cpu_cores,
            ServerInventory.mem_total_bytes,
            ServerInventory.ip_external,
            ServerInventory.block_devices,
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
                product_name=r.product_name,
                cpu_cores=r.cpu_cores,
                mem_total_bytes=r.mem_total_bytes,
                ip_external=r.ip_external,
                block_devices=r.block_devices or [],
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
            cpu_arch=r.arch,
            cpu_bits=r.bits,
            mem_total_bytes=r.mem_total_bytes,
            boot_time=r.boot_time,
            agent_started_at=r.agent_started_at,
            net_interfaces=r.net_interfaces or [],
            ip_external=r.ip_external,
            block_devices=r.block_devices or [],
            lvm_vgs=r.lvm_vgs or [],
            services=r.services,
            listen_ports=r.listen_ports or [],
            last_seen_at=r.last_seen_at,
            service_categories=r.service_categories or [],
            product_name=r.product_name,
            edition=r.edition,
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
                ServerInventory.block_devices,
                ServerInventory.lvm_vgs,
                ServerInventory.last_seen_at,
                ServerInventory.os_family,
            ).where(ServerInventory.id == server_id)
        )
        r = inv_result.one_or_none()
        if not r:
            return None

        rows = await self._latest_per_dimension(ServerFilesystem.__tablename__, "mountpoint", server_id, n=1)
        filesystems = [
            MountUsageRaw(
                mountpoint=row.mountpoint,
                used_bytes=row.used_bytes,
                free_bytes=row.free_bytes,
                inodes_used=row.inodes_used,
                inodes_free=row.inodes_free,
                device_id=row.device_id,
                fstype=row.fstype,
                collected_at=row.collected_at,
            )
            for row in rows
        ]
        return StorageWithUsage(
            server_id=r.id,
            public_id=r.public_id,
            hostname=r.hostname,
            block_devices=r.block_devices or [],
            lvm_vgs=r.lvm_vgs or [],
            filesystems=filesystems,
            inventory_at=r.last_seen_at,
            os_family=r.os_family,
        )

    async def get_network(self, server_id: int) -> NetworkWithIo | None:
        inv_result = await self.session.execute(
            select(
                ServerInventory.id,
                ServerInventory.public_id,
                ServerInventory.hostname,
                ServerInventory.net_interfaces,
                ServerInventory.ip_external,
                ServerInventory.last_seen_at,
                ServerInventory.os_family,
            ).where(ServerInventory.id == server_id)
        )
        r = inv_result.one_or_none()
        if not r:
            return None

        # 인벤토리 speed_mbps null(virtio·Windows NT5.2) 폴백 — metrics network.link.speed 최신값(bit/s) 재사용.
        # 구현체는 본 클래스 상속 트리 밖(metric sub-repo)이라 계약 Protocol 로 좁힌다.
        metric_repo = cast("_LinkSpeedProvider", self)
        link_speeds = await metric_repo.latest_link_speed([server_id], datetime.now(UTC) - timedelta(days=30))
        link_speed_by_iface = link_speeds.get(server_id, {})

        rows = await self._latest_per_dimension(ServerNetIo.__tablename__, "iface_id", server_id, n=2)
        net_io = [
            NetIoRaw(
                iface_id=row.iface_id,
                iface_name=row.iface_name,
                collected_at=row.collected_at,
                rx_bytes=row.rx_bytes,
                tx_bytes=row.tx_bytes,
                rx_packets=row.rx_packets,
                tx_packets=row.tx_packets,
                rx_errors=row.rx_errors,
                tx_errors=row.tx_errors,
                rx_dropped=row.rx_dropped,
                tx_dropped=row.tx_dropped,
                link_speed_bps=row.link_speed_bps,
            )
            for row in rows
        ]
        return NetworkWithIo(
            server_id=r.id,
            public_id=r.public_id,
            hostname=r.hostname,
            net_interfaces=r.net_interfaces or [],
            ip_external=r.ip_external,
            net_io=net_io,
            inventory_at=r.last_seen_at,
            os_family=r.os_family,
            link_speed_by_iface=link_speed_by_iface,
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

    async def list_server_ids(self, limit: int | None = 1000) -> list[int]:
        stmt = select(ServerInventory.id).order_by(ServerInventory.id.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return [r for r in result.scalars().all()]

    async def latest_metric_at(self) -> datetime | None:
        """fleet 전체 최신 메트릭 수집 시각 — 상단 바 데이터 최신성(#C5 window 술어로 partition pruning).

        server_inventory.last_seen_at(인벤토리 하트비트, 저빈도)이 아닌 메트릭 collected_at 기준 — 실수집 신선도.
        """
        window_start = datetime.now(UTC) - _COLLECTION_STATUS_WINDOW
        result = await self.session.execute(
            select(func.max(ServerMetrics.collected_at)).where(ServerMetrics.collected_at >= window_start)
        )
        return result.scalar_one_or_none()
