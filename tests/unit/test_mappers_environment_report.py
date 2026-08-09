from datetime import UTC, datetime
from decimal import Decimal

from assessment_engine.db.dtos.outbound import MetricSeries
from assessment_engine.domain import right_sizing
from assessment_engine.web.services.mappers import constants as m_constants
from assessment_engine.web.services.mappers import environment_report as erm
from assessment_engine.web.services.mappers.constants import RISK_LEVEL_ORDER
from assessment_engine.web.services.mappers.environment_report import to_environment_report
from assessment_engine.web.view_models.attention import (
    ActionTargets,
    AttentionSignals,
    EnvironmentOverview,
)
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary
from tests.approx import approx


def test_provisioning_segment_defs_single_truth():
    assert erm._PROVISIONING_SEGMENT_DEFS is m_constants._DONUT_SEGMENT_DEFS


def _make_row(public_id: str, hostname: str, rec: str = "optimal") -> ReportRowItem:
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
        recommendation=rec,
        recommendation_label=rec,
        badge_class=f"rec-{rec}",
        risk_level="normal",
        risk_label="정상",
        risk_badge_class="rec-optimal",
    )


def test_to_environment_report_precomputes_count_fields():
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
    assert result.top_risks_count == len(result.top_risks)
    assert result.attention_hosts_count == len(result.attention_hosts)
    assert result.capacity_imminent_count == len(result.capacity_imminent)
    assert result.action.total == len(result.action.hosts)


def test_to_environment_report_precomputes_classification_pct():
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
    assert by_key["optimal"].pct == approx(66.7, abs=0.1)
    assert by_key["under_provisioned"].pct == approx(33.3, abs=0.1)
    assert by_key["idle"].pct == 0.0
    assert sum(c.pct for c in result.classification_dist) == approx(100.0, abs=0.1)


def test_to_environment_report_classification_dist_empty_rows_zero_pct():
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


def _series(pairs: list[tuple[datetime, float | Decimal | None]]) -> list[MetricSeries]:
    return [MetricSeries(collected_at=t, value=v, dimension=None) for t, v in pairs]


def test_build_metric_trend_merges_three_series_on_timestamps():
    t1 = datetime(2026, 5, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 12, 1, 0, tzinfo=UTC)
    t3 = datetime(2026, 5, 12, 2, 0, tzinfo=UTC)
    cpu = _series([(t1, 10.0), (t2, 20.0)])
    mem = _series([(t2, 30.0), (t3, 40.0)])
    disk = _series([(t1, 50.0), (t3, 60.0)])

    out = erm.build_metric_trend(cpu, mem, disk)

    assert [d["at"] for d in out] == [t1.isoformat(), t2.isoformat(), t3.isoformat()]
    assert out[0] == {"at": t1.isoformat(), "cpu": 10.0, "mem": None, "disk": 50.0}
    assert out[1] == {"at": t2.isoformat(), "cpu": 20.0, "mem": 30.0, "disk": None}
    assert out[2] == {"at": t3.isoformat(), "cpu": None, "mem": 40.0, "disk": 60.0}


def test_build_metric_trend_rounds_to_one_decimal_and_keeps_none():
    t1 = datetime(2026, 5, 12, 0, 0, tzinfo=UTC)
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
    assert erm.build_metric_trend([], [], []) == []


def test_build_saturation_trend_merges_three_series_on_timestamps():
    t1 = datetime(2026, 5, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 12, 1, 0, tzinfo=UTC)
    cpu = _series([(t1, 1.0), (t2, 0.0)])
    mem = _series([(t1, 0.0)])
    disk = _series([(t2, 1.0)])

    out = erm.build_saturation_trend(cpu, mem, disk)

    assert out == [
        {"at": t1.isoformat(), "cpu_sat": 1.0, "mem_sat": 0.0, "disk_sat": None},
        {"at": t2.isoformat(), "cpu_sat": 0.0, "mem_sat": None, "disk_sat": 1.0},
    ]


def test_build_saturation_trend_empty_series_returns_empty():
    assert erm.build_saturation_trend([], [], []) == []


def _cap_row(
    public_id: str,
    hostname: str,
    *,
    runway: int | None,
    mount: str | None,
    used_pct: float | None = 90.0,
    inode_runway: int | None = None,
    inode_mount: str | None = None,
) -> ReportRowItem:
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
        disk_inode_driving_mount=inode_mount,
        disk_inode_runway_days=inode_runway,
    )


def test_extract_capacity_imminent_filters_and_sorts():
    assert right_sizing.DISK_RUNWAY_DAYS == 30
    rows = [
        _cap_row("u-far", "far", runway=45, mount="/data"),
        _cap_row("u-none", "none", runway=None, mount="/data"),
        _cap_row("u-nomnt", "nomnt", runway=5, mount=None),
        _cap_row("u-b", "host-b", runway=10, mount="/var", used_pct=88.0),
        _cap_row("u-a", "host-a", runway=3, mount="/", used_pct=95.0),
        _cap_row("u-c", "host-c", runway=10, mount="/opt", used_pct=80.0),
    ]

    out = erm._extract_capacity_imminent(rows)

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
    rows = [_cap_row("u-1", "h1", runway=right_sizing.DISK_RUNWAY_DAYS, mount="/data")]
    assert erm._extract_capacity_imminent(rows) == []


def test_extract_capacity_imminent_uses_inode_driving_mount():
    rows = [_cap_row("u-1", "host-1", runway=100, mount="/data", inode_runway=5, inode_mount="/var")]
    out = erm._extract_capacity_imminent(rows)
    assert len(out) == 1
    assert out[0].worst_mount == "/var"
    assert out[0].constraint_label == "inode"
    assert out[0].days_until_full == 5


def test_extract_capacity_imminent_empty():
    assert erm._extract_capacity_imminent([]) == []


def _risk_row(
    public_id: str,
    hostname: str,
    *,
    risk_level: str,
    cpu_p95: float | None,
) -> ReportRowItem:
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
    assert RISK_LEVEL_ORDER == {"high": 0, "attention": 1, "low_usage": 2, "normal": 3}
    rows = [
        _risk_row("u-norm", "norm", risk_level="normal", cpu_p95=10.0),
        _risk_row("u-h-lo", "h-lo", risk_level="high", cpu_p95=40.0),
        _risk_row("u-att", "att", risk_level="attention", cpu_p95=95.0),
        _risk_row("u-h-hi", "h-hi", risk_level="high", cpu_p95=70.0),
        _risk_row("u-low", "low", risk_level="low_usage", cpu_p95=5.0),
    ]

    out = erm._select_top_risks(rows, "engineer")

    assert [r.hostname for r in out] == ["h-hi", "h-lo", "att", "low", "norm"]
    assert len(out) == len(rows)


def test_select_top_risks_engineer_view_limit_is_none():
    rows = [_risk_row(f"u-{i}", f"h{i}", risk_level="high", cpu_p95=float(i)) for i in range(25)]
    out = erm._select_top_risks(rows, "engineer")
    assert len(out) == 25
