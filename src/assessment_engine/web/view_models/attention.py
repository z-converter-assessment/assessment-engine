"""Attention 신호·환경 개요 ViewModel — list 화면 상단 카드 + 환경 활용률 도넛."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AttentionRow:
    """주의 신호 카드 안 1행 — 5 카테고리(gap/disk/days_until_full/os_eol/agent_unstable) 통합 표현.

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
class CapacityWarningItem:
    """14일 평균 자원 부족 서버 — 마이그레이션 capacity 산정 시 instance type 상향 검토.

    triggers: USE Method classify 입력 5 trigger 와 1:1 정합 (스왑/CPU/메모리/Load/디스크).
    - swap_used=True → "스왑"
    - cpu_p95 >= CPU_UPSIZE_P95_PCT → "CPU"
    - mem_p95 >= MEM_UPSIZE_P95_PCT → "메모리"
    - load_15m / cpu_cores >= CPU_SATURATION_LOAD_RATIO → "Load"
    - disk_used >= DISK_CAPACITY_UPSIZE_PCT 또는 iowait_p95 >= IOWAIT_UPSIZE_PCT → "디스크"
    under_provisioned 분류라 최소 1개 trigger 존재. 표시는 뱃지만 (구체값 메타 표시 안 함).
    """

    public_id: str
    hostname: str
    triggers: list[CapacityTriggerBadge] = field(default_factory=list)


@dataclass
class AttentionCatalogEntry:
    """주의 신호 카드 상단 범례 1개 — 6 카탈로그 중 1개.

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
    utilization: list[UtilizationBar] = field(default_factory=list)
    util_sample_size: int = 0
    risk_donut: list[RiskDonutSegment] = field(default_factory=list)
    risk_donut_total: int = 0  # 도넛 중심 표시용 (분류된 서버 수)
    risk_high_count: int = 0  # 도넛 중심 강조 — "위험 N대"
    # USE Method 분포 도넛 아래 표시 — 자원 부족(under_provisioned) 호스트 trigger·메타 상세.
    under_provisioned_hosts: list[CapacityWarningItem] = field(default_factory=list)
    under_provisioned_hosts_count: int = 0  # 템플릿 P3 회피 — mapper precompute (#E1 P3)
