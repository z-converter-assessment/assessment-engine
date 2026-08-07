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

    key: str  # ErrorSignal.key 그대로 — 자원별 상세 탭이 접두(mem_ 등)로 자기 자원 행만 필터한다.
    label: str
    badge_text: str
    badge_class: str
    note: str
    # OOM 은 assess_memory 가 1건이라도 즉시 under 로 보므로 분류 발화까지 표기한다. 나머지 에러(MCE·EDAC·
    # 디스크·NIC)는 사이징과 무관해 "".
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
    has_util: bool  # 네트워크는 처리량=활동 축이라 용량% 가 없어 False.
    detail_slug: str
    # 자원별 판정 — rollup_host 의 자원별 status 라벨. 호스트 종합 배지는 PeriodAssessment 쪽이다.
    verdict_label: str
    verdict_color: str
    # U/S 2축엔 안 들어가지만 진단에 쓰는 원신호 — 빈 list 면 그 자원 미제공.
    extra_groups: list[PeriodExtraGroup] = field(default_factory=list[PeriodExtraGroup])
    # PeriodAssessment.error_rows(호스트 전체) 중 이 자원 몫 부분집합 — 현재 메모리만 채운다.
    error_rows: list[PeriodErrorRow] = field(default_factory=list[PeriodErrorRow])
    # 용량(disk_capacity)과 성능/IO(disk_io)는 독립 축이라 배지 하나로 합치면 우선순위 승자만 남고 나머지 축
    # 상태가 안 보인다. verdict_label/color = 용량 축, 이쪽 = 성능(I/O) 축. "" = 스토리지 외 자원.
    verdict_label2: str = ""
    verdict_color2: str = ""


@dataclass
class PeriodAssessment:
    """서버 세부 '최근 N일' 카드 — 자원별 U/S 2축 + 에러축(E) right-sizing 분류 근거.

    실시간 카드(순간 스냅샷)와 분리 — 이쪽은 분류·판정 근거이고 창은 `right_sizing.WINDOW_DAYS`.
    """

    resources: list[PeriodResource]  # [cpu, mem, disk, net] 순
    error_rows: list[PeriodErrorRow]  # 전 자원 통합 (자원별 부분집합은 PeriodResource.error_rows)
    window_days: int
    # 축별 신호는 이 판정의 입력이고 배지는 classify_host(rollup_host 종합, dual-gate+OOM+용량) 결과다 —
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

    key: str  # 안정 식별자 (cpu_run_queue·mem_psi·disk_await 등)
    label: str
    state: str
    value: float | None = None
    threshold: float | None = None
    unit: str | None = None  # "per_core" | "ms" | "%" | "/s"
    saturated: bool | None = None  # measured 일 때만 유효
    detail: str | None = None  # hover: 이 OS metric·임계 근거 문장
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
    context: str | None = None  # 종류·부가 (disk kinds, EDAC bytes)
    last_at: datetime | None = None
    window_label: str | None = None
    detail: str | None = None  # hover: 신호 의미·출처


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
    # usage_pct >= right_sizing.CPU_PERCORE_HOLD_PCT (단일스레드 병목 임계). 서버 precompute 라
    # 클라(cpu.js)가 임계를 다시 선언하지 않는다.
    hot: bool = False


@dataclass
class MemSnapshot:
    total_bytes: int | None
    used_bytes: int | None  # total - available
    available_bytes: int | None
    cached_bytes: int | None
    buffered_bytes: int | None
    usage_pct: float | None
    # stacked bar 표시용 비율 — 클라가 다시 계산하지 않는다. 메모리 구성 모델은 Used + Available = 100 이고
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
    disk_io: list[DiskIoSnapshot]  # 물리 디스크만 — LV/파티션 통과분 이중집계 제외 (device_filters)
    net_io: list[NetIoSnapshot]  # 물리 인터페이스만 (device_filters)
    mounts: list[MountDashSnapshot]
    disk_usage_pct: float | None = None  # 데이터 볼륨 파일시스템 used/total 집계 % (가상 fs 제외)
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
