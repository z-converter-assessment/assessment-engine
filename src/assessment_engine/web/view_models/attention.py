"""Attention 신호·환경 개요 ViewModel — list 화면 상단 카드 + 환경 활용률 도넛."""

from dataclasses import dataclass, field
from datetime import datetime

from assessment_engine.web.view_models.topology import NetworkTopology


@dataclass
class AttentionRow:
    """주의 신호 카드 안 1행 — 운영신호 3 카테고리(gap/os_eol/agent_unstable) 공용 표현.

    운영신호(AttentionSignals)는 gap/os_eol/agent_unstable 3개뿐 — disk·capacity·days_until_full 은
    USE Method right-sizing(under_provisioned 환경개요·classify·보고서 스토리지 컬럼)으로 이동(중복 회피).

    P2 단일 진실 — 모든 표시 string은 mapper가 결정. template은 attribute access만.

    badge_class: badge CSS class (rec-* — base.html 단일 진실)
    badge_text:  badge 안 텍스트 ("5분", "92%", "EOL", ...)
    link_href:   상세 페이지 경로 ("/servers/{public_id}" 또는 "/servers/{public_id}/storage")
    link_text:   링크 표시 텍스트 (보통 hostname)
    mount_path:  mount path 별도 attribute — 긴 path는 CSS ellipsis + title hover (template에서 처리).
                 None이면 macro는 mount 표시 생략. mount path 없는 카테고리(gap/os_eol/agent_unstable)에 적용.
    meta_text:   meta 영역 정적 텍스트 (mount path 제외한 나머지 — "잔여 12 / 100 GB" 등)
    meta_at:     KST 표시 datetime (있으면 meta_text 뒤에 kst 필터로 표시). KST 변환은 template 필터만 (#F2)
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

    label: "스왑" / "CPU" / "메모리"
    color: 범례 색과 동기화 hex (mapper _CAPACITY_TRIGGER_COLORS 단일 진실)
    active: True면 본 자원 임계 초과 (색 채워진 배지), False면 비활성 (흐리게)
    bg_color/fg_color: 표시용 hex — mapper가 active/inactive 분기로 미리 결정 (P3 단일 진실).
                       템플릿은 inline 색 분기 안 함.
    """

    label: str
    color: str
    active: bool = True
    bg_color: str = ""
    fg_color: str = ""


@dataclass
class CapacityMetric:
    """언더 프로비저닝 카드 안 평가 지표 1개 — assess 입력 6축(CPU/메모리/스왑/Load/디스크/iowait).

    프로비저닝 판정에 쓰인 모든 측정값을 노출(active 만이 아님) — 카드 본문을 채우고 근거 전모 제공.
    active(임계 위반)·measured(관측 여부) 시각 분기는 mapper precompute (P3 — 템플릿 비교 금지).

    label: "CPU p95" / "메모리 p95" / "스왑" / "Load" / "디스크" / "iowait"
    value: 표시 문자열 ("94%" / "발생" / "2.3x" / "N/A")
    active: 임계 위반 (강조 — under_provisioned 기여 trigger)
    measured: 관측됨 (값 존재). Windows 미측정(load/iowait 등)은 False -> "N/A" 흐림.
    color: 값 표시 hex — mapper 결정 (active 빨강 / 정상 진함 / 미관측 흐림).
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
    triggers: 보고서(_env_report_body)용 5종 뱃지 list (active/inactive 시각).
    metrics: 대시보드 카드용 평가 6축 측정값 — 위반 여부 무관 전부 노출(mapper precompute, P3).
    services: 호스트 워크로드 카테고리 카운트 {category: n} — workload_category_counter 단일 진실.
    템플릿은 role_distribution 과 동일하게 service_badge_class 필터 + count 렌더 (호스트명·지표와 분리).
    """

    public_id: str
    hostname: str
    triggers: list[CapacityTriggerBadge] = field(default_factory=list)
    services: dict[str, int] = field(default_factory=dict)
    metrics: list[CapacityMetric] = field(default_factory=list)
    # 상위 N 절단 정렬용 심각도 점수 (mapper precompute) — swap(paging) 최우선 > 위반 자원 수 >
    # 최고 활용률 max(CPU/메모리/디스크 p95·used). build_overview 가 DESC 정렬 후 hostname tie-break.
    severity_score: float = 0.0


@dataclass
class AttentionCatalogEntry:
    """주의 신호 카드 상단 범례 1개 — 운영신호 3 카탈로그(통신끊김/OS 지원종료/에이전트 재시작) 중 1개.

    label: 카테고리 이름 (sub-section 헤더와 동일)
    count: 발화 건수 (list 길이)
    active: count > 0 — 시각 강조 분기 (P3 — 템플릿 분기 금지)
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

    gap_warnings: 모니터링 사각지대 (5분+ 끊김).
    os_eol_warnings: OS EOL 임박/지남 (정적 매핑).
    agent_unstable: 1h 윈도우 안 재시작 임계 초과 서버.
    catalog: 카드 상단 범례 — 3 카탈로그 label·count·active (발화 0건도 노출).
    has_any: 3 카탈로그 중 1개라도 비어있지 않으면 True.
    """

    gap_warnings: list[AttentionRow]
    os_eol_warnings: list[AttentionRow] = field(default_factory=list)
    agent_unstable: list[AttentionRow] = field(default_factory=list)

    @property
    def catalog(self) -> list[AttentionCatalogEntry]:
        """3 카탈로그 범례 — USE Method 외 시스템 운영 이상. 발화 0건 카테고리도 포함."""
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

    pct: None이면 표본 부재 ("—" 표시). bar_color: P3 임계 분기 결과 (mapper 단일 결정).
    dash_length: SVG stroke-dasharray 길이 (도넛 원호). 원주 ≈ 264, pct 0~100 → 0~264.
    템플릿이 산술 못 하므로 mapper가 미리 계산 (P3).
    """

    label: str  # "CPU" / "메모리" / "디스크"
    pct: float | None
    bar_color: str  # 임계별 hex 색 — mapper 결정 (P3 분기 금지)
    dash_length: float  # SVG dasharray — mapper 비례 산술 (P3)


@dataclass
class RiskDonutSegment:
    """USE Method 분포 도넛 1 segment — recommend enum 6 분류 1:1.

    dash_length·dash_offset: SVG stroke-dasharray + stroke-dashoffset (다중 segment 누적).
    템플릿에서 산술 못 하므로 mapper가 미리 계산 (P3).
    description: 범례 옆 간단 보조 설명 (mapper 단일 진실).
    """

    key: str  # recommend enum (영어)
    label: str  # 표시 라벨 (영어 enum 그대로)
    color: str  # hex
    count: int  # 해당 카테고리 서버 수
    dash_length: float  # 본 segment 원호 길이
    dash_offset: float  # 시계방향 시작 위치 (이전 segments 누적 음수)
    description: str = ""  # 한국어 보조 설명 ("자원 부족 (사양 상향)" 등)
    pct: float = 0.0  # 분류 막대 너비 (%) — mapper precompute (P3). 도넛 -> 가로막대 게이지 전환 대응.


@dataclass
class EnvironmentOverview:
    """list 화면 상단 환경 요약 — 총 N대·온라인/오프라인·자원 합계·역할 분포·평균 활용률.

    total_memory_gb: float — 소수 1자리 (작은 환경에서 정수로 묶이면 정보 손실 — 예: 2.5 GB → 2 GB).
    total_disk_gb: int — TB·PB 스케일에서 소수점 의미 적음.
    utilization: 3개 막대(CPU·메모리·디스크) — 환경 평균 활용률. 표본 부재 시 빈 list.
    util_sample_size: 활용률 평균 표본 서버 수 (UI에 "N대 기준" 표시).
    """

    total: int
    online: int
    offline: int
    total_vcpus: int
    total_memory_gb: float
    total_disk_gb: int
    # os_family(windows/linux/unknown) 별 서버 수 — 환경 OS 구성 요약. count DESC.
    os_distribution: dict[str, int] = field(default_factory=dict)
    # 역할 분포 — 각 서버의 모든 서비스 카테고리를 카운트 (대표 1개가 아닌 전체, #E7).
    role_distribution: dict[str, int] = field(default_factory=dict)
    # known 역할(서비스 카테고리) 이 하나도 없는 호스트 수 — 서비스 없음 또는 전부 unknown. 호스트 단위.
    role_unknown_count: int = 0
    utilization: list[UtilizationBar] = field(default_factory=list)
    # p95 활용률 3막대(CPU·메모리·디스크) — 평균과 동일 capacity-weighted 환경 분포 기반(per_ts 95퍼센타일).
    utilization_p95: list[UtilizationBar] = field(default_factory=list)
    util_sample_size: int = 0
    risk_donut: list[RiskDonutSegment] = field(default_factory=list)
    risk_donut_total: int = 0  # 도넛 중심 표시용 (분류된 서버 수)
    risk_high_count: int = 0  # 도넛 중심 강조 — "위험 N대"
    # USE Method 분포 도넛 아래 표시 — 자원 부족(under_provisioned) 호스트 trigger·메타 상세.
    under_provisioned_hosts: list[CapacityWarningItem] = field(default_factory=list)
    under_provisioned_hosts_count: int = 0  # 전체 자원 부족 호스트 수 — 템플릿 P3 회피 mapper precompute (#E1 P3)
    under_provisioned_hosts_shown: int = 0  # 표시 호스트 수(상위 N, 호스트명 ASC) — "shown/total" 표기용 (P3 회피)


@dataclass
class RealtimePeak:
    """실시간 '현재 부하 상위' 1개 셀 — 자원별(CPU/메모리/디스크) 랭킹. pct/color 는 해당 자원 값."""

    hostname: str
    public_id: str
    pct: float
    color: str  # _bar_color(pct) — 푸른 단색 (P3 precompute)


@dataclass
class RealtimePeakGroup:
    """부하 상위 3열 grid 의 1열 — 한 자원의 탑 N (내림차순). label=자원명."""

    label: str
    peaks: list[RealtimePeak] = field(default_factory=list)


@dataclass
class EnvironmentRealtime:
    """list 화면 '환경 실시간 메트릭' 카드 — 현황 모니터링(최신 스냅샷). right-sizing(7일 통계)과 별개 용도.

    utilization: 신선 데이터 서버(sample_size)의 현재 CPU/메모리/디스크(worst mount) 평균 도넛 3개
                 (환경 평균 활용률 도넛과 동일 컴포넌트·푸른 단색 게이지 — UtilizationBar).
    sample_size: 평균 표본 = 최신 스냅샷이 신선(now-TTL 이내)한 서버 수. stale 메트릭은 제외 —
                 표기는 'sample_size/total' (예: 3/4, stale 1대 빠짐).
    online/offline: 최신 스냅샷 신선도 기준(now-TTL 이내 = 온라인, 데이터 유무가 곧 온라인 판정).
                    Redis online flag 이중 게이트 없이 스냅샷 신선도만으로 판단.
    last_collected_at: 환경 전체 최신 수집시각(신선도).
    peak_groups: 자원별(CPU/메모리/디스크) 부하 상위 N — 3열 grid. has_peaks: 전체 빈 여부(empty 분기).
    """

    total: int
    online: int
    offline: int
    sample_size: int  # 평균 표본 = 최신 스냅샷 신선(now-TTL 이내) 서버 수 (avg 분자)
    utilization: list[UtilizationBar] = field(default_factory=list)
    last_collected_at: datetime | None = None
    peak_groups: list[RealtimePeakGroup] = field(default_factory=list)
    has_peaks: bool = False


@dataclass
class DashboardLive:
    """list 화면 fragment=live·page1 공용 묶음 — 공유 기초 데이터(now·report_aggregate·online flags)를
    1회 조회 후 ViewModel 조립. 세 라이브 카드가 같은 스냅샷 기준이라 카드 간 값 일관 (중복 쿼리 제거 + 비결정성 해소).

    topology 는 동일 details 에서 파생하지만 자동갱신 라이브 fragment 가 아니라 page 렌더에서만 1회 표시
    (토폴로지는 정적 인벤토리라 30초 폴링 대상 아님 — list_page 가 page==1 에서만 context 전달)."""

    overview: EnvironmentOverview
    attention: AttentionSignals
    topology: NetworkTopology
    # 환경 부하 추이 (7일 표준 윈도우) — CPU·메모리 평균 시계열. 차트 JS inline(tojson)용 plain dict.
    # 토폴로지처럼 정적 인벤토리 아님(메트릭)이라 fragment 자동갱신 포함. [{"at": iso, "cpu", "mem"}].
    trend: list[dict] = field(default_factory=list)
