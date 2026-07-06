"""Assessment 보고서 ViewModel — server scope 보고서 row + KPI 합계 + 요약."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ReportWorkloadGroup:
    """개별 보고서 customer — 워크로드 카테고리별 제품명 묶음. 예: category="web", names_label="nginx, gunicorn".

    names_label 빈 문자열 = listen 소켓으로만 탐지된 카테고리 (제품명 미상, T15) — 카테고리만 표시.
    """

    category: str
    names_label: str = ""
    # ["80/tcp", "443/tcp"] — 카테고리 귀속 listen 포트 (mapper precompute, P3)
    ports: list[str] = field(default_factory=list)


@dataclass
class ReportServiceUnit:
    """개별 보고서 engineer — 등록 서비스(systemd unit) 1행. unit·카테고리·귀속 listen 포트."""

    unit: str
    category: str
    ports_label: str = ""  # "80/tcp, 443/tcp" — 귀속 포트 join, mapper precompute (P3)


@dataclass
class ReportListenItem:
    """개별 보고서 engineer — listen 소켓 1행. port·proto·process (raw listen_ports 표시본)."""

    port: int
    proto: str
    comm: str = ""
    addr: str = ""
    uid: int | None = None
    pid: int | None = None


@dataclass
class SaturationAxis:
    """USE Saturation 축 1개 — single_report '포화 축 평가' 카드 행 (os-aware precompute, P2/P3).

    단일 서버 deep-dive 전용 — 분류 진단·권고의 근거 수치를 OS별 실측 신호로 노출. 3축(CPU/메모리/디스크 I/O)
    을 OS 무관 축 이름으로 통일하고, 측정 신호 이름·값·임계·판정은 os-aware. 미관측(perflib 미발행)은 status
    '미관측'. E9 발화 가능 정보 노출 — 축은 항상 3행 노출(값 없어도).
    """

    axis: str  # os-neutral 축 이름 (CPU 포화 / 메모리 포화 / 디스크 I/O)
    signal: str  # 해당 OS 측정 신호 이름 (Linux load avg / Windows Processor Queue Length 등)
    value: str  # 형식화 값 (미관측 'N/A')
    threshold: str  # 임계 표기
    status: str  # '포화' | '정상' | '미관측'
    status_class: str  # 템플릿 CSS 클래스 (mapper 결정, P3 — 템플릿 비교 금지)


@dataclass
class ReportRowItem:
    """ReportRowRaw + 표시 파생. 모든 표시 결정(role/recommendation/badge)은 mapper에서 채움 (P2)."""

    server_id: int
    public_id: str
    hostname: str
    role: str
    is_online: bool
    os_family: str | None  # "windows" 면 load(run queue) 통계 N/A 표시 (disk 는 queue 로 측정)
    os_display: str
    kernel_version: str | None
    internal_ip: str | None

    cpu_p95_pct: float | None
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None
    mem_p95_pct: float | None
    mem_avg_pct: float | None
    mem_peak_pct: float | None
    load_15m_max: float | None
    swap_used: bool

    recommendation: str  # USE Method enum 값
    recommendation_label: str  # 한국어
    badge_class: str  # USE 분류 CSS 클래스

    # UI 친화 위험도 매핑
    risk_level: str  # "high" / "attention" / "normal" / "low_usage"
    risk_label: str  # "고위험" / "주의 필요" / "정상" / "저사용"
    risk_badge_class: str  # rec-* CSS 재사용

    # 서버 인벤토리 (정적 — 환경 엔지니어 호스트 상세 표 노출용). None 가능 (신규 등록 직후 등).
    cpu_cores: int | None = None
    mem_total_gb: float | None = None
    disk_total_gb: float | None = None

    # I/O wait — 디스크 병목 신호 (양식 B 컬럼)
    iowait_p95_pct: float | None = None
    iowait_peak_pct: float | None = None

    # Mount 최악 — 서버 안에서 가장 채워진 마운트 1건 (양식 B 컬럼)
    worst_mount: str | None = None
    worst_mount_used_pct: float | None = None
    worst_mount_days_until_full: int | None = None

    # Uptime + 재부팅 + 에이전트 재시작 (양식 B 컬럼 — anchor+window 카운트, 시스템 안정성)
    uptime_days: int | None = None
    reboot_count: int = 0
    agent_restart_count: int = 0

    # USE Saturation 3축 os-aware 평가 — single_report '포화 축 평가' 카드(분류 근거 수치 노출). mapper precompute.
    # (구 saturation_ratio(load/cores 단일값)는 미렌더·Linux 전용이라 폐기 — saturation_axes 가 os-aware 대체.)
    saturation_axes: list[SaturationAxis] = field(default_factory=list)

    # 이상치 변동성 — peak/p95 비율. 1.5 이상이면 변동 큼.
    cpu_variance_ratio: float | None = None
    mem_variance_ratio: float | None = None

    # Disk I/O — baseline(평균) + p95 + peak (양식 B 컬럼 + inventory-export)
    disk_iops_baseline: int | None = None
    disk_iops_p95: float | None = None
    disk_iops_peak: float | None = None
    disk_throughput_kbps: float | None = None
    disk_throughput_kbps_p95: float | None = None
    disk_throughput_kbps_peak: float | None = None

    # Net I/O — baseline(평균) + p95 + peak (양식 B 컬럼 + inventory-export)
    net_rx_kbps: float | None = None
    net_rx_kbps_p95: float | None = None
    net_rx_kbps_peak: float | None = None
    net_tx_kbps: float | None = None
    net_tx_kbps_p95: float | None = None
    net_tx_kbps_peak: float | None = None

    # 진단 텍스트 — saturation·variance·iowait·disk·swap 종합 자동 진단 (양식 B "판단" 컬럼)
    # mapper.build_diagnosis 결정. 우선순위: 메모리 압박 → 디스크 병목 → CPU saturation → 변동성 → 적정
    diagnosis: str = ""

    # 양식 A "권고" 컬럼 — 분류별 권장 조치 (mapper._build_recommendation_action 단일 진실,
    # environment·single_report 공유). under 는 hit trigger 결합("메모리 증설 (스왑 발생) / CPU 증설" 등),
    # over/idle/optimal/insufficient 는 고정 문구.
    recommendation_action: str = ""

    # 부분 평가 — saturation 축 중 해당 OS 의 perflib 미발행 축만 미관측(os-aware, P2/P4). Windows 도 run queue/
    # paging/disk queue 를 실측하되 카운터를 못 읽은 축만 unmeasured. mapper 가 assessment.is_partial(=bool(unmeasured))
    # 로 precompute, 템플릿은 본 bool 만 분기 (P3).
    is_partial: bool = False

    # 분류 confidence 단서 — is_partial(축 미관측) + low_sample(표본 부족) 통합 라벨 (shared.build_confidence_notes).
    # 분류는 가진 데이터로 완결(원칙1), 신뢰도 저하 요인만 본 채널로 분리 노출(원칙2). 템플릿은 list 렌더만 (P3).
    confidence_notes: list[str] = field(default_factory=list)

    # 구동 서비스 (P-A 구성 계층) — 개별 서버 보고서(single_report)에서만 렌더. N대 표·환경 보고서는 미사용.
    # customer: workload_groups (카테고리별 제품명) / engineer: service_units(등록 unit) + listen_ports_detail.
    # mapper 가 service_classifier 로 precompute (P2), 템플릿은 순수 렌더 (P3).
    workload_groups: list[ReportWorkloadGroup] = field(default_factory=list)
    service_units: list[ReportServiceUnit] = field(default_factory=list)
    listen_ports_detail: list[ReportListenItem] = field(default_factory=list)


@dataclass
class ReportTotals:
    """양식 A 묶음 자원 총량 — 마이그레이션 capacity 산정 입력."""

    total_vcpus: int
    # 메모리는 소수 첫째 자리 — int 변환 시 0.5 GB 등 표시 왜곡.
    total_memory_gb: float
    total_disk_gb: int


@dataclass
class ReportSummary:
    """get_report 응답 — 행 list + KPI 집계 (KPI도 service 책임)."""

    rows: list[ReportRowItem]
    period_days: int
    total: int
    online: int
    risk_attention: int  # 주의 필요 — over_provisioned·idle 합산
    risk_high: int  # 고위험 — under_provisioned
    # 환경 활용률 평균 KPI — 고객 보고서가 "환경 전체 활용도"를 한눈에 보여주기 위함.
    # None은 표시 단계에서 "—"로 fallback (모든 서버가 평가 불가일 때).
    avg_cpu_p95_pct: float | None = None
    avg_mem_p95_pct: float | None = None
    totals: ReportTotals = field(default_factory=lambda: ReportTotals(0, 0, 0))
    # 양식 A 정성 요약 — mapper에서 자동 생성 (P2). 컨설턴트가 고객 보고서 첨부 시 활용.
    summary_bullets: list[str] = field(default_factory=list)
    # 양식 A 상단 역할 분포 — {"web": 8, "db": 5, "cache": 3, ...}. service_classifier 카테고리 집계.
    role_distribution: dict[str, int] = field(default_factory=dict)
    # N대 선택 맥락 (P-A 구성) — 이 묶음이 무엇인지 한 줄 요약. mapper precompute (P3 정렬 회피).
    # os_family_summary: "Linux 2 / Windows 1" · workload_summary: "web 2, db 1". 환경 보고서는 미사용(막대 사용).
    os_family_summary: str = ""
    workload_summary: str = ""
    # 보고서 발행 / 평가 기준 시각 (UTC, 표시 단계에서 KST 변환).
    # generated_at = 본 응답 합성 시각 (지금). anchor_at = 평가 윈도우 끝 (period 평가 기준).
    generated_at: datetime | None = None
    anchor_at: datetime | None = None
