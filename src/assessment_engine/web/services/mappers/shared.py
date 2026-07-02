"""Mapper sub-package 공용 상수·타입 카탈로그 (P2 단일 진실).

여러 sub-module 이 공유하는 임계값·카탈로그·타입 alias 만 본 모듈에 둔다. 단일 sub-module 내부에서만
쓰는 상수는 해당 sub-module 내부에 유지 — 본 모듈은 다중 공유 명목으로 한정.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from assessment_engine import recommendation
from assessment_engine.service_classifier import SERVICE_CATALOG
from assessment_engine.web.view_models.server import ServiceBadgeRef

# ─── UI 임계값 — base.html body data-attribute 동기화 (#E1 P3 · ADR 0015) ────
# template_setup.py 가 본 상수를 import 해 Jinja2 globals 로 노출 → body data-attribute 단일 진실.
_USAGE_DANGER_PCT = 90  # 사용률 위험 임계 — disk_warning · server detail badge 공통
_USAGE_WARN_PCT = 75  # 사용률 주의 임계
_SWAP_DANGER_PCT = 0.1  # 스왑 사용 자체가 이슈 — 0.1% 도 빨강 (JS metrics.js 동일)

# 보고서 view 분기 — 라우터 Pydantic Literal 정합 (#F3)
ReportView = Literal["customer", "engineer"]

# 자원 부족 원인 라벨 — trigger key -> os-neutral 축 이름 (단일 진실, P2). attention capacity 카드 active_causes·
# environment_report 원인 집계 순서(_UNDER_CAUSE_ORDER = 본 dict 삽입순) 공유. Windows paging/run queue 포화도
# 이 축 이름으로 잡혀 Linux swap/load 로 오라벨 0. dict 삽입순 = 표시·집계 순서.
_CAUSE_LABEL_BY_TRIGGER: dict[str, str] = {
    "cpu_util": "CPU 이용률",
    "cpu_saturation": "CPU 포화",
    "mem_util": "메모리 이용률",
    "mem_saturation": "메모리 포화",
    "disk_capacity": "디스크 용량",
    "disk_io": "디스크 I/O",
}


@dataclass
class SaturationAxisDisplay:
    """os-aware 포화 축 표시 원자 — single_report 포화 축 카드·attention capacity 지표 공용 (P2 표현 단일 소스).

    axis: os-neutral 축 이름(CPU 포화/메모리 포화/디스크 I/O). signal: 해당 OS 측정 신호 이름. value: 형식화
    값('N/A'=미측정). threshold: 임계 표기. measured: 실측 여부. 포화 여부(bool)는 recommendation helper 별도 —
    본 원자는 표시값만(포맷·라벨·임계 문자열을 한 곳에서 결정해 카드/표 간 표기 drift 차단).
    """

    axis: str
    signal: str
    value: str
    threshold: str
    measured: bool


def saturation_axis_displays(stats: recommendation.ResourceStats) -> list[SaturationAxisDisplay]:
    """포화 3축(CPU·메모리·디스크 I/O) os-aware 표시값 — [cpu, mem, disk] 순 (report·attention 단일 진실, P2).

    OS별 측정 신호·형식화·임계 표기를 한 곳에서 결정 — 판정(saturated bool)은 cpu_saturated/mem_saturated/
    disk_io_saturated helper 몫(임계 재계산 없음). 값 스케일은 stats(=build_resource_stats) raw 그대로.
    """
    rec = recommendation
    cores = stats.cpu_cores
    if stats.os_family == "windows":
        rq = stats.cpu_run_queue_p95 / cores if stats.cpu_run_queue_p95 is not None and cores else None
        return [
            SaturationAxisDisplay(
                "CPU 포화", "Processor Queue Length / core",
                f"{rq:.2f}" if rq is not None else "N/A",
                f">= {rec.CPU_RUN_QUEUE_PER_CORE_SATURATION:g}", rq is not None,
            ),
            SaturationAxisDisplay(
                "메모리 포화", "Memory Pages/sec p95",
                f"{stats.mem_paging_rate_p95:.0f}/s" if stats.mem_paging_rate_p95 is not None else "N/A",
                f">= {rec.MEM_PAGING_RATE_SATURATION:g}/s", stats.mem_paging_rate_p95 is not None,
            ),
            SaturationAxisDisplay(
                "디스크 I/O", "Avg Disk Queue Length p95",
                f"{stats.disk_queue_p95:.2f}" if stats.disk_queue_p95 is not None else "N/A",
                f">= {rec.DISK_QUEUE_PER_DISK_SATURATION:g}", stats.disk_queue_p95 is not None,
            ),
        ]
    ld = stats.cpu_load_15m_max / cores if stats.cpu_load_15m_max is not None and cores else None
    return [
        SaturationAxisDisplay(
            "CPU 포화", "load avg / core", f"{ld:.2f}" if ld is not None else "N/A",
            f">= {rec.CPU_SATURATION_LOAD_RATIO:g}", ld is not None,
        ),
        SaturationAxisDisplay("메모리 포화", "swap page-out", "발생" if stats.swap_used else "없음", "발생 시", True),
        SaturationAxisDisplay(
            "디스크 I/O", "iowait p95",
            f"{stats.iowait_p95_pct:.1f}%" if stats.iowait_p95_pct is not None else "N/A",
            f">= {rec.IOWAIT_UPSIZE_PCT:g}%", stats.iowait_p95_pct is not None,
        ),
    ]


def build_confidence_notes(assessment: recommendation.Assessment) -> list[str]:
    """분류 confidence 단서 라벨 — is_partial(saturation 축 미관측) + low_sample(표본 부족) 통합 (원칙2, P2).

    분류는 가진 데이터로 완결(원칙1)하고, 신뢰도를 떨어뜨리는 요인만 본 채널로 분리 노출. 보고서 행·대시보드
    '자원 부족 상세' 카드가 동일 list 를 렌더(P3) — 비면 신뢰도 저하 요인 없음. report·attention 공용 단일 진실.
    """
    notes: list[str] = []
    if assessment.is_partial:
        notes.append("포화 수치 미관측")
    if assessment.low_sample:
        # 접미구 명시 — 분류 라벨 "표본 부족"(insufficient_data, LABEL_KO)과의 문자열 충돌 방지.
        # 여기 "표본 부족"은 분류가 아니라 이용률 p95 신뢰도 단서다 (참고자료 _thresholds_reference.html 동일 문구).
        notes.append("표본 부족 — 이용률 신뢰도 낮음")
    return notes


def build_service_badge_reference() -> list[ServiceBadgeRef]:
    """참고자료 — 서비스 뱃지 카탈로그 표시 행 (SERVICE_CATALOG 파생, P2). 카탈로그 1곳 수정이 본 표에 자동 반영(F12).

    services_label = port_names 를 "서비스명(포트/포트)" 인라인으로 (예 "nginx(80/443)"). 포트 없는 카테고리(container)는 desc_ko.
    """
    refs: list[ServiceBadgeRef] = []
    for d in SERVICE_CATALOG:
        named_ports = "·".join(
            f"{name}({'/'.join(str(p) for p in ports)})" for name, ports in d.port_names.items()
        )
        refs.append(
            ServiceBadgeRef(
                category=d.key,
                label_ko=d.label_ko,
                desc_ko=d.desc_ko,
                badge_class=d.badge_class,
                services_label=named_ports or d.desc_ko,
            )
        )
    return refs

# ─── USE Method 도넛 카탈로그 — 대시보드 + 환경 보고서 + 서버 리스트 단일 진실 (T13) ────
# USE Method recommendation enum 1:1 매핑. (key, label, hex, description) 튜플 정렬:
#   under(빨강), over(파랑=주색), idle(회색), shutdown(보라), optimal(녹색), insufficient_data(옅은회색).
# over 색 = 테마색1(var(--color-title)) 동일 주색 — 활용률 게이지와 같은 파랑, under 빨강과 대비.
_DONUT_SEGMENT_DEFS: list[tuple[str, str, str, str]] = [
    ("under_provisioned", "under_provisioned", "#ef4444", "자원 부족 — 사양 상향 검토"),
    ("over_provisioned", "over_provisioned", "var(--color-title)", "자원 여유 — 사양 축소 검토"),
    ("idle", "idle", "#64748b", "사용률 매우 낮음 — 용도 재평가"),
    ("shutdown", "shutdown", "#9333ea", "사실상 미사용 — 종료 검토"),
    ("optimal", "optimal", "#22c55e", "적정"),
    ("insufficient_data", "insufficient_data", "#cbd5e1", "평가 표본 부족"),
]

# 게이지 테마 단색 = 테마색1 CSS 변수 (base.html :root --color-title). 활용률 게이지 + Right-sizing 분류 막대 단일 통일.
# CSS background 는 var 직접, SVG stroke 는 inline style 로 적용(presentation attribute 는 var 미지원). 테마 변경 시 자동 추종.
UTIL_GAUGE_COLOR = "var(--color-title)"

# USE Method recommendation enum -> donut segment key (식별 매핑, 라벨·색은 _DONUT_SEGMENT_DEFS 단일 진실).
_DONUT_SEGMENT_FROM_REC: dict[str, str] = {
    "under_provisioned": "under_provisioned",
    "over_provisioned": "over_provisioned",
    "idle": "idle",
    "shutdown": "shutdown",
    "optimal": "optimal",
    "insufficient_data": "insufficient_data",
}

# list 페이지 dropdown option — _DONUT_SEGMENT_DEFS 순서 그대로.
PROVISIONING_CLASSES: tuple[str, ...] = tuple(key for key, _, _, _ in _DONUT_SEGMENT_DEFS)

# list 페이지 dropdown (value, 한글 라벨) 쌍 — value=영어 enum(필터 매칭 data-classification),
# 표시=recommendation.LABEL_KO 한글.
PROVISIONING_CLASS_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (key, recommendation.LABEL_KO.get(key, key)) for key, _, _, _ in _DONUT_SEGMENT_DEFS
)

# ─── 보고서·환경 보고서 공용 capacity 임박 임계 ───
# build_report_summary_bullets (report.py) + _extract_capacity_imminent (environment_report.py).
_CAPACITY_IMMINENT_DAYS = 30

# OS family 표시 라벨 — 보고서(report.py)·환경 보고서(environment_report.py) 공유.
OS_FAMILY_LABEL_KO: dict[str, str] = {"linux": "Linux", "windows": "Windows", "unknown": "미상"}

# risk_level 정렬 우선순위 (위험 우선, 낮을수록 먼저) — N대 비교 표(report.py)·환경 분포(environment_report.py) 공유.
# 미지 키는 맨 뒤(99). risk_level 은 4값 고정이라 default 는 방어값.
RISK_LEVEL_ORDER: dict[str, int] = {"high": 0, "attention": 1, "low_usage": 2, "normal": 3}

# 진단 time_range -> 한국어 표시 라벨 (보고서·대시보드·이력 공용). 표시 라벨이라 mapper 소속
# (도메인 상수 DiagnosticTimeRange/DIAGNOSTIC_RANGE_DAYS 는 repo base_diagnostic_repository 유지).
DIAGNOSTIC_RANGE_LABEL_KR: dict[str, str] = {
    "15m": "15분",
    "1h": "1시간",
    "6h": "6시간",
    "24h": "1일",
    "7d": "7일",
    "14d": "14일",
    "30d": "30일",
}

# ─── OS EOL — endoflife.date 스냅샷 카탈로그 기반 (scripts/snapshot_os_eol.py 생성) ───
# 정적 JSON 을 모듈 로드 시 1회 읽음. 런타임 외부 의존 0 (폐쇄 내부망 #A0). 갱신 = 스냅샷 재실행 + commit.
# 신뢰성: endoflife.date 는 벤더 공식 문서 기반 + 분기 검토 (ADR 0031). 미등록 OS 는 침묵 (의식적 한계).
_EOL_CATALOG_PATH = Path(__file__).parent / "os_eol_catalog.json"
_EOL_CATALOG: dict = json.loads(_EOL_CATALOG_PATH.read_text(encoding="utf-8"))

# agent os_id(/etc/os-release ID) -> endoflife product slug. 대부분 동일, 예외만 명시.
# 미등록 os_id 는 None (EOL 판정 불가 침묵). windows 는 build 기반이라 본 dict 밖 별도 분기.
_OS_ID_TO_EOL_PRODUCT: dict[str, str] = {
    "debian": "debian",
    "ubuntu": "ubuntu",
    "rhel": "rhel",
    "rocky": "rocky-linux",
    "almalinux": "almalinux",
    "centos": "centos",
    "sles": "sles",
    "opensuse": "opensuse",
    "amzn": "amazon-linux",
    "fedora": "fedora",
}

# ─── OS distro 필터 ───
# endoflife.date 카탈로그 product 전체를 OS 필터 옵션으로 (수집 무관 — 지원 distro 노출, ADR 0031 출처).
# 수집 os_id <-> distro(product slug) 정규화. windows 는 os_id=="windows" -> windows-server.
_DISTRO_LABELS: dict[str, str] = {
    "debian": "Debian",
    "ubuntu": "Ubuntu",
    "rhel": "RHEL",
    "rocky-linux": "Rocky Linux",
    "almalinux": "AlmaLinux",
    "centos": "CentOS",
    "centos-stream": "CentOS Stream",
    "sles": "SLES",
    "opensuse": "openSUSE",
    "amazon-linux": "Amazon Linux",
    "fedora": "Fedora",
    "windows-server": "Windows",
}
# 필터 드롭다운 옵션 — (slug, 라벨). 카탈로그(_EOL_CATALOG) 키 순서 단일 진실.
DISTRO_FILTER_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (slug, _DISTRO_LABELS.get(slug, slug)) for slug in _EOL_CATALOG
)


def os_id_to_distro(os_id: str | None) -> str:
    """수집 os_id -> 카탈로그 distro(product slug). windows 별도, linux 는 _OS_ID_TO_EOL_PRODUCT, 미등록은 그대로."""
    if not os_id:
        return ""
    if os_id == "windows":
        return "windows-server"
    return _OS_ID_TO_EOL_PRODUCT.get(os_id, os_id)


def resolve_os_eol(
    os_id: str | None,
    os_version: str | None,
    kernel_version: str | None,
    today: date,
) -> tuple[str, str] | None:
    """OS EOL 단일 판정 — 카탈로그 조회 + EOL 이미 경과면 (eol_iso, 제품 라벨), 아니면 None.

    attention OS EOL 카드 + 보고서 정성 요약 공용 (P2 단일 판정 — 두 표시 경로 일관).
    - Windows: os_id=="windows" -> windows-server 카탈로그, kernel build == latest build 매칭
      (운영=Server 가정, build <-> 제품 1:1). kernel_version "26100.8457" -> build "26100".
    - Linux: os_id -> product slug, os_version == cycle 또는 startswith(cycle+".") (rocky "9.7" -> "9").
    EOL 미도래(아직 지원 중)는 None — 카탈로그 등록만으로 발화하면 미래 EOL(Server 2025=2034) 오발화.
    카탈로그 미등록 OS 도 None (EOL 판정 불가 = 침묵, false negative 한계는 의식적 트레이드오프).
    """
    if not os_id:
        return None

    if os_id == "windows":
        build = (kernel_version or "").split(".")[0]
        for entry in _EOL_CATALOG.get("windows-server", []):
            if entry.get("build") == build:
                eol_iso = entry["eol"]
                if date.fromisoformat(eol_iso) > today:
                    return None
                return (eol_iso, f"Windows Server {entry['cycle']}")
        return None

    product = _OS_ID_TO_EOL_PRODUCT.get(os_id)
    if product is None:
        return None
    ver = os_version or ""
    for entry in _EOL_CATALOG.get(product, []):
        cycle = entry["cycle"]
        if ver == cycle or ver.startswith(cycle + "."):
            eol_iso = entry["eol"]
            if date.fromisoformat(eol_iso) > today:
                return None
            label = " ".join(p for p in [os_id, os_version] if p) or "-"
            return (eol_iso, label)
    return None


# 레거시 Windows Server (build <= 9600) kernel build -> 표시용 버전 라벨.
# agent 가 Server 세대 os_version 을 빈값으로 보낸다(DisplayVersion/ReleaseId 레지스트리 키가 Server 2016
# 이전엔 부재) -> 표시가 "windows" 로만 나옴. kernel build(CurrentBuildNumber, 모든 Windows 존재)로 보강.
# build <-> 제품 출처는 resolve_os_eol 과 동일 카탈로그(windows-server), 표시 라벨만 별도(같은 build 가 여러
# SP/edition 공유 — 노이즈 제외, 운영=Server 가정). 2016(14393)+ 은 미등록(레거시 한정, os_version 보강 범위 결정).
_WINDOWS_LEGACY_BUILD_LABEL: dict[str, str] = {
    "9600": "2012 R2",
    "9200": "2012",
    "7601": "2008 R2",
    "6003": "2008",
    "3790": "2003",
    "2195": "2000",
}


def windows_legacy_version_from_build(kernel_version: str | None) -> str | None:
    """레거시 Windows Server(build <= 9600) kernel build -> 표시 버전 라벨. 비레거시·미매칭은 None."""
    if not kernel_version:
        return None
    build = kernel_version.split(".")[0]
    return _WINDOWS_LEGACY_BUILD_LABEL.get(build)


def format_net_rate(kbps_total: float | None) -> str | None:
    """환경 합산 네트워크 rate(kB/s) -> 표시 문자열 — 실시간 현재 자원 현황·보고서 환경 현황 공용 단일 진실.

    1024 kB/s 이상은 MB/s 승급. None(표본 부재)은 None — 호출자가 placeholder 처리.
    단위 표기는 차트(chart-utils fmtKbChart)·참조 문서와 동일한 "kB/s"/"MB/s" 관습으로 통일.
    """
    if kbps_total is None:
        return None
    if kbps_total >= 1024:
        return f"{kbps_total / 1024:.1f} MB/s"
    return f"{kbps_total:.1f} kB/s"
