"""Attention 신호·환경 개요 ViewModel — list 화면 상단 카드 + 환경 활용률 도넛."""

from dataclasses import dataclass, field
from datetime import datetime

from assessment_engine.domain.right_sizing import Recommendation


@dataclass
class AttentionRow:
    """주의 신호 카드 안 1행 — 운영신호 3 카테고리(gap/os_eol/agent_unstable) 공용 표현."""

    badge_class: str
    badge_text: str
    link_href: str
    link_text: str
    mount_path: str | None = None
    meta_text: str = ""
    meta_at: datetime | None = None
    # 지원 종료 경과일 — 양수가 지난 날수. os_eol 행에만 채워진다.
    eol_days_over: int | None = None


@dataclass
class CapacityWarningItem:
    """조치 대상 호스트 1행 — 분류·근본원인·권고·신뢰도를 한 행에.

    services 는 서비스 목록이 아니라 워크로드 카테고리별 인스턴스 수다.
    """

    public_id: str
    hostname: str

    classification: Recommendation = "under_provisioned"
    classification_label: str = "자원 부족"
    badge_class: str = "rec-under_provisioned"

    classification_rank: int = 0
    active_causes: list[str] = field(default_factory=list[str])
    services: dict[str, int] = field(default_factory=dict[str, int])
    confidence_notes: list[str] = field(default_factory=list[str])

    recommendation_action: str = ""
    root_cause_label: str = ""

    severity_score: float = 0.0

    net_status_label: str = ""
    net_status_color: str = ""
    # root_cause_label 은 under_provisioned 호스트의 인과 기여분만 노출해 CPU·메모리는 정상인데

    disk_io_status_label: str = ""
    disk_io_status_color: str = ""

    spec_display: str = ""


@dataclass
class AttentionCatalogEntry:
    """주의 신호 카드 상단 범례 1개 — 운영신호 3 카탈로그(통신끊김/OS 지원종료/에이전트 재시작) 중 1개.

    active 는 count > 0 을 mapper 가 미리 계산한 값이다 — 템플릿이 비교하지 않게.
    """

    label: str
    count: int
    active: bool
    description: str = ""


@dataclass
class AttentionSignals:
    """list 화면 운영 신호 카드 — 모니터링·시스템 운영 이상 3 카테고리 (USE Method 와 완전 분리).

    디스크(capacity·IO)는 USE Method 분류가 이미 다루므로 본 카탈로그에 없다.
    """

    gap_warnings: list[AttentionRow]
    os_eol_warnings: list[AttentionRow] = field(default_factory=list[AttentionRow])
    agent_unstable: list[AttentionRow] = field(default_factory=list[AttentionRow])

    @property
    def catalog(self) -> list[AttentionCatalogEntry]:
        return [
            AttentionCatalogEntry("통신 끊김", len(self.gap_warnings), bool(self.gap_warnings)),
            AttentionCatalogEntry("OS 지원종료", len(self.os_eol_warnings), bool(self.os_eol_warnings)),
            AttentionCatalogEntry("에이전트 재시작", len(self.agent_unstable), bool(self.agent_unstable)),
        ]

    @property
    def has_any(self) -> bool:
        return any([self.gap_warnings, self.os_eol_warnings, self.agent_unstable])


@dataclass
class UtilizationBar:
    """환경 평균 자원 활용률 도넛 1개 — list 화면 상단.

    pct None 은 표본 부재. dash_length 는 SVG stroke-dasharray 값(원주에 pct 비례).
    """

    label: str
    pct: float | None
    bar_color: str
    dash_length: float


@dataclass
class RiskDonutSegment:
    """USE Method 분포 도넛 1 segment — 자원 적정성 5 상태 1:1.

    dash_length·dash_offset 은 SVG stroke-dasharray·stroke-dashoffset 값. offset 은 시계방향
    시작 위치라 이전 segment 누적의 음수다.
    """

    key: str
    label: str
    color: str
    count: int
    dash_length: float
    dash_offset: float
    description: str = ""
    pct: float = 0.0


@dataclass
class EnvironmentOverview:
    """list 화면 상단 환경 요약 — 총 N대·온라인/오프라인·자원 합계·역할 분포·평균 활용률.

    메모리 합계만 소수 1자리다 — 작은 환경에서 정수로 묶으면 2.5 GB 가 2 GB 로 뭉갠다.
    디스크는 TB·PB 스케일이라 소수점이 의미 없어 정수로 둔다.
    """

    total: int
    online: int
    offline: int
    total_vcpus: int
    total_memory_gb: float
    total_disk_gb: int
    os_distribution: dict[str, int] = field(default_factory=dict[str, int])

    role_distribution: dict[str, int] = field(default_factory=dict[str, int])
    workload_donut: list[RiskDonutSegment] = field(default_factory=list[RiskDonutSegment])
    workload_total: int = 0
    role_unknown_count: int = 0
    utilization: list[UtilizationBar] = field(default_factory=list[UtilizationBar])

    utilization_p95: list[UtilizationBar] = field(default_factory=list[UtilizationBar])
    util_sample_size: int = 0

    saturation_donuts: list[SaturationDonut] = field(default_factory=list["SaturationDonut"])
    error_fleet: list[FleetErrorItem] = field(default_factory=list["FleetErrorItem"])

    os_eol_passed: int = 0  # paid_only·ended 합산 — 유상 계약 여부는 수집할 수 없다
    os_eol_security_only: int = 0
    os_eol_unknown: int = 0
    os_eol_supported: int = 0
    risk_donut: list[RiskDonutSegment] = field(default_factory=list[RiskDonutSegment])
    risk_donut_total: int = 0
    risk_high_count: int = 0
    under_provisioned_hosts: list[CapacityWarningItem] = field(default_factory=list[CapacityWarningItem])

    under_provisioned_hosts_count: int = 0
    under_provisioned_hosts_shown: int = 0


@dataclass
class ActionTargets:
    """통합 조치 대상 표 데이터 — 자원 부족/과다 할당/유휴 호스트를 한 표에.

    최초 정렬은 분류 우선순위(자원 부족 > 과다 > 유휴) 후 심각도.
    efficiency_* 는 과다·유휴 호스트가 점유한 자원 합 (캡션용).
    """

    hosts: list[CapacityWarningItem] = field(default_factory=list[CapacityWarningItem])
    total: int = 0
    under_count: int = 0
    efficiency_count: int = 0
    efficiency_vcpus: int = 0
    efficiency_memory_gb: float = 0.0
    efficiency_disk_gb: int = 0


@dataclass
class EnvironmentAssessment:
    """환경 자원 평가 페이지(/environment/assessment) 전용 — overview(분포 도넛) + 통합 조치 대상 표."""

    overview: EnvironmentOverview
    action: ActionTargets = field(default_factory=ActionTargets)


@dataclass
class RealtimeLoadCell:
    """실시간 부하 표 셀 — 정렬용 raw + 표시 문자열.

    value None 은 미측정("—", 정렬 시 맨 뒤). color 빈 문자열은 무강조 — 판정이 있는 축만 채운다.
    """

    value: float | None
    display: str
    color: str = ""


@dataclass
class RealtimeLoadRow:
    """서버별 실시간 부하 표 1행 — 호스트당 7축 전체 노출.

    축별 top-N 리스트 7개 대신 한 표로 묶어 칼럼 클릭 정렬로 축별 랭킹을 얻는다.
    """

    hostname: str
    public_id: str
    cpu: RealtimeLoadCell
    mem: RealtimeLoadCell
    run_queue: RealtimeLoadCell
    paging: RealtimeLoadCell
    disk_util: RealtimeLoadCell
    disk_io: RealtimeLoadCell
    network: RealtimeLoadCell


@dataclass
class SaturationDonut:
    """실시간 포화 비율 도넛 1개 — 채움 = 포화 호스트 수 / 표본.

    처리량(IOPS·MB/s) 절대 총량은 비교 기준점이 없어 쓰지 않는다 — 포화 비율은 "지금 몇 대가
    굶고 있나"로 읽혀 실시간 화면에서 의미가 선다.
    """

    label: str
    count: int
    total: int
    dash_length: float
    color: str


@dataclass
class FleetErrorItem:
    """환경 fleet 에러 표시자 1개 — 에러축 창내 발생 호스트 수 / 표본.

    에러는 대부분 0 인 카운트형이라 도넛이 아니라 표시자다. 0 이어도 노출한다 (#E9).
    """

    key: str
    label: str
    affected: int
    total: int
    detail: str | None = None
    tone: str = "danger"


@dataclass
class EnvironmentRealtime:
    """list 화면 '환경 실시간 메트릭' 카드 — 최신 스냅샷 현황. right-sizing 윈도우 통계와 별개 용도.

    online/offline 은 스냅샷 신선도만으로 가른다 — Redis online flag 이중 게이트를 두지 않는다.
    """

    total: int
    online: int
    offline: int
    sample_size: int
    utilization: list[UtilizationBar] = field(default_factory=list[UtilizationBar])
    last_collected_at: datetime | None = None
    load_rows: list[RealtimeLoadRow] = field(default_factory=list[RealtimeLoadRow])
    saturation_donuts: list[SaturationDonut] = field(default_factory=list[SaturationDonut])
