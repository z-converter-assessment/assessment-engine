"""환경 보고서 ViewModel — environment scope 보고서 (전체 등록 서버 대상)."""

from dataclasses import dataclass, field
from datetime import datetime

from assessment_engine.web.view_models.attention import (
    AttentionSignals,
    CapacityWarningItem,
    EnvironmentOverview,
)
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary
from assessment_engine.web.view_models.server import IpAddr
from assessment_engine.web.view_models.topology import NetworkTopology


@dataclass
class ClassificationCount:
    """USE Method 분포 1 segment — 환경 보고서 전용 (양식 A/B 공통).

    label 은 right-sizing 한국어 분류명(recommendation.LABEL_KO 단일 진실) — 보고서 전역 동일 어휘.
    description: 보고서 분포 막대 옆 조치 방향 보조 설명.
    pct: 본 segment 가 전체 classification_dist 중 차지하는 % (mapper precompute, P3 회피).
    """

    key: str  # recommend enum
    label: str  # 영어 enum 그대로
    count: int
    color: str  # hex 색
    description: str = ""  # 한국어 보조 설명
    pct: float = 0.0  # 0~100 — mapper 가 _count_classifications 직후 채움


@dataclass
class OsCount:
    """OS distro·version 그룹별 카운트 — 환경 보고서 OS 분포 섹션."""

    os_display: str  # mapper `_os_display` 결과
    count: int


@dataclass
class DistributionBar:
    """구성 분포 1 segment — OS family / 워크로드 카테고리 공용 (환경 보고서 구성 계층).

    단순 분포 막대 (위험도 색 아님) — P-E 단일 색, 막대마다 색 달리하지 않음.
    pct: 분포 내 최대 count 대비 막대 너비 % (mapper precompute, P3 회피). count 가 절대값.
    """

    label: str
    count: int
    pct: float = 0.0


@dataclass
class ServerInventory:
    """개별 서버 보고서 인벤토리 — ServerDetail 충실 표시 (생략·왜곡 없음).

    IP 전체(IPv4/IPv6 모두, 임의 1개 선택 금지) + 식별자·하드웨어·부팅 정보. customer/engineer 공용,
    customer 는 식별자(composite_id/machine_id) 미표시(template 분기).
    """

    hostname: str
    os_display: str
    os_codename: str | None
    kernel_version: str | None
    cpu_model: str | None
    cpu_cores: int | None
    mem_total_gb: float | None
    swap_total_gb: float | None
    disk_total_gb: int | None
    ip_internal: list[IpAddr]
    ip_external: list[IpAddr]
    boot_time: datetime | None
    agent_version: str | None
    composite_id: str
    machine_id: str | None
    is_online: bool


@dataclass
class VolumeUsage:
    """개별 보고서 마운트별 스토리지 — 윈도우 평균 사용률 + 총량 (worst 1개 아닌 전체)."""

    mount: str
    total_gb: float | None
    used_pct: float | None


@dataclass
class MemoryBreakdown:
    """개별 보고서 메모리 구성 — used/available/cached/buffers (전체 대비 %, 윈도우 평균)."""

    used_pct: float | None
    available_pct: float | None
    cached_pct: float | None
    buffers_pct: float | None


@dataclass
class CpuBreakdown:
    """개별 보고서 CPU 분류 — user/system/iowait (jiffies delta 기반 %, 윈도우 평균)."""

    user_pct: float | None
    system_pct: float | None
    iowait_pct: float | None


@dataclass
class ServiceHost:
    """서비스 구동 호스트 1개 — engineer 서비스 구성에서 서버 상세 링크용 (public_id = /servers/{id})."""

    hostname: str
    public_id: str


@dataclass
class ServiceNameCount:
    """서비스 구성 — 구체 서비스명 1개 + 등장 서버 수 + 구동 호스트 list (engineer 표시).

    예: name="redis", count=3, hosts=[3대]. customer 는 name·count 만, engineer 는 hosts 호스트명 링크 노출.
    """

    name: str
    count: int
    hosts: list[ServiceHost] = field(default_factory=list)


@dataclass
class ServiceCatalogGroup:
    """서비스 구성 카드 — 워크로드 카테고리 1개 + 그 안 구체 서비스명·개수 list (뱃지 색 = 카테고리).

    범례(카테고리 색) + 색 뱃지(서비스명·개수)로 표시. base.rows workload_groups 를 카테고리 기준 집계 (mapper, P2).
    """

    category: str
    services: list[ServiceNameCount] = field(default_factory=list)


@dataclass
class AttentionHostItem:
    """운영 신호 발화 호스트 — 통신 끊김 / OS EOL / 에이전트 재시작 빈번 중 1개 이상 hit.

    AttentionSignals 3 카테고리 (gap_warnings / os_eol_warnings / agent_unstable) 를
    호스트 기준 unified 합성 — 동일 호스트가 여러 신호 발화 시 한 row 안에 표시.
    engineer 보고서 즉시 점검 list (Right-sizing 분류와 독립).
    """

    public_id: str
    hostname: str
    os_display: str
    # 카테고리별 발화 메타 — None 이면 비활성. mapper 결정 (P2).
    gap_label: str | None  # "5분" — gap_warnings badge_text
    os_eol_label: str | None  # "centos 7 · EOL 2024-06-30" — os_eol_warnings meta_text
    restart_label: str | None  # "12회" — agent_unstable badge_text
    active_count: int  # 활성 신호 카운트 (1~3)


@dataclass
class CapacityImminentItem:
    """디스크 capacity 임박 호스트 — worst_mount_days_until_full <= 30 (engineer 보고서).

    linear projection 기반 — 30일 안 디스크 full 위험. 운영 계획 입력.
    """

    public_id: str
    hostname: str
    worst_mount: str
    days_until_full: int
    used_pct: float | None


@dataclass
class EnvironmentReportSummary:
    """환경 단위 보고서 (전체 등록 서버 대상) — server scope ReportSummary 와 별도 양식.

    server scope 보고서: row 단위 검토 중심 (선택 N대 상세).
    environment scope 보고서: high-level overview·분류 분포·top risk·OS 분포 중심.
    view ('customer'|'engineer') 분기 — summary_bullets_env 가 view 별 다른 텍스트.
    time_range/anchor_at: AI 진단과 동일 윈도우 매트릭스 (15m~30d) + 운영자 명시 anchor.
    """

    view: str
    time_range: str  # "15m"/"1h"/"6h"/"24h"/"7d"/"14d"/"30d"
    time_range_label: str  # "15분"/"1시간"/...  한국어 표시 단일 진실 (mapper)
    anchor_at: datetime  # 분석 기준 시각 (보고서 본문 끝점)
    generated_at: datetime  # 응답 합성 시각 (DB 저장·UI 표시용)
    overview: EnvironmentOverview  # 기존 list 페이지와 동일 source (utilization 3 bar 포함)
    attention: AttentionSignals  # 운영신호 3 카탈로그 (gap/os_eol/agent_unstable)
    base: ReportSummary  # 전체 서버 raw aggregation 결과 (KPI·totals·rows 전부)
    classification_dist: list[ClassificationCount]
    os_distribution: list[OsCount]
    top_risks: list[ReportRowItem]  # base.rows 위험도 정렬 Top N (기본 5)
    summary_bullets_env: list[str]  # 환경 단위 view 별 정성 요약
    # 구성 계층 (P-A) — OS family(Windows/Linux) 구성·워크로드 카테고리 분포 막대. customer·engineer 공통.
    os_family_dist: list[DistributionBar] = field(default_factory=list)
    workload_dist: list[DistributionBar] = field(default_factory=list)
    # 분류된 역할이 없는 호스트 수 (서비스 없음 또는 전부 unknown) — discoverability(#E9)
    workload_unknown_count: int = 0
    # 고객 보고서 의사결정 보조 (mapper precompute, P3) — 모두 기존 분류·신호 단일 진실 재사용.
    evaluated_count: int = 0  # 평가 가능 호스트 수 (데이터 부족 제외) — 분포가 전체에 적용된다는 오해 방지
    # 환경 현황 메트릭 카드 5축 (engineer) — {label, value, sub} plain dict (스냅샷 복원 불요, trend 동일).
    env_metrics: list[dict] = field(default_factory=list)
    os_eol_count: int = 0  # OS 지원 종료 호스트 수 (attention.os_eol_warnings len)
    # 효율화 검토 대상(과다 프로비저닝·유휴·종료 권장) 호스트 수 + 점유 자원 합 — 전환 비용 논의 입력.
    efficiency_target_count: int = 0
    efficiency_target_vcpus: int = 0
    efficiency_target_memory_gb: float = 0.0
    # 엔지니어 환경 구성 — 에이전트 버전 목록 (중복 제거·정렬). "어디 적용"은 미표시, 버전만 명시.
    agent_versions_label: str = ""
    # 네트워크 토폴로지 (engineer) — ip_internal CIDR 공동소속 그래프. 발행 시점 정적 스냅샷.
    topology: NetworkTopology | None = None
    # 환경 시계열 추이 (engineer) — 발행 모달 time_range 윈도우의 CPU·메모리 평균 버킷. 정적 스냅샷.
    # 차트 JS inline(tojson)용 plain dict: [{"at": iso, "cpu": float|None, "mem": float|None}].
    trend: list[dict] = field(default_factory=list)
    under_provisioned_hosts: list[CapacityWarningItem] = field(default_factory=list)
    # 서비스 구성 — 선택 N대 전체의 워크로드 카테고리별 제품명 집합 (뱃지 + 매칭 서비스명). base.rows 의
    # workload_groups 를 카테고리 기준 merge (mapper 집계, P2). 카테고리 뱃지에 정확히 매칭되는 서비스명 노출.
    service_catalog: list[ServiceCatalogGroup] = field(default_factory=list)
    # 개별 서버 보고서(single) 전용 — ServerDetail 충실 인벤토리 (전체 IP·하드웨어·식별자). 환경·선택은 None.
    server_inventory: "ServerInventory | None" = None
    # 개별 보고서 심화 메트릭 (single engineer) — 마운트별 스토리지·메모리 구성·CPU 분류. 그 외 빈/None.
    volumes: list["VolumeUsage"] = field(default_factory=list)
    memory_breakdown: "MemoryBreakdown | None" = None
    cpu_breakdown: "CpuBreakdown | None" = None
    # 엔지니어 보고서 전용 — 운영 신호 발화 호스트 통합 list (gap / os_eol / agent_unstable).
    attention_hosts: list[AttentionHostItem] = field(default_factory=list)
    # 엔지니어 보고서 전용 — 디스크 capacity 임박 (30일 안 full 위험, linear projection).
    capacity_imminent: list[CapacityImminentItem] = field(default_factory=list)
    # 엔지니어 보고서 전용 — 평가 표본 부족 호스트 (에이전트 점검 대상).
    # 템플릿 P3 회피 precompute count — mapper 가 list len 단일 합성 (#E1 P3).
    top_risks_count: int = 0
    attention_hosts_count: int = 0
    capacity_imminent_count: int = 0
    under_provisioned_hosts_count: int = 0
