"""Attention 신호·환경 개요 ViewModel — list 화면 상단 카드 + 환경 활용률 도넛."""

from dataclasses import dataclass, field
from datetime import datetime


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
    # OS EOL 전용 — 지원 종료 경과일(양수=지남, 마이그레이션 시급도). 다른 신호(gap/agent)는 None.
    eol_days_over: int | None = None


@dataclass
class CapacityWarningItem:
    """7일 평균 자원 부족 서버 — 마이그레이션 capacity 산정 시 instance type 상향 검토.

    active_causes: 발화한 trigger 의 os-neutral 원인 라벨 목록 (assess.triggers 파생, 고정 순서). 환경 요약
      "자원 부족(메모리 포화 2대 · CPU 이용률 1대)" 원인 집계(environment_report._under_cause_summary)의 단일
      소스. OS 무관 축 이름이라 Windows paging/run queue 포화도 정확히 집계(Linux swap/load 로 오라벨 안 함).
    services: 호스트 워크로드 카테고리 카운트 {category: n} — workload_category_counter 단일 진실.
    """

    public_id: str
    hostname: str
    # 분류 — 통합 조치 대상 표에서 under/over/idle 한 표에 섞이므로 행별 분류 노출 (classify_host 파생).
    classification: str = "under_provisioned"  # 분류 enum 키
    classification_label: str = "자원 부족"  # 표시 라벨 (LABEL_KO)
    badge_class: str = "rec-under_provisioned"  # 뱃지 CSS (BADGE_CLASS)
    classification_rank: int = 0  # 분류 칼럼 정렬값 (ACTION_PRIORITY — 자원 부족 0 > 과다 1 > 유휴 2)
    active_causes: list[str] = field(default_factory=list)
    services: dict[str, int] = field(default_factory=dict)
    # 분류 confidence 단서 — 포화 축 미관측 + 표본 부족 통합 라벨 (shared.build_host_confidence_notes,
    # 원칙2). 보고서 행과 동일 채널 — 카드가 list 렌더(P3). 발화 trigger(빨강)와 시각 구분.
    confidence_notes: list[str] = field(default_factory=list)
    # 증설 권고 — 자원별 독립 처방(recommendation.under_prescription 단일 진실, ADR 0056). 자원 부족 표 권고 칼럼.
    recommendation_action: str = ""
    # 근본원인 — recommendation.root_cause_display 단일 진실. 단일 부족=자원명 / 인과 결합="메모리 (CPU 유발)" /
    # 복수 독립="CPU·디스크 I/O" 나열. 부족 없으면 빈 문자열(표시 "—"). 처방을 거르지 않는 진단 근거 표시 축.
    root_cause_label: str = ""
    # 상위 N 절단 정렬용 심각도 점수 (mapper precompute) — swap(paging) 최우선 > 위반 자원 수 >
    # 최고 활용률 max(CPU/메모리/디스크 p95·used). build_overview 가 DESC 정렬 후 hostname tie-break.
    severity_score: float = 0.0
    # 네트워크 품질(정상/혼잡/미측정) — 사이징 분류 비관여(orthogonal flag, host_status 미구동) 전용 필드.
    net_status_label: str = ""
    net_status_color: str = ""
    # 디스크 I/O 품질(I/O 정상/I/O 병목/미측정) — network 와 동형 orthogonal flag(host_status 미구동, 크기로
    # 안 풀리는 advisory/tier hint). root_cause_label 은 이미 under_provisioned 로 분류된 호스트의 인과
    # 기여분만 노출해 CPU·메모리는 정상인데 디스크만 io_bound 인 호스트는 안 드러남 — network 와 동일하게
    # 전용 필드로 분리해 분류 무관 항상 노출(환경 자원 평가 compact 표 "디스크 I/O 상태" 칼럼 전용 소스).
    disk_io_status_label: str = ""
    disk_io_status_color: str = ""
    # 정적 배정 사양 한 줄("4코어 · 8.00GB · 100GB") — 서버 목록 `ServerListItem.spec_display` 와 동일 산식.
    # 환경 자원 평가(compact 표)에서 호스트 옆에 노출해 권고(recommendation_action, 예: "CPU: 11코어")와
    # 현재 배정을 한눈에 비교 — 사용률 아닌 배정량(P1 raw 스냅샷과 무관, inventory 사양).
    spec_display: str = ""


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
    (dash_length = SVG stroke-dasharray, 원주 2*pi*42 에 pct 0~100 비례).
    """

    label: str  # "CPU" / "메모리" / "디스크"
    pct: float | None
    bar_color: str
    dash_length: float


@dataclass
class RiskDonutSegment:
    """USE Method 분포 도넛 1 segment — 자원 적정성 5 상태 1:1.

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
    # 주요 워크로드 분포 — 카테고리별 환경 전체 인스턴스 개수(호스트 dedup 아님, 모든 카테고리 0 포함, #E7 E9).
    role_distribution: dict[str, int] = field(default_factory=dict)
    # 주요 워크로드 원형차트 세그먼트(RiskDonutSegment 재사용 — color·count·dash precompute) + 총 인스턴스.
    workload_donut: list = field(default_factory=list)
    workload_total: int = 0
    role_unknown_count: int = 0  # 특징 워크로드 0 호스트 수 (보고서 workload_unknown_count 용)
    utilization: list[UtilizationBar] = field(default_factory=list)
    # 평균과 동일 capacity-weighted 환경 분포 기반(per_ts 95퍼센타일).
    utilization_p95: list[UtilizationBar] = field(default_factory=list)
    util_sample_size: int = 0
    # 포화 4축 도넛 (CPU 포화·메모리 압박·디스크 I/O 포화·네트워크 혼잡) — 자원 적정성 창(14일) 기준 호스트 카운트/표본.
    # 실시간현황 7도넛(이용률 3 + 신호 4)과 동일 시각·게이지색, 다만 스냅샷 아닌 윈도우 기준(#E3 화면 간 정합).
    saturation_donuts: list["SaturationDonut"] = field(default_factory=list)
    # 에러축 fleet 표시자 (MCE·OOM·EDAC·디스크·NIC) — 창내 발생 호스트 수/표본. 정상=0 발화(E9). 대시보드 전용.
    error_fleet: list["FleetErrorItem"] = field(default_factory=list)
    # OS 지원(EOL) 4상태 종합 — 서버 목록 칼럼(os_eol_status)과 동일 판정(lookup_os_eol). os_id 있는 서버만 집계.
    os_eol_passed: int = 0  # 무상 보안 패치 종료 (paid_only·ended 합산 — 유상 계약 여부는 수집 불가)
    os_eol_security_only: int = 0  # 보안 패치만 (기능 업데이트 종료)
    os_eol_unknown: int = 0  # 미상(카탈로그 미수록·미매칭 — 판정 불가)
    os_eol_supported: int = 0  # 기능 업데이트 + 보안 패치
    risk_donut: list[RiskDonutSegment] = field(default_factory=list)
    risk_donut_total: int = 0  # 도넛 중심 표시 (분류된 서버 수)
    risk_high_count: int = 0  # 도넛 중심 강조 — "위험 N대"
    under_provisioned_hosts: list[CapacityWarningItem] = field(default_factory=list)
    under_provisioned_hosts_count: int = 0  # 전체 자원 부족 호스트 수 — P3 회피 mapper precompute
    under_provisioned_hosts_shown: int = 0  # 표시 호스트 수(상위 N) — "shown/total" 표기 (P3 회피)


@dataclass
class ActionTargets:
    """통합 조치 대상 표 데이터 — 자원 부족/과다 할당/유휴 호스트를 한 표에 (build_action_targets 단일 진실).

    hosts: CapacityWarningItem(근본원인·권고·신뢰도 + 분류)을 조치 대상 전체에. 최초 정렬 =
      분류 우선순위(자원 부족>과다>유휴) 후 심각도.
    under_count/efficiency_*: 캡션용 카운트·점유 자원 합.
    """

    hosts: list[CapacityWarningItem] = field(default_factory=list)
    total: int = 0  # 표 총 행수 = len(hosts) (전 서버, P3 회피 precompute)
    under_count: int = 0
    efficiency_count: int = 0
    efficiency_vcpus: int = 0
    efficiency_memory_gb: float = 0.0
    efficiency_disk_gb: int = 0  # 과다·유휴 호스트 점유 스토리지 합 (효율화 검토 — CPU·메모리와 함께 3자원)


@dataclass
class EnvironmentAssessment:
    """환경 자원 평가 페이지(/environment/assessment) 전용 — overview(분포 도넛) + 통합 조치 대상 표."""

    overview: EnvironmentOverview
    action: ActionTargets = field(default_factory=ActionTargets)


@dataclass
class RealtimeLoadCell:
    """실시간 부하 표 셀 — 정렬용 raw + 표시 문자열. value=None 은 미측정("—", 정렬 시 맨 뒤, P2 precompute).

    color: 강조색(P2 precompute, P3 템플릿은 적용만) — 판정 있는 축(네트워크 혼잡 등) 전용, 빈 문자열은 무강조.
    """

    value: float | None
    display: str
    color: str = ""


@dataclass
class RealtimeLoadRow:
    """서버별 실시간 부하 표 1행 — 호스트당 7축 전체 노출(top-N 절단 없음, 서버 목록과 동일 sortable-table 관례).

    칼럼 클릭 정렬로 특정 축 부하 순 랭킹을 볼 수 있게 — 7개 분리 top-N 리스트 대신 한 표로 통합.
    """

    hostname: str
    public_id: str
    cpu: RealtimeLoadCell
    mem: RealtimeLoadCell
    run_queue: RealtimeLoadCell
    paging: RealtimeLoadCell
    disk_util: RealtimeLoadCell  # 디스크 I/O 이용률 % (Utilization 축, worst device busy%)
    disk_io: RealtimeLoadCell  # 디스크 응답지연 (Saturation 축, await 지수)
    network: RealtimeLoadCell  # 네트워크 혼잡 판정(정상/혼잡) — net_signal_active, 처리량 아님(판정 대상과 표시값 일치)


@dataclass
class SaturationDonut:
    """실시간 포화 비율 도넛 1개 — 포화/압박 호스트 수 / 표본. 채움 = count/total 비율(제대로 된 비율 도넛).

    처리량(IOPS·MB/s) 절대 총량은 기준점 없어 폐기 — 포화 비율은 "지금 몇 대가 굶고 있나"라 실시간 유의미.
    dash_length·color 는 mapper precompute (P3).
    """

    label: str  # "CPU 포화" / "디스크 I/O 포화" / "메모리 압박"
    count: int  # 포화·압박 호스트 수 (분자)
    total: int  # 신선 표본 (분모)
    dash_length: float  # (count/total) * 원주 — SVG 채움 (precompute)
    color: str  # count>0 강조(빨강) / 0 회색


@dataclass
class FleetErrorItem:
    """환경 fleet 에러 표시자 1개 — 에러축(MCE·OOM·EDAC·디스크·NIC) 창내 발생 호스트 수 / 표본. 정상=0 발화(E9).

    에러는 카운트형(대부분 0)이라 도넛 아닌 표시자 — affected=0 이면 '이상 없음', >0 이면 'N대 영향'.
    """

    key: str  # cpu_mce | mem_oom | mem_corrupted | disk_errors | net_errors | os_eol
    label: str  # 표시 라벨 ("머신체크(MCE)" 등)
    affected: int  # 발생 호스트 수 (분자)
    total: int  # 표본 (분모)
    detail: str | None = None  # 신호 의미(hover)
    tone: str = "danger"  # "danger"(빨강, 하드웨어/런타임 에러) | "warn"(앰버, OS EOL 등 정적 리스크)


@dataclass
class EnvironmentRealtime:
    """list 화면 '환경 실시간 메트릭' 카드 — 현황 모니터링(최신 스냅샷). right-sizing(14일 통계)과 별개 용도.

    sample_size: 평균 표본 = 최신 스냅샷이 신선(now-TTL 이내)한 서버 수 (stale 제외, 'sample_size/total' 표기).
    online/offline: 스냅샷 신선도만으로 판단 (데이터 유무가 곧 온라인 — Redis online flag 이중 게이트 없음).
    """

    total: int
    online: int
    offline: int
    sample_size: int  # 평균 표본 = 최신 스냅샷 신선(now-TTL 이내) 서버 수 (avg 분자)
    utilization: list[UtilizationBar] = field(default_factory=list)
    last_collected_at: datetime | None = None
    load_rows: list[RealtimeLoadRow] = field(default_factory=list)
    # 포화 비율 도넛 (CPU 포화·디스크 I/O 포화·메모리 압박 = 포화 호스트 수/표본).
    saturation_donuts: list[SaturationDonut] = field(default_factory=list)
