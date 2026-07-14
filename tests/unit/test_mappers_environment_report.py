"""mappers.environment_report — 환경 보고서 합성 + P3 회피 precompute 필드 검증.

핵심:
- `_PROVISIONING_SEGMENT_DEFS` 는 `mappers._DONUT_SEGMENT_DEFS` 와 동일 객체 (이중 정의 단일화 회귀)
- `to_environment_report` 가 precompute count 필드 (top_risks_count, attention_hosts_count 등) 채움
- `ClassificationCount.pct` 가 mapper precompute (templates 산술 회피, P3)
"""

from datetime import UTC, datetime

import pytest

from assessment_engine import recommendation
from assessment_engine.db.dtos.outbound import MetricSeries
from assessment_engine.web.services.mappers import environment_report as erm
from assessment_engine.web.services.mappers import shared as m_shared
from assessment_engine.web.services.mappers.environment_report import to_environment_report
from assessment_engine.web.services.mappers.shared import RISK_LEVEL_ORDER
from assessment_engine.web.view_models.attention import (
    ActionTargets,
    AttentionSignals,
    EnvironmentOverview,
)
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary


def test_provisioning_segment_defs_single_truth():
    """environment_report._PROVISIONING_SEGMENT_DEFS 는 mappers.shared._DONUT_SEGMENT_DEFS alias."""
    assert erm._PROVISIONING_SEGMENT_DEFS is m_shared._DONUT_SEGMENT_DEFS


def _make_row(public_id: str, hostname: str, rec: str = "optimal") -> ReportRowItem:
    """ReportRowItem 최소 fixture — 분류 카운트 회귀용."""
    return ReportRowItem(
        server_id=1,
        public_id=public_id,
        hostname=hostname,
        role="other",
        os_family=None,
        os_display="ubuntu 22.04",
        kernel_version="6.5.0",
        internal_ip="10.0.0.1",
        is_online=True,
        cpu_avg_pct=10.0,
        cpu_p95_pct=20.0,
        cpu_peak_pct=30.0,
        mem_avg_pct=20.0,
        mem_p95_pct=30.0,
        mem_peak_pct=40.0,
        # v1 load_15m_max/swap_used 폐기 — v2 CPU 포화는 os-aware cpu_run_queue_p95,
        # 메모리 압박은 mem_swap_paging(둘 다 default 존재, 본 카운트/pct 회귀와 무관).
        recommendation=rec,
        recommendation_label=rec,
        badge_class=f"rec-{rec}",
        risk_level="normal",
        risk_label="정상",
        risk_badge_class="rec-optimal",
    )


def test_to_environment_report_precomputes_count_fields():
    """`*_count` precompute 필드가 len() 결과와 일치 — templates P3 회피 (#E1 P3)."""
    rows = [
        _make_row("u-1", "host-1", "optimal"),
        _make_row("u-2", "host-2", "optimal"),
        _make_row("u-3", "host-3", "under_provisioned"),
    ]
    overview = EnvironmentOverview(
        total=3,
        online=3,
        offline=0,
        total_vcpus=12,
        total_memory_gb=48.0,
        total_disk_gb=300,
        utilization=[],
        util_sample_size=3,
        risk_donut=[],
        risk_donut_total=0,
        risk_high_count=0,
    )
    from assessment_engine.web.view_models.report import ReportTotals

    base = ReportSummary(
        rows=rows,
        period_days=14,
        total=3,
        online=3,
        risk_attention=0,
        risk_high=1,
        totals=ReportTotals(total_vcpus=12, total_memory_gb=48, total_disk_gb=300),
        summary_bullets=[],
        role_distribution={"other": 3},
    )
    attention = AttentionSignals(gap_warnings=[])  # 모든 신호 빈 list

    result = to_environment_report(
        view="customer",
        base=base,
        overview=overview,
        attention=attention,
        details=[],
        time_range="14d",
        anchor_at=datetime(2026, 5, 12, tzinfo=UTC),
        generated_at=datetime(2026, 5, 12, tzinfo=UTC),
        action=ActionTargets(),
    )
    assert result.top_risks_count == len(result.top_risks)
    assert result.attention_hosts_count == len(result.attention_hosts)
    assert result.capacity_imminent_count == len(result.capacity_imminent)
    assert result.action.total == len(result.action.hosts)


def test_to_environment_report_precomputes_classification_pct():
    """ClassificationCount.pct 가 mapper precompute (templates 산술 회피, P3).

    optimal 2 + under_provisioned 1 = 총 3대 → optimal 66.7%, under 33.3%, 나머지 0%.
    """
    rows = [
        _make_row("u-1", "host-1", "optimal"),
        _make_row("u-2", "host-2", "optimal"),
        _make_row("u-3", "host-3", "under_provisioned"),
    ]
    overview = EnvironmentOverview(
        total=3,
        online=3,
        offline=0,
        total_vcpus=12,
        total_memory_gb=48.0,
        total_disk_gb=300,
        utilization=[],
        util_sample_size=3,
        risk_donut=[],
        risk_donut_total=0,
        risk_high_count=0,
    )
    from assessment_engine.web.view_models.report import ReportTotals

    base = ReportSummary(
        rows=rows,
        period_days=14,
        total=3,
        online=3,
        risk_attention=0,
        risk_high=1,
        totals=ReportTotals(total_vcpus=12, total_memory_gb=48, total_disk_gb=300),
        summary_bullets=[],
        role_distribution={"other": 3},
    )
    attention = AttentionSignals(gap_warnings=[])

    result = to_environment_report(
        view="customer",
        base=base,
        overview=overview,
        attention=attention,
        details=[],
        time_range="14d",
        anchor_at=datetime(2026, 5, 12, tzinfo=UTC),
        generated_at=datetime(2026, 5, 12, tzinfo=UTC),
        action=ActionTargets(),
    )
    by_key = {c.key: c for c in result.classification_dist}
    assert by_key["optimal"].pct == pytest.approx(66.7, abs=0.1)
    assert by_key["under_provisioned"].pct == pytest.approx(33.3, abs=0.1)
    # 미해당 segment 는 pct 0
    assert by_key["idle"].pct == 0.0
    # pct 합 == 100 (insufficient_data 제외하면 100, 모두 합치면 100)
    assert sum(c.pct for c in result.classification_dist) == pytest.approx(100.0, abs=0.1)


def test_to_environment_report_classification_dist_empty_rows_zero_pct():
    """rows 0건 — classified_total=0 분기 → pct 모두 0.0 (ZeroDivision 방어)."""
    overview = EnvironmentOverview(
        total=0,
        online=0,
        offline=0,
        total_vcpus=0,
        total_memory_gb=0.0,
        total_disk_gb=0,
        utilization=[],
        util_sample_size=0,
        risk_donut=[],
        risk_donut_total=0,
        risk_high_count=0,
    )
    from assessment_engine.web.view_models.report import ReportTotals

    base = ReportSummary(
        rows=[],
        period_days=14,
        total=0,
        online=0,
        risk_attention=0,
        risk_high=0,
        totals=ReportTotals(0, 0, 0),
        summary_bullets=[],
        role_distribution={},
    )
    result = to_environment_report(
        view="customer",
        base=base,
        overview=overview,
        attention=AttentionSignals(gap_warnings=[]),
        details=[],
        time_range="14d",
        anchor_at=datetime(2026, 5, 12, tzinfo=UTC),
        generated_at=datetime(2026, 5, 12, tzinfo=UTC),
        action=ActionTargets(),
    )
    for c in result.classification_dist:
        assert c.pct == 0.0
        assert c.count == 0


# ---------- build_metric_trend ----------


def _series(pairs: list[tuple[datetime, float | None]]) -> list[MetricSeries]:
    """MetricSeries list 헬퍼 — collected_at·value 만 build_metric_trend 가 사용."""
    return [MetricSeries(collected_at=t, value=v, dimension=None) for t, v in pairs]


def test_build_metric_trend_merges_three_series_on_timestamps():
    """3계열을 버킷 시각 union 기준 merge → 정렬된 timestamp 별 1행, isoformat at."""
    t1 = datetime(2026, 5, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 12, 1, 0, tzinfo=UTC)
    t3 = datetime(2026, 5, 12, 2, 0, tzinfo=UTC)
    cpu = _series([(t1, 10.0), (t2, 20.0)])
    mem = _series([(t2, 30.0), (t3, 40.0)])
    disk = _series([(t1, 50.0), (t3, 60.0)])

    out = erm.build_metric_trend(cpu, mem, disk)

    assert [d["at"] for d in out] == [t1.isoformat(), t2.isoformat(), t3.isoformat()]
    # t1: cpu·disk 있고 mem gap None
    assert out[0] == {"at": t1.isoformat(), "cpu": 10.0, "mem": None, "disk": 50.0}
    # t2: cpu·mem 있고 disk gap None
    assert out[1] == {"at": t2.isoformat(), "cpu": 20.0, "mem": 30.0, "disk": None}
    # t3: mem·disk 있고 cpu gap None
    assert out[2] == {"at": t3.isoformat(), "cpu": None, "mem": 40.0, "disk": 60.0}


def test_build_metric_trend_rounds_to_one_decimal_and_keeps_none():
    """value 는 float 변환 + 소수 1자리 반올림, None 표본은 그대로 None (차트 gap)."""
    t1 = datetime(2026, 5, 12, 0, 0, tzinfo=UTC)
    from decimal import Decimal

    cpu = _series([(t1, Decimal("12.34"))])
    mem = _series([(t1, None)])
    disk = _series([(t1, 99.96)])

    out = erm.build_metric_trend(cpu, mem, disk)

    assert len(out) == 1
    assert out[0]["cpu"] == 12.3
    assert isinstance(out[0]["cpu"], float)
    assert out[0]["mem"] is None
    assert out[0]["disk"] == 100.0


def test_build_metric_trend_empty_series_returns_empty():
    """모든 계열 빈 list → 빈 결과 (timestamp union 공집합)."""
    assert erm.build_metric_trend([], [], []) == []


# ---------- _extract_capacity_imminent ----------


def _cap_row(
    public_id: str,
    hostname: str,
    *,
    runway: int | None,
    mount: str | None,
    used_pct: float | None = 90.0,
) -> ReportRowItem:
    """capacity 임박 필드만 채운 ReportRowItem — 나머지는 회귀 무관 default."""
    return ReportRowItem(
        server_id=1,
        public_id=public_id,
        hostname=hostname,
        role="other",
        os_family=None,
        os_display="ubuntu 22.04",
        kernel_version="6.5.0",
        internal_ip="10.0.0.1",
        is_online=True,
        cpu_avg_pct=10.0,
        cpu_p95_pct=20.0,
        cpu_peak_pct=30.0,
        mem_avg_pct=20.0,
        mem_p95_pct=30.0,
        mem_peak_pct=40.0,
        recommendation="optimal",
        recommendation_label="optimal",
        badge_class="rec-optimal",
        risk_level="normal",
        risk_label="정상",
        risk_badge_class="rec-optimal",
        worst_mount_used_pct=used_pct,
        disk_capacity_driving_mount=mount,
        disk_capacity_runway_days=runway,
    )


def test_extract_capacity_imminent_filters_and_sorts():
    """runway < RS_DISK_RUNWAY_DAYS + 구동 마운트 있는 호스트만, days ASC → hostname ASC 정렬."""
    assert recommendation.RS_DISK_RUNWAY_DAYS == 30
    rows = [
        _cap_row("u-far", "far", runway=45, mount="/data"),  # runway >= 30 제외
        _cap_row("u-none", "none", runway=None, mount="/data"),  # runway None 제외
        _cap_row("u-nomnt", "nomnt", runway=5, mount=None),  # 구동 마운트 가드 제외
        _cap_row("u-b", "host-b", runway=10, mount="/var", used_pct=88.0),
        _cap_row("u-a", "host-a", runway=3, mount="/", used_pct=95.0),
        _cap_row("u-c", "host-c", runway=10, mount="/opt", used_pct=80.0),
    ]

    out = erm._extract_capacity_imminent(rows)

    # 3건만 통과, days ASC → 동일 days 는 hostname ASC
    assert [(i.hostname, i.days_until_full) for i in out] == [
        ("host-a", 3),
        ("host-b", 10),
        ("host-c", 10),
    ]
    first = out[0]
    assert first.public_id == "u-a"
    assert first.worst_mount == "/"
    assert first.used_pct == 95.0


def test_extract_capacity_imminent_boundary_at_threshold_excluded():
    """runway == RS_DISK_RUNWAY_DAYS 는 미포함 (>= 제외 경계)."""
    rows = [_cap_row("u-1", "h1", runway=recommendation.RS_DISK_RUNWAY_DAYS, mount="/data")]
    assert erm._extract_capacity_imminent(rows) == []


def test_extract_capacity_imminent_empty():
    """빈 rows → 빈 list."""
    assert erm._extract_capacity_imminent([]) == []


# ---------- _select_top_risks ----------


def _risk_row(
    public_id: str,
    hostname: str,
    *,
    risk_level: str,
    cpu_p95: float | None,
) -> ReportRowItem:
    """risk_level·cpu_p95 만 의미있는 ReportRowItem — top risk 선정 회귀용."""
    return ReportRowItem(
        server_id=1,
        public_id=public_id,
        hostname=hostname,
        role="other",
        os_family=None,
        os_display="ubuntu 22.04",
        kernel_version="6.5.0",
        internal_ip="10.0.0.1",
        is_online=True,
        cpu_avg_pct=10.0,
        cpu_p95_pct=cpu_p95,
        cpu_peak_pct=30.0,
        mem_avg_pct=20.0,
        mem_p95_pct=30.0,
        mem_peak_pct=40.0,
        recommendation="optimal",
        recommendation_label="optimal",
        badge_class="rec-optimal",
        risk_level=risk_level,
        risk_label=risk_level,
        risk_badge_class="rec-optimal",
    )


def test_select_top_risks_customer_only_high_sorted_by_cpu_desc():
    """customer: risk_level=='high' 만 필터, cpu_p95 DESC 정렬 (정상·주의 제외)."""
    rows = [
        _risk_row("u-h1", "h1", risk_level="high", cpu_p95=50.0),
        _risk_row("u-att", "att", risk_level="attention", cpu_p95=99.0),
        _risk_row("u-h2", "h2", risk_level="high", cpu_p95=80.0),
        _risk_row("u-norm", "norm", risk_level="normal", cpu_p95=90.0),
        _risk_row("u-h3", "h3", risk_level="high", cpu_p95=None),
    ]

    out = erm._select_top_risks(rows, "customer")

    assert [r.hostname for r in out] == ["h2", "h1", "h3"]
    assert all(r.risk_level == "high" for r in out)


def test_select_top_risks_engineer_all_rows_priority_ordered():
    """engineer: 전체를 RISK_LEVEL_ORDER 우선 정렬, 동순위는 cpu_p95 DESC, limit None(전수)."""
    assert RISK_LEVEL_ORDER == {"high": 0, "attention": 1, "low_usage": 2, "normal": 3}
    rows = [
        _risk_row("u-norm", "norm", risk_level="normal", cpu_p95=10.0),
        _risk_row("u-h-lo", "h-lo", risk_level="high", cpu_p95=40.0),
        _risk_row("u-att", "att", risk_level="attention", cpu_p95=95.0),
        _risk_row("u-h-hi", "h-hi", risk_level="high", cpu_p95=70.0),
        _risk_row("u-low", "low", risk_level="low_usage", cpu_p95=5.0),
    ]

    out = erm._select_top_risks(rows, "engineer")

    # high(cpu DESC) → attention → low_usage → normal, 전수 노출(N 잘림 없음)
    assert [r.hostname for r in out] == ["h-hi", "h-lo", "att", "low", "norm"]
    assert len(out) == len(rows)


def test_select_top_risks_engineer_view_limit_is_none():
    """engineer 는 _TOP_RISK_N_BY_VIEW 에서 None → 대량 rows 도 잘림 없음."""
    rows = [_risk_row(f"u-{i}", f"h{i}", risk_level="high", cpu_p95=float(i)) for i in range(25)]
    out = erm._select_top_risks(rows, "engineer")
    assert len(out) == 25
