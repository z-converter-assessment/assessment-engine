"""Mapper sub-package 공용 상수·타입 카탈로그 (P2 단일 진실).

여러 sub-module 이 공유하는 임계값·카탈로그·타입 alias 만 본 모듈에 둔다. 단일 sub-module 내부에서만
쓰는 상수는 해당 sub-module 내부에 유지 — 본 모듈은 다중 공유 명목으로 한정.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, NamedTuple

from assessment_engine import recommendation
from assessment_engine.service_classifier import SERVICE_CATALOG
from assessment_engine.web.services.device_filters import disk_total_bytes, is_virtual_interface
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib
from assessment_engine.web.view_models.server import ServiceBadgeRef

# ─── UI 임계값 — base.html body data-attribute 동기화 (#E1 P3 · ADR 0015) ────
# template_setup.py 가 본 상수를 import 해 Jinja2 globals 로 노출 → body data-attribute 단일 진실.
_USAGE_DANGER_PCT = 90  # 사용률 위험 임계 — disk_warning · server detail badge 공통
_USAGE_WARN_PCT = 75  # 사용률 주의 임계

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
    # 단일 게이트 crossing — 이 raw 신호값이 자기 임계를 넘었는가(값·임계와 같은 자리 산출, 단일 진실). 종합
    # 판정(dual-gate: 이용률 AND 포화)은 cpu_saturated 등 별도 — 축 표시는 신호 자체가 임계 넘었는지(비정상)를 보임.
    crossed: bool = False


def saturation_axis_displays(stats: recommendation.ResourceStats) -> list[SaturationAxisDisplay]:
    """포화 3축(CPU·메모리·디스크 I/O) os-aware 표시값 — [cpu, mem, disk] 순 (report·attention 단일 진실, P2).

    OS별 측정 신호·형식화·임계 표기를 한 곳에서 결정 — 판정(saturated bool)은 cpu_saturated/mem_saturated/
    disk_io_saturated helper 몫(임계 재계산 없음). 값 스케일은 stats(=build_resource_stats) raw 그대로.
    """
    rec = recommendation
    cores = stats.cpu_cores
    await_ms = stats.disk_await_p95_ms
    disk_over = await_ms is not None and await_ms > rec.RS_DISKIO_AWAIT_MS
    if stats.os_family == "windows":
        rq = stats.cpu_run_queue_p95 / cores if stats.cpu_run_queue_p95 is not None and cores else None
        pages = stats.mem_pages_input_rate_p95
        return [
            SaturationAxisDisplay(
                "CPU 포화",
                "Processor Queue Length / core",
                f"W {rq:.2f}" if rq is not None else "N/A",  # W 태그 — Linux(실행큐)와 의미·임계 달라 값만으론 구분 불가
                f">= {rec.CPU_RUN_QUEUE_PER_CORE_SATURATION:g}",
                rq is not None,
                crossed=rq is not None and rq >= rec.CPU_RUN_QUEUE_PER_CORE_SATURATION,
            ),
            SaturationAxisDisplay(
                "메모리 포화",
                "Memory Pages Input/sec p95",
                f"W {pages:.0f}/s" if pages is not None else "N/A",
                f">= {rec.WIN_PAGES_INPUT_SATURATION:g}/s",
                pages is not None,
                crossed=pages is not None and pages >= rec.WIN_PAGES_INPUT_SATURATION,
            ),
            SaturationAxisDisplay(
                "디스크 I/O 포화",
                "await p95 (IOCTL ReadTime/WriteTime)",
                f"{await_ms:.0f}ms" if await_ms is not None else "N/A",
                f"> {rec.RS_DISKIO_AWAIT_MS:g}ms",
                await_ms is not None,
                crossed=disk_over,
            ),
        ]
    rq = stats.procs_running_p95 / cores if stats.procs_running_p95 is not None and cores else None
    return [
        SaturationAxisDisplay(
            "CPU 포화",
            "run queue (procs_running) / core",
            f"L {rq:.2f}" if rq is not None else "N/A",  # L 태그 — Windows(Processor Queue)와 의미·임계 달라 구분
            f">= {rec.PROCS_RUNNING_PER_CORE_SATURATION:g}",
            rq is not None,
            crossed=rq is not None and rq >= rec.PROCS_RUNNING_PER_CORE_SATURATION,
        ),
        SaturationAxisDisplay(
            "메모리 포화", "swap page-out", "L 발생" if stats.mem_swap_paging else "L 없음", "발생 시", True,
            crossed=stats.mem_swap_paging,
        ),
        SaturationAxisDisplay(
            "디스크 I/O 포화",
            "await p95",
            f"{await_ms:.0f}ms" if await_ms is not None else "N/A",
            f"> {rec.RS_DISKIO_AWAIT_MS:g}ms",
            await_ms is not None,
            crossed=disk_over,
        ),
    ]


def build_host_confidence_notes(host: recommendation.HostAssessment) -> list[str]:
    """호스트 confidence 단서 라벨 (신 모델 rollup_host 기반) — 자원별 ConfidenceNote 를 호스트 단위 OR 종합.

    구 단일-assess 대비 rollup_host 기반 — coverage_gap(포화 축 미관측)·low_precision(이력<30h·버스티)를
    호스트 단위로 노출. biased(virtio 구조 편향)는 disk_io 가 상시 True 라 표시 노이즈 -> 노트 제외
    (다운사이즈 게이트 내부용). report·attention 이 rollup_host 로 이관 후 본 함수 공용.

    '창 대비 관측 부족'은 30h 절대 바닥(low_precision)과 별개 축 — 선택 창을 다 못 덮으면(예: 14일 창에
    2일 데이터) sample_sufficiency 가 낮아 발화. 짧은 창으로 판정 시 저커버리지를 정직하게 노출.
    """
    notes: list[str] = []
    if recommendation.host_saturation_unmeasured(host):  # 포화 축(cpu·mem·disk_io) 한정 — 용량·네트워크 제외
        notes.append("포화 수치 미관측")
    if any(r.confidence.low_precision for r in host.resources.values()):
        notes.append("표본 부족")
    if host.sample_sufficiency is not None and host.sample_sufficiency < recommendation.RS_DOWNSIZE_MIN_SUFFICIENCY:
        notes.append("창 대비 관측 부족")
    return notes


def primary_ip(raw) -> str | None:
    """물리(physical/bond_master) 인터페이스의 첫 IPv4 — API identity.primary_ip. topology/상세와 동일 술어(P2 공용)."""
    for i in raw.net_interfaces or []:
        if is_virtual_interface(i.get("kind")):
            continue
        for a in i.get("addresses") or []:
            if a.get("family") == "ipv4":
                return a.get("address")
    return None


def spec_display_line(cpu_cores: int | None, mem_total_bytes: int | None, block_devices: list[dict] | None) -> str:
    """정적 배정 사양 한 줄("4코어 · 8.00GB · 100GB") — 서버 목록·환경 자원 평가 compact 표 공용(P2 단일 진실).

    실무정석: 값은 2진(GiB, 2^30)이되 라벨은 "GB"(free -h·df -h·클라우드 콘솔 관습) — OS·RAM·OpenStack
    프로비저닝이 2진 기준이라 30GiB 디스크가 "30GB"로 떨어져 딱 맞음. 각 값 부재는 "—".
    """
    disk_bytes = disk_total_bytes(block_devices)
    # 메모리 = RAM 계열 bytes_to_gib(1dp, 카탈로그 단일진실 — 인라인 나눗셈 대신 함수 경유로 base 변경 시 한 곳).
    # 디스크 = bytes_to_gb(카탈로그). compact 표는 정수 표기(.0f)라 두 함수의 소수 자릿수는 포맷이 덮음.
    mem_gib = bytes_to_gib(mem_total_bytes)
    disk_gb = bytes_to_gb(disk_bytes) if disk_bytes else None
    return " · ".join(
        [
            f"{cpu_cores}코어" if cpu_cores else "—",
            f"{mem_gib:.1f}GB" if mem_gib else "—",
            f"{disk_gb:.0f}GB" if disk_gb else "—",
        ]
    )


def resource_confidence_notes(c: recommendation.ConfidenceNote) -> list[str]:
    """자원별 신뢰도 하향 사유 — biased(virtio 구조 편향)는 상시라 노이즈로 제외. right-sizing/assessment API 공용."""
    notes: list[str] = []
    if c.low_precision:
        notes.append("표본 부족")
    if c.coverage_gap:
        notes.append("포화 수치 미관측")
    if c.nonstationary:
        notes.append("상승 추세")
    return notes


def saturation_dict(signal: str, value: float | None, threshold: float | None, unit: str, saturated: bool | None) -> dict:
    """포화 신호 1건 — raw numeric(파싱 계약). network.signals 와 동형, value 미측정 시 null. API 공용."""
    return {
        "signal": signal,
        "value": round(value, 2) if value is not None else None,
        "threshold": threshold,
        "unit": unit,
        "measured": value is not None,
        "saturated": saturated,
    }


def saturation_block(kind: str, stats) -> dict:
    """자원별 포화 신호 — os-aware raw 수치(계약용 numeric). right-sizing/assessment API 공용(P2 단일 진실)."""
    win = stats.os_family == "windows"
    if kind == "cpu":
        rq = stats.cpu_run_queue_p95 if win else stats.procs_running_p95
        val = recommendation.cpu_saturation_index(rq, stats.cpu_cores, stats.os_family)
        thr = recommendation.CPU_RUN_QUEUE_PER_CORE_SATURATION if win else recommendation.PROCS_RUNNING_PER_CORE_SATURATION
        sig = "Processor Queue Length/core" if win else "run queue (procs_running)/core"
        return saturation_dict(sig, val, thr, "per_core", recommendation.cpu_saturated(stats))
    if kind == "memory":
        if win:
            return saturation_dict(
                "Pages Input/sec", stats.mem_pages_input_rate_p95, recommendation.WIN_PAGES_INPUT_SATURATION,
                "per_sec", recommendation.mem_saturated(stats),
            )
        # Linux swap page-out 은 발생 이벤트(수치 없음) — 판정은 saturated 로.
        sat = recommendation.mem_saturated(stats)
        return {"signal": "swap page-out", "value": None, "threshold": None, "unit": "event",
                "measured": sat is not None, "saturated": sat}
    # disk_io — await 우선(양 OS), 구세대 viostor 만 큐 폴백.
    if stats.disk_await_p95_ms is not None:
        return saturation_dict("await", stats.disk_await_p95_ms, recommendation.RS_DISKIO_AWAIT_MS, "ms",
                               recommendation.disk_io_saturated(stats))
    if stats.disk_queue_p95 is not None:
        return saturation_dict("Avg Disk Queue Length", stats.disk_queue_p95, recommendation.DISK_QUEUE_PER_DISK_SATURATION,
                               "queue", recommendation.disk_io_saturated(stats))
    return {"signal": "await", "value": None, "threshold": recommendation.RS_DISKIO_AWAIT_MS, "unit": "ms",
            "measured": False, "saturated": recommendation.disk_io_saturated(stats)}


def build_service_badge_reference() -> list[ServiceBadgeRef]:
    """참고자료 — 서비스 뱃지 카탈로그 표시 행 (SERVICE_CATALOG 파생, P2). 카탈로그 1곳 수정이 본 표에 자동 반영(F12).

    services_label = port_names 를 "서비스명(포트/포트)" 인라인으로 (예 "nginx(80/443)"). 포트 없는 카테고리(container)는 desc_ko.
    """
    refs: list[ServiceBadgeRef] = []
    for d in SERVICE_CATALOG:
        named_ports = "·".join(f"{name}({'/'.join(str(p) for p in ports)})" for name, ports in d.port_names.items())
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
# 자원 적정성 상태 enum 1:1 매핑. (key, label, hex, description) 튜플 정렬:
#   under(빨강), over(파랑=주색), idle(회색), optimal(녹색), insufficient_data(옅은회색).
# over 색 = 테마색1(var(--color-title)) 동일 주색 — 활용률 게이지와 같은 파랑, under 빨강과 대비.
# idle = 미사용 상태(수요 거의 0). 종료·통합 조치는 파생 권고 층(상태 아님).
_DONUT_SEGMENT_DEFS: list[tuple[str, str, str, str]] = [
    ("under_provisioned", "under_provisioned", "#ef4444", "자원 부족 — 사양 상향 검토"),
    ("over_provisioned", "over_provisioned", "var(--color-title)", "자원 여유 — 사양 축소 검토"),
    ("idle", "idle", "#64748b", "미사용 — 종료·통합 검토"),
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
    "optimal": "optimal",
    "insufficient_data": "insufficient_data",
}

# list 페이지 dropdown (value, 한글 라벨) 쌍 — value=영어 enum(필터 매칭 data-classification),
# 표시=recommendation.LABEL_KO 한글.
PROVISIONING_CLASS_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (key, recommendation.LABEL_KO.get(key, key)) for key, _, _, _ in _DONUT_SEGMENT_DEFS
)

# OS family 표시 라벨 — 보고서(report.py)·환경 보고서(environment_report.py) 공유.
OS_FAMILY_LABEL_KO: dict[str, str] = {"linux": "Linux", "windows": "Windows", "unknown": "미상"}

# risk_level 정렬 우선순위 (위험 우선, 낮을수록 먼저) — N대 비교 표(report.py)·환경 분포(environment_report.py) 공유.
# 미지 키는 맨 뒤(99). risk_level 은 4값 고정이라 default 는 방어값.
RISK_LEVEL_ORDER: dict[str, int] = {"high": 0, "attention": 1, "low_usage": 2, "normal": 3}

# 진단 time_range -> 한국어 표시 라벨 (보고서·대시보드·이력 공용). 표시 라벨이라 mapper 소속
# (윈도우 타입·상수 TimeRange/DIAGNOSTIC_RANGE_DAYS 는 db/repositories/query/types.py 단일 진실 #F10).
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
    "ol": "oracle-linux",  # Oracle Linux /etc/os-release ID="ol"
    "oracle": "oracle-linux",  # 일부 배포 변형 alias
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
    "oracle-linux": "Oracle Linux",
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


class OsEolInfo(NamedTuple):
    """OS 지원 종료 판정 결과 — 대표 eol/메인스트림 종료일/라벨/상태. 미매칭(판정 불가)은 None 으로 표현.

    status 4상태 중 unknown 은 여기 안 담김(None) — 담기는 건 eol/extended/supported 3상태:
    - "eol"        연장지원까지 종료(보안 패치 중단). eol 경과.
    - "extended"   메인스트림 지원 종료, 연장지원 단계(보안 패치는 유지). support 경과 + eol 미도래.
    - "supported"  메인스트림 지원 중. support 미도래(또는 support 미수록 + eol 미도래).
    support_iso 는 메인스트림 종료일(있으면) — 현재 Windows Server 만 채움(Linux 카탈로그 미수록 -> None).
    """

    eol_iso: str
    support_iso: str | None
    label: str
    status: str  # "eol" | "extended" | "supported"


def _classify_eol(support_iso: str | None, eol_iso: str, today: date) -> str:
    """support/eol 2 날짜 -> 3상태. support 없으면 eol 단일 판정(Linux — extended 개념 없음)."""
    if date.fromisoformat(eol_iso) <= today:
        return "eol"
    if support_iso and date.fromisoformat(support_iso) <= today:
        return "extended"
    return "supported"


def _eol_info(os_id: str | None, os_version: str | None, kernel_version: str | None, today: date) -> OsEolInfo | None:
    """카탈로그 매칭 + support/eol 2단계 상태 판정 단일 진실. 미매칭(판정 불가)은 None.

    - Windows: os_id=="windows" -> windows-server 카탈로그, kernel build == latest build 매칭
      (운영=Server 가정). kernel_version "26100.8457" -> build "26100". 커널 빌드 하나가 복수 채널에
      겹치면(예: 17763 = SAC "1809-sac" + LTSC "2019") 후보 전체로 정직하게 판정 — 후보 전부 eol 경과면
      "eol", 전부 support 경과면 "extended", 아니면 "supported". 대표 라벨·날짜는 eol 최장(LTSC) 후보로
      표시(불확실하면 과소지원 오판정보다 안전 쪽 — #E9 침묵 원칙과 동일 방향).
    - Linux: os_id -> product slug, os_version == cycle 또는 startswith(cycle+"."). support 미수록이라
      extended 없이 eol/supported 2상태(기존 동작 유지).
    카탈로그 미등록 OS 는 None (판정 불가 = 침묵, false negative 한계는 의식적 트레이드오프).
    """
    if not os_id:
        return None
    if os_id == "windows":
        build = (kernel_version or "").split(".")[0]
        matches = [e for e in _EOL_CATALOG.get("windows-server", []) if e.get("build") == build]
        if not matches:
            return None
        rep = max(matches, key=lambda e: e["eol"])  # 대표 = eol 최장(LTSC) — 표시 라벨·날짜
        all_eol_passed = all(date.fromisoformat(e["eol"]) <= today for e in matches)
        all_support_passed = all(e.get("support") and date.fromisoformat(e["support"]) <= today for e in matches)
        status = "eol" if all_eol_passed else ("extended" if all_support_passed else "supported")
        return OsEolInfo(rep["eol"], rep.get("support"), f"Windows Server {rep['cycle']}", status)
    product = _OS_ID_TO_EOL_PRODUCT.get(os_id)
    if product is None:
        return None
    ver = os_version or ""
    for entry in _EOL_CATALOG.get(product, []):
        cycle = entry["cycle"]
        if ver == cycle or ver.startswith(cycle + "."):
            label = " ".join(p for p in [os_id, os_version] if p) or "-"
            status = _classify_eol(entry.get("support"), entry["eol"], today)
            return OsEolInfo(entry["eol"], entry.get("support"), label, status)
    return None


def resolve_os_eol(
    os_id: str | None,
    os_version: str | None,
    kernel_version: str | None,
    today: date,
) -> tuple[str, str] | None:
    """OS 지원 종료 발화 판정 — 연장지원까지 끝난(보안 패치 중단) 경우만 (eol_iso, 제품 라벨), 아니면 None.

    attention OS EOL 카드 + 보고서 정성 요약 공용 (P2 단일 판정). 연장지원(extended) 단계는 보안 패치가
    유지되므로 여기서 발화하지 않는다 — 발화는 status=="eol"(완전 종료) 한정. extended 는 서버별 상태
    칼럼(지원 중/연장지원/종료)으로만 노출(lookup_os_eol).
    """
    info = _eol_info(os_id, os_version, kernel_version, today)
    if info is None or info.status != "eol":
        return None
    return (info.eol_iso, info.label)


def lookup_os_eol(
    os_id: str | None, os_version: str | None, kernel_version: str | None, today: date
) -> OsEolInfo | None:
    """인벤토리 표시용 EOL 조회 — 미래 EOL·연장지원도 반환. OsEolInfo 또는 미등록 시 None.

    resolve_os_eol(발화용, 완전 종료만)와 달리 시스템 정보 카드·서버 목록 상태 칼럼용 — 아직 지원 중이어도
    종료 예정일과 3상태(supported/extended/eol)를 노출.
    """
    return _eol_info(os_id, os_version, kernel_version, today)


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


# ProductName(CurrentVersion 원문) -> 짧은 표시 라벨. os_version(DisplayVersion)이 LTSC/SAC 를 구분 못 하고
# 동일 값("1809")을 공유하는 한계를 product_name 의 연도/세대 토큰으로 보강 — Server 는 연도(2019·2012 R2),
# client 는 세대(10·11). SAC(Server Core 반기 채널)는 MS 가 ProductName 에 연도를 의도적으로 안 박으므로
# ("Windows Server Datacenter") 미매칭 -> None 반환해 호출자가 os_version 폴백(SAC 는 그쪽이 정확).
_WIN_SERVER_YEAR_RE = re.compile(r"Windows Server (\d{4})(?:\s+(R2))?", re.IGNORECASE)
_WIN_CLIENT_GEN_RE = re.compile(r"Windows (10|11)\b", re.IGNORECASE)


def windows_short_label_from_product_name(product_name: str | None) -> str | None:
    """product_name 원문 -> 짧은 표시 라벨("2019"·"2012 R2"·"10"). SAC(연도 없음)·미매칭은 None(os_version 폴백)."""
    if not product_name:
        return None
    m = _WIN_SERVER_YEAR_RE.search(product_name)
    if m:
        return f"{m.group(1)} R2" if m.group(2) else m.group(1)
    m = _WIN_CLIENT_GEN_RE.search(product_name)
    return m.group(1) if m else None
