"""Attention 신호·환경 개요 mapper (P2).

운영신호 카드(AttentionSignals) = gap·os_eol·agent_unstable 3개 (`query_service._assemble_attention` 조립) +
환경 활용률 도넛·bar·USE Method 분포·under_provisioned 호스트(EnvironmentOverview) 합성. 책임은 raw 신호 → ViewModel.
capacity(under_provisioned)·disk·days_until_full 은 운영신호가 아니라 USE Method right-sizing 소속 —
to_capacity_warning_item 은 EnvironmentOverview.under_provisioned_hosts 로 간다.
임계 분류·표시 색은 shared.py 또는 본 모듈 상단 상수 단일 진실.
"""

import math
from collections import Counter
from datetime import date, datetime

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import FleetErrorRaw, MetricGapWarningRaw
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
    resolve_os_eol,
    saturation_axis_displays,
)
from assessment_engine.web.services.unit_converter import bytes_to_gb, bytes_to_gib
from assessment_engine.web.view_models.attention import (
    ActionTargets,
    AttentionRow,
    CapacityMetric,
    CapacityWarningItem,
    EnvironmentOverview,
    EnvironmentRealtime,
    FleetErrorItem,
    RealtimePeak,
    RealtimePeakGroup,
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

# 도넛 SVG 원주 — 템플릿 SVG r="42" 와 정합. pct 0~100 을 0~_UTIL_DONUT_CIRC 로 매핑(dash-array precompute).
_DONUT_RADIUS = 42
_UTIL_DONUT_CIRC = 2 * math.pi * _DONUT_RADIUS

# 자원 부족 카드 지표 값 색 — 위반 강조 / 정상 / 미관측(N/A) 흐림 3분기.
# 위반은 빨강(#dc2626, red-600) + 굵기로 강조 — 발화 축을 즉시 식별. 정상은 중간 회색(#475569)으로 대비.
_METRIC_VIOLATION_COLOR = "#dc2626"
_METRIC_NORMAL_COLOR = "#475569"
_METRIC_UNMEASURED_COLOR = "#94a3b8"  # 미관측(N/A) 흐림 — Windows perflib 미발행 축 등


# 포화·품질 축 헤더 — Linux(L)/Windows(W) 임계 병기. 값·단위는 os별(혼합 표라 한 칼럼에 두 OS 행 공존)이므로
# 헤더에 양 OS 임계를 표기해 해석 가능하게. 임계 상수는 recommendation 단일 진실에서 조립(F10, drift 0).
_L = recommendation
# 이용률·용량 칼럼 임계 병기 — 값만으론 under 판정선을 모르니 헤더에 명시(p95 이용률·디스크 용량).
_CPU_UTIL_LABEL = f"CPU p95 (>={_L.RS_CPU_UNDER_PCT:g}%)"
_MEM_UTIL_LABEL = f"메모리 p95 (>={_L.RS_MEM_UNDER_PCT:g}%)"
_DISK_CAP_LABEL = f"디스크 용량 (>={_L.RS_DISK_STATIC_GUARD_PCT:g}% or 30일)"
_CPU_SAT_LABEL = f"CPU 포화 (L>={_L.PROCS_RUNNING_PER_CORE_SATURATION:g} · W>={_L.CPU_RUN_QUEUE_PER_CORE_SATURATION:g})"
_MEM_SAT_LABEL = f"메모리 포화 (L page-out · W>={_L.WIN_PAGES_INPUT_SATURATION:g}/s)"
_DISK_IO_LABEL = f"디스크 I/O 포화 (await > {_L.RS_DISKIO_AWAIT_MS:g}ms)"
# 네트워크 — 사이징과 별개 품질 축(orthogonal). 다른 자원처럼 수치 2칼럼(재전송율·드롭율) + 판정(상태) 칼럼.
# 판정 근거 assess_network(재전송>1% or 드롭>0.5% or conntrack>=80% -> 혼잡, monitoring 표준). conntrack 은 서버 상세.
_NET_RETRANS_LABEL = f"재전송율 (>{_L.RS_NET_RETRANS_PCT:g}%)"
_NET_DROP_LABEL = f"드롭율 (>{_L.RS_NET_DROP_PCT:g}%)"
_NET_LABEL = "네트워크 상태"
_NET_STATUS_LABEL: dict[str, str] = {"quality_ok": "정상", "congested": "혼잡", "unmeasured": "미측정"}


def _pct(v: float | None) -> str:
    """float 백분율 -> '94.0%' 표시 (소수 1자리). None(미관측)은 'N/A'."""
    return f"{v:.1f}%" if v is not None else "N/A"


def _disk_cap_value(raw) -> str:
    """디스크 용량 표시 — 현재 used% + (차오르는 중이면) 30일 후 예상 used% 병기. 확장 근거를 숫자로 노출.

    예: "43% (30일 100%)" = 지금 43%, 현재 성장률로 30일 뒤 소진. 목표 GB(장기 외삽)와 달리 근시라 짧은 span 도 신뢰.
    """
    used = raw.worst_mount_used_pct
    if used is None:
        return "N/A"
    proj = raw.disk_capacity_proj_30d_pct
    driving = raw.disk_capacity_driving_used_pct
    # 30일 예상 병기 시엔 그 예상이 나온 마운트의 used% 로 짝 맞춤 — worst-used 마운트와 다른 마운트일 수 있어 혼입 방지.
    if proj is not None and driving is not None and proj > driving + 1:
        return f"{driving:.0f}% (30일 {proj:.0f}%)"
    return f"{used:.1f}%"


def _metric(label: str, value: str, active: bool, measured: bool) -> CapacityMetric:
    """CapacityMetric 1개 — active(위반) 강조 / 정상 진함 / 미관측(N/A) 흐림 precompute (P3)."""
    if not measured:
        color = _METRIC_UNMEASURED_COLOR
    else:
        color = _METRIC_VIOLATION_COLOR if active else _METRIC_NORMAL_COLOR
    return CapacityMetric(label=label, value=value, active=active, measured=measured, color=color)


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


def build_risk_donut_segments(risk_counts: dict[str, int]) -> tuple[list, int, int]:
    """카테고리별 카운트 -> (RiskDonutSegment list, total, under_count).

    risk_counts 예: {"under_provisioned": 1, "over_provisioned": 2, "optimal": 7} (키 = _DONUT_SEGMENT_DEFS 6종).
    누락 키는 0으로 취급. dash_length·dash_offset은 누적 비례 계산.
    under_count는 도넛 중앙 강조용 (가장 시급한 카테고리).
    """
    total = sum(risk_counts.values())
    segments: list = []
    cum_offset = 0.0
    for key, label, _color, description in _DONUT_SEGMENT_DEFS:
        count = risk_counts.get(key, 0)
        dash_length = (count / total) * _UTIL_DONUT_CIRC if (total > 0 and count > 0) else 0.0
        pct = round(count / total * 100, 1) if total > 0 else 0.0
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
    dash = (count / total) * _UTIL_DONUT_CIRC if (total > 0 and count > 0) else 0.0
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

    # 역할 분포 — 카테고리별 "그 워크로드를 돌리는 호스트 수"(호스트당 카테고리 1회, 인스턴스 수 아님).
    # 한 서버에서 같은 카테고리가 여러 개(파일 5개 등) 인식돼도 그 카테고리는 +1 — 환경 구성 파악에 적합.
    # 카테고리 집합은 union(services 이름 ∪ listen 소켓 탐지). known 역할 0 호스트는 role_unknown 으로 분리.
    role_counter: Counter[str] = Counter()
    role_unknown = 0  # known 역할 0인 호스트 수 (서비스 없음 또는 전부 unknown) — 호스트 단위
    for d in details:
        counter = workload_category_counter(d.services, d.listen_ports)
        if counter:
            role_counter.update(counter.keys())  # 카테고리별 호스트 1회 (dedup within host)
        else:
            role_unknown += 1

    util_bars: list = []
    util_bars_p95: list = []
    util_sample = 0
    if utilization is not None:
        util_sample = utilization.sample_size
        util_bars = [
            UtilizationBar(
                label="CPU",
                pct=utilization.cpu_avg_pct,
                bar_color=_bar_color(utilization.cpu_avg_pct),
                dash_length=_dash_length(utilization.cpu_avg_pct),
            ),
            UtilizationBar(
                label="메모리",
                pct=utilization.mem_avg_pct,
                bar_color=_bar_color(utilization.mem_avg_pct),
                dash_length=_dash_length(utilization.mem_avg_pct),
            ),
            UtilizationBar(
                label="디스크 용량",  # fs used%(capacity 축) — 활용률 아님, 용량 명시 (detail 도넛과 일관)
                pct=utilization.disk_avg_pct,
                bar_color=_bar_color(utilization.disk_avg_pct),
                dash_length=_dash_length(utilization.disk_avg_pct),
            ),
        ]
        # p95 활용률 — 평균과 동일 capacity-weighted 환경 분포 기반(per_ts 95퍼센타일). CPU·메모리만 —
        # 디스크는 물리디스크/디바이스 인식이 Windows 에서 불완전해 capacity 합이 신뢰 불가라 제외.
        util_bars_p95 = [
            UtilizationBar(
                label="CPU",
                pct=utilization.cpu_p95_pct,
                bar_color=_bar_color(utilization.cpu_p95_pct),
                dash_length=_dash_length(utilization.cpu_p95_pct),
            ),
            UtilizationBar(
                label="메모리",
                pct=utilization.mem_p95_pct,
                bar_color=_bar_color(utilization.mem_p95_pct),
                dash_length=_dash_length(utilization.mem_p95_pct),
            ),
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
    role_sorted = dict(sorted(role_counter.items(), key=lambda kv: (-kv[1], kv[0])))

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
        role_unknown_count=role_unknown,
        role_identified_count=total - role_unknown,
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
    )


def build_environment_realtime(
    total: int,
    online: int,
    snapshots: list[dict],
    last_collected_at,
    top_n: int = 3,
) -> EnvironmentRealtime:
    """온라인 서버 최신 스냅샷 snapshots -> EnvironmentRealtime.

    호출자가 온라인 서버만 snapshots 로 전달 (오프라인 stale 메트릭 제외 — sample_size = len(snapshots)).
    snapshots 키: hostname/public_id + 부하상위용 서버별 값(cpu_pct/mem_pct/disk_pct + disk_iops/net_kbps)
                + capacity-weighted 가중치(cpu_cores·mem_used_bytes·mem_total_bytes·fs_used_gb·fs_total_gb).
    utilization: CPU/메모리/디스크 평균 도넛 3개 — capacity-weighted(environment_utilization 동일 정의).
    io_*: 환경 I/O 총량(신선 표본 합산) — 평균 섹션 수치 카드(rate 라 도넛 아님).
    peak_groups: 자원별(CPU/메모리/디스크/디스크 I/O/네트워크 I/O) top_n 5열 — I/O 는 2행 페어 delta(build_dashboard).
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
    avg_disk = _ratio("fs_used_gb", "fs_total_gb")
    util_bars = [
        UtilizationBar(label="CPU", pct=avg_cpu, bar_color=_bar_color(avg_cpu), dash_length=_dash_length(avg_cpu)),
        UtilizationBar(label="메모리", pct=avg_mem, bar_color=_bar_color(avg_mem), dash_length=_dash_length(avg_mem)),
        UtilizationBar(
            label="디스크 용량", pct=avg_disk, bar_color=_bar_color(avg_disk), dash_length=_dash_length(avg_disk)
        ),
    ]

    def _top_pct(key: str) -> list[RealtimePeak]:
        ranked = sorted((s for s in snapshots if s.get(key) is not None), key=lambda s: s[key], reverse=True)
        return [
            RealtimePeak(hostname=s["hostname"], public_id=s["public_id"], value=s[key], display=f"{s[key]:.1f}%")
            for s in ranked[:top_n]
        ]

    # 부하 상위는 이용률 순위만 — 포화 지수는 Linux(await·실행큐)/Windows(큐 깊이) 신호가 달라 한 줄 랭킹이
    # 애매하다(포화는 카운트 도넛으로만). 처리량(IOPS·MB/s)은 기준점 없어 폐기.
    peak_groups = [
        RealtimePeakGroup(label="CPU", peaks=_top_pct("cpu_pct")),
        RealtimePeakGroup(label="메모리", peaks=_top_pct("mem_pct")),
        RealtimePeakGroup(label="디스크", peaks=_top_pct("disk_pct")),
    ]

    # 포화 비율 도넛 — 포화/압박 호스트 수 / 표본. 색·게이지는 _build_saturation_donut 공용(환경개요와 통일).
    sample = len(snapshots)
    cpu_sat_count = sum(1 for s in snapshots if (s.get("cpu_sat_index") or 0) >= 1.0)
    disk_sat_count = sum(1 for s in snapshots if (s.get("disk_sat_index") or 0) >= 1.0)
    mem_pressure_count = sum(1 for s in snapshots if s.get("mem_pressure"))
    saturation_donuts = [
        _build_saturation_donut("CPU 포화", cpu_sat_count, sample),
        _build_saturation_donut("메모리 압박", mem_pressure_count, sample),
        _build_saturation_donut("디스크 I/O 포화", disk_sat_count, sample),
    ]
    return EnvironmentRealtime(
        total=total,
        online=online,
        offline=total - online,
        sample_size=len(snapshots),
        utilization=util_bars,
        last_collected_at=last_collected_at,
        peak_groups=peak_groups,
        has_peaks=any(g.peaks for g in peak_groups),
        saturation_donuts=saturation_donuts,
    )


# ─── capacity(USE Method) · os_eol/agent_unstable(운영신호) · disk_days(dead) ──


def to_capacity_warning_item(raw):
    """ReportRowRaw -> CapacityWarningItem. build_action_targets 가 전 분류(under/over/idle/optimal/insufficient)에 대해 호출.

    active_causes·지표 강조 — rollup_host per-resource 트리거 집합 파생(고정 순서, 카드 편입 classify_host 와 정합).
    환경 요약 원인 집계(_under_cause_summary)의 단일 소스. 임계 재계산 없이 rollup 이 잡은 trigger 키를 매핑
    (drift 방지, runway 소진 디스크 등도 강조). saturation 3축(CPU·메모리·디스크 I/O)은 rollup 내부
    os-aware helper 로 판정 — 메모리 포화는 Linux swap page-out / Windows Pages Input/sec rate, CPU 포화는 Linux
    run queue(procs_running) / Windows Processor Queue (P2). 지표 라벨은 os-neutral 축 이름, 값·measured 는 os-aware.
    """
    stats = build_resource_stats(raw)
    # 분류·근본원인·처방·지표 강조·신뢰도 전부 rollup_host 단일 모델 — 화면 간 정합(#E3, ADR 0052 Phase D).
    # root 만 처방(하류는 증상). confidence_notes 도 host 기반(build_host_confidence_notes) — 구 assess 미경유.
    host = recommendation.rollup_host(stats)
    classification = recommendation.host_status_to_recommendation(host.host_status)
    # 지표 강조(active) = rollup per-resource 트리거 집합 — 분류가 잡은 축이 곧 강조 축(runway 소진 디스크 포함).
    hit = {t for r in host.resources.values() for t in r.triggers}
    swap_active = "mem_saturation" in hit
    cpu_active = "cpu_util" in hit
    mem_active = "mem_util" in hit
    load_active = "cpu_saturation" in hit
    # 원인 라벨 — trigger key 를 os-neutral 축 이름으로(고정 순서, _CAUSE_LABEL_BY_TRIGGER dict 삽입순 = 표시순).
    active_causes = [lbl for key, lbl in _CAUSE_LABEL_BY_TRIGGER.items() if key in hit]

    # 카드 지표 — 판단 6축 전부 노출. saturation 3축(CPU·메모리·디스크 I/O) 표시값·라벨은 os-aware 이나
    # `shared.saturation_axis_displays` 단일 진실 경유 — single_report 포화 축 카드와 표기 공유(drift 차단, P2).
    # 라벨은 OS 중립 축 이름(공유 테이블 헤더가 혼합 OS 행에 유효, C-E1), 값·measured 만 os-aware(M1).
    # active 는 rollup 트리거(hit) 재사용(임계 재계산 0). d_cpu/d_mem/d_disk = [cpu, mem, disk] 순.
    # 지표 순서 = 자원별 그룹(CPU 이용률·포화 / 메모리 이용률·포화 / 디스크 용량·I/O) — 근거 읽기 쉽게.
    d_cpu, d_mem, d_disk = saturation_axis_displays(stats)
    net_res = host.resources["network"]
    net_congested = net_res.status == "congested"
    net_measured = net_res.status != "unmeasured"
    # 네트워크 — 수치 2칼럼(재전송율·드롭율) + 판정(상태). 상태는 '분류'처럼 정상/혼잡/미측정. conntrack 은 서버 상세.
    retrans = raw.net_retrans_pct
    drop = raw.net_drop_pct
    net_status_value = _NET_STATUS_LABEL.get(net_res.status, net_res.status)
    metrics = [
        _metric(_CPU_UTIL_LABEL, _pct(raw.cpu_p95_pct), cpu_active, raw.cpu_p95_pct is not None),
        _metric(_CPU_SAT_LABEL, d_cpu.value, load_active, d_cpu.measured),
        _metric(_MEM_UTIL_LABEL, _pct(raw.mem_p95_pct), mem_active, raw.mem_p95_pct is not None),
        _metric(_MEM_SAT_LABEL, d_mem.value, swap_active, d_mem.measured),
        _metric(_DISK_CAP_LABEL, _disk_cap_value(raw), "disk_capacity" in hit, raw.worst_mount_used_pct is not None),
        _metric(_DISK_IO_LABEL, d_disk.value, "disk_io" in hit, d_disk.measured),
        # 네트워크 그룹 — 근거(재전송·드롭 수치) 뒤 판정(상태). active = 각 임계 초과.
        _metric(_NET_RETRANS_LABEL, f"{retrans:.2f}%" if retrans is not None else "N/A",
                retrans is not None and retrans > _L.RS_NET_RETRANS_PCT, retrans is not None),
        _metric(_NET_DROP_LABEL, f"{drop:.2f}%" if drop is not None else "N/A",
                drop is not None and drop > _L.RS_NET_DROP_PCT, drop is not None),
        _metric(_NET_LABEL, net_status_value, net_congested, net_measured),
    ]
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
        metrics=metrics,
        confidence_notes=build_host_confidence_notes(host),
        recommendation_action=action,
        root_cause_label=recommendation.root_cause_display(host),
        severity_score=severity_score,
    )


def build_action_targets(raws) -> ActionTargets:
    """서버별 자원 적정성 통합 표 — 전 서버(모든 분류) 한 표에. 최초 정렬 = 분류 순서(부족>과다>유휴>정상>표본) > 심각도.

    CapacityWarningItem 단일 행 타입(분류·근본원인·권고·6축·신뢰도). 자원 평가 페이지·환경 보고서 공유 단일 진실.
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
        metric_labels=[m.label for m in items[0].metrics] if items else [],
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
