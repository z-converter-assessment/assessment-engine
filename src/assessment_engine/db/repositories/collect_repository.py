import dataclasses
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.db.models.server_disk_io import ServerDiskIo
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_mount_usage import ServerMountUsage
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository, MetricInsertResult
from assessment_engine.db.repositories.inbound import (
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
    ServerInventoryCreate,
    ServerMetricCreate,
)


class CollectRepository(BaseCollectRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    # ─── server_inventory ──────────────────────────────────────────────────

    async def find_server_id(self, machine_id: str) -> int | None:
        result = await self.session.execute(
            select(ServerInventory.id).where(ServerInventory.machine_id == machine_id)
        )
        return result.scalar_one_or_none()

    async def upsert_server(self, data: ServerInventoryCreate) -> int:
        # values()와 set_={}에 같은 컬럼 dict를 재사용 — 컬럼 추가 시 한 곳만 수정.
        # machine_id는 conflict 키이므로 set_에서 제외 (자기 자신을 자기 값으로 덮어쓰는 무의미한 동작 회피).
        row = {
            "machine_id":     data.machine_id,
            "hostname":       data.hostname,
            "agent_version":  data.agent_version,
            "os_id":          data.os_id,
            "os_version":     data.os_version,
            "os_codename":    data.os_codename,
            "kernel_version": data.kernel_version,
            "cpu_cores":      data.cpu_cores,
            "cpu_model":      data.cpu_model,
            "mem_total_kb":   data.mem_total_kb,
            "swap_total_kb":  data.swap_total_kb,
            "boot_time":      data.boot_time,
            "ip_internal":    data.ip_internal,
            "ip_external":    data.ip_external,
            "disks":          data.disks,
            "mounts":         data.mounts,
            "services":       data.services,
            "listen_ports":   data.listen_ports,
            "last_seen_at":   data.collected_at,
        }
        update_set = {k: v for k, v in row.items() if k != "machine_id"}

        stmt = (
            pg_insert(ServerInventory)
            .values(**row)
            .on_conflict_do_update(index_elements=["machine_id"], set_=update_set)
            .returning(ServerInventory.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def ensure_server_id(
        self,
        machine_id: str,
        fallback: ServerInventoryCreate,
    ) -> tuple[int, bool]:
        # 1. 이미 등록 → 그대로 사용 (placeholder upsert 금지 — 진짜 inventory 보호)
        server_id = await self.find_server_id(machine_id)
        if server_id is not None:
            return server_id, False

        # 2. INSERT 시도. inventory_handler가 동시에 commit 중이면 ON CONFLICT DO NOTHING으로 보호
        #    (placeholder가 진짜 inventory의 OS/CPU/Memory 등을 None으로 덮어쓰는 race 방지).
        new_id = await self._insert_placeholder_server(fallback)
        if new_id is not None:
            return new_id, True

        # 3. 충돌 = 다른 핸들러가 방금 INSERT. 다시 find — 이번엔 보임.
        server_id = await self.find_server_id(machine_id)
        if server_id is None:
            raise RuntimeError(f"failed to ensure server_id for {machine_id} (race not resolved)")
        return server_id, False

    async def _insert_placeholder_server(self, data: ServerInventoryCreate) -> int | None:
        """placeholder 전용 INSERT. ON CONFLICT DO NOTHING — 이미 있으면 건드리지 않고 None 반환.

        `upsert_server`(ON CONFLICT DO UPDATE)를 placeholder가 호출하면 진짜 inventory의 OS/CPU/Memory 등을
        None으로 덮어쓰는 race(에이전트 부팅 직후 inventory와 metrics 거의 동시 도착) 발생.
        placeholder는 "이미 있으면 손대지 않는다"는 의미가 자연스러움.
        """
        row = {
            "machine_id":     data.machine_id,
            "hostname":       data.hostname,
            "agent_version":  data.agent_version,
            "os_id":          data.os_id,
            "os_version":     data.os_version,
            "os_codename":    data.os_codename,
            "kernel_version": data.kernel_version,
            "cpu_cores":      data.cpu_cores,
            "cpu_model":      data.cpu_model,
            "mem_total_kb":   data.mem_total_kb,
            "swap_total_kb":  data.swap_total_kb,
            "boot_time":      data.boot_time,
            "ip_internal":    data.ip_internal,
            "ip_external":    data.ip_external,
            "disks":          data.disks,
            "mounts":         data.mounts,
            "services":       data.services,
            "listen_ports":   data.listen_ports,
            "last_seen_at":   data.collected_at,
        }
        stmt = (
            pg_insert(ServerInventory)
            .values(**row)
            .on_conflict_do_nothing(index_elements=["machine_id"])
            .returning(ServerInventory.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ─── 시계열 (record_metrics) ───────────────────────────────────────────

    async def record_metrics(
        self,
        server_id: int,
        data: ServerMetricCreate,
    ) -> MetricInsertResult:
        # ON CONFLICT DO NOTHING — Redis 멱등성 키(24h TTL) 만료/evict/Redis 장애로 중복 메시지가
        # 들어와도 자연키 UNIQUE 위반을 silent no-op으로 처리. 4개 테이블 모두 동일 정책.
        # `pg_insert(...).on_conflict_do_nothing(...)` 결과의 rowcount는 실제 INSERT된 행 수.
        metrics_n = await self._insert_scalar_metrics(server_id, data)
        disk_io_n = await self._insert_disk_io(server_id, data.collected_at, data.disk_io)
        net_io_n  = await self._insert_net_io(server_id, data.collected_at, data.net_io)
        mount_n   = await self._insert_mount_usage(server_id, data.collected_at, data.mounts)
        return MetricInsertResult(
            metrics=metrics_n,
            disk_io=disk_io_n,
            net_io=net_io_n,
            mount_usage=mount_n,
        )

    async def _insert_scalar_metrics(
        self,
        server_id: int,
        data: ServerMetricCreate,
    ) -> int:
        stmt = pg_insert(ServerMetrics).values(
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
        ).on_conflict_do_nothing(index_elements=["server_id", "collected_at"])
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def _insert_disk_io(
        self,
        server_id: int,
        collected_at: datetime,
        entries: list[DiskIoEntry],
    ) -> int:
        if not entries:
            return 0
        stmt = pg_insert(ServerDiskIo).values([
            {"server_id": server_id, "collected_at": collected_at, **dataclasses.asdict(e)}
            for e in entries
        ]).on_conflict_do_nothing(index_elements=["server_id", "device", "collected_at"])
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def _insert_net_io(
        self,
        server_id: int,
        collected_at: datetime,
        entries: list[NetIoEntry],
    ) -> int:
        if not entries:
            return 0
        stmt = pg_insert(ServerNetIo).values([
            {"server_id": server_id, "collected_at": collected_at, **dataclasses.asdict(e)}
            for e in entries
        ]).on_conflict_do_nothing(index_elements=["server_id", "interface", "collected_at"])
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def _insert_mount_usage(
        self,
        server_id: int,
        collected_at: datetime,
        entries: list[MountUsageEntry],
    ) -> int:
        if not entries:
            return 0
        stmt = pg_insert(ServerMountUsage).values([
            {"server_id": server_id, "collected_at": collected_at, **dataclasses.asdict(e)}
            for e in entries
        ]).on_conflict_do_nothing(index_elements=["server_id", "mount", "collected_at"])
        result = await self.session.execute(stmt)
        return result.rowcount or 0