"""메트릭 표시 ViewModel — dashboard snapshot + collection status + 시계열 항목."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PeriodSignalRow:
    """평가 카드 신호 1행 — value·threshold 는 형식화 문자열, over=임계 이상, measured=False 면 muted."""

    label: str
    value: str
    threshold: str
    over: bool
    measured: bool


@dataclass
class PeriodErrorRow:
    """평가 카드 에러축(E) 1행 — 카운트형 표시자."""

    key: str
    label: str
    badge_text: str
    badge_class: str
    note: str

    sizing_signal: str


@dataclass
class PeriodExtraGroup:
    """자원별 상세 탭 "추가 지표" 하위 그룹 — 성격별 묶음(예: CPU "부하"/"신뢰도")."""

    label: str
    rows: list[PeriodSignalRow]


@dataclass
class PeriodResource:
    """평가 카드 자원 1개 — 이용률(U)·포화(S) 두 컬럼 행 + 컬럼별 임계 이상 개수."""

    name: str
    util_rows: list[PeriodSignalRow]
    util_over: int
    sat_rows: list[PeriodSignalRow]
    sat_over: int
    has_util: bool
    detail_slug: str

    verdict_label: str
    verdict_color: str

    extra_groups: list[PeriodExtraGroup] = field(default_factory=list[PeriodExtraGroup])

    error_rows: list[PeriodErrorRow] = field(default_factory=list[PeriodErrorRow])

    verdict_label2: str = ""
    verdict_color2: str = ""


@dataclass
class PeriodAssessment:
    """서버 세부 '최근 N일' 카드 — 자원별 U/S 2축 + 에러축(E) right-sizing 분류 근거.

    실시간 카드(순간 스냅샷)와 분리 — 이쪽은 분류·판정 근거이고 창은 `right_sizing.WINDOW_DAYS`.
    """

    resources: list[PeriodResource]
    error_rows: list[PeriodErrorRow]
    window_days: int

    # 둘을 함께 노출해야 목록-세부가 맞는다.
    classification_label: str
    classification_color: str


@dataclass
class SaturationSignal:
    """os-aware 포화 스냅샷 신호 — 이 호스트 OS 에 해당하는 값·임계만 담는다(양 OS 설명 인라인 없음).

    판정(saturated)은 도메인 os-aware helper 경유 — 여기서 임계를 다시 계산하지 않는다.
    state = "measured" | "no_data"(미수집) | "not_applicable"(이 OS/구성 미지원) | "insufficient"(표본 부족,
    현재 스냅샷 축엔 미사용).
    """

    key: str
    label: str
    state: str
    value: float | None = None
    threshold: float | None = None
    unit: str | None = None
    saturated: bool | None = None
    detail: str | None = None
    na_reason: str | None = None


@dataclass
class ErrorSignal:
    """에러 축 표시자 (Errors) — 카운트형 신호, 정상=0 발화.

    시계열 차트로 두지 않는다 — 대부분 0 이라 빈 차트가 된다. 카운트 + 종류 + 시점 컨텍스트로 표시.
    state = "clean"(창내 0) | "occurred" | "no_data"(표본 없음, 나중에 나타날 수 있음) | "not_applicable"
    (이 OS 구조적 미지원, 영구 N/A — 예: Windows EDAC. no_data 와 구분해 "수집 대기"로 오인 표시 안 함).
    """

    key: str
    label: str
    state: str
    count: int | None = None
    context: str | None = None
    last_at: datetime | None = None
    window_label: str | None = None
    detail: str | None = None


@dataclass
class CpuSnapshot:
    usage_pct: float | None
    user_pct: float | None
    system_pct: float | None
    iowait_pct: float | None
    steal_pct: float | None = None  # 가상화 경합 — 하이퍼바이저가 vCPU 시간 뺏김 (Linux 전용, Windows null)
    # 저우선순위(niced) user 프로세스 시간 — usage_pct 분모엔 포함되나 User 에 안 잡히는 성분이라 별도 노출
    # (Linux 전용, Windows 는 nice 개념 부재로 null). 없으면 User+System+I/O Wait 합이 사용률보다 작아 보인다.
    nice_pct: float | None = None


@dataclass
class CpuCoreSnapshot:
    """코어별 순간 사용률 — 단일스레드 병목 실시간 표시(Linux 전용, Windows 는 빈 list)."""

    core_id: int
    usage_pct: float | None

    hot: bool = False


@dataclass
class MemSnapshot:
    total_bytes: int | None
    used_bytes: int | None
    available_bytes: int | None
    cached_bytes: int | None
    buffered_bytes: int | None
    usage_pct: float | None

    # Cached/Buffers 는 Available 안 회수 가능 세부라, bar 구획 used|cached|buffers|free 합이 100 이다.
    cached_pct: float | None = None
    buffers_pct: float | None = None
    free_pct: float | None = None  # Available 중 cached/buffers 제외 잔여


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
    disk_io: list[DiskIoSnapshot]
    net_io: list[NetIoSnapshot]
    mounts: list[MountDashSnapshot]
    disk_usage_pct: float | None = None
    cpu_saturation: list[SaturationSignal] = field(default_factory=list[SaturationSignal])
    mem_saturation: list[SaturationSignal] = field(default_factory=list[SaturationSignal])
    disk_saturation: list[SaturationSignal] = field(default_factory=list[SaturationSignal])
    net_saturation: list[SaturationSignal] = field(default_factory=list[SaturationSignal])
    errors: list[ErrorSignal] = field(default_factory=list[ErrorSignal])
    cpu_cores: list[CpuCoreSnapshot] = field(default_factory=list[CpuCoreSnapshot])


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


@dataclass
class FleetStatus:
    """전역 상단 바 데이터 최신성 — 온라인 대수/전체 + 마지막 메트릭 수집 시각 (전 페이지 폴링)."""

    online_count: int
    total_count: int
    last_collected_at: datetime | None


@dataclass
class HostSearchItem:
    """전역 호스트 검색(jump-to) 결과 1건 — hostname 부분일치."""

    hostname: str
    public_id: str
    os_id: str | None
