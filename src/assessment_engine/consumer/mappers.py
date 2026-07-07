from assessment_engine.consumer.metric_normalize import clamp_ceiling
from assessment_engine.consumer.schemas import InventoryInput, MetricsInput, SaturationInfo
from assessment_engine.db.dtos.inbound import (
    CpuCoreEntry,
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
    ServerInventoryCreate,
    ServerMetricCreate,
)

# 분류 단일 진실(service_classifier)을 ingest 가 호출 — 순수 함수라 레이어 결합 없음(인프라 DI 아님, F4).
from assessment_engine.service_classifier import compute_service_categories


def _max_disk_queue(sat: SaturationInfo | None) -> float | None:
    """per-disk 큐 배열 -> 가장 큰 디스크당 큐 (saturation 판정용, 합 아님). 빈/전부 None 이면 None.

    agent 가 디스크별 [{device, queue}] 발행 -> 엔진은 "가장 바쁜 디스크" 큐만 저장(server_metrics.sat_disk_queue).
    합·정규화 우회 제거 — 디스크당 임계로 바로 판정 (recommendation.disk_io_saturated).
    """
    if sat is None or not sat.disk_queue:
        return None
    queues = [e.queue for e in sat.disk_queue if e.queue is not None]
    return max(queues) if queues else None


def _await_fields(sat: SaturationInfo | None) -> dict[str, int | None]:
    """per-disk IOCTL await 원자료 -> device 합산 sat_disk_{read,write}_{time,count} dict.

    Windows disk_io 가 시스템 전역 단일 엔트리라 disk await 도 device 합산으로 시스템 전역 응답 지연 산출
    (엔진 counter_agg delta(time)/delta(count)). 값 없는 축(구세대 viostor IOCTL 미부착 -> 빈 배열)은 None 유지
    (0 날조 금지 — counter_agg 가 NULL 을 미관측으로 흡수). 축별로 하나라도 실측 있으면 그 축 합산.
    """
    entries = sat.disk_queue if (sat and sat.disk_queue) else []

    def _sum(attr: str) -> int | None:
        vals = [getattr(e, attr) for e in entries if getattr(e, attr) is not None]
        return sum(vals) if vals else None

    return {
        "sat_disk_read_time": _sum("read_time"),
        "sat_disk_write_time": _sum("write_time"),
        "sat_disk_read_count": _sum("read_count"),
        "sat_disk_write_count": _sum("write_count"),
    }


def build_placeholder_inventory(data: MetricsInput) -> ServerInventoryCreate:
    """metrics 메시지로부터 최소 정보의 placeholder inventory 생성.

    agent_id 미등록 상태에서 metrics 가 도착하면 drop 하지 않기 위한 임시 등록. 정적 정보는
    None/빈 배열로 두고, 다음 진짜 inventory 도착 시 `upsert_server` ON CONFLICT DO UPDATE 로 덮어씀.
    """
    return ServerInventoryCreate(
        agent_id=str(data.agent_id),
        composite_id=data.composite_id,
        machine_id=data.machine_id,
        hostname=data.hostname,
        agent_version=data.agent_version,
        collected_at=data.collected_at,
        boot_time=data.boot_time,
        agent_started_at=data.agent_started_at,
        os_family=None,
        os_id=None,
        os_version=None,
        os_codename=None,
        kernel_version=None,
        cpu_cores=None,
        cpu_model=None,
        mem_total_kb=None,
        swap_total_kb=None,
        interfaces=[],
        ip_external=None,
        mac_addresses=[],
        disks=[],
        mounts=[],
        services=None,
        listen_ports=[],
        service_categories=[],  # metrics placeholder — services/listen_ports 부재. 다음 진짜 inventory 가 채움.
    )


def to_inventory_create(data: InventoryInput) -> ServerInventoryCreate:
    return ServerInventoryCreate(
        agent_id=str(data.agent_id),
        composite_id=data.composite_id,
        machine_id=data.machine_id,
        hostname=data.hostname,
        agent_version=data.agent_version,
        collected_at=data.collected_at,
        boot_time=data.boot_time,
        agent_started_at=data.agent_started_at,
        os_family=data.os_family,
        os_id=data.os_id,
        os_version=data.os_version,
        os_codename=data.os_codename,
        kernel_version=data.kernel_version,
        cpu_cores=data.cpu_cores,
        cpu_model=data.cpu_model,
        mem_total_kb=data.mem_total_kb,
        swap_total_kb=data.swap_total_kb,
        interfaces=[
            {
                "name": i.name,
                "address": i.address,
                "prefix": i.prefix,
                "family": i.family,
                "kind": i.kind,
                "gateway": i.gateway,
            }
            for i in data.interfaces
        ],
        ip_external=data.ip_external,
        mac_addresses=data.mac_addresses,
        disks=[
            {
                "name": d.name,
                "size_bytes": d.size_bytes,
                "type": d.type,
                "major": d.major,
                "minor": d.minor,
                "kind": d.kind,
            }
            for d in data.disks
        ],
        mounts=[
            {
                "mount": m.mount,
                "fstype": m.fstype,
                "total_bytes": m.total_bytes,
                "major": m.major,
                "minor": m.minor,
                "kind": m.kind,
            }
            for m in data.mounts
        ],
        services=[{"unit": s.unit, "sub": s.sub, "pid": s.pid, "exe": s.exe} for s in data.services]
        if data.services is not None
        else None,
        listen_ports=[
            {"proto": p.proto, "addr": p.addr, "port": p.port, "uid": p.uid, "pid": p.pid, "comm": p.comm}
            for p in data.listen_ports
        ],
        # 서비스 카테고리 ingest 사전계산 — services(unit 이름) ∪ listen_ports(comm·port) 단일 산식.
        service_categories=compute_service_categories(
            [{"unit": s.unit, "sub": s.sub, "pid": s.pid} for s in data.services]
            if data.services is not None
            else None,
            [{"proto": p.proto, "port": p.port, "comm": p.comm} for p in data.listen_ports],
        ),
    )


def to_metric_create(data: MetricsInput) -> ServerMetricCreate:
    cpu = data.cpu_stat
    return ServerMetricCreate(
        # boot_time·agent_started_at은 시계열 행에 저장 — counter reset 정밀 식별 (#C1).
        collected_at=data.collected_at,
        boot_time=data.boot_time,
        agent_started_at=data.agent_started_at,
        cpu_user=cpu.user if cpu else None,
        cpu_nice=cpu.nice if cpu else None,
        cpu_system=cpu.system if cpu else None,
        cpu_idle=cpu.idle if cpu else None,
        cpu_iowait=cpu.iowait if cpu else None,
        cpu_irq=cpu.irq if cpu else None,
        cpu_softirq=cpu.softirq if cpu else None,
        cpu_steal=cpu.steal if cpu else None,
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
        load_1m=data.load_1m,
        load_5m=data.load_5m,
        load_15m=data.load_15m,
        sat_disk_queue=_max_disk_queue(data.saturation),
        sat_cpu_run_queue=data.saturation.cpu_run_queue if data.saturation else None,
        sat_mem_paging_rate=data.saturation.mem_paging_rate if data.saturation else None,
        **_await_fields(data.saturation),
        # disk_io·metrics mount 의 major/minor 는 시계열에 미저장 (물리 판정·data-volume 판정은 kind).
        # mount-disk 조인용 major/minor 는 inventory mount(정적)만 보유.
        disk_io=[
            DiskIoEntry(
                device=d.device,
                reads_completed=d.reads_completed,
                writes_completed=d.writes_completed,
                sectors_read=d.sectors_read,
                sectors_written=d.sectors_written,
                kind=d.kind,
                time_reading_ms=d.time_reading_ms,
                time_writing_ms=d.time_writing_ms,
                io_ticks_ms=d.io_ticks_ms,
                weighted_io_ms=d.weighted_io_ms,
            )
            for d in data.disk_io
        ],
        mounts=[
            MountUsageEntry(
                mount=m.mount,
                total_bytes=m.total_bytes,
                free_bytes=m.free_bytes,
                avail_bytes=m.avail_bytes,
                kind=m.kind,
                inodes_total=m.inodes_total,
                inodes_free=m.inodes_free,
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
                kind=n.kind,
                rx_drops=n.rx_drops,
                tx_drops=n.tx_drops,
            )
            for n in data.net_io
        ],
        # per-core — 배열 위치 = core_id (/proc/stat cpu0..N 순서). server_cpu_core 행 매핑.
        cpu_per_core=[
            CpuCoreEntry(
                core_id=idx,
                cpu_user=c.user,
                cpu_nice=c.nice,
                cpu_system=c.system,
                cpu_idle=c.idle,
                cpu_iowait=c.iowait,
                cpu_irq=c.irq,
                cpu_softirq=c.softirq,
                cpu_steal=c.steal,
            )
            for idx, c in enumerate(data.cpu_per_core)
        ],
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
    )
