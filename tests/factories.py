"""테스트 데이터 빌더 (wire). factory_boy 같은 무거운 라이브러리 대신 단순 함수.

본 프로젝트 규모(단일 도메인·dataclass DTO)에는 함수 빌더가 정석:
- 명시적인 default
- 파라미터로 일부 필드만 override
- factory_boy의 SubFactory/LazyAttribute 같은 추상화는 과도

단위 canonical (v2): 시간 s(float), 크기 By(int). device 축 = 안정 id 문자열(이름 아님).
시계열 device_id/iface_id = inventory (id_type):(id) 재구성값 — 기본값은 물리 필터·조인에 정합.
"""

from datetime import UTC, datetime
from uuid import NAMESPACE_DNS, uuid5

from assessment_engine.db.dtos.inbound import (
    CpuCoreEntry,
    DiskIoEntry,
    FilesystemEntry,
    NetIoEntry,
    ServerInventoryCreate,
    ServerMetricCreate,
    TaskResultUpdate,
)
from assessment_engine.db.dtos.outbound import TaskRow
from assessment_engine.service_classifier import compute_service_categories

_DEFAULT_BOOT_TIME = datetime(2026, 1, 1, tzinfo=UTC)
_DEFAULT_AGENT_STARTED_AT = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)

# 기본 device 안정 id — inventory block_device / net_interface 와 시계열 device_id/iface_id 조인 정합.
# 시계열 device_id = (id_type):(id) 규약 (물리 필터·per-device 집계 조인).
_DISK_ID = "virtio-pci-0000:00:05.0"  # block_device id (raw, id_type=by-path)
_DISK_DEVICE_ID = f"by-path:{_DISK_ID}"  # server_disk_io device_id
_PART_ID = "aaaa-0001"  # 루트 파티션 id (id_type=partuuid)
_MAC = "52:54:00:12:34:56"  # net_interface id (raw MAC, id_type=mac)
_IFACE_ID = f"mac:{_MAC}"  # server_net_io iface_id


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
    os_family: str = "linux",
    collected_at: datetime | None = None,
    cpu_cores: int | None = 4,
    mem_total_bytes: int | None = 8 * 1024**3,  # v2 By (v1 kB 폐기)
    boot_time: datetime | None = _DEFAULT_BOOT_TIME,
    agent_started_at: datetime | None = _DEFAULT_AGENT_STARTED_AT,
    block_devices: list[dict] | None = None,
    net_interfaces: list[dict] | None = None,
    lvm_vgs: list[dict] | None = None,
    services: list[dict] | None = None,
    listen_ports: list[dict] | None = None,
    arch: str | None = None,
    bits: int | None = None,
    boot_firmware: str | None = None,
    secure_boot: bool | None = None,
    edition: str | None = None,
    product_name: str | None = None,
    timezone: str | None = None,
    rtc_utc: bool | None = None,
    boot: dict | None = None,
    nonblock_mounts: list[dict] | None = None,
) -> ServerInventoryCreate:
    """기본값은 placeholder가 아닌 '정상' inventory — 미지정 시 실제와 유사한 v2 값.

    기본 block_devices = 물리 디스크(type=disk) + 루트 파티션(type=part, mountpoint=/). 물리 필터·
    per-mount 조인에 정합. swap 은 v1 컬럼이 아니라 block_devices type=swap 노드로 표현.
    """
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
        boot_time=boot_time,
        agent_started_at=agent_started_at,
        os_family=os_family,
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15.0",
        cpu_cores=cpu_cores,
        cpu_model="test-cpu",
        mem_total_bytes=mem_total_bytes,
        block_devices=block_devices
        if block_devices is not None
        else [
            {"id": _DISK_ID, "id_type": "by-path", "name": "vda", "type": "disk",
             "size_bytes": 100 * 10**9, "parent": None},
            {"id": _PART_ID, "id_type": "partuuid", "name": "vda1", "type": "part",
             "parent": _DISK_ID, "size_bytes": 50 * 10**9, "fstype": "ext4", "mountpoint": "/"},
        ],
        net_interfaces=net_interfaces
        if net_interfaces is not None
        else [
            {"id": _MAC, "id_type": "mac", "name": "eth0", "kind": "physical",
             "speed_mbps": 1000, "gateway": "10.0.0.254",
             "addresses": [{"address": "10.0.0.1", "prefix": 24, "family": "ipv4"}]},
        ],
        lvm_vgs=lvm_vgs if lvm_vgs is not None else [],
        ip_external=None,
        services=services,
        listen_ports=listen_ports if listen_ports is not None else [],
        service_categories=compute_service_categories(services, listen_ports),
        arch=arch,
        bits=bits,
        boot_firmware=boot_firmware,
        secure_boot=secure_boot,
        edition=edition,
        product_name=product_name,
        timezone=timezone,
        rtc_utc=rtc_utc,
        boot=boot,
        nonblock_mounts=nonblock_mounts,
    )


def make_metrics(
    *,
    collected_at: datetime,
    boot_time: datetime | None = _DEFAULT_BOOT_TIME,
    agent_started_at: datetime | None = _DEFAULT_AGENT_STARTED_AT,
    # CPU 호스트 집계 (s counter)
    cpu_user_s: float | None = 1000.0,
    cpu_nice_s: float | None = 0.0,
    cpu_system_s: float | None = 200.0,
    cpu_idle_s: float | None = 8000.0,
    cpu_iowait_s: float | None = 50.0,
    cpu_irq_s: float | None = 0.0,
    cpu_softirq_s: float | None = 0.0,
    cpu_steal_s: float | None = 0.0,
    cpu_logical_count: int | None = 4,
    cpu_run_queue: float | None = None,
    cpu_blocked: float | None = None,
    cpu_mce: int | None = None,
    # 메모리 (By)
    mem_free_bytes: int | None = 4 * 1024**3,
    mem_cached_bytes: int | None = 1 * 1024**3,
    mem_buffered_bytes: int | None = 200 * 1024**2,
    mem_available_bytes: int | None = 5 * 1024**3,
    mem_used_bytes: int | None = 3 * 1024**3,
    mem_limit_bytes: int | None = 8 * 1024**3,
    mem_commit_usage_bytes: int | None = None,
    mem_commit_limit_bytes: int | None = None,
    mem_hardware_corrupted_bytes: int | None = None,
    mem_oom_kill: int | None = None,
    # paging (counter)
    paging_in: int | None = None,
    paging_out: int | None = None,
    paging_major: int | None = None,
    # 네트워크 host-wide
    net_tcp_retransmits: int | None = None,
    net_conntrack_usage: int | None = None,
    net_conntrack_limit: int | None = None,
    # 시계열 nested 행 (미지정 시 물리 device 1개씩)
    disk_io: list[DiskIoEntry] | None = None,
    net_io: list[NetIoEntry] | None = None,
    filesystems: list[FilesystemEntry] | None = None,
    cpu_per_core: list[CpuCoreEntry] | None = None,
) -> ServerMetricCreate:
    """raw 누적값 (v2). 시간 흐름 시뮬은 호출자가 collected_at + 누적 카운터 증가로."""
    return ServerMetricCreate(
        collected_at=collected_at,
        boot_time=boot_time,
        agent_started_at=agent_started_at,
        cpu_user_s=cpu_user_s,
        cpu_nice_s=cpu_nice_s,
        cpu_system_s=cpu_system_s,
        cpu_idle_s=cpu_idle_s,
        cpu_iowait_s=cpu_iowait_s,
        cpu_irq_s=cpu_irq_s,
        cpu_softirq_s=cpu_softirq_s,
        cpu_steal_s=cpu_steal_s,
        cpu_logical_count=cpu_logical_count,
        cpu_run_queue=cpu_run_queue,
        cpu_blocked=cpu_blocked,
        cpu_mce=cpu_mce,
        mem_free_bytes=mem_free_bytes,
        mem_cached_bytes=mem_cached_bytes,
        mem_buffered_bytes=mem_buffered_bytes,
        mem_available_bytes=mem_available_bytes,
        mem_used_bytes=mem_used_bytes,
        mem_limit_bytes=mem_limit_bytes,
        mem_commit_usage_bytes=mem_commit_usage_bytes,
        mem_commit_limit_bytes=mem_commit_limit_bytes,
        mem_hardware_corrupted_bytes=mem_hardware_corrupted_bytes,
        mem_oom_kill=mem_oom_kill,
        paging_in=paging_in,
        paging_out=paging_out,
        paging_major=paging_major,
        net_tcp_retransmits=net_tcp_retransmits,
        net_conntrack_usage=net_conntrack_usage,
        net_conntrack_limit=net_conntrack_limit,
        disk_io=disk_io
        if disk_io is not None
        else [
            DiskIoEntry(
                device_id=_DISK_DEVICE_ID, device_name="vda",
                io_read_bytes=1_024_000, io_write_bytes=512_000,
                ops_read=100, ops_write=50,
                io_time_s=5.0, op_read_time_s=2.0, op_write_time_s=1.0, pending_ops=0.0,
            ),
        ],
        net_io=net_io
        if net_io is not None
        else [
            NetIoEntry(
                iface_id=_IFACE_ID, iface_name="eth0",
                rx_bytes=1_000_000, tx_bytes=500_000,
                rx_packets=1000, tx_packets=500,
                rx_errors=0, tx_errors=0, rx_dropped=0, tx_dropped=0,
                link_speed_bps=1_000_000_000,
            ),
        ],
        filesystems=filesystems
        if filesystems is not None
        else [
            FilesystemEntry(
                mountpoint="/", device_id=f"partuuid:{_PART_ID}", fstype="ext4",
                used_bytes=30 * 10**9, free_bytes=20 * 10**9,
                inodes_used=100_000, inodes_free=900_000,
            ),
        ],
        cpu_per_core=cpu_per_core if cpu_per_core is not None else [],
    )


# ─── Task 빌더 ──────────────────────────────────────────────────

_DEFAULT_TASK_PUBLIC_ID = "00000000-0000-4000-8000-000000000001"
_DEFAULT_TASK_COMPLETED_AT = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
_DEFAULT_TASK_AGENT_ID = "11111111-1111-4111-8111-111111111111"


def make_task_result_payload(
    *,
    agent_id: str | None = _DEFAULT_TASK_AGENT_ID,  # task.result 한정 nullable (매칭은 task_id)
    composite_id: str | None = "test-composite-id-0001",
    task_id: str = _DEFAULT_TASK_PUBLIC_ID,
    status: str = "success",
    failure_reason: str | None = None,
    exit_code: int | None = 0,
    task_policy: bool | None = None,
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
    schema_version: str = "1.0",
) -> dict:
    """task.result wire JSON 빌더 — TaskResultInput.model_validate_json 검증용.

    Default 는 success 경로. failure 시 status='failure' + failure_reason 지정.
    boot_time / agent_started_at default None — agent worker 가 항상 null 발행.
    """
    return {
        "schema_version": schema_version,
        "message_type": "task.result",
        "agent_id": agent_id,
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
        "task_policy": task_policy,
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
    task_policy: bool | None = None,
) -> TaskResultUpdate:
    return TaskResultUpdate(
        public_id=public_id,
        status=status,
        failure_reason=failure_reason,
        exit_code=exit_code,
        signal_no=signal_no,
        task_policy=task_policy,
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
