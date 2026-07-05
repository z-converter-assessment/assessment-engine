import dataclasses

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from assessment_engine.boot_time import boot_time_changed
from assessment_engine.db.dtos.inbound import (
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
    ServerInventoryCreate,
    ServerMetricCreate,
    TaskCreate,
    TaskResultUpdate,
)
from assessment_engine.db.models.server_disk_io import ServerDiskIo
from assessment_engine.db.models.server_inventory import ServerInventory
from assessment_engine.db.models.server_inventory_history import ServerInventoryHistory
from assessment_engine.db.models.server_metrics import ServerMetrics
from assessment_engine.db.models.server_mount_usage import ServerMountUsage
from assessment_engine.db.models.server_net_io import ServerNetIo
from assessment_engine.db.models.task import Task
from assessment_engine.db.repositories.base_collect_repository import BaseCollectRepository, MetricInsertResult


class CollectRepository(BaseCollectRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    # ─── server_inventory ──────────────────────────────────────────────────

    async def find_server_id(self, agent_id: str) -> int | None:
        result = await self.session.execute(select(ServerInventory.id).where(ServerInventory.agent_id == agent_id))
        return result.scalar_one_or_none()

    # 비교 대상 컬럼만 SELECT (매 inventory 메시지 hot path — 불필요 컬럼 read 절약).
    # agent_id 는 UNIQUE 키라 비교 무의미, composite_id/machine_id 는 감사·표시용(history 미추적)이라 제외.
    _INVENTORY_COMPARE_COLS = (
        ServerInventory.agent_version,
        ServerInventory.os_family,
        ServerInventory.os_id,
        ServerInventory.os_version,
        ServerInventory.os_codename,
        ServerInventory.kernel_version,
        ServerInventory.cpu_cores,
        ServerInventory.cpu_model,
        ServerInventory.mem_total_kb,
        ServerInventory.swap_total_kb,
        ServerInventory.boot_time,
        ServerInventory.agent_started_at,
        ServerInventory.interfaces,
        ServerInventory.ip_external,
        ServerInventory.disks,
        ServerInventory.mounts,
        ServerInventory.services,
        ServerInventory.listen_ports,
    )

    async def upsert_server(self, data: ServerInventoryCreate) -> int:
        # 변경 감지: 직전 행과 다를 때만 history 한 행 INSERT (앱 레벨 trigger).
        # agent_id 가 부팅 무관 불변이라 재부팅해도 동일 agent_id 가 자연히 같은 행을 잡는다 (호스트 재연결 로직 불요).
        prev_q = await self.session.execute(
            select(*self._INVENTORY_COMPARE_COLS).where(ServerInventory.agent_id == data.agent_id)
        )
        prev = prev_q.first()

        # values()/set_ 에 같은 dict 재사용 — 컬럼 추가 시 한 곳만 수정.
        # agent_id 는 UNIQUE 키라 set_ 제외 (자기 덮어쓰기 무의미). composite_id 는 감사용이라 update 대상.
        row = {
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
            "mem_total_kb": data.mem_total_kb,
            "swap_total_kb": data.swap_total_kb,
            "boot_time": data.boot_time,
            "agent_started_at": data.agent_started_at,
            "interfaces": data.interfaces,
            "ip_external": data.ip_external,
            "mac_addresses": data.mac_addresses,
            "disks": data.disks,
            "mounts": data.mounts,
            "services": data.services,
            "listen_ports": data.listen_ports,
            "service_categories": data.service_categories,
            "last_seen_at": data.collected_at,
        }
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
    def _inventory_changed(prev: ServerInventory, new: ServerInventoryCreate) -> bool:
        """변경 감지. collected_at·last_seen_at·agent_id·composite_id·machine_id·hostname 제외 비교."""
        return (
            prev.agent_version != new.agent_version
            or prev.os_family != new.os_family
            or prev.os_id != new.os_id
            or prev.os_version != new.os_version
            or prev.os_codename != new.os_codename
            or prev.kernel_version != new.kernel_version
            or prev.cpu_cores != new.cpu_cores
            or prev.cpu_model != new.cpu_model
            or prev.mem_total_kb != new.mem_total_kb
            or prev.swap_total_kb != new.swap_total_kb
            or boot_time_changed(prev.boot_time, new.boot_time)
            or prev.agent_started_at != new.agent_started_at
            or prev.interfaces != new.interfaces
            or prev.ip_external != new.ip_external
            or prev.disks != new.disks
            or prev.mounts != new.mounts
            or prev.services != new.services
            or prev.listen_ports != new.listen_ports
        )

    async def _append_inventory_history(self, server_id: int, data: ServerInventoryCreate) -> None:
        """인벤토리 스냅샷을 history에 append.

        ON CONFLICT DO NOTHING — broker 재전송·동시 워커 race 로 동일 (server_id, collected_at)
        2번째 INSERT 가 와도 silent no-op (시계열 4개 테이블과 동일 안전망).
        """
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
                cpu_cores=data.cpu_cores,
                cpu_model=data.cpu_model,
                mem_total_kb=data.mem_total_kb,
                swap_total_kb=data.swap_total_kb,
                boot_time=data.boot_time,
                agent_started_at=data.agent_started_at,
                interfaces=data.interfaces,
                ip_external=data.ip_external,
                disks=data.disks,
                mounts=data.mounts,
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
        # 1. 이미 등록 → 그대로 사용 (placeholder upsert 금지 — 진짜 inventory 보호)
        server_id = await self.find_server_id(agent_id)
        if server_id is not None:
            return server_id, False

        # 2. INSERT 시도. ON CONFLICT DO NOTHING — inventory_handler 동시 commit 시 보호.
        new_id = await self._insert_placeholder_server(fallback)
        if new_id is not None:
            return new_id, True

        # 3. 충돌 = 다른 핸들러가 방금 INSERT. 다시 find — 이번엔 보임.
        server_id = await self.find_server_id(agent_id)
        if server_id is None:
            raise RuntimeError(f"failed to ensure server_id for agent_id={agent_id} (race not resolved)")
        return server_id, False

    async def _insert_placeholder_server(self, data: ServerInventoryCreate) -> int | None:
        """placeholder 전용 INSERT. ON CONFLICT DO NOTHING — 이미 있으면 손대지 않고 None 반환.

        upsert_server(DO UPDATE)를 placeholder 가 호출하면 진짜 inventory 의 OS/CPU/Memory 를
        None 으로 덮어쓰는 race(부팅 직후 inventory·metrics 거의 동시 도착) 발생 — 그래서 DO NOTHING.
        """
        row = {
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
            "mem_total_kb": data.mem_total_kb,
            "swap_total_kb": data.swap_total_kb,
            "boot_time": data.boot_time,
            "agent_started_at": data.agent_started_at,
            "interfaces": data.interfaces,
            "ip_external": data.ip_external,
            "mac_addresses": data.mac_addresses,
            "disks": data.disks,
            "mounts": data.mounts,
            "services": data.services,
            "listen_ports": data.listen_ports,
            "service_categories": data.service_categories,
            "last_seen_at": data.collected_at,
        }
        stmt = (
            pg_insert(ServerInventory)
            .values(**row)
            .on_conflict_do_nothing(index_elements=["agent_id"])
            .returning(ServerInventory.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ─── tasks ─────────────────────────────────────────────────────────────

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
        # DB now() 로 서버 시각 단일 비교 (클라이언트 시각차 회피).
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
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def expire_all_overdue_tasks(self) -> int:
        # reaper 전역 버전 — server_ids 무필터. DB now() 단일 비교(클라이언트 시각차 회피).
        stmt = (
            update(Task)
            .where(
                Task.status == "pending",
                Task.deadline_at.is_not(None),
                Task.deadline_at < func.now(),
            )
            .values(status="failure", failure_reason="timeout", completed_at=func.now())
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def find_pending_deadline_servers(self, server_ids: list[int]) -> list[int]:
        if not server_ids:
            return []
        # expire 직후 호출 가정 — 만료분은 이미 failure 전이됨.
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
                duration_ms=data.duration_ms,
                stdout_tail=data.stdout_tail,
                stderr_tail=data.stderr_tail,
            )
        )
        result = await self.session.execute(stmt)
        return (result.rowcount or 0) > 0

    # ─── 시계열 (record_metrics) ───────────────────────────────────────────

    async def record_metrics(
        self,
        server_id: int,
        data: ServerMetricCreate,
    ) -> MetricInsertResult:
        # ON CONFLICT DO NOTHING — Redis 멱등성 키 만료/장애로 중복 메시지가 들어와도
        # 자연키 UNIQUE 위반을 silent no-op (D2 2단 방어). 4개 테이블 동일 정책.
        # boot_time/agent_started_at 은 4개 테이블에 동일 시점값 — calculator counter reset 식별용
        # (mount_usage 는 시점값이라 직접 활용 없으나 메타데이터 일관성 위해 보존).
        metrics_n = await self._insert_scalar_metrics(server_id, data)
        disk_io_n = await self._insert_disk_io(server_id, data, data.disk_io)
        net_io_n = await self._insert_net_io(server_id, data, data.net_io)
        mount_n = await self._insert_mount_usage(server_id, data, data.mounts)
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
        stmt = (
            pg_insert(ServerMetrics)
            .values(
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
                sat_disk_queue=data.sat_disk_queue,
                sat_cpu_run_queue=data.sat_cpu_run_queue,
                sat_mem_paging_rate=data.sat_mem_paging_rate,
                procs_running=data.procs_running,
                procs_blocked=data.procs_blocked,
                schedstat_run_wait_ns=data.schedstat_run_wait_ns,
                pswpin=data.pswpin,
                pswpout=data.pswpout,
                oom_kill=data.oom_kill,
                mem_pages_input=data.mem_pages_input,
                tcp_retrans_segs=data.tcp_retrans_segs,
                tcp_tw=data.tcp_tw,
                conntrack_count=data.conntrack_count,
                conntrack_max=data.conntrack_max,
                boot_time=data.boot_time,
                agent_started_at=data.agent_started_at,
            )
            .on_conflict_do_nothing(index_elements=["server_id", "collected_at"])
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def _insert_disk_io(
        self,
        server_id: int,
        data: ServerMetricCreate,
        entries: list[DiskIoEntry],
    ) -> int:
        if not entries:
            return 0
        stmt = (
            pg_insert(ServerDiskIo)
            .values(
                [
                    {
                        "server_id": server_id,
                        "collected_at": data.collected_at,
                        "boot_time": data.boot_time,
                        "agent_started_at": data.agent_started_at,
                        **dataclasses.asdict(e),
                    }
                    for e in entries
                ]
            )
            .on_conflict_do_nothing(index_elements=["server_id", "device", "collected_at"])
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def _insert_net_io(
        self,
        server_id: int,
        data: ServerMetricCreate,
        entries: list[NetIoEntry],
    ) -> int:
        if not entries:
            return 0
        stmt = (
            pg_insert(ServerNetIo)
            .values(
                [
                    {
                        "server_id": server_id,
                        "collected_at": data.collected_at,
                        "boot_time": data.boot_time,
                        "agent_started_at": data.agent_started_at,
                        **dataclasses.asdict(e),
                    }
                    for e in entries
                ]
            )
            .on_conflict_do_nothing(index_elements=["server_id", "interface", "collected_at"])
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def _insert_mount_usage(
        self,
        server_id: int,
        data: ServerMetricCreate,
        entries: list[MountUsageEntry],
    ) -> int:
        if not entries:
            return 0
        stmt = (
            pg_insert(ServerMountUsage)
            .values(
                [
                    {
                        "server_id": server_id,
                        "collected_at": data.collected_at,
                        "boot_time": data.boot_time,
                        "agent_started_at": data.agent_started_at,
                        **dataclasses.asdict(e),
                    }
                    for e in entries
                ]
            )
            .on_conflict_do_nothing(index_elements=["server_id", "mount", "collected_at"])
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0
