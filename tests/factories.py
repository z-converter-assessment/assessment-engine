"""테스트 데이터 빌더. factory_boy 같은 무거운 라이브러리 대신 단순 함수.

본 프로젝트 규모(단일 도메인·dataclass DTO)에는 함수 빌더가 정석:
- 명시적인 default
- 파라미터로 일부 필드만 override
- factory_boy의 SubFactory/LazyAttribute 같은 추상화는 과도
"""

from datetime import UTC, datetime
from uuid import NAMESPACE_DNS, uuid5

from assessment_engine.db.dtos.inbound import (
    CpuCoreEntry,
    DiskIoEntry,
    MountUsageEntry,
    NetIoEntry,
    ServerInventoryCreate,
    ServerMetricCreate,
    TaskResultUpdate,
)
from assessment_engine.db.dtos.outbound import TaskRow
from assessment_engine.service_classifier import compute_service_categories

_DEFAULT_BOOT_TIME = datetime(2026, 1, 1, tzinfo=UTC)
_DEFAULT_AGENT_STARTED_AT = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)


def agent_id_for(label: str) -> str:
    """테스트 편의 — 라벨(구 composite_id)을 deterministic agent_id(UUID)로 파생.

    식별키가 agent_id(UUID) 라, 테스트가 composite_id 라벨로 서버를 구분하던 관습을 유지하면서
    서버마다 고유한 agent_id 를 얻는다. find_server_id 등 식별 API 는 본 헬퍼로 파생한 값을 쓴다.
    """
    return str(uuid5(NAMESPACE_DNS, label))


def make_inventory(
    *,
    agent_id: str | None = None,  # 미지정 시 composite_id 라벨 파생 (서버마다 고유 — 식별키는 agent_id)
    composite_id: str | None = "test-composite-id-0001",
    # 미지정 시 composite_id 파생 (서버마다 고유, clone collision 진단용 감사 컬럼). 명시 None 은 보존.
    machine_id: str | None = "__DERIVE__",
    hostname: str = "test-host-01",
    agent_version: str = "1.0.0",
    collected_at: datetime | None = None,
    cpu_cores: int | None = 4,
    mem_total_kb: int | None = 8 * 1024 * 1024,
    boot_time: datetime | None = _DEFAULT_BOOT_TIME,
    agent_started_at: datetime | None = _DEFAULT_AGENT_STARTED_AT,
    disks: list[dict] | None = None,
    mounts: list[dict] | None = None,
    services: list[dict] | None = None,
    listen_ports: list[dict] | None = None,
) -> ServerInventoryCreate:
    """기본값은 placeholder가 아닌 '정상' inventory — 미지정 시 실제와 유사한 값."""
    if agent_id is None:
        agent_id = agent_id_for(composite_id or "none")
    if machine_id == "__DERIVE__":
        machine_id = f"mid-{composite_id}"
    return ServerInventoryCreate(
        agent_id=agent_id,
        composite_id=composite_id,
        machine_id=machine_id,
        hostname=hostname,
        agent_version=agent_version,
        collected_at=collected_at or datetime.now(UTC),
        os_family="linux",
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15.0",
        cpu_cores=cpu_cores,
        cpu_model="test-cpu",
        mem_total_kb=mem_total_kb,
        swap_total_kb=2 * 1024 * 1024,
        boot_time=boot_time,
        agent_started_at=agent_started_at,
        interfaces=[
            {
                "name": "eth0",
                "address": "10.0.0.1",
                "prefix": 24,
                "family": "ipv4",
                "kind": "physical",
                "gateway": "10.0.0.254",
            }
        ],
        ip_external=None,
        mac_addresses=[],
        service_categories=compute_service_categories(services, listen_ports),
        disks=disks
        if disks is not None
        else [
            {"name": "sda", "size_bytes": 100 * 10**9, "type": "disk", "major": 8, "minor": 0},
        ],
        mounts=mounts
        if mounts is not None
        else [
            {"mount": "/", "fstype": "ext4", "total_bytes": 50 * 10**9, "major": 8, "minor": 1},
        ],
        services=services,
        listen_ports=listen_ports if listen_ports is not None else [],
    )


def make_metrics(
    *,
    collected_at: datetime,
    boot_time: datetime | None = _DEFAULT_BOOT_TIME,
    agent_started_at: datetime | None = _DEFAULT_AGENT_STARTED_AT,
    cpu_user: int = 1000,
    cpu_nice: int = 0,
    cpu_system: int = 200,
    cpu_idle: int = 8000,
    cpu_iowait: int = 50,
    cpu_irq: int = 0,
    cpu_softirq: int = 0,
    cpu_steal: int = 0,
    mem_total_kb: int = 8 * 1024 * 1024,
    mem_free_kb: int = 4 * 1024 * 1024,
    mem_available_kb: int = 5 * 1024 * 1024,
    mem_buffers_kb: int = 200 * 1024,
    mem_cached_kb: int = 1 * 1024 * 1024,
    swap_total_kb: int = 2 * 1024 * 1024,
    swap_free_kb: int = 2 * 1024 * 1024,
    load_1m: float = 0.5,
    load_5m: float = 0.4,
    load_15m: float = 0.3,
    sat_disk_queue: float | None = None,
    sat_cpu_run_queue: float | None = None,
    sat_mem_paging_rate: float | None = None,
    sat_disk_read_time: int | None = None,
    sat_disk_write_time: int | None = None,
    sat_disk_read_count: int | None = None,
    sat_disk_write_count: int | None = None,
    sat_disk_idle_time: int | None = None,
    sat_disk_query_time: int | None = None,
    psi_cpu_some_total: int | None = None,
    psi_mem_some_total: int | None = None,
    psi_io_some_total: int | None = None,
    collection_interval_sec: int | None = None,
    # ADR 0052 host-wide 신 신호 (raw 카운터/gauge, default None — 옛 테스트 무손상)
    procs_running: int | None = None,
    procs_blocked: int | None = None,
    schedstat_run_wait_ns: int | None = None,
    pswpin: int | None = None,
    pswpout: int | None = None,
    oom_kill: int | None = None,
    mem_pages_input: int | None = None,
    tcp_retrans_segs: int | None = None,
    tcp_tw: int | None = None,
    conntrack_count: int | None = None,
    conntrack_max: int | None = None,
    disk_io: list[DiskIoEntry] | None = None,
    mounts: list[MountUsageEntry] | None = None,
    net_io: list[NetIoEntry] | None = None,
    cpu_per_core: list[CpuCoreEntry] | None = None,
) -> ServerMetricCreate:
    """raw 누적값. 시간 흐름 시뮬은 호출자가 collected_at + 누적 카운터 증가로."""
    return ServerMetricCreate(
        collected_at=collected_at,
        boot_time=boot_time,
        agent_started_at=agent_started_at,
        cpu_user=cpu_user,
        cpu_nice=cpu_nice,
        cpu_system=cpu_system,
        cpu_idle=cpu_idle,
        cpu_iowait=cpu_iowait,
        cpu_irq=cpu_irq,
        cpu_softirq=cpu_softirq,
        cpu_steal=cpu_steal,
        mem_total_kb=mem_total_kb,
        mem_free_kb=mem_free_kb,
        mem_available_kb=mem_available_kb,
        mem_buffers_kb=mem_buffers_kb,
        mem_cached_kb=mem_cached_kb,
        swap_total_kb=swap_total_kb,
        swap_free_kb=swap_free_kb,
        load_1m=load_1m,
        load_5m=load_5m,
        load_15m=load_15m,
        sat_disk_queue=sat_disk_queue,
        sat_cpu_run_queue=sat_cpu_run_queue,
        sat_mem_paging_rate=sat_mem_paging_rate,
        sat_disk_read_time=sat_disk_read_time,
        sat_disk_write_time=sat_disk_write_time,
        sat_disk_read_count=sat_disk_read_count,
        sat_disk_write_count=sat_disk_write_count,
        sat_disk_idle_time=sat_disk_idle_time,
        sat_disk_query_time=sat_disk_query_time,
        psi_cpu_some_total=psi_cpu_some_total,
        psi_mem_some_total=psi_mem_some_total,
        psi_io_some_total=psi_io_some_total,
        collection_interval_sec=collection_interval_sec,
        procs_running=procs_running,
        procs_blocked=procs_blocked,
        schedstat_run_wait_ns=schedstat_run_wait_ns,
        pswpin=pswpin,
        pswpout=pswpout,
        oom_kill=oom_kill,
        mem_pages_input=mem_pages_input,
        tcp_retrans_segs=tcp_retrans_segs,
        tcp_tw=tcp_tw,
        conntrack_count=conntrack_count,
        conntrack_max=conntrack_max,
        disk_io=disk_io
        if disk_io is not None
        else [
            # kind — 물리/data 집계 필터(cagg physical·mount data) 통과용 기본값 (agent 공용 분류기).
            DiskIoEntry(
                device="sda",
                reads_completed=100,
                writes_completed=50,
                sectors_read=2000,
                sectors_written=1000,
                kind="physical",
            ),
        ],
        mounts=mounts
        if mounts is not None
        else [
            MountUsageEntry(
                mount="/", total_bytes=50 * 10**9, free_bytes=20 * 10**9, avail_bytes=18 * 10**9, kind="data"
            ),
        ],
        net_io=net_io
        if net_io is not None
        else [
            NetIoEntry(
                interface="eth0",
                rx_bytes=1_000_000,
                tx_bytes=500_000,
                rx_packets=1000,
                tx_packets=500,
                rx_errors=0,
                tx_errors=0,
                kind="physical",
            ),
        ],
        cpu_per_core=cpu_per_core if cpu_per_core is not None else [],
    )


# ─── Task 빌더 (ADR 0007) ──────────────────────────────────────────────────

_DEFAULT_TASK_PUBLIC_ID = "00000000-0000-4000-8000-000000000001"
_DEFAULT_TASK_COMPLETED_AT = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)


def make_task_result_payload(
    *,
    composite_id: str = "test-composite-id-0001",
    task_id: str = _DEFAULT_TASK_PUBLIC_ID,
    status: str = "success",
    failure_reason: str | None = None,
    exit_code: int | None = 0,
    duration_ms: int = 30,
    stdout_tail: str = "ok",
    stderr_tail: str = "",
    completed_at: datetime = _DEFAULT_TASK_COMPLETED_AT,
    signal_no: int | None = None,
    boot_time: datetime | None = None,
    agent_started_at: datetime | None = None,
    os_family: str = "linux",
    os_version: str | None = None,
    message_id: str = "550e8400-e29b-41d4-a716-446655440099",
) -> dict:
    """task.result wire JSON 빌더 — TaskResultInput.model_validate_json 검증용.

    Default 는 success 경로. failure 시 status='failure' + failure_reason 지정.
    boot_time / agent_started_at default None — agent worker 가 항상 null 발행 (ADR 0007).
    """
    return {
        "message_type": "task.result",
        "composite_id": composite_id,
        "agent_version": "1.0.0",
        "collected_at": completed_at.isoformat().replace("+00:00", "Z"),
        "hostname": "test-host-01",
        "message_id": message_id,
        "boot_time": boot_time.isoformat().replace("+00:00", "Z") if boot_time else None,
        "agent_started_at": agent_started_at.isoformat().replace("+00:00", "Z") if agent_started_at else None,
        "task_id": task_id,
        "status": status,
        "failure_reason": failure_reason,
        "exit_code": exit_code,
        "signal_no": signal_no,
        "os_family": os_family,
        "os_version": os_version,
        "duration_ms": duration_ms,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
    }


def make_task_result_update(
    *,
    public_id: str = _DEFAULT_TASK_PUBLIC_ID,
    status: str = "success",
    failure_reason: str | None = None,
    exit_code: int | None = 0,
    duration_ms: int = 30,
    stdout_tail: str = "ok",
    stderr_tail: str = "",
    completed_at: datetime = _DEFAULT_TASK_COMPLETED_AT,
    signal_no: int | None = None,
    install_verified: bool | None = None,
) -> TaskResultUpdate:
    return TaskResultUpdate(
        public_id=public_id,
        status=status,
        failure_reason=failure_reason,
        exit_code=exit_code,
        install_verified=install_verified,
        signal_no=signal_no,
        duration_ms=duration_ms,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        completed_at=completed_at,
    )


def make_task_row(
    *,
    public_id: str = _DEFAULT_TASK_PUBLIC_ID,
    target_server_id: int = 1,
    target_public_id: str | None = "11111111-1111-4111-8111-111111111111",
    target_hostname: str | None = "test-host-01",
    task_type: str = "zconverter_install",
    status: str = "success",
    created_at: datetime = _DEFAULT_TASK_COMPLETED_AT,
    completed_at: datetime | None = _DEFAULT_TASK_COMPLETED_AT,
    failure_reason: str | None = None,
    exit_code: int | None = 0,
    duration_ms: int | None = 30,
    stdout_tail: str | None = "ok",
    stderr_tail: str | None = "",
    signal_no: int | None = None,
) -> TaskRow:
    return TaskRow(
        public_id=public_id,
        target_server_id=target_server_id,
        target_public_id=target_public_id,
        target_hostname=target_hostname,
        task_type=task_type,
        status=status,
        created_at=created_at,
        completed_at=completed_at,
        failure_reason=failure_reason,
        exit_code=exit_code,
        signal_no=signal_no,
        duration_ms=duration_ms,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )
