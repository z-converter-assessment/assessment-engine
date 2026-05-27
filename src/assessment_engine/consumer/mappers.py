from assessment_engine.consumer.metric_normalize import clamp_ceiling
from assessment_engine.consumer.schemas import InventoryInput, MetricsInput
from assessment_engine.db.dtos.inbound import (
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
    ServerInventoryCreate,
    ServerMetricCreate,
)


def build_placeholder_inventory(data: MetricsInput) -> ServerInventoryCreate:
    """metrics 메시지로부터 최소 정보의 placeholder inventory 생성.

    auto-register 시나리오: server_inventory에 composite_id가 없는데 metrics가 들어왔을 때
    metrics를 drop하지 않기 위한 임시 등록. 정적 정보(OS·CPU·메모리·디스크 등)는 None/빈 배열로
    채우고, 다음 진짜 inventory가 도착하면 `upsert_server`의 `ON CONFLICT DO UPDATE`로
    자동 풀 정보 덮어씀 (composite_id UNIQUE 제약).

    공통 메타(boot_time·agent_started_at)는 metrics에도 포함되므로 placeholder도 채움 (option A).
    placeholder 상태에서 web UI는 정보 부족 표시(현재 ViewModel의 None 처리로 자연 노출).
    """
    return ServerInventoryCreate(
        # ─── 공통 메타데이터 ─────────────────────────────────────────────────
        composite_id=data.composite_id,
        machine_id=data.machine_id,
        hostname=data.hostname,
        agent_version=data.agent_version,
        collected_at=data.collected_at,
        boot_time=data.boot_time,
        agent_started_at=data.agent_started_at,
        # ─── inventory 본문 (placeholder는 모두 None/빈 배열) ────────────────
        os_family=None,
        os_id=None,
        os_version=None,
        os_codename=None,
        kernel_version=None,
        cpu_cores=None,
        cpu_model=None,
        mem_total_kb=None,
        swap_total_kb=None,
        ip_internal=[],
        ip_external=None,
        mac_addresses=[],
        disks=[],
        mounts=[],
        services=None,
        listen_ports=[],
    )


def to_inventory_create(data: InventoryInput) -> ServerInventoryCreate:
    return ServerInventoryCreate(
        # ─── 공통 메타데이터 ─────────────────────────────────────────────────
        composite_id=data.composite_id,
        machine_id=data.machine_id,
        hostname=data.hostname,
        agent_version=data.agent_version,
        collected_at=data.collected_at,
        boot_time=data.boot_time,
        agent_started_at=data.agent_started_at,
        # ─── inventory 본문 ──────────────────────────────────────────────────
        os_family=data.os_family,
        os_id=data.os_id,
        os_version=data.os_version,
        os_codename=data.os_codename,
        kernel_version=data.kernel_version,
        cpu_cores=data.cpu_cores,
        cpu_model=data.cpu_model,
        mem_total_kb=data.mem_total_kb,
        swap_total_kb=data.swap_total_kb,
        ip_internal=data.ip_internal,
        ip_external=data.ip_external,
        mac_addresses=data.mac_addresses,
        disks=[
            {"name": d.name, "size_bytes": d.size_bytes, "type": d.type, "major": d.major, "minor": d.minor}
            for d in data.disks
        ],
        mounts=[
            {"mount": m.mount, "fstype": m.fstype, "total_bytes": m.total_bytes, "major": m.major, "minor": m.minor}
            for m in data.mounts
        ],
        services=[{"unit": s.unit, "sub": s.sub} for s in data.services] if data.services is not None else None,
        listen_ports=[
            {"proto": p.proto, "addr": p.addr, "port": p.port, "uid": p.uid, "pid": p.pid, "comm": p.comm}
            for p in data.listen_ports
        ],
    )


def to_metric_create(data: MetricsInput) -> ServerMetricCreate:
    cpu = data.cpu_stat
    return ServerMetricCreate(
        # ─── 공통 메타데이터 ─────────────────────────────────────────────────
        # composite_id는 handler가 server_id 해석에 직접 사용. boot_time·agent_started_at은
        # 시계열 행에 저장 — counter reset 정밀 식별 (CLAUDE.md #C1).
        collected_at=data.collected_at,
        boot_time=data.boot_time,
        agent_started_at=data.agent_started_at,
        # ─── /proc/stat CPU jiffies ──────────────────────────────────────────
        cpu_user=cpu.user if cpu else None,
        cpu_nice=cpu.nice if cpu else None,
        cpu_system=cpu.system if cpu else None,
        cpu_idle=cpu.idle if cpu else None,
        cpu_iowait=cpu.iowait if cpu else None,
        cpu_irq=cpu.irq if cpu else None,
        cpu_softirq=cpu.softirq if cpu else None,
        cpu_steal=cpu.steal if cpu else None,
        # ─── 메모리·스왑 (kB) ────────────────────────────────────────────────
        # canonical 정규화 경계 — available/free 가 total 초과 시 클램프 (used 음수 방지, Windows 매핑 방어).
        mem_total_kb=data.mem_total_kb,
        mem_free_kb=data.mem_free_kb,
        mem_available_kb=clamp_ceiling(
            data.mem_available_kb, data.mem_total_kb, field="mem_available_kb", composite_id=data.composite_id
        ),
        mem_buffers_kb=data.mem_buffers_kb,
        mem_cached_kb=data.mem_cached_kb,
        swap_total_kb=data.swap_total_kb,
        swap_free_kb=clamp_ceiling(
            data.swap_free_kb, data.swap_total_kb, field="swap_free_kb", composite_id=data.composite_id
        ),
        # ─── load average ────────────────────────────────────────────────────
        load_1m=data.load_1m,
        load_5m=data.load_5m,
        load_15m=data.load_15m,
        # ─── 시계열 4개 테이블 nested 행 매핑 ────────────────────────────────
        # major/minor는 시계열 테이블에 컬럼 없어 미저장. 활용 시 ServerDiskIo/
        # ServerMountUsage 모델에 컬럼 추가 + 스키마 변경(down -v) 필요.
        disk_io=[
            DiskIoEntry(
                device=d.device,
                reads_completed=d.reads_completed,
                writes_completed=d.writes_completed,
                sectors_read=d.sectors_read,
                sectors_written=d.sectors_written,
            )
            for d in data.disk_io
        ],
        mounts=[
            MountUsageEntry(
                mount=m.mount,
                total_bytes=m.total_bytes,
                free_bytes=m.free_bytes,
                avail_bytes=m.avail_bytes,
            )
            for m in data.mounts
        ],
        net_io=[
            NetIoEntry(
                interface=n.interface,
                rx_bytes=n.rx_bytes,
                tx_bytes=n.tx_bytes,
                rx_packets=n.rx_packets,
                tx_packets=n.tx_packets,
                rx_errors=n.rx_errors,
                tx_errors=n.tx_errors,
            )
            for n in data.net_io
        ],
    )
