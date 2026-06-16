"""Attention 신호·환경 개요 ViewModel — list 화면 상단 카드 + 환경 활용률 도넛."""

from dataclasses import dataclass, field
from datetime import datetime

from assessment_engine.web.view_models.report import ReportRowItem


@dataclass
class AttentionRow:
    """주의 신호 카드 안 1행 — 운영신호 3 카테고리(gap/os_eol/agent_unstable) 공용 표현.

    P2 단일 진실 — 모든 표시 string은 mapper가 결정. template은 attribute access만.
    meta_at: KST 변환은 template 필터만 (#F2).
    """

    badge_class: str
    badge_text: str
    link_href: str
    link_text: str
    mount_path: str | None = None
    meta_text: str = ""
    meta_at: datetime | None = None


@dataclass
class CapacityTriggerBadge:
    """자원 부족 trigger 하나 — 본문 한 줄에 3종 모두 표시 (active False는 비활성 시각).

    color: 범례 색과 동기화 hex (mapper _CAPACITY_TRIGGER_COLORS 단일 진실).
    bg_color/fg_color: mapper가 active/inactive 분기로 미리 결정 (P3 — 템플릿 색 분기 금지).
    """

    label: str
    color: str
    active: bool = True
    bg_color: str = ""
    fg_color: str = ""


@dataclass
class CapacityMetric:
    """자원 부족 카드 안 평가 지표 1개 — assess 입력 6축(CPU/메모리/스왑/Load/디스크/iowait) 전부 노출.

    미관측 축(예: Windows load/iowait OS 부재)도 "N/A" 흐림 placeholder 로 노출(제외 안 함 — 평가 6축 전모 제공).
    active(임계 위반)·measured(관측 여부) 시각 분기는 mapper precompute (P3 — 템플릿 비교 금지).
    color: mapper 결정 (active 빨강 / 정상 진함 / 미관측 흐림).
    """

    label: str
    value: str
    active: bool
    measured: bool
    color: str


@dataclass
class CapacityWarningItem:
    """7일 평균 자원 부족 서버 — 마이그레이션 capacity 산정 시 instance type 상향 검토.

    triggers: USE Method classify 입력 5 trigger 와 1:1 정합 (스왑/CPU/메모리/Load/디스크).
    - swap_used=True → "스왑"
    - cpu_p95 >= CPU_UPSIZE_P95_PCT → "CPU"
    - mem_p95 >= MEM_UPSIZE_P95_PCT → "메모리"
    - load_15m / cpu_cores >= CPU_SATURATION_LOAD_RATIO → "Load"
    - disk_used >= DISK_CAPACITY_UPSIZE_PCT 또는 iowait_p95 >= IOWAIT_UPSIZE_PCT → "디스크"
    under_provisioned 분류라 최소 1개 trigger 존재.
    metrics: 평가 6축 측정값 — 위반 여부 무관 전부 노출(mapper precompute, P3).
    services: 호스트 워크로드 카테고리 카운트 {category: n} — workload_category_counter 단일 진실.
    """

    public_id: str
    hostname: str
    triggers: list[CapacityTriggerBadge] = field(default_factory=list)
    services: dict[str, int] = field(default_factory=dict)
    metrics: list[CapacityMetric] = field(default_factory=list)
    # 분류 confidence 단서 — is_partial(축 미관측) + low_sample(표본 부족) 통합 라벨 (shared.build_confidence_notes,
    # 원칙2). 보고서 행과 동일 채널 — 카드가 list 렌더(P3). 발화 trigger(빨강)와 시각 구분.
    confidence_notes: list[str] = field(default_factory=list)
    # 증설 권고 — hit trigger 별 결합 문구(report._build_under_provisioned_reason 단일 진실). 자원 부족 표 권고 칼럼.
    recommendation_action: str = ""
    # 상위 N 절단 정렬용 심각도 점수 (mapper precompute) — swap(paging) 최우선 > 위반 자원 수 >
    # 최고 활용률 max(CPU/메모리/디스크 p95·used). build_overview 가 DESC 정렬 후 hostname tie-break.
    severity_score: float = 0.0


@dataclass
class AttentionCatalogEntry:
    """주의 신호 카드 상단 범례 1개 — 운영신호 3 카탈로그(통신끊김/OS 지원종료/에이전트 재시작) 중 1개.

    active: count > 0 — 시각 강조 분기 (P3 — 템플릿 분기 금지).
    """

    label: str
    count: int
    active: bool
    description: str = ""  # 임계 근거 한국어 보조 (">= 85%" 등)


@dataclass
class AttentionSignals:
    """list 화면 운영 신호 카드 — 모니터링·시스템 운영 이상 3 카테고리 (USE Method 와 완전 분리).

    USE Method(자원 평가)에서 다루지 못하는 인프라 이상만 표시.
    디스크(capacity·IO)는 USE Method classify 에 통합 — 본 catalog 에서 제외 (중복 회피).
    """

    gap_warnings: list[AttentionRow]
    os_eol_warnings: list[AttentionRow] = field(default_factory=list)
    agent_unstable: list[AttentionRow] = field(default_factory=list)

    @property
    def catalog(self) -> list[AttentionCatalogEntry]:
        """3 카탈로그 범례 — 발화 0건 카테고리도 포함 (#E9)."""
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

    pct None이면 표본 부재 ("—" 표시). bar_color·dash_length 는 P3 회피 mapper precompute
    (dash_length = SVG stroke-dasharray, 원주 ≈ 264 에 pct 0~100 비례).
    """

    label: str  # "CPU" / "메모리" / "디스크"
    pct: float | None
    bar_color: str
    dash_length: float


@dataclass
class RiskDonutSegment:
    """USE Method 분포 도넛 1 segment — recommend enum 6 분류 1:1.

    dash_length·dash_offset: SVG stroke-dasharray + stroke-dashoffset (다중 segment 누적) — mapper precompute (P3).
    """

    key: str
    label: str
    color: str
    count: int
    dash_length: float
    dash_offset: float  # 시계방향 시작 위치 (이전 segments 누적 음수)
    description: str = ""  # 한국어 보조 설명
    pct: float = 0.0  # 분류 막대 너비 (%) — mapper precompute (P3)


@dataclass
class EnvironmentOverview:
    """list 화면 상단 환경 요약 — 총 N대·온라인/오프라인·자원 합계·역할 분포·평균 활용률.

    total_memory_gb: float — 소수 1자리 (작은 환경에서 정수로 묶이면 정보 손실 — 예: 2.5 GB → 2 GB).
    total_disk_gb: int — TB·PB 스케일에서 소수점 의미 적음.
    """

    total: int
    online: int
    offline: int
    total_vcpus: int
    total_memory_gb: float
    total_disk_gb: int
    # os_family(windows/linux/unknown) 별 서버 수. count DESC.
    os_distribution: dict[str, int] = field(default_factory=dict)
    # 역할 분포 — 각 서버의 모든 서비스 카테고리를 카운트 (대표 1개가 아닌 전체, #E7).
    role_distribution: dict[str, int] = field(default_factory=dict)
    role_unknown_count: int = 0  # known 역할 0인 호스트 수 (서비스 없음 또는 전부 unknown)
    role_identified_count: int = 0  # = total - role_unknown_count
    utilization: list[UtilizationBar] = field(default_factory=list)
    # 평균과 동일 capacity-weighted 환경 분포 기반(per_ts 95퍼센타일).
    utilization_p95: list[UtilizationBar] = field(default_factory=list)
    util_sample_size: int = 0
    risk_donut: list[RiskDonutSegment] = field(default_factory=list)
    risk_donut_total: int = 0  # 도넛 중심 표시 (분류된 서버 수)
    risk_high_count: int = 0  # 도넛 중심 강조 — "위험 N대"
    under_provisioned_hosts: list[CapacityWarningItem] = field(default_factory=list)
    under_provisioned_hosts_count: int = 0  # 전체 자원 부족 호스트 수 — P3 회피 mapper precompute
    under_provisioned_hosts_shown: int = 0  # 표시 호스트 수(상위 N) — "shown/total" 표기 (P3 회피)


@dataclass
class EnvironmentAssessment:
    """환경 자원 평가 페이지(/environment/assessment) 전용 — overview + 효율화/자원 부족 표 데이터.

    EnvironmentOverview 에 효율화(ReportRowItem 보유) 필드를 얹지 않는 이유: overview 는 보고서 스냅샷에
    nested 직렬화되므로(report_serializer) 표시 전용 필드로 오염시키지 않는다. 효율화 산출은 보고서와
    동일 헬퍼(`build_efficiency_summary`)·동일 정렬 단일 진실.
    """

    overview: EnvironmentOverview
    efficiency_hosts: list[ReportRowItem] = field(default_factory=list)
    efficiency_hosts_count: int = 0
    efficiency_target_count: int = 0
    efficiency_target_vcpus: int = 0
    efficiency_target_memory_gb: float = 0.0
    # 자원 부족 표 헤더 라벨 — 첫 호스트 metrics 라벨 precompute (P3 인덱싱 회피).
    under_provisioned_metric_labels: list[str] = field(default_factory=list)


@dataclass
class RealtimePeak:
    """실시간 '현재 부하 상위' 1개 셀 — 자원별 랭킹. value=정렬용 raw, display=표시 문자열(mapper precompute)."""

    hostname: str
    public_id: str
    value: float  # 정렬용 raw 값 (%·IOPS·kbps 등 자원별)
    display: str  # 표시 문자열 — "{pct}%" / "{iops} IOPS" / "{mbps} MB/s" (P3 precompute)


@dataclass
class RealtimePeakGroup:
    """부하 상위 3열 grid 의 1열 — 한 자원의 탑 N (내림차순). label=자원명."""

    label: str
    peaks: list[RealtimePeak] = field(default_factory=list)


@dataclass
class EnvironmentRealtime:
    """list 화면 '환경 실시간 메트릭' 카드 — 현황 모니터링(최신 스냅샷). right-sizing(7일 통계)과 별개 용도.

    sample_size: 평균 표본 = 최신 스냅샷이 신선(now-TTL 이내)한 서버 수 (stale 제외, 'sample_size/total' 표기).
    online/offline: 스냅샷 신선도만으로 판단 (데이터 유무가 곧 온라인 — Redis online flag 이중 게이트 없음).
    """

    total: int
    online: int
    offline: int
    sample_size: int  # 평균 표본 = 최신 스냅샷 신선(now-TTL 이내) 서버 수 (avg 분자)
    utilization: list[UtilizationBar] = field(default_factory=list)
    last_collected_at: datetime | None = None
    peak_groups: list[RealtimePeakGroup] = field(default_factory=list)
    has_peaks: bool = False
    # 환경 I/O 총량(신선 표본 합산) — rate 라 게이지 없는 원. None = 표본 전부 페어 부재.
    io_net_value: str | None = None   # Σ(rx+tx) 처리량 값 — 동적 단위(kBps/MBps), mapper precompute
    io_net_unit: str | None = None    # 처리량 단위 (kBps 또는 MBps)
    io_disk_iops: float | None = None  # Σ(read+write) IOPS
