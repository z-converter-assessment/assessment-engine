"""메트릭 표시 ViewModel — dashboard snapshot + collection status + 시계열 항목."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class PeriodSignalRow:
    """14일 평가 카드 신호 1행 — 이용률/포화 축의 값·임계·판정 (SSR precompute, P2/P3).

    값·임계는 형식화 문자열(템플릿 계산 0), over=임계 이상(글로벌 주황), measured=측정 여부(미측정 muted "N/A").
    """

    label: str  # 축 신호 라벨 ("사용률" / "실행 큐" / "재전송" ...)
    value: str  # 형식화 값 ("68.2%" / "1.20" / "N/A")
    threshold: str  # 임계 표기 ("임계 70%" / ">= 1.0")
    over: bool  # 임계 이상 (글로벌 주황 강조)
    measured: bool  # 측정 여부 (False면 muted)


@dataclass
class PeriodErrorRow:
    """14일 평가 카드 에러축(E) 1행 — 카운트형 표시자(정상=0 발화 E9). 배지 precompute(P3)."""

    key: str  # 원 ErrorSignal.key ("mem_oom" 등) — 자원별 상세 탭이 자기 자원 관련 행만 필터(예: mem_* -> 메모리).
    label: str  # "머신체크(MCE)" / "OOM Kill" ...
    badge_text: str  # "이상 없음" / "6건" / "수집 대기"
    badge_class: str  # badge-ok / badge-danger / badge-muted
    note: str  # 발생 시 종류·창 부기 (없으면 "")
    # 분류 발화 표기 — OOM 이 1건이라도 발생(occurred)하면 "메모리 자원 부족"(빨강, 임계 이상급). assess_memory 가
    # OOM 을 즉시 under 로 보기 때문. 나머지 에러(MCE·EDAC·디스크·NIC)는 사이징 무관(표시 전용). 미발화면 "".
    sizing_signal: str  # "메모리 자원 부족" (OOM occurred) / "" (그 외)


@dataclass
class PeriodExtraGroup:
    """자원별 상세 탭 "추가 지표" 하위 그룹 — 성격별로 묶어 나열(예: CPU "부하"/"신뢰도"). 라벨 + 신호 행 목록."""

    label: str
    rows: list[PeriodSignalRow]


@dataclass
class PeriodResource:
    """14일 평가 카드 자원 1개 — 이용률(U) | 포화(S) 두 컬럼 행 + 각 컬럼 임계 이상 개수 + 상세 탭 slug."""

    name: str  # CPU / 메모리 / 스토리지 / 네트워크
    util_rows: list[PeriodSignalRow]  # 이용률 축 신호 (네트워크는 빈 list)
    util_over: int  # 이용률 임계 이상 개수 (컬럼 헤더)
    sat_rows: list[PeriodSignalRow]  # 포화 축 신호
    sat_over: int  # 포화 임계 이상 개수
    has_util: bool  # 이용률 컬럼 노출 여부 (네트워크 False — 처리량=활동 축, 용량% 없음)
    detail_slug: str  # 상세 탭 경로 slug (cpu/memory/storage/network) — 카드 내 자원별 상세 버튼
    # 자원별 판정 — rollup_host 자원별 status(RS_STATUS_LABEL_KO). 호스트 배지(종합)가 어느 자원발인지 소제목
    # 옆에 표기 (부족=빨강 / 과다=파랑 / 혼잡=주황 / 정상·유휴·미측정=회색). 정상은 muted, 문제 자원만 색으로 부각.
    verdict_label: str
    verdict_color: str
    # 자원별 상세 탭 전용 추가 지표 — U/S 2축엔 안 들어가지만 진단에 유용한 원신호, 성격별 그룹(예: CPU/메모리
    # "부하 신호"/"통계 신뢰도"). 자원마다 선택적으로 채움(빈 list = 해당 자원 미제공). 상세 탭 "신뢰도" 카드.
    extra_groups: list[PeriodExtraGroup] = field(default_factory=list[PeriodExtraGroup])
    # 자원별 상세 탭 전용 에러 축 — error_rows(호스트 전체) 중 이 자원 key 접두(mem_ 등)만 필터. 현재 메모리만
    # 채움(OOM Kill·EDAC) — 다른 자원은 빈 list(상세 탭이 U/S 2열 유지, 메모리만 에러 3열째 추가).
    error_rows: list[PeriodErrorRow] = field(default_factory=list[PeriodErrorRow])
    # 스토리지 전용 2번째 판정 — 용량(disk_capacity)·성능/IO(disk_io)는 서로 독립 축이라 하나의 배지로
    # 합치면(우선순위 승자만 노출) 나머지 축 상태가 안 보임("I/O 병목"만 뜨면 용량은 괜찮은지 알 수 없음).
    # verdict_label/color = 용량 축, 이 필드 = 성능(I/O) 축. 빈 문자열("") = 미사용(스토리지 외 자원).
    verdict_label2: str = ""
    verdict_color2: str = ""


@dataclass
class PeriodAssessment:
    """서버 세부 '최근 N일' 카드 — 자원별 이용률+포화 2축 + 에러축(USE 완결) right-sizing 분류 기준 (14일 창).

    실시간 카드(순간 도넛+활동)와 분리 — 이쪽은 분류·판정 근거(창=recommendation.WINDOW_DAYS). SSR precompute.
    """

    resources: list[PeriodResource]  # [cpu, mem, disk, net]
    error_rows: list[PeriodErrorRow]  # 에러축(E) — 전 자원 통합 (MCE·OOM·EDAC·디스크·네트워크)
    window_days: int
    # 종합 판정 배지 — 목록 자원 적정성과 동일 단일 진실(classify_host = rollup_host 종합, 14일). 축별 신호는
    # 이 판정의 근거(입력)이고 배지는 dual-gate+OOM+용량 종합 결과라, 둘을 함께 노출해 목록-세부 정합.
    classification_label: str
    classification_color: str


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
    state = "clean"(창내 0, 초록 이상 없음) · "occurred"(발생, 빨강 카운트) · "no_data"(표본 없음, 회색 —
    일시적 미수집, 나중에 나타날 수 있음) · "not_applicable"(이 OS 구조적 미지원, 회색 — 영구히 N/A. 예:
    Windows EDAC — WHEA 소스 미구현. no_data 와 구분해 "수집 대기"로 오인 표시 안 함).
    """

    key: str  # cpu_mce | mem_oom | mem_corrupted | net_errors | disk_errors
    label: str  # 표시 라벨 ("머신체크(MCE)", "OOM Kill", "디스크 에러" ...)
    state: str  # "clean" | "occurred" | "no_data" | "not_applicable"
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
    # 저우선순위(niced) user 프로세스 시간 — usage_pct 분모엔 포함되나 User 에 안 잡히는 성분이라 별도 노출
    # (Linux 전용, Windows 는 nice 개념 부재로 null). 없으면 User+System+I/O Wait 합이 사용률보다 작아 보임.
    nice_pct: float | None = None


@dataclass
class CpuCoreSnapshot:
    """코어별 순간 사용률 — 단일스레드 병목 실시간 표시(Linux 전용, Windows 는 빈 list). CPU 상세 전용."""

    core_id: int
    usage_pct: float | None
    # 코어 하이라이트 플래그 — usage_pct >= RS_CPU_PERCORE_HOLD_PCT(85, 단일스레드 병목 임계). 서버 precompute 로
    # 클라(cpu.js)의 임계 재선언(P4 위반) 제거 — 임계 단일 진실은 recommendation.RS_CPU_PERCORE_HOLD_PCT.
    hot: bool = False


@dataclass
class MemSnapshot:
    total_bytes: int | None
    used_bytes: int | None  # total - available
    available_bytes: int | None
    cached_bytes: int | None
    buffered_bytes: int | None
    usage_pct: float | None
    # stacked bar 표시용 비율 — metrics_calculator 산출값이고 클라가 다시 계산하지 않는다.
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
    # 디스크 I/O 활동 스냅샷 — 물리 디스크만(build_dashboard 가 block_devices type=="disk" 조인 필터, I/O 활동
    # 축 = 물리 디스크 단일 규칙 device_filters). LV/파티션 통과분 이중집계 제외. 어태치별 R/W + IOPS.
    disk_io: list[DiskIoSnapshot]
    net_io: list[NetIoSnapshot]  # 물리 인터페이스만(net_interfaces kind physical/bond_master). 인터페이스별 RX/TX.
    mounts: list[MountDashSnapshot]
    # 디스크 이용률 축 — 데이터 볼륨 파일시스템 used/total 집계 %(P2 서버 산출). 실시간 카드 도넛 값.
    disk_usage_pct: float | None = None
    # 포화(S) 스냅샷 신호 (os-aware 서버 판정, P2) — 자원별 SaturationSignal 리스트. 클라는 렌더만.
    # build_saturation_signals(mappers/metric) 단일 산출. 개요·자원 탭 스냅샷 카드 공통 소비.
    cpu_saturation: list[SaturationSignal] = field(default_factory=list[SaturationSignal])
    mem_saturation: list[SaturationSignal] = field(default_factory=list[SaturationSignal])
    disk_saturation: list[SaturationSignal] = field(default_factory=list[SaturationSignal])
    net_saturation: list[SaturationSignal] = field(default_factory=list[SaturationSignal])
    # 에러 축 표시자 (호스트 공통 fleet — MCE·OOM·EDAC·디스크·네트워크). 정상=0 발화(E9). build_error_signals 산출.
    errors: list[ErrorSignal] = field(default_factory=list[ErrorSignal])
    # 코어별 순간 사용률 — 단일스레드 병목 실시간(Linux 전용, Windows 빈 list). CPU 상세 전용 축.
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
    """전역 상단 바 데이터 최신성 — 온라인 대수/전체 + 마지막 메트릭 수집 시각 (전 페이지 폴링). 파생 없음(P1)."""

    online_count: int
    total_count: int
    last_collected_at: datetime | None


@dataclass
class HostSearchItem:
    """전역 호스트 검색(jump-to) 결과 1건 — hostname 부분일치. public_id 로 상세 이동(#E4)."""

    hostname: str
    public_id: str
    os_id: str | None
