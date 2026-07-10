from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class ServerSummary:
    id: int
    public_id: str
    composite_id: str | None  # 감사·표시용 (식별은 agent_id, URL 은 public_id)
    hostname: str
    os_id: str | None
    os_version: str | None
    # 레거시 Windows Server 표시명 보강용 (build -> 버전, os_version 빈값 Server 세대). _os_display 단일 소비.
    kernel_version: str | None
    cpu_cores: int | None
    mem_total_bytes: int | None
    ip_external: list[str] | None
    block_devices: list[dict]  # 인벤토리 스토리지 트리 (size 합산용)
    # 서비스 뱃지 — ingest 사전계산 카테고리 키 집합(service_classifier 단일 진실). services JSONB 는 목록 미로드(경량).
    service_categories: list[str]
    last_seen_at: datetime | None  # Redis online TTL fallback 용도


@dataclass
class ServerDetail:
    id: int
    public_id: str
    agent_id: str  # 식별 단일 키 (UUID) — task.install 라우팅 대상
    composite_id: str | None  # 감사·표시용 (식별 미사용)
    machine_id: str | None  # raw machine-id 표시 전용
    hostname: str
    agent_version: str | None
    os_family: str | None  # "linux" | "windows" — task.install dispatch 단일 진실
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    cpu_cores: int | None
    cpu_model: str | None
    mem_total_bytes: int | None
    boot_time: datetime | None
    agent_started_at: datetime | None
    net_interfaces: list[dict]  # [{name,id,id_type,kind,speed_mbps,addresses:[...],gateway}]
    ip_external: list[str] | None
    block_devices: list[dict]  # 스토리지 트리 (type=swap 노드 포함 — swap 용량은 여기서)
    lvm_vgs: list[dict]  # [{name,size_bytes,free_bytes,..}] — 확장여력
    services: list[dict] | None
    listen_ports: list[dict]
    last_seen_at: datetime | None
    # ingest 사전계산 워크로드 카테고리(service_classifier 단일 진실, E7) — 토폴로지 노드 역할 주석 등 소비.
    service_categories: list[str] | None = None


@dataclass
class CollectionStatus:
    last_metric_at: datetime | None
    last_inventory_at: datetime | None


@dataclass
class TaskRow:
    """Task row raw — query repo 가 채워 service 가 받는 형식. 표시 파생(badge_class·duration_label)은 mapper."""

    public_id: str
    target_server_id: int
    target_public_id: str | None  # JOIN server_inventory.public_id (목록 응답 시각·운영자용)
    target_hostname: str | None  # JOIN server_inventory.hostname
    task_type: str
    status: str  # "pending" / "success" / "failure"
    created_at: datetime
    completed_at: datetime | None
    failure_reason: str | None
    exit_code: int | None
    signal_no: int | None  # 시그널 사망 시 시그널 번호 (exit_code 와 상호배타)
    duration_ms: int | None
    stdout_tail: str | None
    stderr_tail: str | None
    params: dict | None = None  # install task 발행 파라미터
    # 응답 마감 — mapper 가 경과 pending 을 "응답 시간 초과"로 파생 (install 외 None)
    deadline_at: datetime | None = None


# ---------- Dashboard raw DTOs (delta 계산용 2행 페어) ----------


@dataclass
class MetricPairRaw:
    collected_at: datetime
    # CPU 시간 (seconds counter, calculator 가 prev-cur delta 로 util%)
    cpu_user_s: float | None
    cpu_nice_s: float | None
    cpu_system_s: float | None
    cpu_idle_s: float | None
    cpu_iowait_s: float | None
    cpu_irq_s: float | None
    cpu_softirq_s: float | None
    cpu_steal_s: float | None
    # 메모리 (By)
    mem_limit_bytes: int | None
    mem_free_bytes: int | None
    mem_available_bytes: int | None
    mem_buffered_bytes: int | None
    mem_cached_bytes: int | None
    mem_used_bytes: int | None
    # 실행 큐 gauge — Linux procs_running / Windows Processor Queue. 스냅샷 os-aware 표시.
    cpu_run_queue: float | None = None
    cpu_logical_count: int | None = None  # 코어 수 — 실행 큐 코어당 정규화용 (calculator 가 run_queue/cores)
    # counter reset 정밀 식별 (calculator가 prev-cur 비교).
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


@dataclass
class DiskIoRaw:
    device_id: str  # 안정키 ("<scheme>:<value>")
    collected_at: datetime
    io_read_bytes: int | None = None
    io_write_bytes: int | None = None
    ops_read: int | None = None
    ops_write: int | None = None
    op_read_time_s: float | None = None  # await 산출
    op_write_time_s: float | None = None
    io_time_s: float | None = None  # %util 산출
    pending_ops: float | None = None  # 큐 gauge
    device_name: str | None = None  # 표시명
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


@dataclass
class NetIoRaw:
    iface_id: str  # 안정키 (mac:..)
    collected_at: datetime
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    rx_packets: int | None = None
    tx_packets: int | None = None
    rx_errors: int | None = None
    tx_errors: int | None = None
    rx_dropped: int | None = None
    tx_dropped: int | None = None
    link_speed_bps: int | None = None
    iface_name: str | None = None  # 표시명
    boot_time: datetime | None = None
    agent_started_at: datetime | None = None


@dataclass
class MountUsageRaw:
    mountpoint: str
    used_bytes: int | None = None
    free_bytes: int | None = None
    inodes_used: int | None = None
    inodes_free: int | None = None
    device_id: str | None = None  # block_devices 조인
    fstype: str | None = None  # 가상 fs 제외·ext inode 게이트
    collected_at: datetime | None = None


@dataclass
class DashboardRaw:
    metrics: list[MetricPairRaw]  # 최대 2행, collected_at desc
    disk_io: list[DiskIoRaw]  # 디바이스당 최대 2행, desc within device
    net_io: list[NetIoRaw]  # 인터페이스당 최대 2행, desc within interface
    filesystems: list[MountUsageRaw]  # 마운트당 최신 1행


# ---------- Storage / Network 풍부화 DTOs ----------


@dataclass
class StorageWithUsage:
    server_id: int
    public_id: str
    hostname: str
    block_devices: list[dict]  # 인벤토리 스토리지 트리 (type=swap 포함)
    lvm_vgs: list[dict]  # 확장여력
    filesystems: list[MountUsageRaw]  # 마운트별 최신 사용량 시계열
    inventory_at: datetime | None


@dataclass
class NetworkWithIo:
    server_id: int
    public_id: str
    hostname: str
    net_interfaces: list[dict]
    ip_external: list[str] | None
    net_io: list[NetIoRaw]  # 인터페이스당 최대 2행 (delta 계산용)
    inventory_at: datetime | None


@dataclass
class EnvironmentUtilizationRaw:
    """환경(또는 선택 N대) capacity-weighted 평균 활용률 (sum(used) / sum(total)).

    윈도우 안 전 서버·전 시점 통합 비율 — CPU는 시간 delta 합, MEM/DISK는 total 가중(가상 mount 제외).
    거대 VM이 큰 비중 = 물리 자원 관점(서버 동등 가중 아님). 산식 단일 진실 = repo environment_utilization.
    """

    cpu_avg_pct: float | None
    mem_avg_pct: float | None
    disk_avg_pct: float | None
    sample_size: int  # 기간 내 metric 발행 서버 distinct count — UI 표본 표시
    cpu_p95_pct: float | None = None
    mem_p95_pct: float | None = None


@dataclass
class DiskIoBaselineRaw:
    """report_disk_io_baseline — 서버별 디스크 I/O baseline + p95/peak. baseline=SUM(delta)/SUM(dt)."""

    iops_baseline: int | None
    throughput_kbps_baseline: float | None
    iops_p95: float | None
    iops_peak: float | None
    kbps_p95: float | None
    kbps_peak: float | None


@dataclass
class NetIoBaselineRaw:
    """report_net_io_baseline — 서버별 네트워크 I/O baseline + p95/peak."""

    rx_kbps_baseline: float | None
    tx_kbps_baseline: float | None
    rx_p95: float | None
    rx_peak: float | None
    tx_p95: float | None
    tx_peak: float | None


@dataclass
class SaturationRaw:
    """latest_saturation — 신선 표본 실시간 포화 원자료 (os-aware). 미존재 server 는 빈 인스턴스(전 필드 None) 사용."""

    run_queue: float | None = None  # CPU 실행 큐 (Linux procs_running / Windows Processor Queue)
    await_ms: float | None = None  # 디스크 응답 (op_time delta / ops delta)
    pending_ops: float | None = None  # 디스크 큐 깊이 (await 폴백)
    paging_major_rate: float | None = None  # 하드폴트 rate (Linux refault / Windows Pages Input)
    retrans_pct: float | None = None  # TCP 재전송율 %
    drop_pct: float | None = None  # 드롭율 %
    conntrack_ratio: float | None = None  # conntrack 사용/상한 %


@dataclass
class ReportMountUsageRaw:
    """마운트별 윈도우 평균 사용률 — 개별 보고서 스토리지 상세 (worst 1개 아닌 전체 마운트)."""

    mountpoint: str
    total_bytes: int | None
    used_pct: float | None


@dataclass
class MountCapacityRaw:
    """마운트별 용량 사이징 raw (per-mount) — /api/assessment 디스크 축 입력.

    runway/target 은 가용 이력 전체 span 산출(report_aggregate mount_calc 와 동일 산식, 임계 상수 단일 진실).
    target_bytes = 소진 임박 시 목표 총 용량(바이트). None=안 참(유지). assess_mount_capacity 가 사이징 판정.
    """

    mountpoint: str
    total_bytes: int | None
    used_pct: float | None
    byte_runway_days: float | None
    inode_runway_days: float | None
    inode_used_pct: float | None
    target_bytes: int | None


@dataclass
class MemoryBreakdownRaw:
    """메모리 구성 윈도우 평균 — used/available/cached/buffers (전체 메모리 대비 %, 시점값 avg)."""

    used_pct: float | None
    available_pct: float | None
    cached_pct: float | None
    buffers_pct: float | None


@dataclass
class CpuBreakdownRaw:
    """CPU 분류 윈도우 평균 — user/system/iowait (시간 delta 기반 %, reset 정책 metric_trend 동일)."""

    user_pct: float | None
    system_pct: float | None
    iowait_pct: float | None


# ---------- Series ----------


@dataclass
class MetricSeries:
    collected_at: datetime
    value: float | None
    dimension: str | None
    kind: str | None = None  # per-dimension 차트 필터용 (물리/가상 선별은 query 시 inventory 조인). 환경 합산선 None.


# ---------- Reboot / Agent restart 이벤트 (차트 vertical marker용) ----------


@dataclass
class ReportRowRaw:
    """Assessment 보고서 한 행의 raw stats — repository가 반환 (P1).

    표시 파생(role/is_online/recommendation/os_display 등)은 service mapper에서 ReportRowItem으로.
    USE Method (Utilization/Saturation/Errors) 기반 — v2 신호 (run_queue·paging·await·steal·conntrack).
    """

    server_id: int
    public_id: str
    hostname: str
    os_family: str | None  # "linux"|"windows" — os-aware 신호 분기
    os_id: str | None
    os_version: str | None
    os_codename: str | None
    kernel_version: str | None
    net_interfaces: list[dict] | None
    services: list[dict] | None  # service_classifier 입력 (role 추론용)
    last_seen_at: datetime | None

    # USE Method Utilization (p95 + avg + peak)
    cpu_p95_pct: float | None
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_avg_pct: float | None
    mem_peak_pct: float | None
    mem_near_peak_pct: float | None = None  # near-peak(버킷별 max 의 p95) — 메모리 사이징 통계(비탄력 피크 대표)

    # service_classifier listen 신호 (개별 보고서 구동 서비스 표시·role 보강).
    listen_ports: list[dict] | None = None

    # I/O wait (cpu iowait 시간 / total non-idle 비율) — Linux 디스크 병목 참고 신호
    iowait_p95_pct: float | None = None
    iowait_peak_pct: float | None = None
    # Windows CPU saturation — Processor Queue Length p95 (Linux run queue 등가 축, os-aware 소비)
    cpu_run_queue_p95: float | None = None
    # Windows Memory saturation — Pages Input/sec rate p95 (Linux paging_major 등가 축, os-aware 소비)
    mem_pages_input_rate_p95: float | None = None

    # Inventory 합계 산정용 — query_service.get_report가 totals 계산 시 사용
    cpu_cores: int | None = None
    mem_total_bytes: int | None = None
    block_devices: list[dict] | None = None  # 합계 산정 위해 size_bytes 합산
    lvm_vgs: list[dict] | None = None  # 확장여력 (용량 처방)
    boot_time: datetime | None = None  # uptime_days = now - boot_time

    # 서버 안 가장 채워진 마운트의 used%(most-full, fs cagg) — 디스크 이용률 KPI. 임박(runway)과 별개 축.
    worst_mount_used_pct: float | None = None

    # Uptime + period 내 재부팅 횟수 — 별도 SQL(`report_uptime_stats`)에서 채움
    reboot_count: int = 0
    # period 내 에이전트 재시작 횟수 — 별도 SQL(`report_agent_restart_stats`), anchor+window 정합 (#F10).
    agent_restart_count: int = 0

    # Disk I/O — baseline(평균) + p95 + peak (모든 device 시점별 합산 후 통계)
    disk_iops_baseline: int | None = None
    disk_iops_p95: float | None = None
    disk_iops_peak: float | None = None
    disk_throughput_kbps: float | None = None
    disk_throughput_kbps_p95: float | None = None
    disk_throughput_kbps_peak: float | None = None

    # Net I/O — baseline(평균) + p95 + peak (모든 interface 시점별 합산 후 통계)
    net_rx_kbps: float | None = None
    net_rx_kbps_p95: float | None = None
    net_rx_kbps_peak: float | None = None
    net_tx_kbps: float | None = None
    net_tx_kbps_p95: float | None = None
    net_tx_kbps_peak: float | None = None

    # 표본 충분성 — 실측 cpu/mem 샘플 / 윈도우 기대 샘플 비율. p95 신뢰도 단서, None = 측정 축 부재.
    cpu_sufficiency: float | None = None
    mem_sufficiency: float | None = None

    # ─── rollup_host 입력 raw — report_aggregate 산출, build_resource_stats 가 ResourceStats 배선 ───
    cpu_steal_p95_pct: float | None = None  # steal% p95 (가상화 경합 — 충실도 편향 + 인과 분리)
    cpu_burst_ratio: float | None = None  # cpu p95/median (버스티 -> 통계 정밀도 하향)
    procs_blocked_p95: float | None = None  # D-state 블록 p95 (IO발 CPU 로드 분리 근본원인)
    procs_running_p95: float | None = None  # R-state 실행 큐 p95 (Linux CPU 포화 — load 대체)
    mem_swap_paging: bool = False  # paging_major(refault) rate sustained (Linux 메모리 포화 dual-gate 입력)
    oom_occurred: bool = False  # 창 안 OOM kill 발생 (메모리 under 사후 증거)
    history_hours: float | None = None  # 관측 버킷(5분) 누적 시간 — 통계 정밀도 바닥(30h floor)
    disk_await_p95_ms: float | None = None  # worst await p95 (op_time delta / ops delta, s->ms)
    disk_capacity_runway_days: float | None = None  # 바이트 소진까지 남은 일수(가장 빨리 차는 마운트=구동 마운트)
    disk_capacity_driving_mount: str | None = None  # 구동 마운트 이름(runway·driving_used_pct 짝) — 임박 표시 단일 진실
    disk_inode_runway_days: float | None = None  # inode 소진까지 남은 일수
    disk_inode_used_pct: float | None = None  # worst mount inode 사용률 % — 정적 가드(바이트 85% 대칭)
    disk_capacity_target_gb: float | None = None  # 1년 수명 목표 총 용량(GB) — 소진 마운트 확장 목표
    disk_capacity_proj_30d_pct: float | None = None  # 30일 후 예상 used%(현재 rate 외삽) — 확장 근거 근시 신호
    disk_capacity_driving_used_pct: float | None = None  # 소진 임박/최고-used 마운트의 현재 used%

    net_drop_pct: float | None = None  # 드롭/패킷 % (로컬 saturation)
    net_retrans_pct: float | None = None  # TCP 재전송/tx패킷 % (Errors — OutSegs 미수집이라 tx_packets 분모 근사)
    conntrack_ratio: float | None = None  # conntrack 사용/상한 — 연결테이블 고갈 임박(모듈 미로드 시 None)
    cpu_trend_slope: float | None = None  # cpu 이용률 최소제곱 기울기 %/day (도메인이 상승 추세 판정)
    mem_trend_slope: float | None = None  # mem 이용률 최소제곱 기울기 %/day
    cpu_percore_p95_max: float | None = None  # 가장 바쁜 코어의 이용률 p95 (단일스레드 병목, server_cpu_core)


@dataclass
class RebootEvent:
    """server_inventory_history에서 boot_time / agent_started_at 변경 시점 추출.

    kind 분류:
    - "reboot": boot_time 변경 (시스템 재부팅) 또는 첫 등록 (이전 행 없음)
    - "restart": boot_time 동일 + agent_started_at 변경 (에이전트 단독 재시작)
    """

    collected_at: datetime
    boot_time: datetime | None
    agent_started_at: datetime | None
    kind: Literal["reboot", "restart"]


# ---------- 주의 신호 (목록 화면 상단 운영신호 — gap 전용) ----------


@dataclass
class MetricGapWarningRaw:
    """metric 발행 갭 — last_metric_at이 임계 초과로 끊김. raw (P1). 통신 끊김 운영신호."""

    public_id: str
    hostname: str
    last_metric_at: datetime


# --- 보고서 발행 job 조회 결과 ---


@dataclass
class DiagnosticJobRecord:
    """보고서 발행 job 단건 — 라우터 조회 응답·발행 이력 표현.

    id는 UUID 문자열 (PG_UUID as_uuid=False). result·error_message는 status 따라 둘 중 하나만 채움.
    job_type: 'customer_report' | 'engineer_report'
    """

    id: str
    job_type: str
    scope: str
    input_params: dict
    input_hash: str
    status: str
    progress_stage: str | None
    result: dict | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    requested_by: str | None
