"""mappers.environment_report — 환경 보고서 합성 + P3 회피 precompute 필드 검증.

핵심:
- `_PROVISIONING_SEGMENT_DEFS` 는 `mappers._DONUT_SEGMENT_DEFS` 와 동일 객체 (이중 정의 단일화 회귀)
- `_CAPACITY_IMMINENT_DAYS` 도 mappers 단일 진실 alias
- `to_environment_report` 가 precompute count 필드 (top_risks_count, attention_hosts_count 등) 채움
- `ClassificationCount.pct` 가 mapper precompute (templates 산술 회피, P3)
"""

from datetime import UTC, datetime

import pytest

from assessment_engine.web.services.mappers import environment_report as erm
from assessment_engine.web.services.mappers import shared as m_shared
from assessment_engine.web.services.mappers.environment_report import to_environment_report
from assessment_engine.web.view_models.attention import (
    ActionTargets,
    AttentionSignals,
    EnvironmentOverview,
)
from assessment_engine.web.view_models.report import ReportRowItem, ReportSummary


def test_provisioning_segment_defs_single_truth():
    """environment_report._PROVISIONING_SEGMENT_DEFS 는 mappers.shared._DONUT_SEGMENT_DEFS alias."""
    assert erm._PROVISIONING_SEGMENT_DEFS is m_shared._DONUT_SEGMENT_DEFS


def test_capacity_imminent_days_single_truth():
    """environment_report._CAPACITY_IMMINENT_DAYS 는 mappers.shared._CAPACITY_IMMINENT_DAYS alias."""
    assert erm._CAPACITY_IMMINENT_DAYS == m_shared._CAPACITY_IMMINENT_DAYS


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
        load_15m_max=None,
        swap_used=False,
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
