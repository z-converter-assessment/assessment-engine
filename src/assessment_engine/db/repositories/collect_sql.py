from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import CursorResult, Row, Table, UniqueConstraint, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from assessment_engine.db.models.server_cpu_core import ServerCpuCore
from assessment_engine.db.models.server_disk_error import ServerDiskError
from assessment_engine.db.models.server_disk_io import ServerDiskIo
from assessment_engine.db.models.server_filesystem import ServerFilesystem
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_inventory_history import ServerInventoryHistory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.models.server_pressure import ServerPressure
from assessment_engine.db.models.task import Task
from assessment_engine.db.repositories.collect import MetricInsertResult
from assessment_engine.domain.boot_time import boot_time_changed

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from assessment_engine.db.dtos.inbound import (
        CpuCoreEntry,
        DiskErrorEntry,
        DiskIoEntry,
        FilesystemEntry,
        NetIoEntry,
        PressureEntry,
        ServerInventoryCreate,
        ServerMetricCreate,
        TaskCreate,
        TaskResultUpdate,
    )


def _natural_key(model: type) -> list[str]:
    """모델이 선언한 시계열 자연키 — 멱등성 2단 방어가 이 제약에 기대므로 다른 UNIQUE 위반은 삼키지 않는다.

    `Table.constraints` 는 set 이라 UNIQUE 가 둘 이상이면 어느 쪽이 잡힐지 프로세스마다 달라진다.
    자연키를 하나로 특정할 수 없는 모델은 침묵 대신 거부한다.
    """
    table = cast("Table", model.__table__)  # pyright: ignore[reportUnknownMemberType]
    uniques = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
    if len(uniques) != 1:
        raise RuntimeError(f"{model.__name__} 의 자연키 UNIQUE 가 {len(uniques)}개 — 충돌 대상을 특정할 수 없다")
    return [col.name for col in uniques[0].columns]


_NATURAL_KEYS: dict[type, list[str]] = {
    m: _natural_key(m)
    for m in (ServerCpuCore, ServerDiskIo, ServerNetIo, ServerFilesystem, ServerPressure, ServerDiskError)
}


class SqlCollectRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_server_id(self, agent_id: str) -> int | None:
        result = await self.session.execute(select(ServerInventory.id).where(ServerInventory.agent_id == agent_id))
        return result.scalar_one_or_none()

    # agent_id 는 UNIQUE 키라 비교가 무의미하고, composite_id·machine_id 는 감사용(history 미추적)이라 제외.
    _INVENTORY_COMPARE_COLS = (
        ServerInventory.agent_version,
        ServerInventory.os_family,
        ServerInventory.os_id,
        ServerInventory.os_version,
        ServerInventory.os_codename,
        ServerInventory.kernel_version,
        ServerInventory.arch,
        ServerInventory.bits,
        ServerInventory.boot_firmware,
        ServerInventory.secure_boot,
        ServerInventory.edition,
        ServerInventory.product_name,
        ServerInventory.timezone,
        ServerInventory.rtc_utc,
        ServerInventory.cpu_cores,
        ServerInventory.cpu_model,
        ServerInventory.mem_total_bytes,
        ServerInventory.boot_time,
        ServerInventory.agent_started_at,
        ServerInventory.block_devices,
        ServerInventory.net_interfaces,
        ServerInventory.lvm_vgs,
        ServerInventory.boot,
        ServerInventory.nonblock_mounts,
        ServerInventory.ip_external,
        ServerInventory.services,
        ServerInventory.listen_ports,
    )

    @staticmethod
    def _inventory_row(data: ServerInventoryCreate) -> dict[str, Any]:
        return {
            "agent_id": data.agent_id,
            "composite_id": data.composite_id,
            "machine_id": data.machine_id,
            "hostname": data.hostname,
            "agent_version": data.agent_version,
            "os_family": data.os_family,
            "os_id": data.os_id,
            "os_version": data.os_version,
            "os_codename": data.os_codename,
            "kernel_version": data.kernel_version,
            "cpu_cores": data.cpu_cores,
            "cpu_model": data.cpu_model,
            "mem_total_bytes": data.mem_total_bytes,
            "boot_time": data.boot_time,
            "agent_started_at": data.agent_started_at,
            "block_devices": data.block_devices,
            "net_interfaces": data.net_interfaces,
            "lvm_vgs": data.lvm_vgs,
            "ip_external": data.ip_external,
            "services": data.services,
            "listen_ports": data.listen_ports,
            "service_categories": data.service_categories,
            "arch": data.arch,
            "bits": data.bits,
            "boot_firmware": data.boot_firmware,
            "secure_boot": data.secure_boot,
            "edition": data.edition,
            "product_name": data.product_name,
            "timezone": data.timezone,
            "rtc_utc": data.rtc_utc,
            "boot": data.boot,
            "nonblock_mounts": data.nonblock_mounts,
            "last_seen_at": data.collected_at,
        }

    async def upsert_server(self, data: ServerInventoryCreate) -> int:

        prev_q = await self.session.execute(
            select(*self._INVENTORY_COMPARE_COLS).where(ServerInventory.agent_id == data.agent_id)
        )
        prev = prev_q.first()

        row = self._inventory_row(data)

        update_set = {k: v for k, v in row.items() if k != "agent_id"}

        stmt = (
            pg_insert(ServerInventory)
            .values(**row)
            .on_conflict_do_update(
                index_elements=["agent_id"],
                set_=update_set,
            )
            .returning(ServerInventory.id)
        )
        result = await self.session.execute(stmt)
        server_id = result.scalar_one()

        if prev is None or self._inventory_changed(prev, data):
            await self._append_inventory_history(server_id, data)

        return server_id

    @staticmethod
    def _inventory_changed(prev: Row[Any], new: ServerInventoryCreate) -> bool:
        return (
            prev.agent_version != new.agent_version
            or prev.os_family != new.os_family
            or prev.os_id != new.os_id
            or prev.os_version != new.os_version
            or prev.os_codename != new.os_codename
            or prev.kernel_version != new.kernel_version
            or prev.arch != new.arch
            or prev.bits != new.bits
            or prev.boot_firmware != new.boot_firmware
            or prev.secure_boot != new.secure_boot
            or prev.edition != new.edition
            or prev.product_name != new.product_name
            or prev.timezone != new.timezone
            or prev.rtc_utc != new.rtc_utc
            or prev.cpu_cores != new.cpu_cores
            or prev.cpu_model != new.cpu_model
            or prev.mem_total_bytes != new.mem_total_bytes
            or boot_time_changed(prev.boot_time, new.boot_time)
            or prev.agent_started_at != new.agent_started_at
            or prev.block_devices != new.block_devices
            or prev.net_interfaces != new.net_interfaces
            or prev.lvm_vgs != new.lvm_vgs
            or prev.boot != new.boot
            or prev.nonblock_mounts != new.nonblock_mounts
            or prev.ip_external != new.ip_external
            or prev.services != new.services
            or prev.listen_ports != new.listen_ports
        )

    async def _append_inventory_history(self, server_id: int, data: ServerInventoryCreate) -> None:
        """DO NOTHING — broker 재전송·워커 race 로 같은 (server_id, collected_at) 이 또 와도 no-op."""
        stmt = (
            pg_insert(ServerInventoryHistory)
            .values(
                server_id=server_id,
                collected_at=data.collected_at,
                hostname=data.hostname,
                agent_version=data.agent_version,
                os_family=data.os_family,
                os_id=data.os_id,
                os_version=data.os_version,
                os_codename=data.os_codename,
                kernel_version=data.kernel_version,
                arch=data.arch,
                bits=data.bits,
                boot_firmware=data.boot_firmware,
                secure_boot=data.secure_boot,
                edition=data.edition,
                product_name=data.product_name,
                timezone=data.timezone,
                rtc_utc=data.rtc_utc,
                cpu_cores=data.cpu_cores,
                cpu_model=data.cpu_model,
                mem_total_bytes=data.mem_total_bytes,
                boot_time=data.boot_time,
                agent_started_at=data.agent_started_at,
                block_devices=data.block_devices,
                net_interfaces=data.net_interfaces,
                lvm_vgs=data.lvm_vgs,
                boot=data.boot,
                nonblock_mounts=data.nonblock_mounts,
                ip_external=data.ip_external,
                services=data.services,
                listen_ports=data.listen_ports,
            )
            .on_conflict_do_nothing(index_elements=["server_id", "collected_at"])
        )
        await self.session.execute(stmt)

    async def ensure_server_id(
        self,
        agent_id: str,
        fallback: ServerInventoryCreate,
    ) -> tuple[int, bool]:
        server_id = await self.find_server_id(agent_id)
        if server_id is not None:
            return server_id, False

        new_id = await self._insert_placeholder_server(fallback)
        if new_id is not None:
            return new_id, True

        server_id = await self.find_server_id(agent_id)
        if server_id is None:
            raise RuntimeError(f"failed to ensure server_id for agent_id={agent_id} (race not resolved)")
        return server_id, False

    async def _insert_placeholder_server(self, data: ServerInventoryCreate) -> int | None:
        stmt = (
            pg_insert(ServerInventory)
            .values(**self._inventory_row(data))
            .on_conflict_do_nothing(index_elements=["agent_id"])
            .returning(ServerInventory.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_task(self, data: TaskCreate) -> str:
        stmt = (
            pg_insert(Task)
            .values(
                target_server_id=data.target_server_id,
                target_agent_id=data.target_agent_id,
                task_type=data.task_type,
                params=data.params,
                status="pending",
                deadline_at=data.deadline_at,
            )
            .returning(Task.public_id)
        )
        result = await self.session.execute(stmt)
        return str(result.scalar_one())

    async def expire_overdue_tasks(self, server_ids: list[int]) -> int:
        if not server_ids:
            return 0

        stmt = (
            update(Task)
            .where(
                Task.target_server_id.in_(server_ids),
                Task.status == "pending",
                Task.deadline_at.is_not(None),
                Task.deadline_at < func.now(),
            )
            .values(status="failure", failure_reason="timeout", completed_at=func.now())
        )

        result = cast("CursorResult[Any]", await self.session.execute(stmt))
        return result.rowcount or 0

    async def expire_all_overdue_tasks(self) -> int:

        stmt = (
            update(Task)
            .where(
                Task.status == "pending",
                Task.deadline_at.is_not(None),
                Task.deadline_at < func.now(),
            )
            .values(status="failure", failure_reason="timeout", completed_at=func.now())
        )
        result = cast("CursorResult[Any]", await self.session.execute(stmt))
        return result.rowcount or 0

    async def find_pending_deadline_servers(self, server_ids: list[int]) -> list[int]:
        if not server_ids:
            return []

        stmt = (
            select(Task.target_server_id)
            .where(
                Task.target_server_id.in_(server_ids),
                Task.status == "pending",
                Task.deadline_at.is_not(None),
            )
            .distinct()
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def complete_task(self, data: TaskResultUpdate) -> bool:
        stmt = (
            update(Task)
            .where(Task.public_id == data.public_id)
            .values(
                status=data.status,
                completed_at=data.completed_at,
                failure_reason=data.failure_reason,
                exit_code=data.exit_code,
                signal_no=data.signal_no,
                task_policy=data.task_policy,
                duration_ms=data.duration_ms,
                stdout_tail=data.stdout_tail,
                stderr_tail=data.stderr_tail,
            )
        )
        result = cast("CursorResult[Any]", await self.session.execute(stmt))
        return (result.rowcount or 0) > 0

    async def record_metrics(
        self,
        server_id: int,
        data: ServerMetricCreate,
    ) -> MetricInsertResult:
        # 중복 메시지는 전부 자연키 UNIQUE 가 흡수한다 (#D2 2단 방어).

        return MetricInsertResult(
            metrics=await self._insert_scalar_metrics(server_id, data),
            disk_io=await self._insert_child(ServerDiskIo, server_id, data, data.disk_io),
            net_io=await self._insert_child(ServerNetIo, server_id, data, data.net_io),
            filesystem=await self._insert_child(ServerFilesystem, server_id, data, data.filesystems),
            cpu_core=await self._insert_child(ServerCpuCore, server_id, data, data.cpu_per_core),
            pressure=await self._insert_child(ServerPressure, server_id, data, data.pressure),
            # Postgres 는 UNIQUE 에서 NULL 을 서로 다르게 보므로 member None 을 '' 로 정규화한다.
            disk_error=await self._insert_child(
                ServerDiskError,
                server_id,
                data,
                data.disk_errors,
                row_hook=lambda row: {**row, "member": row["member"] or ""},
            ),
        )

    async def _insert_child(
        self,
        model: type,
        server_id: int,
        data: ServerMetricCreate,
        entries: Sequence[CpuCoreEntry | DiskIoEntry | NetIoEntry | FilesystemEntry | PressureEntry | DiskErrorEntry],
        row_hook: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> int:
        if not entries:
            return 0
        rows: list[dict[str, Any]] = [
            {"server_id": server_id, "collected_at": data.collected_at, **vars(e)} for e in entries
        ]
        if row_hook is not None:
            rows = [row_hook(row) for row in rows]
        stmt = pg_insert(model).values(rows).on_conflict_do_nothing(index_elements=_NATURAL_KEYS[model])
        result = cast("CursorResult[Any]", await self.session.execute(stmt))
        return result.rowcount or 0

    async def _insert_scalar_metrics(
        self,
        server_id: int,
        data: ServerMetricCreate,
    ) -> int:
        stmt = (
            pg_insert(ServerMetrics)
            .values(
                server_id=server_id,
                collected_at=data.collected_at,
                cpu_user_s=data.cpu_user_s,
                cpu_nice_s=data.cpu_nice_s,
                cpu_system_s=data.cpu_system_s,
                cpu_idle_s=data.cpu_idle_s,
                cpu_iowait_s=data.cpu_iowait_s,
                cpu_irq_s=data.cpu_irq_s,
                cpu_softirq_s=data.cpu_softirq_s,
                cpu_steal_s=data.cpu_steal_s,
                cpu_logical_count=data.cpu_logical_count,
                cpu_run_queue=data.cpu_run_queue,
                cpu_blocked=data.cpu_blocked,
                cpu_mce=data.cpu_mce,
                mem_free_bytes=data.mem_free_bytes,
                mem_cached_bytes=data.mem_cached_bytes,
                mem_buffered_bytes=data.mem_buffered_bytes,
                mem_available_bytes=data.mem_available_bytes,
                mem_used_bytes=data.mem_used_bytes,
                mem_limit_bytes=data.mem_limit_bytes,
                mem_commit_usage_bytes=data.mem_commit_usage_bytes,
                mem_commit_limit_bytes=data.mem_commit_limit_bytes,
                mem_hardware_corrupted_bytes=data.mem_hardware_corrupted_bytes,
                mem_oom_kill=data.mem_oom_kill,
                paging_in=data.paging_in,
                paging_out=data.paging_out,
                paging_major=data.paging_major,
                net_tcp_retransmits=data.net_tcp_retransmits,
                net_conntrack_usage=data.net_conntrack_usage,
                net_conntrack_limit=data.net_conntrack_limit,
                boot_time=data.boot_time,
                agent_started_at=data.agent_started_at,
            )
            .on_conflict_do_nothing(index_elements=["server_id", "collected_at"])
        )
        result = cast("CursorResult[Any]", await self.session.execute(stmt))
        return result.rowcount or 0
