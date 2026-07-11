"""메트릭 표시 ViewModel — dashboard snapshot + collection status + 시계열 항목."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SaturationSignal:
    """os-aware 포화 스냅샷 신호 — 서버 단일 판정(P2), 클라는 렌더만(P4).

    이 호스트 OS 에 해당하는 값·임계만 담고(양 OS 설명 인라인 없음), 판정(saturated)은 도메인 os-aware
    helper(cpu_saturation_index·disk_io_saturation_index·mem_pressure_active) 경유 — 임계 재계산 금지(E3).

    state = 미발화 4상태 어휘:
    - "measured": 값 있음(value/saturated 유효).
    - "no_data": 이 신호 미수집(첫 표본·수집 끊김) — 회색 "수집 대기".
    - "not_applicable": 이 OS/구성 미지원(예 Windows PSI·steal) — na_reason 사유.
    - "insufficient": 측정됐으나 신뢰 표본 부족(현재 스냅샷 축엔 미사용, 향후 확장 슬롯).
    """

    # 안정 식별자 — cpu_run_queue|cpu_steal|cpu_psi|mem_paging|mem_psi|disk_await|disk_psi
    # |net_retrans|net_drop|net_conntrack.
    key: str
    label: str  # 축 의미 라벨 ("실행 큐", "PSI", "응답 지연" ...)
    state: str  # "measured" | "no_data" | "not_applicable" | "insufficient"
    value: float | None = None  # 이 호스트 OS 의 값
    threshold: float | None = None  # 이 호스트 OS 의 포화 임계 (표시 기준선)
    unit: str | None = None  # "per_core" | "ms" | "%" | "/s"
    saturated: bool | None = None  # os-aware 판정 (measured 일 때만)
    detail: str | None = None  # hover: 이 OS metric·임계 근거 문장
    na_reason: str | None = None  # not_applicable 사유 ("Windows 미지원")


@dataclass
class ErrorSignal:
    """에러 축 표시자 (Errors) — 카운트형 신호, 정상=0 발화(E9). 서버 판정, 클라 렌더만.

    시계열 차트 아님(대부분 0이라 빈 차트 안티패턴) — 카운트 + 종류 + 시점 컨텍스트.
    state = "clean"(창내 0, 초록 이상 없음) · "occurred"(발생, 빨강 카운트) · "no_data"(표본 없음, 회색).
    """

    key: str  # cpu_mce | mem_oom | mem_corrupted | net_errors | disk_errors
    label: str  # 표시 라벨 ("머신체크(MCE)", "OOM Kill", "디스크 에러" ...)
    state: str  # "clean" | "occurred" | "no_data"
    count: int | None = None  # 창내 발생 건수 (occurred)
    context: str | None = None  # 종류·부가 (disk kinds, EDAC bytes) — 발생 시 무엇
    last_at: datetime | None = None  # 최근 관측 시각 (시점 컨텍스트)
    window_label: str | None = None  # 관측 창 ("최근 24h")
    detail: str | None = None  # hover: 신호 의미·출처


@dataclass
class CpuSnapshot:
    usage_pct: float | None
    user_pct: float | None
    system_pct: float | None
    iowait_pct: float | None
    steal_pct: float | None = None  # 가상화 경합 — 하이퍼바이저가 vCPU 시간 뺏김 (Linux 전용, Windows null)


@dataclass
class MemSnapshot:
    total_bytes: int | None
    used_bytes: int | None  # total - available
    available_bytes: int | None
    cached_bytes: int | None
    buffered_bytes: int | None
    usage_pct: float | None
    # stacked bar 표시용 비율 (P5: 클라이언트 재계산 금지, metrics_calculator 산출).
    # 메모리 구성 모델(_METRIC_EXPR): Used + Available = 100, Cached/Buffers 는 Available 안 회수 가능 세부.
    # bar 구획 = used(usage_pct) | cached_pct | buffers_pct | free_pct, 합 = 100.
    cached_pct: float | None = None
    buffers_pct: float | None = None
    free_pct: float | None = None  # Available 중 cached/buffers 제외 잔여 — bar 마지막 구획


@dataclass
class DiskIoSnapshot:
    device: str
    read_iops: float | None
    write_iops: float | None
    read_kbps: float | None
    write_kbps: float | None


@dataclass
class NetIoSnapshot:
    interface: str
    rx_kbps: float | None
    tx_kbps: float | None
    rx_pps: float | None
    tx_pps: float | None


@dataclass
class MountDashSnapshot:
    mount: str
    total_gb: float | None
    used_gb: float | None
    avail_gb: float | None
    usage_pct: float | None


@dataclass
class MetricDashboard:
    collected_at: datetime | None
    cpu: CpuSnapshot | None
    memory: MemSnapshot | None
    # 디스크 I/O 스냅샷 — device 단일 리스트. v2 시계열(server_disk_io)에 device type 축이 없어 물리/LVM/파티션
    # 분류가 불가(인벤토리 조인 없이). 표시는 전체 device flat (차트 물리필터도 현재 no-op 이라 정합).
    disk_io: list[DiskIoSnapshot]
    net_io: list[NetIoSnapshot]
    mounts: list[MountDashSnapshot]
    # 포화 스냅샷 신호 (os-aware 서버 판정, P2) — 자원별 SaturationSignal 리스트. 클라는 렌더만.
    # build_saturation_signals(mappers/metric) 단일 산출. 개요·자원 탭 스냅샷 카드 공통 소비.
    cpu_saturation: list[SaturationSignal] = field(default_factory=list)
    mem_saturation: list[SaturationSignal] = field(default_factory=list)
    disk_saturation: list[SaturationSignal] = field(default_factory=list)
    net_saturation: list[SaturationSignal] = field(default_factory=list)
    # 에러 축 표시자 (호스트 공통 fleet — MCE·OOM·EDAC·디스크·네트워크). 정상=0 발화(E9). build_error_signals 산출.
    errors: list[ErrorSignal] = field(default_factory=list)


@dataclass
class CollectionStatusItem:
    last_metric_at: datetime | None
    last_inventory_at: datetime | None
    is_online: bool


@dataclass
class MetricSeriesItem:
    collected_at: datetime
    value: float | None
    dimension: str | None
