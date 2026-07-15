"""Attention 신호·환경 개요 mapper (P2).

운영신호 카드(AttentionSignals) = gap·os_eol·agent_unstable 3개 (`query_service._assemble_attention` 조립) +
환경 활용률 도넛·bar·USE Method 분포·under_provisioned 호스트(EnvironmentOverview) 합성. 책임은 raw 신호 → ViewModel.
capacity(under_provisioned)·disk·days_until_full 은 운영신호가 아니라 USE Method right-sizing 소속 —
to_capacity_warning_item 은 EnvironmentOverview.under_provisioned_hosts 로 간다.
임계 분류·표시 색은 shared.py 또는 본 모듈 상단 상수 단일 진실.
"""

import math
from collections import Counter
from datetime import UTC, date, datetime

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import FleetErrorRaw, MetricGapWarningRaw
from assessment_engine.service_classifier import SIGNATURE_CATEGORIES
from assessment_engine.web.services.device_filters import disk_total_bytes
from assessment_engine.web.services.mappers.report import (
    build_resource_stats,
)
from assessment_engine.web.services.mappers.server import workload_category_counter
from assessment_engine.web.services.mappers.shared import (
    _CAUSE_LABEL_BY_TRIGGER,
    _DONUT_SEGMENT_DEFS,
    UTIL_GAUGE_COLOR,
    build_host_confidence_notes,
    lookup_os_eol,
    resolve_os_eol,
    spec_display_line,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib
from assessment_engine.web.view_models.attention import (
    ActionTargets,
    AttentionRow,
    CapacityWarningItem,
    EnvironmentOverview,
    EnvironmentRealtime,
    FleetErrorItem,
    RealtimeLoadCell,
    RealtimeLoadRow,
    RiskDonutSegment,
    SaturationDonut,
    UtilizationBar,
)

# ─── 표시 임계·색 단일 진실 ────────────────────────────────────────────────
# 운영 신호 단일 active 색 클래스. base.html 단일 진실. 카테고리간 강도 비교 근거 부족 →
# 임계 초과 발화 자체가 시그널이라 단일 색으로 통일.
_ATTN_ACTIVE_BADGE = "attn-active"

# UtilizationBar 게이지 색 — 환경 평균 활용률 도넛/바 단색 (그라데이션·임계 분기 제거).
# 활용률 정도는 게이지 길이(dash_length)로, 색은 값 무관 단일 푸른색. 색으로 임계 의미를 주지
# 않는다 — 위험도 색은 Right-sizing 분류 도넛이 별도 담당.
_UTIL_COLOR_GAUGE = UTIL_GAUGE_COLOR  # 푸른 단색 (blue-500) — shared.UTIL_GAUGE_COLOR 단일 진실
_UTIL_COLOR_NONE = "#cbd5e1"  # 표본 부재 (회색)

# 주요 워크로드 도넛 세그먼트 색 — SIGNATURE_CATEGORIES 대응. base.html .badge-cat-* 뱃지 색의 시각적 쌍둥이
# (SVG stroke 는 hex 필요 — CSS 클래스와 별도 소스, 값 동기화 의무).
_WORKLOAD_COLORS: dict[str, str] = {
    "web": "#2563eb", "db": "#e11d48", "cache": "#059669",
    "mq": "#9333ea", "container": "#0891b2", "monitor": "#ea580c",
}

# 도넛 SVG 원주 — 템플릿 SVG r="42" 와 정합. pct 0~100 을 0~_UTIL_DONUT_CIRC 로 매핑(dash-array precompute).
_DONUT_RADIUS = 42
_UTIL_DONUT_CIRC = 2 * math.pi * _DONUT_RADIUS

# 네트워크 상태 전용 색(orthogonal) — 혼잡만 강조, 정상/미측정은 중립. disk_io_status_color 도 재사용.
_NET_CONGESTED_COLOR = "#dc2626"

# 네트워크 상태 — 사이징과 별개 품질 축(orthogonal). 판정 근거 assess_network(재전송>1% or 드롭>0.5% or
# conntrack>=80% -> 혼잡, monitoring 표준). conntrack 은 서버 상세. host_status 미구동이라 전용 필드로 분리.
_NET_STATUS_LABEL: dict[str, str] = {"quality_ok": "정상", "congested": "혼잡", "unmeasured": "미측정"}


# ─── gap (운영신호) ────────────────────────────────────────────────────────


def to_gap_warning_item(raw: MetricGapWarningRaw, now: datetime) -> AttentionRow:
    """MetricGapWarningRaw → AttentionRow (P2: gap_minutes·badge·표시 string 단일 변환).

    last_metric_at은 KST 표시 위해 meta_at으로 그대로 전달 (KST 변환은 template kst 필터 #F2).
    mount path 없는 카테고리 — mount_path=None.
    """
    gap_min = int((now - raw.last_metric_at).total_seconds() // 60)
    return AttentionRow(
        badge_class=_ATTN_ACTIVE_BADGE,
        badge_text=f"{gap_min}분",
        link_href=f"/servers/{raw.public_id}",
        link_text=raw.hostname,
        meta_text="마지막 수집 ",
        meta_at=raw.last_metric_at,
    )


# ─── 환경 활용률 도넛·bar (UtilizationBar + RiskDonutSegment) ────────────


def _bar_color(pct: float | None) -> str:
    """환경 평균 사용률 게이지 색 — 단색 푸른색 (표본 부재 시 회색).

    활용률 정도는 게이지 길이(dash_length)로 표현하고 색은 값 무관 단일 — 색으로 임계 의미를
    주지 않는다 (Right-sizing 분류 도넛이 위험도 색을 별도 담당).
    """
    if pct is None:
        return _UTIL_COLOR_NONE
    return _UTIL_COLOR_GAUGE


def _dash_length(pct: float | None) -> float:
    if pct is None:
        return 0.0
    return max(0.0, min(pct, 100.0)) / 100.0 * _UTIL_DONUT_CIRC


def _donut_dash(count: float, total: float) -> float:
    """도넛 세그먼트 dash 길이 = 비율 x 원주 (count·total 0 가드). 위험·포화·워크로드 도넛 3종 공용."""
    return (count / total) * _UTIL_DONUT_CIRC if (total > 0 and count > 0) else 0.0


def _donut_pct(count: float, total: float) -> float:
    """도넛 세그먼트 백분율 (total 0 가드)."""
    return round(count / total * 100, 1) if total > 0 else 0.0


def _util_bar(label: str, pct: float | None) -> UtilizationBar:
    """이용률 바 1개 — pct 로 색·dash 파생 (None 안전, _bar_color·_dash_length 가 가드)."""
    return UtilizationBar(label=label, pct=pct, bar_color=_bar_color(pct), dash_length=_dash_length(pct))


def build_risk_donut_segments(risk_counts: dict[str, int]) -> tuple[list, int, int]:
    """카테고리별 카운트 -> (RiskDonutSegment list, total, under_count).

    risk_counts 예: {"under_provisioned": 1, "over_provisioned": 2, "optimal": 7} (키 = _DONUT_SEGMENT_DEFS 6종).
    누락 키는 0으로 취급. dash_length·dash_offset은 누적 비례 계산.
    under_count(자원 부족 카테고리 수)는 EnvironmentOverview.risk_high_count 로 전달되는 요약 신호 —
    risk 분포는 막대(provisioning_dist_bar)로 렌더돼 도넛 중앙 라벨은 없다(옛 중앙 강조 규약 폐기, #E8).
    """
    total = sum(risk_counts.values())
    segments: list = []
    cum_offset = 0.0
    for key, label, _color, description in _DONUT_SEGMENT_DEFS:
        count = risk_counts.get(key, 0)
        dash_length = _donut_dash(count, total)
        pct = _donut_pct(count, total)
        segments.append(
            RiskDonutSegment(
                key=key,
                # 표시 라벨 = right-sizing 한국어 분류명(LABEL_KO 단일 진실).
                # 보고서·대시보드 통일, 영어 enum 노출 금지.
                label=recommendation.LABEL_KO.get(key, label),
                # 색 = 게이지 테마 단색 통일 (분류 막대 — 라벨이 의미 전달, 색 무관). _DONUT_SEGMENT_DEFS 다색 미사용.
                color=UTIL_GAUGE_COLOR,
                count=count,
                pct=pct,
                dash_length=dash_length,
                dash_offset=-cum_offset,  # 음수 offset = 시계방향 이동
                description=description,
            )
        )
        cum_offset += dash_length
    under_count = risk_counts.get("under_provisioned", 0)
    return segments, total, under_count


# 자원 부족(언더 프로비저닝) 상세 표시 상한 — 심각도 상위 N(탑3)만 카드 표시, 전체 수는 count 로 노출.
_UNDER_PROVISIONED_DISPLAY_MAX = 3


def _build_saturation_donut(label: str, count: int, total: int) -> SaturationDonut:
    """포화 도넛 1개 — 채움 = count/total 비율, 색은 이용률 게이지와 동일 단색(색으로 임계 의미 안 줌).

    실시간현황·환경개요 공용 — 스냅샷(realtime)이든 윈도우(overview) 기준이든 시각·게이지색 통일.
    """
    dash = _donut_dash(count, total)
    return SaturationDonut(label=label, count=count, total=total, dash_length=dash, color=_UTIL_COLOR_GAUGE)


def _build_error_fleet(err: FleetErrorRaw | None) -> list[FleetErrorItem]:
    """환경 fleet 에러 표시자 5종 — 창내 발생 호스트 수/표본. err=None(assessment 경로)이면 빈 list."""
    if err is None:
        return []
    t = err.total
    return [
        FleetErrorItem("cpu_mce", "머신체크(MCE)", err.mce_hosts, t, "CPU/메모리 하드웨어 정정불가 오류(machine check)"),
        FleetErrorItem("mem_oom", "OOM Kill", err.oom_hosts, t, "메모리 부족으로 커널이 프로세스 강제 종료"),
        FleetErrorItem("mem_corrupted", "메모리 손상(EDAC)", err.corrupted_hosts, t, "ECC 정정된 하드웨어 메모리 손상"),
        FleetErrorItem("disk_errors", "디스크 에러", err.disk_error_hosts, t, "RAID degraded·파일시스템 손상·IO 오류"),
        FleetErrorItem("net_errors", "NIC 에러", err.net_error_hosts, t, "네트워크 인터페이스 rx/tx 오류 프레임"),
    ]


def _os_eol_summary(details: list, today) -> tuple[int, int, int, int]:
    """OS 지원(EOL) 4상태 종합 -> (지원 종료, 연장지원, 미상, 지원 중). 서버 목록 os_eol_status 와 동일 판정.

    os_id 없는 서버(인벤토리 미수집)는 EOL 종합 대상 아님 — 미상(판정 불가)과 구분해 제외.
    lookup_os_eol: None=미상(카탈로그 미수록·미매칭) / status eol=완전 종료 / extended=연장지원 / supported=지원 중.
    """
    passed = extended = unknown = supported = 0
    for d in details:
        if not d.os_id:
            continue
        info = lookup_os_eol(d.os_id, d.os_version, d.kernel_version, today)
        if info is None:
            unknown += 1
        elif info.status == "eol":
            passed += 1
        elif info.status == "extended":
            extended += 1
        else:
            supported += 1
    return passed, extended, unknown, supported


def _workload_donut_segments(role_sorted: dict[str, int]) -> tuple[list, int]:
    """시그니처 워크로드 인스턴스 분포 -> 누적 도넛 세그먼트 + 총합. 0 카테고리도 세그먼트 유지(범례 노출, E9)."""
    total = sum(role_sorted.values())
    segments: list = []
    cum = 0.0
    for cat, cnt in role_sorted.items():
        dash = _donut_dash(cnt, total)
        segments.append(
            RiskDonutSegment(
                key=cat, label=cat, color=_WORKLOAD_COLORS.get(cat, "#94a3b8"),
                count=cnt, pct=_donut_pct(cnt, total), dash_length=dash, dash_offset=-cum, description="",
            )
        )
        cum += dash
    return segments, total


def build_environment_overview(
    details: list,
    online_count: int,
    utilization=None,
    risk_counts=None,
    under_provisioned_hosts: list | None = None,
    under_limit: int | None = _UNDER_PROVISIONED_DISPLAY_MAX,
    saturation_counts: dict[str, int] | None = None,
    error_summary: FleetErrorRaw | None = None,
):
    """ServerDetail list + online_count + EnvironmentUtilizationRaw + risk_counts -> EnvironmentOverview.

    list 화면 상단 환경 요약 — 총 N대·자원 합계·역할 분포·온라인/오프라인·평균 활용률·위험도 분포·자원 부족 상세.
    utilization=None이면 활용률 빈 list. risk_counts=None이면 위험도 도넛 빈 list.
    under_provisioned_hosts=None이면 빈 list (도넛 아래 상세 sub-block 미표시).
    """
    total = len(details)
    total_vcpus = sum(d.cpu_cores or 0 for d in details)
    total_mem_bytes = sum(d.mem_total_bytes or 0 for d in details)
    # 디스크 총량 — block_devices type=disk size_bytes 합 (양 OS 단일 산식, device_filters).
    total_disk_bytes = sum(disk_total_bytes(d.block_devices or []) for d in details)
    # OS 구성 — os_family(windows/linux) 별 서버 수.
    os_counter: Counter[str] = Counter()
    for d in details:
        os_counter[d.os_family or "unknown"] += 1

    # 주요 워크로드 분포 — 환경 전체 인스턴스 개수(호스트 dedup 아님: 한 서버에 mq 2개면 +2). "환경에 각 워크로드가
    # 몇 개 떠 있냐"가 핵심 정보. container 는 single_instance(런타임 스택 1). role_unknown 은 보고서 집계용 유지.
    role_counter: Counter[str] = Counter()
    role_unknown = 0  # 특징 워크로드 0 호스트 수 — 보고서 workload_unknown_count 용
    for d in details:
        counter = workload_category_counter(d.services, d.listen_ports)
        if counter:
            role_counter.update(counter)  # 인스턴스 합산(카테고리별 값 누적)
        else:
            role_unknown += 1

    util_bars: list = []
    util_bars_p95: list = []
    util_sample = 0
    if utilization is not None:
        util_sample = utilization.sample_size
        util_bars = [
            _util_bar("CPU", utilization.cpu_avg_pct),
            _util_bar("메모리", utilization.mem_avg_pct),
            # "디스크 용량" — fs used%(capacity 축) — 활용률 아님, 용량 명시 (detail 도넛과 일관).
            _util_bar("디스크 용량", utilization.disk_avg_pct),
        ]
        # p95 활용률 — 평균과 동일 capacity-weighted 환경 분포 기반(per_ts 95퍼센타일). CPU·메모리만 —
        # 디스크는 물리디스크/디바이스 인식이 Windows 에서 불완전해 capacity 합이 신뢰 불가라 제외.
        util_bars_p95 = [
            _util_bar("CPU", utilization.cpu_p95_pct),
            _util_bar("메모리", utilization.mem_p95_pct),
        ]

    risk_segments: list = []
    risk_total = 0
    risk_under = 0
    if risk_counts is not None:
        risk_segments, risk_total, risk_under = build_risk_donut_segments(risk_counts)

    # 자원 부족 상세 — 심각도(severity_score) DESC 정렬 후 상위 N(탑3)만 표시(P2). 전체 수 count·표시 수 shown 분리.
    # 마이그레이션 우선순위: swap(paging) > 위반 자원 수 > 최고 활용률. 동률은 hostname ASC tie-break.
    _under_all = sorted(under_provisioned_hosts or [], key=lambda c: (-c.severity_score, c.hostname.lower()))
    _under_shown = _under_all if under_limit is None else _under_all[:under_limit]
    # 시그니처 워크로드만 노출(환경 성격 규정 티어 — SIGNATURE_CATEGORIES). 0 포함(E9) — 인스턴스 개수 desc, 동수는 카탈로그 순서.
    role_sorted = dict(sorted(
        ((cat, role_counter.get(cat, 0)) for cat in SIGNATURE_CATEGORIES),
        key=lambda kv: (-kv[1], SIGNATURE_CATEGORIES.index(kv[0])),
    ))
    # 원형차트 도넛 세그먼트 — 카테고리별 인스턴스 비율(누적 dash). 0 카테고리도 세그먼트 유지(범례 노출, E9).
    workload_segments, _wl_total = _workload_donut_segments(role_sorted)

    # 포화 3축 도넛 — 자원 적정성 창 기준 포화 호스트 카운트/표본 (호출자가 raws 순회로 산출).
    sat_donuts: list = []
    if saturation_counts is not None:
        _sat_total = saturation_counts.get("total", 0)
        sat_donuts = [
            _build_saturation_donut("CPU 포화", saturation_counts.get("cpu", 0), _sat_total),
            _build_saturation_donut("메모리 압박", saturation_counts.get("mem", 0), _sat_total),
            _build_saturation_donut("디스크 I/O 포화", saturation_counts.get("disk_io", 0), _sat_total),
            _build_saturation_donut("네트워크 혼잡", saturation_counts.get("net", 0), _sat_total),
        ]

    _eol_passed, _eol_extended, _eol_unknown, _eol_supported = _os_eol_summary(details, datetime.now(UTC).date())
    return EnvironmentOverview(
        total=total,
        online=online_count,
        offline=total - online_count,
        total_vcpus=total_vcpus,
        total_memory_gb=bytes_to_gib(total_mem_bytes) or 0.0,
        total_disk_gb=int(bytes_to_gb(total_disk_bytes) or 0),
        # count 내림차순 + 동count는 이름 오름차순 tie-break (most_common 동순위는 삽입순=DB row 순서라 비결정적).
        os_distribution=dict(sorted(os_counter.items(), key=lambda kv: (-kv[1], kv[0]))),
        role_distribution=role_sorted,
        workload_donut=workload_segments,
        workload_total=_wl_total,
        role_unknown_count=role_unknown,
        utilization=util_bars,
        utilization_p95=util_bars_p95,
        util_sample_size=util_sample,
        risk_donut=risk_segments,
        risk_donut_total=risk_total,
        risk_high_count=risk_under,
        under_provisioned_hosts=_under_shown,
        under_provisioned_hosts_count=len(_under_all),
        under_provisioned_hosts_shown=len(_under_shown),
        saturation_donuts=sat_donuts,
        error_fleet=_build_error_fleet(error_summary),
        os_eol_passed=_eol_passed,
        os_eol_extended=_eol_extended,
        os_eol_unknown=_eol_unknown,
        os_eol_supported=_eol_supported,
    )


def build_environment_realtime(
    total: int,
    online: int,
    snapshots: list[dict],
    last_collected_at,
) -> EnvironmentRealtime:
    """온라인 서버 최신 스냅샷 snapshots -> EnvironmentRealtime.

    호출자가 온라인 서버만 snapshots 로 전달 (오프라인 stale 메트릭 제외 — sample_size = len(snapshots)).
    snapshots 키: hostname/public_id/os_family + 부하표용 서버별 값(cpu_pct/mem_pct/cpu_sat_index/disk_sat_index/
                disk_util_pct/paging_rate/net_kbps) + capacity-weighted 가중치(cpu_cores·mem_used_bytes·mem_total_bytes).
    utilization: CPU/메모리 capacity-weighted 평균 도넛 2개. 디스크 용량(fill%)은 느린 누적 축이라 여전히 제외.
                디스크 I/O 이용률(worst device busy%)은 장치 종류별 신뢰도 편차(SSD/NVMe 병렬 처리라 여유 있어도
                100%로 오탐 가능, right-sizing-thresholds.md "Disk IO" 절 Gregg 근거)라 환경 평균 도넛으로
                안 묶는다 — load_rows 칼럼으로만 호스트별 raw 값 노출(판정·집계 없음).
    load_rows: 7축(CPU·메모리 이용률 + 실행 큐·페이징·디스크 이용률·디스크 응답지연·네트워크) 호스트당 1행 전체 —
                이용률 도넛 2개 + 신호 도넛 4개와 겹치되 디스크 이용률만 표 전용(도넛 미보유). 디스크 이용률
                (Utilization)·응답지연(Saturation)은 USE Method상 별개 축 — 이용률 0%(유휴 실측)와 응답지연
                미측정("—", I/O 0건이라 await 계산 불가)이 같은 호스트에 동시에 나타날 수 있음(모순 아님).
                페이징은 무정규화 raw rate라 OS별 원 지표·임계 상이 — 값 앞 L(Linux)/W(Windows) 접두(_os_cell,
                single_report 포화 축 카드의 shared.saturation_axis_displays 표기 관례와 동일). 실행 큐는 지수
                정규화(값/threshold)돼 있어 OS 무관 비교 가능 — 접두 없음.
    """

    # capacity-weighted 평균 — 환경 전체 자원 풀 활용률(단순 산술평균 X). environment_utilization SQL 과 동일 정의:
    #   CPU = Σ(usage%·cores)/Σcores (시점 usage 라 jiffies delta 대신 코어 가중 근사),
    #   mem = Σused_bytes/Σtotal_bytes, disk = Σused_gb/Σtotal_gb (전 mount 통합, worst mount 아님).
    def _cap_weighted(value_key: str, weight_key: str) -> float | None:
        num = sum(s[value_key] * s[weight_key] for s in snapshots if s.get(value_key) is not None and s.get(weight_key))
        den = sum(s[weight_key] for s in snapshots if s.get(value_key) is not None and s.get(weight_key))
        return round(num / den, 1) if den else None

    def _ratio(used_key: str, total_key: str) -> float | None:
        used = sum(s[used_key] for s in snapshots if s.get(used_key) is not None and s.get(total_key))
        total = sum(s[total_key] for s in snapshots if s.get(used_key) is not None and s.get(total_key))
        return round(used / total * 100, 1) if total else None

    avg_cpu = _cap_weighted("cpu_pct", "cpu_cores")
    avg_mem = _ratio("mem_used_bytes", "mem_total_bytes")
    # 디스크 용량(fill%)은 실시간 신호가 아니라(느린 누적 축) 제외. 디스크 I/O 이용률(worst device busy%)은
    # 호스트마다 개별 판단이 필요한 raw 신호라(장치 종류별 신뢰도 편차, right-sizing-thresholds.md "Disk IO" 절)
    # 환경 전체 평균 도넛으로는 안 묶고 서버별 실시간 부하 표의 칼럼(disk_util)으로만 노출 — CPU·메모리만 도넛.
    util_bars = [
        _util_bar("CPU", avg_cpu),
        _util_bar("메모리", avg_mem),
    ]

    def _net_status_cell(congested: bool) -> RealtimeLoadCell:
        """네트워크 칼럼 — 처리량(kbps) 아닌 혼잡 판정(net_signal_active)만 표시.

        처리량은 판정 대상(재전송·드롭·conntrack)과 다른 원자료라 칼럼에 임계를 적을 수 없었음(사이징
        임계도 없음, right-sizing-thresholds.md) — 아예 판정 결과(정상/혼잡)로 바꿔 표시-판정 일치.
        """
        if congested:
            return RealtimeLoadCell(value=1.0, display="혼잡", color=_NET_CONGESTED_COLOR)
        return RealtimeLoadCell(value=0.0, display="정상")

    def _cell(value, fmt, exceeded: bool = False) -> RealtimeLoadCell:
        """exceeded=True 면 신호 도넛과 동일 임계 초과 강조(빨강, _NET_CONGESTED_COLOR 재사용 — 동일 의미=동일
        hex, E8). 임계 없는 축(CPU·메모리 이용률, 디스크 이용률)은 기본값 False 로 무강조 유지."""
        if value is None:
            return RealtimeLoadCell(value=None, display="—")
        return RealtimeLoadCell(value=value, display=fmt(value), color=_NET_CONGESTED_COLOR if exceeded else "")

    def _os_tag(os_family: str | None) -> str:
        return "W" if os_family == "windows" else "L"

    def _os_cell(value, os_family, fmt, exceeded: bool = False) -> RealtimeLoadCell:
        """페이징 전용 — 값 앞 L/W 접두(shared.saturation_axis_displays 표기 관례).

        무정규화 raw rate라 OS 무관 해석 불가 — Linux refault(any>0 압박) vs Windows Pages Input/sec
        (>=20 압박), 같은 숫자가 다른 의미. 실행 큐는 지수 정규화(값/threshold, >=1.0 포화)로 이미
        OS 무관 비교 가능해 접두 불필요(원 카운터가 달라도 정규화된 지수는 동일 척도).

        fmt 는 소수점 2자리(.2f) 고정 의무 — Linux 임계가 "> 0"(정수 반올림이면 0.03/s 같은 실측이 "0"으로
        묻혀 페이징 신호 도넛 카운트와 표 값이 안 맞아 보임, 판정 근거가 표에서 안 드러남).
        exceeded 는 호출자가 mem_pressure_active(OS-aware 판정, 페이징 신호 도넛과 동일 원자료)로 넘겨준다 —
        여기서 재계산 안 함(drift 방지).
        """
        if value is None:
            return RealtimeLoadCell(value=None, display="—")
        color = _NET_CONGESTED_COLOR if exceeded else ""
        return RealtimeLoadCell(value=value, display=f"{_os_tag(os_family)} {fmt(value)}", color=color)

    # 서버별 실시간 부하 표 — 현황 도넛(이용률 2 + 포화/압박 4)과 겹치는 6축 + 디스크 이용률(표 전용, 도넛 없음)
    # 총 7축을 호스트당 1행으로 통합(top-N 절단 없음, 서버 목록과 동일 sortable-table 관례). 유휴(0)도 그대로
    # 노출 — 칼럼 정렬로 원하는 축 랭킹을 본다.
    # 포화 지수(실행 큐·응답 지연)는 임계 정규화(>=1.0 포화)라 "x" 표기, OS 무관 비교 가능해 접두 없음.
    # 페이징은 무정규화 raw rate라 OS별 원 지표·임계 상이 — 값 앞 L/W 접두(_os_cell). hostname 오름차순 기본
    # 정렬(칼럼 클릭 정렬은 클라 TableUtils).
    load_rows = sorted(
        (
            RealtimeLoadRow(
                hostname=s["hostname"],
                public_id=s["public_id"],
                cpu=_cell(s.get("cpu_pct"), lambda v: f"{v:.1f}%"),
                mem=_cell(s.get("mem_pct"), lambda v: f"{v:.1f}%"),
                run_queue=_cell(
                    s.get("cpu_sat_index"), lambda v: f"{v:.2f}x", exceeded=(s.get("cpu_sat_index") or 0) >= 1.0
                ),
                paging=_os_cell(
                    s.get("paging_rate"), s.get("os_family"), lambda v: f"{v:.2f}/s",
                    exceeded=bool(s.get("mem_pressure")),
                ),
                disk_util=_cell(s.get("disk_util_pct"), lambda v: f"{v:.0f}%"),
                disk_io=_cell(
                    s.get("disk_sat_index"), lambda v: f"{v:.2f}x", exceeded=(s.get("disk_sat_index") or 0) >= 1.0
                ),
                network=_net_status_cell(bool(s.get("net_congested"))),
            )
            for s in snapshots
        ),
        key=lambda r: r.hostname,
    )

    # 신호 임계 초과 도넛 — 순간 단일신호(이용률 게이트 없음)라 라벨을 신호명으로(환경개요 dual-gate "포화" 도넛과 구분).
    # "포화" 판정어는 dual-gate(자원 평가)에만 쓴다 — 여기 카운트는 임계 초과 신호 호스트 수(순간 스냅샷).
    sample = len(snapshots)
    cpu_sat_count = sum(1 for s in snapshots if (s.get("cpu_sat_index") or 0) >= 1.0)
    disk_sat_count = sum(1 for s in snapshots if (s.get("disk_sat_index") or 0) >= 1.0)
    mem_pressure_count = sum(1 for s in snapshots if s.get("mem_pressure"))
    net_congested_count = sum(1 for s in snapshots if s.get("net_congested"))
    saturation_donuts = [
        _build_saturation_donut("실행 큐 임계", cpu_sat_count, sample),
        _build_saturation_donut("페이징", mem_pressure_count, sample),
        _build_saturation_donut("디스크 응답지연 임계", disk_sat_count, sample),
        _build_saturation_donut("네트워크 혼잡", net_congested_count, sample),
    ]
    return EnvironmentRealtime(
        total=total,
        online=online,
        offline=total - online,
        sample_size=len(snapshots),
        utilization=util_bars,
        last_collected_at=last_collected_at,
        load_rows=load_rows,
        saturation_donuts=saturation_donuts,
    )


# ─── capacity(USE Method) · os_eol/agent_unstable(운영신호) · disk_days(dead) ──


def to_capacity_warning_item(raw):
    """ReportRowRaw -> CapacityWarningItem. build_action_targets 가 전 분류(under/over/idle/optimal/insufficient)에 대해 호출.

    active_causes — rollup_host per-resource 트리거 집합 파생(고정 순서, 카드 편입 classify_host 와 정합).
    환경 요약 원인 집계(_under_cause_summary)의 단일 소스. 임계 재계산 없이 rollup 이 잡은 trigger 키를 매핑
    (drift 방지, runway 소진 디스크 등도 강조).
    """
    stats = build_resource_stats(raw)
    # 분류·근본원인·처방·신뢰도 전부 rollup_host 단일 모델 — 화면 간 정합(#E3). 처방은 자원별 독립(ADR 0056),
    # confidence_notes 도 host 기반(build_host_confidence_notes) — 구 assess 미경유.
    host = recommendation.rollup_host(stats)
    classification = recommendation.host_status_to_recommendation(host.host_status)
    hit = {t for r in host.resources.values() for t in r.triggers}
    swap_active = "mem_saturation" in hit
    # 원인 라벨 — trigger key 를 os-neutral 축 이름으로(고정 순서, _CAUSE_LABEL_BY_TRIGGER dict 삽입순 = 표시순).
    active_causes = [lbl for key, lbl in _CAUSE_LABEL_BY_TRIGGER.items() if key in hit]

    net_res = host.resources["network"]
    net_congested = net_res.status == "congested"
    # 네트워크 — verdict 1칼럼(정상/혼잡/미측정), host under/over 미구동 orthogonal flag 라 전용 필드
    # (net_status_label/color). 원시 수치(재전송·드롭·conntrack)는 서버 상세.
    net_status_value = _NET_STATUS_LABEL.get(net_res.status, net_res.status)
    net_status_color = _NET_CONGESTED_COLOR if net_congested else ""
    # 디스크 I/O — network 와 동형 orthogonal flag(io_bound/io_ok/unmeasured, host_status 미구동). RS_STATUS_LABEL_KO
    # (분류 enum 아닌 축 status 전용 라벨, LABEL_KO 와 다른 딕셔너리) 가 이미 세 상태 전부 보유(io_bound="I/O 병목"
    # 등)라 별도 라벨 딕셔너리 불요 — net_status 와 동일 색 재사용(동일 의미=동일 hex, E8). classification 무관
    # 항상 노출(root_cause_label 은 under_provisioned 인과 기여 시에만 노출돼 CPU·메모리 정상인 io_bound 호스트는
    # 안 드러나는 사각지대 보완).
    disk_io_res = host.resources["disk_io"]
    disk_io_status_value = recommendation.RS_STATUS_LABEL_KO.get(disk_io_res.status, disk_io_res.status)
    disk_io_status_color = _NET_CONGESTED_COLOR if disk_io_res.status == "io_bound" else ""
    # 정적 배정 사양 — 서버 목록과 동일 단일 진실(spec_display_line). 환경 자원 평가 표에서 권고와 대조.
    spec_display = spec_display_line(raw.cpu_cores, raw.mem_total_bytes, raw.block_devices)
    # 상위 N 절단용 심각도 — swap(paging) 최우선 > 위반 자원 수 > 최고 활용률(CPU/메모리/디스크 max).
    # 가중치 자릿수 분리(swap 1e4 > active*100(max 500) > util(max 100))로 우선순위 충돌 없음.
    util_vals = [v for v in (raw.cpu_p95_pct, raw.mem_p95_pct, raw.worst_mount_used_pct) if v is not None]
    peak_util = max(util_vals) if util_vals else 0.0
    # 권고·심각도는 분류별 — 자원 부족은 root 처방 + 위반 심각도(swap>원인수>활용률), 과다/유휴는 상태 조치 + 낮은 활용률(낭비) 우선.
    if classification == "under_provisioned":
        action = recommendation.under_prescription(host)
        severity_score = (10000.0 if swap_active else 0.0) + len(active_causes) * 100.0 + peak_util
    else:
        action = recommendation.recommend_action(classification, stats)
        severity_score = 100.0 - peak_util  # 낮은 활용률 = 낭비 큼 -> 정렬 우선
    return CapacityWarningItem(
        public_id=raw.public_id,
        hostname=raw.hostname,
        classification=classification,
        classification_label=recommendation.LABEL_KO.get(classification, classification),
        badge_class=recommendation.BADGE_CLASS.get(classification, ""),
        classification_rank=recommendation.CLASSIFICATION_ORDER.get(classification, 99),
        active_causes=active_causes,
        # 워크로드 카테고리 카운트 — role_distribution 과 동일 단일 진실 (services 이름 ∪ listen 소켓).
        services=dict(workload_category_counter(raw.services, raw.listen_ports)),
        confidence_notes=build_host_confidence_notes(host),
        recommendation_action=action,
        root_cause_label=recommendation.root_cause_display(host),
        severity_score=severity_score,
        net_status_label=net_status_value,
        net_status_color=net_status_color,
        disk_io_status_label=disk_io_status_value,
        disk_io_status_color=disk_io_status_color,
        spec_display=spec_display,
    )


def build_action_targets(raws) -> ActionTargets:
    """서버별 자원 적정성 통합 표 — 전 서버(모든 분류) 한 표에. 최초 정렬 = 분류 순서(부족>과다>유휴>정상>표본) > 심각도.

    CapacityWarningItem 단일 행 타입(분류·근본원인·권고·신뢰도). 자원 평가 페이지·환경 보고서 공유 단일 진실.
    """
    items: list[CapacityWarningItem] = []
    eff_raws = []
    for raw in raws:
        # to_capacity_warning_item 이 이미 rollup_host 로 classification 산출 — 재계산 대신 결과 재사용(요청당 rollup 1회, #E3 동일 산식).
        item = to_capacity_warning_item(raw)
        items.append(item)
        if item.classification in ("over_provisioned", "idle"):
            eff_raws.append(raw)
    items.sort(key=lambda it: (recommendation.CLASSIFICATION_ORDER[it.classification], -it.severity_score, it.hostname))
    return ActionTargets(
        hosts=items,
        total=len(items),
        under_count=sum(1 for it in items if it.classification == "under_provisioned"),
        efficiency_count=len(eff_raws),
        efficiency_vcpus=sum(r.cpu_cores or 0 for r in eff_raws),
        efficiency_memory_gb=round(sum((r.mem_total_bytes or 0) / 1024**3 for r in eff_raws), 1),
        # 점유 스토리지 합 — block_devices type=disk size_bytes 합 (disk_total_bytes 단일 산식, 양 OS).
        efficiency_disk_gb=int(
            bytes_to_gb(sum(disk_total_bytes(r.block_devices or []) for r in eff_raws)) or 0
        ),
    )


def to_os_eol_warning_item(raw, now: datetime) -> AttentionRow | None:
    """ReportRowRaw -> AttentionRow if EOL 경과(resolve_os_eol 공용 판정), else None.

    판정(Windows build / Linux os_version + EOL 경과 비교)은 shared.resolve_os_eol 단일 진실 —
    보고서 정성 요약과 동일 로직. 본 함수는 표시(AttentionRow) 변환만.
    """
    result = resolve_os_eol(raw.os_id, raw.os_version, raw.kernel_version, now.date())
    if result is None:
        return None
    eol_iso, label = result
    # 경과일 — EOL 지난 지 며칠(양수). 마이그레이션 시급도 칼럼. 날짜 파싱 실패 시 None(칼럼 "—").
    days_over: int | None = None
    try:
        days_over = (now.date() - date.fromisoformat(eol_iso)).days
    except (ValueError, TypeError):
        days_over = None
    return AttentionRow(
        badge_class=_ATTN_ACTIVE_BADGE,
        badge_text=label,  # 호스트명 - distro(날짜 제외) 점선 leader. 원인 칸엔 distro 만 짧게.
        link_href=f"/servers/{raw.public_id}",
        link_text=raw.hostname,
        meta_text=f"{label} · EOL {eol_iso}",
        eol_days_over=days_over,
    )


def to_agent_unstable_item(public_id: str, hostname: str, restart_count: int) -> AttentionRow:
    """raw -> AttentionRow. caller가 임계 필터링 후 호출."""
    return AttentionRow(
        badge_class=_ATTN_ACTIVE_BADGE,
        badge_text=f"{restart_count}회",
        link_href=f"/servers/{public_id}",
        link_text=hostname,
        meta_text="신뢰도 낮음",
    )
