from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.server_disk_io import ServerDiskIo
from db.models.server_inventory import ServerInventory
from db.models.server_metrics import ServerMetrics
from db.models.server_mount_usage import ServerMountUsage
from db.models.server_net_io import ServerNetIo
from db.repositories.base_collect_repository import BaseCollectRepository
from db.repositories.inbound import ServerInventoryCreate, ServerMetricCreate


class CollectRepository(BaseCollectRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_server_id(self, machine_id: str) -> int | None:
        result = await self.session.execute(select(ServerInventory.id)
                                            .where(ServerInventory.machine_id == machine_id))
        return result.scalar_one_or_none()

    async def upsert_server(self, data: ServerInventoryCreate) -> None:
        stmt = pg_insert(ServerInventory).values(
            machine_id=data.machine_id,
            hostname=data.hostname,
            agent_version=data.agent_version,
            os_id=data.os_id,
            os_version=data.os_version,
            os_codename=data.os_codename,
            kernel_version=data.kernel_version,
            cpu_cores=data.cpu_cores,
            cpu_model=data.cpu_model,
            mem_total_kb=data.mem_total_kb,
            swap_total_kb=data.swap_total_kb,
            boot_time=data.boot_time,
            ip_internal=data.ip_internal,
            ip_external=data.ip_external,
            disks=data.disks,
            mounts=data.mounts,
            last_seen_at=data.collected_at,
        ).on_conflict_do_update(
            index_elements=["machine_id"],
            set_={
                "hostname": data.hostname,
                "agent_version": data.agent_version,
                "os_id": data.os_id,
                "os_version": data.os_version,
                "os_codename": data.os_codename,
                "kernel_version": data.kernel_version,
                "cpu_cores": data.cpu_cores,
                "cpu_model": data.cpu_model,
                "mem_total_kb": data.mem_total_kb,
                "swap_total_kb": data.swap_total_kb,
                "boot_time": data.boot_time,
                "ip_internal": data.ip_internal,
                "ip_external": data.ip_external,
                "disks": data.disks,
                "mounts": data.mounts,
                "last_seen_at": data.collected_at,
            },
        )
        await self.session.execute(stmt)

    async def insert_metric(self, server_id: int, data: ServerMetricCreate) -> None:
        self.session.add(ServerMetrics(
            server_id=server_id,
            collected_at=data.collected_at,
            cpu_user=data.cpu_user,
            cpu_nice=data.cpu_nice,
            cpu_system=data.cpu_system,
            cpu_idle=data.cpu_idle,
            cpu_iowait=data.cpu_iowait,
            cpu_irq=data.cpu_irq,
            cpu_softirq=data.cpu_softirq,
            cpu_steal=data.cpu_steal,
            mem_total_kb=data.mem_total_kb,
            mem_free_kb=data.mem_free_kb,
            mem_available_kb=data.mem_available_kb,
            mem_buffers_kb=data.mem_buffers_kb,
            mem_cached_kb=data.mem_cached_kb,
            swap_total_kb=data.swap_total_kb,
            swap_free_kb=data.swap_free_kb,
            load_1m=data.load_1m,
            load_5m=data.load_5m,
            load_15m=data.load_15m,
        ))

        if data.disk_io:
            await self.session.execute(
                insert(ServerDiskIo),
                [{"server_id": server_id, "collected_at": data.collected_at, **d} for d in data.disk_io],
            )

        if data.net_io:
            await self.session.execute(
                insert(ServerNetIo),
                [{"server_id": server_id, "collected_at": data.collected_at, **d} for d in data.net_io],
            )

        if data.mounts:
            await self.session.execute(
                insert(ServerMountUsage),
                [{"server_id": server_id, "collected_at": data.collected_at, **d} for d in data.mounts],
            )