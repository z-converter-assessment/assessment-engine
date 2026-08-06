"""Mapper sub-package 공용 상수·타입 카탈로그 (P2 단일 진실).

여러 sub-module 이 공유하는 임계값·카탈로그·타입 alias 만 본 모듈에 둔다. 단일 sub-module 내부에서만
쓰는 상수는 해당 sub-module 내부에 유지 — 본 모듈은 다중 공유 명목으로 한정.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from assessment_engine import recommendation
from assessment_engine.json_types import JsonObject, json_list
from assessment_engine.service_classifier import SERVICE_CATALOG
from assessment_engine.web.services.device_filters import disk_total_bytes, is_virtual_interface
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib
from assessment_engine.web.view_models.server import ServiceBadgeRef

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw

# --- UI 임계값 — base.html body data-attribute 동기화 (#E1 P3 · ADR 0015) ----
# template_setup.py 가 본 상수를 import 해 Jinja2 globals 로 노출 → body data-attribute 단일 진실.
_USAGE_DANGER_PCT = 90  # 사용률 위험 임계 — disk_warning · server detail badge 공통
_USAGE_WARN_PCT = 75  # 사용률 주의 임계

# 보고서 view 분기 — 라우터 Pydantic Literal 정합 (#F3)
type ReportView = Literal["customer", "engineer"]

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
                f"W {rq:.2f}"
                if rq is not None
                else "N/A",  # W 태그 — Linux(실행큐)와 의미·임계 달라 값만으론 구분 불가
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
            "메모리 포화",
            "swap page-out",
            "L 발생" if stats.mem_swap_paging else "L 없음",
            "발생 시",
            True,
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

    coverage_gap(포화 축 미관측)·low_precision(이력<30h·버스티)를 호스트 단위로 노출한다. biased(virtio 구조
    편향)는 disk_io 가 상시 True 라 표시 노이즈여서 노트에서 빼고 다운사이즈 게이트 안에서만 쓴다.
    report·attention 공용.

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


def primary_ip(raw: ReportRowRaw) -> str | None:
    """물리(physical/bond_master) 인터페이스의 첫 IPv4 — API identity.primary_ip. topology/상세와 동일 술어(P2 공용)."""
    for i in raw.net_interfaces or []:
        if is_virtual_interface(i.get("kind")):
            continue
        for a in json_list(i, "addresses"):
            if a.get("family") == "ipv4":
                return a.get("address")
    return None


def spec_display_line(
    cpu_cores: int | None, mem_total_bytes: int | None, block_devices: list[JsonObject] | None
) -> str:
    """정적 배정 사양 한 줄("4코어 · 8.00GB · 100GB") — 서버 목록·환경 자원 평가 compact 표 공용(P2 단일 진실).

    실무정석: 값은 2진(GiB, 2^30)이되 라벨은 "GB"(free -h·df -h·클라우드 콘솔 관습) — OS·RAM·OpenStack
    프로비저닝이 2진 기준이라 30GiB 디스크가 "30GB"로 떨어져 딱 맞음. 각 값 부재는 "—".
    """
    disk_bytes = disk_total_bytes(block_devices or [])
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


def saturation_dict(
    signal: str, value: float | None, threshold: float | None, unit: str, saturated: bool | None
) -> JsonObject:
    """포화 신호 1건 — raw numeric(파싱 계약). network.signals 와 동형, value 미측정 시 null. API 공용."""
    return {
        "signal": signal,
        "value": round(value, 2) if value is not None else None,
        "threshold": threshold,
        "unit": unit,
        "measured": value is not None,
        "saturated": saturated,
    }


def saturation_block(kind: str, stats: recommendation.ResourceStats) -> JsonObject:
    """자원별 포화 신호 — os-aware raw 수치(계약용 numeric). right-sizing/assessment API 공용(P2 단일 진실)."""
    win = stats.os_family == "windows"
    if kind == "cpu":
        rq = stats.cpu_run_queue_p95 if win else stats.procs_running_p95
        val = recommendation.cpu_saturation_index(rq, stats.cpu_cores, stats.os_family)
        thr = (
            recommendation.CPU_RUN_QUEUE_PER_CORE_SATURATION
            if win
            else recommendation.PROCS_RUNNING_PER_CORE_SATURATION
        )
        sig = "Processor Queue Length/core" if win else "run queue (procs_running)/core"
        return saturation_dict(sig, val, thr, "per_core", recommendation.cpu_saturated(stats))
    if kind == "memory":
        if win:
            return saturation_dict(
                "Pages Input/sec",
                stats.mem_pages_input_rate_p95,
                recommendation.WIN_PAGES_INPUT_SATURATION,
                "per_sec",
                recommendation.mem_saturated(stats),
            )
        # Linux swap page-out 은 발생 이벤트(수치 없음) — 판정은 saturated 로.
        sat = recommendation.mem_saturated(stats)
        return {
            "signal": "swap page-out",
            "value": None,
            "threshold": None,
            "unit": "event",
            "measured": sat is not None,
            "saturated": sat,
        }
    # disk_io — await 우선(양 OS), 구세대 viostor 만 큐 폴백.
    if stats.disk_await_p95_ms is not None:
        return saturation_dict(
            "await",
            stats.disk_await_p95_ms,
            recommendation.RS_DISKIO_AWAIT_MS,
            "ms",
            recommendation.disk_io_saturated(stats),
        )
    if stats.disk_queue_p95 is not None:
        return saturation_dict(
            "Avg Disk Queue Length",
            stats.disk_queue_p95,
            recommendation.DISK_QUEUE_PER_DISK_SATURATION,
            "queue",
            recommendation.disk_io_saturated(stats),
        )
    return {
        "signal": "await",
        "value": None,
        "threshold": recommendation.RS_DISKIO_AWAIT_MS,
        "unit": "ms",
        "measured": False,
        "saturated": recommendation.disk_io_saturated(stats),
    }


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


# --- USE Method 도넛 카탈로그 — 대시보드 + 환경 보고서 + 서버 리스트 단일 진실 (T13) ----
# 자원 적정성 상태 enum 1:1 매핑. (key, label, hex, description) 튜플 정렬:
#   under(빨강), over(파랑=주색), idle(회색), optimal(녹색), insufficient_data(옅은회색).
# over 색 = 테마색1(var(--color-title)) 동일 주색 — 활용률 게이지와 같은 파랑, under 빨강과 대비.
# idle = 미사용 상태(수요 거의 0). 종료·통합 조치는 파생 권고 층(상태 아님).
# (분류, 색, 조치 설명). 한국어 분류명은 `recommendation.LABEL_KO` 단일 진실이라 여기 두지 않는다.
# 원소 순서가 곧 도넛 세그먼트 순서이자 서버 목록 드롭다운 option 순서다.
_DONUT_SEGMENT_DEFS: list[tuple[recommendation.Recommendation, str, str]] = [
    ("under_provisioned", "#ef4444", "자원 부족 — 사양 상향 검토"),
    ("over_provisioned", "var(--color-title)", "자원 여유 — 사양 축소 검토"),
    ("idle", "#64748b", "미사용 — 종료·통합 검토"),
    ("optimal", "#22c55e", "적정"),
    ("insufficient_data", "#cbd5e1", "평가 표본 부족"),
]

# 분류 -> 배지 CSS 클래스. 값이 템플릿 클래스명이라 표시 계층 소관이다 (#E1 P2) — 도메인 모듈이
# CSS 를 알 이유가 없다. 한국어 라벨(`recommendation.LABEL_KO`)은 도메인 어휘라 그대로 둔다.
BADGE_CLASS: dict[recommendation.Recommendation, str] = {
    "idle": "rec-idle",
    "over_provisioned": "rec-over_provisioned",
    "under_provisioned": "rec-under_provisioned",
    "optimal": "rec-optimal",
    "insufficient_data": "rec-insufficient_data",
}

# 게이지 테마 단색 = 테마색1 CSS 변수 (base.html :root --color-title). 활용률 게이지 + Right-sizing 분류 막대 단일 통일.
# CSS background 는 var 직접, SVG stroke 는 inline style 로 적용(presentation attribute 는 var 미지원). 테마 변경 시 자동 추종.
UTIL_GAUGE_COLOR = "var(--color-title)"

# list 페이지 dropdown (value, 한글 라벨) 쌍 — value=영어 enum(필터 매칭 data-classification),
# 표시=recommendation.LABEL_KO 한글.
PROVISIONING_CLASS_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (key, recommendation.LABEL_KO[key]) for key, _, _ in _DONUT_SEGMENT_DEFS
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
