import dataclasses
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, get_args

import pytest

from assessment_engine.db.dtos.outbound import (
    EnvironmentUtilizationRaw,
    ReportRowRaw,
    ServerDetail,
)
from assessment_engine.domain import right_sizing
from assessment_engine.domain.right_sizing import Recommendation
from assessment_engine.web.services.mappers.attention import (
    _UTIL_COLOR_GAUGE,
    _UTIL_COLOR_NONE,
    _UTIL_DONUT_CIRC,
    build_environment_overview,
    build_risk_donut_segments,
    to_agent_unstable_item,
    to_capacity_warning_item,
    to_os_eol_warning_item,
)
from assessment_engine.web.services.mappers.constants import _DONUT_SEGMENT_DEFS
from assessment_engine.web.services.mappers.os_eol import (
    _classify_eol,
    lookup_os_eol,
    resolve_os_eol,
)
from assessment_engine.web.services.mappers.period_assessment import build_period_assessment
from assessment_engine.web.services.mappers.report import (
    _RISK_FROM_RECOMMENDATION,
    _build_recommendation_action,
    build_role_distribution,
    compute_report_avg_p95,
    compute_report_totals_from_raw,
    to_report_row_item,
)
from assessment_engine.web.services.mappers.report_summary import build_report_summary_bullets
from assessment_engine.web.services.mappers.resource_stats import build_resource_stats

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


from tests.builders import report_row_raw

_NOW = datetime(2026, 5, 12, tzinfo=UTC)


def _raw(
    *,
    server_id: int = 1,
    public_id: str = "a",
    hostname: str = "h",
    os_family: str | None = None,
    os_id: str | None = "ubuntu",
    os_version: str | None = "22.04",
    os_codename: str | None = "jammy",
    kernel_version: str | None = "5.15",
    net_interfaces: list[JsonObject] | None = None,
    services: list[JsonObject] | None = None,
    cpu_avg: float | None = None,
    cpu_p95: float | None = None,
    cpu_peak: float | None = None,
    mem_avg: float | None = None,
    mem_p95: float | None = None,
    mem_peak: float | None = None,
    iowait_p95: float | None = None,
    iowait_peak: float | None = None,
    cpu_run_queue_p95: float | None = None,
    mem_pages_input_rate_p95: float | None = None,
    cpu_cores: int | None = 2,
    mem_total_kb: int | None = 2 * 1024 * 1024,
    block_devices: list[JsonObject] | None = None,
    boot_time: datetime | None = None,
    worst_mount: str | None = None,
    worst_used: float | None = None,
    reboot_count: int = 0,
    disk_iops: int | None = None,
    disk_throughput: float | None = None,
    net_rx: float | None = None,
    net_tx: float | None = None,
    cpu_sufficiency: float | None = None,
    mem_sufficiency: float | None = None,
    procs_blocked_p95: float | None = None,
    mem_swap_paging: bool | None = False,
    disk_await_p95_ms: float | None = None,
    disk_capacity_runway_days: float | None = None,
    disk_inode_runway_days: float | None = None,
    net_retrans_pct: float | None = None,
    net_drop_pct: float | None = None,
    history_hours: float | None = None,
    cpu_burst_ratio: float | None = None,
    cpu_trend_slope: float | None = None,
    mem_trend_slope: float | None = None,
    cpu_steal_p95: float | None = None,
    cpu_percore_p95_max: float | None = None,
    procs_running_p95: float | None = None,
    oom_occurred: bool = False,
) -> ReportRowRaw:
    optional: dict[str, Any] = {}
    if net_interfaces is not None:
        optional["net_interfaces"] = net_interfaces
    if block_devices is not None:
        optional["block_devices"] = block_devices
    if boot_time is not None:
        optional["boot_time"] = boot_time
    return report_row_raw(
        **optional,
        server_id=server_id,
        public_id=public_id,
        hostname=hostname,
        os_family=os_family,
        os_id=os_id,
        os_version=os_version,
        os_codename=os_codename,
        kernel_version=kernel_version,
        services=list(services) if services else None,
        cpu_avg_pct=cpu_avg,
        cpu_p95_pct=cpu_p95,
        cpu_peak_pct=cpu_peak,
        mem_avg_pct=mem_avg,
        mem_p95_pct=mem_p95,
        mem_peak_pct=mem_peak,
        iowait_p95_pct=iowait_p95,
        iowait_peak_pct=iowait_peak,
        cpu_run_queue_p95=cpu_run_queue_p95,
        mem_pages_input_rate_p95=mem_pages_input_rate_p95,
        cpu_cores=cpu_cores,
        mem_total_bytes=(mem_total_kb * 1024 if mem_total_kb is not None else None),
        disk_capacity_driving_mount=worst_mount,
        worst_mount_used_pct=worst_used,
        reboot_count=reboot_count,
        disk_iops_baseline=disk_iops,
        disk_throughput_kbps=disk_throughput,
        net_rx_kbps=net_rx,
        net_tx_kbps=net_tx,
        cpu_sufficiency=cpu_sufficiency,
        mem_sufficiency=mem_sufficiency,
        procs_blocked_p95=procs_blocked_p95,
        mem_swap_paging=mem_swap_paging,
        disk_await_p95_ms=disk_await_p95_ms,
        disk_capacity_runway_days=disk_capacity_runway_days,
        disk_inode_runway_days=disk_inode_runway_days,
        net_retrans_pct=net_retrans_pct,
        net_drop_pct=net_drop_pct,
        history_hours=history_hours,
        cpu_burst_ratio=cpu_burst_ratio,
        cpu_trend_slope=cpu_trend_slope,
        mem_trend_slope=mem_trend_slope,
        cpu_steal_p95_pct=cpu_steal_p95,
        cpu_percore_p95_max=cpu_percore_p95_max,
        procs_running_p95=procs_running_p95,
        oom_occurred=oom_occurred,
    )


@pytest.mark.parametrize(
    ("rec", "risk_level", "risk_label"),
    [
        ("under_provisioned", "high", "고위험"),
        ("idle", "attention", "주의 필요"),
        ("over_provisioned", "attention", "주의 필요"),
        ("optimal", "normal", "정상"),
        ("insufficient_data", "normal", "정상"),
    ],
)
def test_risk_mapping_all_recommendations(rec: str, risk_level: str, risk_label: str):
    level, label, badge = _RISK_FROM_RECOMMENDATION[rec]
    assert level == risk_level
    assert label == risk_label
    assert badge.startswith("rec-")


def test_report_row_cpu_variance():
    raw = _raw(cpu_p95=50.0, cpu_peak=100.0)
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.cpu_variance_ratio == 2.0


def test_report_row_uptime_days():
    boot = _NOW - timedelta(days=30, hours=5)
    raw = _raw(boot_time=boot)
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.uptime_days == 30


def test_report_row_uptime_none_when_boot_time_missing():
    raw = replace(_raw(), boot_time=None)
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.uptime_days is None


def test_report_row_under_provisioned_maps_to_high():
    raw = _raw(cpu_p95=95.0, cpu_peak=99.0, mem_p95=92.0, mem_peak=98.0, mem_swap_paging=True)
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.risk_level == "high"
    assert item.risk_label == "고위험"


def test_report_row_is_partial_by_unmeasured_saturation():
    assert to_report_row_item(_raw(cpu_p95=40.0, mem_p95=60.0, os_family="windows"), True, _NOW).is_partial is True
    assert (
        to_report_row_item(
            _raw(
                cpu_p95=40.0, mem_p95=60.0, os_family="linux", procs_running_p95=0.5, cpu_cores=4, disk_await_p95_ms=5.0
            ),
            True,
            _NOW,
        ).is_partial
        is False
    )


def test_report_row_windows_swap_not_high_risk():
    stats: dict[str, Any] = {
        "cpu_p95": 20.0,
        "cpu_peak": 25.0,
        "mem_p95": 92.0,
        "mem_peak": 95.0,
        "mem_swap_paging": True,
    }
    linux = to_report_row_item(_raw(os_family="linux", **stats), True, _NOW)
    windows = to_report_row_item(_raw(os_family="windows", **stats), True, _NOW)
    assert linux.risk_level == "high"
    assert windows.risk_level == "high"
    assert "스왑" in linux.diagnosis
    assert "스왑" not in windows.diagnosis


def test_role_distribution_counts_categories():
    raws = [
        _raw(server_id=1, services=[{"unit": "nginx.service", "sub": "running"}]),
        _raw(server_id=2, services=[{"unit": "postgresql.service", "sub": "running"}]),
        _raw(server_id=3, services=[{"unit": "nginx.service", "sub": "running"}]),
        _raw(server_id=4, services=None),
    ]
    dist = build_role_distribution(raws)
    assert dist["web"] == 2
    assert dist["db"] == 1
    assert dist["unknown"] == 1


def test_report_totals_sum_vcpu_memory_disk():
    raws = [
        _raw(
            server_id=1,
            cpu_cores=4,
            mem_total_kb=8 * 1024 * 1024,
            block_devices=[
                {"name": "sda", "size_bytes": 50 * 10**9, "type": "disk"},
                {"name": "sdb", "size_bytes": 100 * 10**9, "type": "disk"},
            ],
        ),
        _raw(
            server_id=2,
            cpu_cores=2,
            mem_total_kb=4 * 1024 * 1024,
            block_devices=[{"name": "vda", "size_bytes": 30 * 10**9, "type": "disk"}],
        ),
    ]
    t = compute_report_totals_from_raw(raws)
    assert t.total_vcpus == 6
    assert t.total_memory_gb == 12
    assert t.total_disk_gb == 167


def test_report_totals_handles_null_fields():
    raws = [_raw(cpu_cores=None, mem_total_kb=None, block_devices=[])]
    t = compute_report_totals_from_raw(raws)
    assert t.total_vcpus == 0
    assert t.total_memory_gb == 0
    assert t.total_disk_gb == 0


def _detail(
    *,
    id_: int,
    hostname: str,
    cpu_cores: int,
    mem_total_kb: int,
    disk_size: int,
    role_unit: str | None = None,
):
    return ServerDetail(
        id=id_,
        public_id=f"p{id_}",
        agent_id=f"00000000-0000-4000-8000-{id_:012d}",
        composite_id=f"m{id_}",
        machine_id=None,
        hostname=hostname,
        agent_version="1.0",
        os_family=None,
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15",
        cpu_cores=cpu_cores,
        cpu_model="x86",
        cpu_arch="x86_64",
        cpu_bits=64,
        mem_total_bytes=mem_total_kb * 1024,
        boot_time=None,
        agent_started_at=None,
        net_interfaces=[
            {
                "id": "52:54:00:12:34:56",
                "id_type": "mac",
                "name": "eth0",
                "kind": "physical",
                "speed_mbps": 1000,
                "gateway": None,
                "addresses": [{"address": "10.0.0.1", "prefix": 24, "family": "ipv4"}],
            }
        ],
        ip_external=None,
        block_devices=[{"name": "sda", "size_bytes": disk_size, "type": "disk"}],
        lvm_vgs=[],
        services=[{"unit": role_unit, "sub": "running"}] if role_unit else [],
        listen_ports=[],
        last_seen_at=_NOW,
    )


def test_environment_overview_aggregates():
    details = [
        _detail(
            id_=1,
            hostname="db-01",
            cpu_cores=4,
            mem_total_kb=8 * 1024 * 1024,
            disk_size=50 * 10**9,
            role_unit="postgresql.service",
        ),
        _detail(
            id_=2,
            hostname="web-01",
            cpu_cores=2,
            mem_total_kb=4 * 1024 * 1024,
            disk_size=100 * 10**9,
            role_unit="nginx.service",
        ),
    ]
    ov = build_environment_overview(details, online_count=1)
    assert ov.total == 2
    assert ov.online == 1
    assert ov.offline == 1
    assert ov.total_vcpus == 6
    assert ov.total_memory_gb == 12.0
    assert ov.total_disk_gb == 139
    assert ov.role_distribution == {"web": 1, "db": 1, "cache": 0, "mq": 0, "container": 0, "monitor": 0}
    assert ov.workload_total == 2


def test_environment_overview_memory_keeps_decimal():
    details = [_detail(id_=1, hostname="x", cpu_cores=1, mem_total_kb=int(2.5 * 1024 * 1024), disk_size=10**9)]
    ov = build_environment_overview(details, online_count=1)
    assert ov.total_memory_gb == 2.5


def test_environment_overview_utilization_default_empty():
    details = [_detail(id_=1, hostname="x", cpu_cores=1, mem_total_kb=1024 * 1024, disk_size=10**9)]
    ov = build_environment_overview(details, online_count=1)
    assert ov.utilization == []
    assert ov.util_sample_size == 0


@pytest.mark.parametrize(
    ("pct", "expected_color"),
    [
        (0.0, _UTIL_COLOR_GAUGE),
        (50.0, _UTIL_COLOR_GAUGE),
        (100.0, _UTIL_COLOR_GAUGE),
        (None, _UTIL_COLOR_NONE),
    ],
)
def test_environment_overview_utilization_bar_color(pct: float | None, expected_color: str):
    details = [_detail(id_=1, hostname="x", cpu_cores=1, mem_total_kb=1024 * 1024, disk_size=10**9)]
    util = EnvironmentUtilizationRaw(
        cpu_avg_pct=pct,
        mem_avg_pct=pct,
        disk_avg_pct=pct,
        sample_size=1,
    )
    ov = build_environment_overview(details, online_count=1, utilization=util)
    assert len(ov.utilization) == 3
    assert ov.utilization[0].label == "CPU"
    assert ov.utilization[1].label == "메모리"
    assert ov.utilization[2].label == "디스크 용량"
    for bar in ov.utilization:
        assert bar.bar_color == expected_color
    assert ov.util_sample_size == 1


@pytest.mark.parametrize(
    ("pct", "expected_dash"),
    [
        (0.0, 0.0),
        (50.0, 263.89 / 2),
        (100.0, 263.89),
        (150.0, 263.89),
        (-10.0, 0.0),
        (None, 0.0),
    ],
)
def test_environment_overview_utilization_dash_length(pct: float | None, expected_dash: float):
    details = [_detail(id_=1, hostname="x", cpu_cores=1, mem_total_kb=1024 * 1024, disk_size=10**9)]
    util = EnvironmentUtilizationRaw(
        cpu_avg_pct=pct,
        mem_avg_pct=pct,
        disk_avg_pct=pct,
        sample_size=1,
    )
    ov = build_environment_overview(details, online_count=1, utilization=util)
    for bar in ov.utilization:
        assert abs(bar.dash_length - expected_dash) < 0.1


def test_risk_donut_segments_order_and_colors():
    segs, total, under = build_risk_donut_segments(
        {
            "under_provisioned": 1,
            "over_provisioned": 2,
            "idle": 1,
            "optimal": 5,
            "insufficient_data": 1,
        }
    )
    assert [s.key for s in segs] == [
        "under_provisioned",
        "over_provisioned",
        "idle",
        "optimal",
        "insufficient_data",
    ]
    assert [s.label for s in segs] == [
        "자원 부족",
        "과다 할당",
        "유휴",
        "정상",
        "표본 부족",
    ]
    assert total == 10
    assert under == 1


def test_risk_donut_segments_dash_accumulates():
    segs, total, _ = build_risk_donut_segments({"under_provisioned": 1, "over_provisioned": 1, "optimal": 1})
    expected_each = _UTIL_DONUT_CIRC / 3
    assert abs(segs[0].dash_length - expected_each) < 0.1
    assert segs[0].dash_offset == 0.0
    assert abs(segs[1].dash_offset - (-expected_each)) < 0.1
    assert abs(segs[3].dash_offset - (-2 * expected_each)) < 0.1
    assert total == 3


def test_risk_donut_segments_zero_count_zero_length():
    segs, _, _ = build_risk_donut_segments({"under_provisioned": 0, "over_provisioned": 0, "optimal": 5})
    assert segs[0].dash_length == 0
    assert segs[1].dash_offset == 0
    assert abs(segs[3].dash_length - _UTIL_DONUT_CIRC) < 0.1


def test_risk_donut_segments_empty_total():
    segs, total, under = build_risk_donut_segments({})
    assert total == 0
    assert under == 0
    assert all(s.dash_length == 0 for s in segs)


def test_donut_segments_cover_every_recommendation():
    segment_keys = {key for key, _, _ in _DONUT_SEGMENT_DEFS}

    assert segment_keys == set(get_args(Recommendation.__value__))


def test_capacity_warning_active_causes_only_hit():
    item = to_capacity_warning_item(_raw(mem_p95=92.0, mem_swap_paging=True))
    assert item.active_causes == ["메모리 이용률", "메모리 포화"]


def test_capacity_warning_active_causes_multi_fixed_order():
    item = to_capacity_warning_item(_raw(mem_swap_paging=True, cpu_p95=95.0, mem_p95=90.0))
    assert item.active_causes == ["CPU 이용률", "메모리 이용률", "메모리 포화"]


def test_capacity_warning_active_causes_os_neutral_windows():
    item = to_capacity_warning_item(
        _raw(
            os_family="windows",
            mem_p95=92.0,
            cpu_cores=4,
            mem_pages_input_rate_p95=2000.0,
            cpu_run_queue_p95=12.0,
        )
    )
    assert "메모리 포화" in item.active_causes
    assert "CPU 포화" in item.active_causes
    assert "스왑" not in item.active_causes
    assert "Load" not in item.active_causes


def test_bullets_empty_when_no_rows():
    assert build_report_summary_bullets([]) == ["대상 서버 없음."]


def test_bullets_skip_risk_category_count():
    raws = [_raw(hostname="db-01", cpu_p95=95.0, cpu_peak=99.0, mem_p95=92.0, mem_peak=98.0, mem_swap_paging=True)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert not any("고위험" in b for b in bullets)
    assert not any("주의 필요" in b for b in bullets)


def test_bullets_disk_io_await_signal():
    raws = [_raw(hostname="io-01", disk_await_p95_ms=25.0, cpu_p95=50.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert any("디스크 I/O 포화" in b and "io-01" in b for b in bullets)


def test_bullets_mount_imminent_signal():
    raws = [
        _raw(
            hostname="full-01",
            worst_mount="/data",
            worst_used=90.0,
            disk_capacity_runway_days=12.0,
            cpu_p95=50.0,
            mem_p95=50.0,
        )
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert any("임박" in b and "full-01" in b and "/data" in b for b in bullets)


def test_bullets_reboot_signal_threshold_3():
    raws = [_raw(hostname="unstable-01", reboot_count=4, cpu_p95=50.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert any("재부팅" in b and "unstable-01" in b for b in bullets)


def test_bullets_saturation_signal():
    raws = [_raw(hostname="sat-01", procs_running_p95=5.0, cpu_cores=2, cpu_p95=85.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="engineer")
    assert any("CPU 포화" in b and "sat-01" in b for b in bullets)


def test_bullets_cpu_variance_signal():
    raws = [_raw(hostname="var-01", cpu_p95=30.0, cpu_peak=80.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="engineer")
    assert any("변동" in b and "var-01" in b for b in bullets)


def test_bullets_os_eol_signal():
    raws = [_raw(hostname="legacy-01", os_id="centos", os_version="7.9", cpu_p95=50.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert any("EOL" in b and "legacy-01" in b and "centos" in b for b in bullets)


def test_bullets_role_avg_cpu_signal():
    raws = [
        _raw(server_id=1, hostname="db-01", services=[{"unit": "postgresql.service"}], cpu_p95=85.0, mem_p95=50.0),
        _raw(server_id=2, hostname="db-02", services=[{"unit": "mysql.service"}], cpu_p95=90.0, mem_p95=50.0),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="engineer")
    assert any("db 계열" in b and "평균 CPU p95" in b for b in bullets)


def test_bullets_customer_view_excludes_saturation():
    raws = [_raw(hostname="sat-01", procs_running_p95=5.0, cpu_cores=2, cpu_p95=85.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="customer")
    assert not any("Saturation" in b for b in bullets)


def test_bullets_customer_view_excludes_cpu_variance():
    raws = [_raw(hostname="var-01", cpu_p95=30.0, cpu_peak=80.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="customer")
    assert not any("변동" in b for b in bullets)


def test_bullets_customer_view_excludes_role_avg_cpu():
    raws = [
        _raw(server_id=1, hostname="db-01", services=[{"unit": "postgresql.service"}], cpu_p95=85.0, mem_p95=50.0),
        _raw(server_id=2, hostname="db-02", services=[{"unit": "mysql.service"}], cpu_p95=90.0, mem_p95=50.0),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="customer")
    assert not any("평균 CPU p95" in b for b in bullets)


def test_compute_report_avg_p95_simple_average():
    raws = [
        _raw(server_id=1, cpu_p95=20.0, mem_p95=40.0),
        _raw(server_id=2, cpu_p95=40.0, mem_p95=60.0),
        _raw(server_id=3, cpu_p95=60.0, mem_p95=80.0),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    avg_cpu, avg_mem = compute_report_avg_p95(items)
    assert avg_cpu == 40.0
    assert avg_mem == 60.0


def test_compute_report_avg_p95_none_excluded():
    raws = [
        _raw(server_id=1, cpu_p95=30.0, mem_p95=None),
        _raw(server_id=2, cpu_p95=None, mem_p95=50.0),
        _raw(server_id=3, cpu_p95=60.0, mem_p95=70.0),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    avg_cpu, avg_mem = compute_report_avg_p95(items)
    assert avg_cpu == 45.0
    assert avg_mem == 60.0


def test_compute_report_avg_p95_all_none_returns_none():
    raws = [_raw(server_id=1, cpu_p95=None, mem_p95=None)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    avg_cpu, avg_mem = compute_report_avg_p95(items)
    assert avg_cpu is None
    assert avg_mem is None


def test_compute_report_avg_p95_empty_returns_none():
    avg_cpu, avg_mem = compute_report_avg_p95([])
    assert avg_cpu is None
    assert avg_mem is None


def test_bullets_customer_view_keeps_iowait_mount_reboot_eol():
    raws = [
        _raw(
            hostname="db-01",
            os_id="centos",
            os_version="7.9",
            cpu_p95=95.0,
            cpu_peak=99.0,
            mem_p95=92.0,
            mem_peak=98.0,
            mem_swap_paging=True,
            disk_await_p95_ms=30.0,
            worst_mount="/data",
            worst_used=90.0,
            disk_capacity_runway_days=10.0,
            reboot_count=5,
        ),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="customer")
    assert any("디스크 I/O 포화" in b and "db-01" in b for b in bullets)
    assert any("임박" in b and "db-01" in b for b in bullets)
    assert any("재부팅" in b and "db-01" in b for b in bullets)
    assert any("EOL" in b and "db-01" in b for b in bullets)
    assert not any("고위험" in b for b in bullets)


def test_bullets_normal_fallback_empty():
    raws = [_raw(hostname="ok-01", cpu_p95=50.0, cpu_peak=60.0, mem_p95=60.0, mem_peak=68.0, cpu_cores=4)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert bullets == []


def test_capacity_warning_item_fields():
    raw = _raw(cpu_p95=95.0, mem_p95=92.0, mem_swap_paging=True)
    item = to_capacity_warning_item(raw)
    assert item.public_id == raw.public_id
    assert item.hostname == raw.hostname
    assert item.active_causes == ["CPU 이용률", "메모리 이용률", "메모리 포화"]


@pytest.mark.parametrize(
    ("cpu_p95", "mem_p95", "mem_swap_paging", "expected_causes"),
    [
        (None, None, True, []),
        (95.0, 92.0, True, ["CPU 이용률", "메모리 이용률", "메모리 포화"]),
        (95.0, 92.0, False, ["CPU 이용률", "메모리 이용률"]),
        (95.0, 60.0, False, ["CPU 이용률"]),
        (50.0, 90.0, False, ["메모리 이용률"]),
        (50.0, 60.0, False, []),
    ],
)
def test_capacity_warning_item_active_causes(
    cpu_p95: float | None,
    mem_p95: float | None,
    mem_swap_paging: bool | None,
    expected_causes: list[str],
):
    raw = _raw(cpu_p95=cpu_p95, mem_p95=mem_p95, mem_swap_paging=mem_swap_paging)
    item = to_capacity_warning_item(raw)
    assert item.active_causes == expected_causes


def test_period_assessment_windows_os_aware_and_hit():
    stats = build_resource_stats(
        _raw(
            os_family="windows",
            cpu_p95=75.0,
            mem_p95=92.0,
            cpu_cores=4,
            cpu_run_queue_p95=12.0,
            mem_pages_input_rate_p95=2000.0,
            disk_await_p95_ms=30.0,
        ),
        disk_baseline=None,
    )
    pa = build_period_assessment(stats)
    cpu, mem, disk = pa.resources[0], pa.resources[1], pa.resources[2]
    assert [r.name for r in pa.resources] == ["CPU", "메모리", "스토리지", "네트워크"]
    assert cpu.sat_rows[0].value == "W 3.00"
    assert cpu.sat_rows[0].over
    assert mem.sat_rows[0].value == "W 2000/s"
    assert mem.sat_rows[0].over
    assert disk.sat_rows[0].value == "30ms"
    assert disk.sat_rows[0].over


def test_period_assessment_linux_signals_and_ok():
    stats = build_resource_stats(
        _raw(
            os_family="linux",
            cpu_p95=40.0,
            mem_p95=60.0,
            cpu_cores=4,
            procs_running_p95=1.0,
            mem_swap_paging=False,
            disk_await_p95_ms=5.0,
        ),
        disk_baseline=None,
    )
    pa = build_period_assessment(stats)
    cpu, mem, disk = pa.resources[0], pa.resources[1], pa.resources[2]
    assert cpu.sat_rows[0].label == "실행 큐"
    assert cpu.sat_rows[0].value == "L 0.25"
    assert mem.sat_rows[0].label == "페이징"
    assert mem.sat_rows[0].value == "L 없음"
    assert disk.sat_rows[0].value == "5ms"
    assert not any(r.over for r in cpu.sat_rows + mem.sat_rows + disk.sat_rows)


def test_period_assessment_unmeasured_when_counter_absent():
    stats = build_resource_stats(
        _raw(
            os_family="windows",
            cpu_p95=40.0,
            mem_p95=92.0,
            cpu_run_queue_p95=None,
            mem_pages_input_rate_p95=None,
            disk_await_p95_ms=None,
        ),
        disk_baseline=None,
    )
    pa = build_period_assessment(stats)
    cpu, mem, disk = pa.resources[0], pa.resources[1], pa.resources[2]
    assert not any(r.measured for r in cpu.sat_rows + mem.sat_rows + disk.sat_rows)


def test_period_assessment_defers_network_rate_threshold_under_low_traffic():
    stats = build_resource_stats(
        _raw(net_rx=right_sizing.NET_MIN_TRAFFIC_KBPS - 1, net_retrans_pct=2.0), disk_baseline=None
    )
    pa = build_period_assessment(stats)
    network = pa.resources[3]
    assert network.verdict_label == "정상"
    assert network.sat_rows[0].measured is True
    assert network.sat_rows[0].over is False


@pytest.mark.parametrize(
    ("os_id", "os_version", "should_match"),
    [
        ("centos", "7.9", True),
        ("rhel", "7.4", True),
        ("ubuntu", "18.04.5", True),
        ("debian", "10.11", True),
        ("centos", "8", True),
        ("ubuntu", "22.04", False),
        ("rocky", "9.6", False),
    ],
)
def test_os_eol_matching(os_id: str, os_version: str, should_match: bool):
    raw = _raw(os_id=os_id, os_version=os_version)
    item = to_os_eol_warning_item(raw, _NOW)
    assert (item is not None) == should_match


def test_windows_ambiguous_build_takes_least_severe_candidate():
    info = lookup_os_eol("windows", None, "17763.4644", _NOW.date())
    assert info is not None
    assert info.label == "Windows Server 2019"
    assert info.eol_iso == "2029-01-09"
    assert info.support_iso == "2024-01-09"
    assert info.status == "security_only"


def test_windows_2019_security_only_does_not_fire():
    assert resolve_os_eol("windows", None, "17763.4644", _NOW.date()) is None


def test_windows_2012_r2_fires():
    info = lookup_os_eol("windows", None, "9600.1", _NOW.date())
    assert info is not None
    assert info.status == "paid_only"
    assert info.extended_support_iso is not None
    assert resolve_os_eol("windows", None, "9600.1", _NOW.date()) is not None


@pytest.mark.parametrize(
    ("support", "eol", "extended", "expected"),
    [
        ("2030-01-01", "2035-01-01", "2040-01-01", "full"),
        ("2000-01-01", "2030-01-01", "2035-01-01", "security_only"),
        ("2000-01-01", "2000-06-01", "2035-01-01", "paid_only"),
        ("2000-01-01", "2000-06-01", "2001-01-01", "ended"),
        (None, "2030-01-01", "2035-01-01", "full"),
        (None, "2000-01-01", "2035-01-01", "paid_only"),
        ("2000-01-01", "2030-01-01", None, "security_only"),
        (None, "2000-01-01", None, "ended"),
        (None, "2030-01-01", None, "full"),
        ("2026-05-12", "2030-01-01", None, "security_only"),
        ("2000-01-01", "2026-05-12", "2035-01-01", "paid_only"),
        ("2000-01-01", "2000-06-01", "2026-05-12", "ended"),
        ("2030-01-01", "2030-01-01", "2036-01-01", "full"),
        ("2000-01-01", "2000-01-01", "2036-01-01", "paid_only"),
    ],
)
def test_classify_eol_boundaries(support: str | None, eol: str, extended: str | None, expected: str):
    assert _classify_eol(support, eol, extended, _NOW.date()) == expected


def test_windows_2022_full_support():
    info = lookup_os_eol("windows", None, "20348.2340", _NOW.date())
    assert info is not None
    assert info.status == "full"


def test_linux_carries_all_boundaries():
    ubuntu = lookup_os_eol("ubuntu", "22.04", "5.15", _NOW.date())
    assert ubuntu is not None
    assert ubuntu.support_iso is not None
    assert ubuntu.extended_support_iso is not None
    ended = lookup_os_eol("centos", "7.9", "3.10", _NOW.date())
    assert ended is not None
    assert ended.status == "ended"
    assert ended.extended_support_iso is None


def test_agent_unstable_item_fields():
    item = to_agent_unstable_item("pid", "h", 5)
    assert item.badge_text == "5회"
    assert item.badge_class == "attn-active"
    assert item.link_href == "/servers/pid"
    assert item.link_text == "h"


@pytest.mark.parametrize(
    ("os_id", "os_version"),
    [("centos", "7"), ("rhel", "7"), ("ubuntu", "18.04")],
)
def test_resolve_os_eol_known_eol_distros(os_id: str, os_version: str):
    assert resolve_os_eol(os_id, os_version, None, _NOW.date()) is not None


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"mem_p95": 92.0, "mem_swap_paging": True}, "메모리 부족 (스왑 발생)"),
        ({"cpu_p95": 20.0, "mem_p95": 30.0, "disk_await_p95_ms": 25.0}, "디스크 I/O 병목"),
        ({"cpu_cores": 4, "procs_running_p95": 5.0}, "CPU 포화"),
        ({"mem_p95": 92.0}, "메모리 압박"),
        ({"cpu_p95": 75.0}, "CPU 압박"),
        ({"cpu_p95": 50.0, "cpu_peak": 99.0}, "부하 변동 큼"),
        ({"cpu_p95": 0.8, "cpu_peak": 1.3, "net_rx": 0.0, "net_tx": 0.0}, "거의 미사용"),
        ({"cpu_p95": 20.0, "cpu_peak": 25.0, "mem_p95": 34.0, "mem_peak": 60.0}, "부하 변동 큼"),
        ({"cpu_p95": 2.0, "net_rx": 0.0, "net_tx": 0.0}, "거의 미사용"),
        ({"cpu_p95": 20.0, "mem_p95": 30.0}, "여유 있음"),
        ({"cpu_p95": 50.0, "mem_p95": 85.0}, "정상"),
    ],
)
def test_diagnosis_priority(kwargs: Any, expected: str):
    raw = _raw(**kwargs)
    item = to_report_row_item(raw, True, _NOW)
    assert item.diagnosis == expected


def test_report_row_item_disk_net_io_p95_peak_passthrough():
    raw = _raw(
        disk_iops=120,
        disk_throughput=850.0,
        net_rx=300.0,
        net_tx=180.0,
    )
    raw = replace(
        raw,
        disk_iops_p95=280.0,
        disk_iops_peak=540.0,
        disk_throughput_kbps_p95=2100.0,
        disk_throughput_kbps_peak=4800.0,
        net_rx_kbps_p95=700.0,
        net_rx_kbps_peak=1200.0,
        net_tx_kbps_p95=420.0,
        net_tx_kbps_peak=900.0,
    )
    item = to_report_row_item(raw, True, _NOW)
    assert item.disk_iops_p95 == 280.0
    assert item.disk_iops_peak == 540.0
    assert item.disk_throughput_kbps_p95 == 2100.0
    assert item.disk_throughput_kbps_peak == 4800.0
    assert item.net_rx_kbps_p95 == 700.0
    assert item.net_rx_kbps_peak == 1200.0
    assert item.net_tx_kbps_p95 == 420.0
    assert item.net_tx_kbps_peak == 900.0


def _rs(**kw: Any) -> right_sizing.ResourceStats:
    base = right_sizing.ResourceStats(
        cpu_p95_pct=None,
        cpu_peak_pct=None,
        cpu_cores=None,
        mem_p95_pct=None,
        disk_used_pct=None,
        net_avg_kbytes_per_s=None,
    )
    return dataclasses.replace(base, **kw)


def _host(recommendation: right_sizing.Recommendation) -> right_sizing.HostAssessment:
    return right_sizing.HostAssessment(resources={}, recommendation=recommendation)


@pytest.mark.parametrize(
    ("recommendation", "expected"),
    [
        ("over_provisioned", "관찰 지속"),
        ("optimal", "적정 — 유지"),
        ("insufficient_data", "표본 부족 — 관측 지속"),
    ],
)
def test_recommendation_action_fixed_phrases(recommendation: right_sizing.Recommendation, expected: str):
    assert _build_recommendation_action(_host(recommendation), _rs()) == expected


def test_recommendation_action_idle_strong_vs_weak():
    strong = _rs(cpu_peak_pct=0.5, net_avg_kbytes_per_s=0.5)
    weak = _rs(cpu_peak_pct=2.0, net_avg_kbytes_per_s=100.0)
    assert _build_recommendation_action(_host("idle"), strong) == "즉시 종료 검토"
    assert _build_recommendation_action(_host("idle"), weak) == "통합·재배치 검토"


def test_build_resource_stats_sums_net_rx_tx():
    assert build_resource_stats(_raw(net_rx=10.0, net_tx=5.0), disk_baseline=None).net_avg_kbytes_per_s == 15.0


def test_build_resource_stats_net_none_when_both_missing():
    assert build_resource_stats(_raw(), disk_baseline=None).net_avg_kbytes_per_s is None


def test_build_resource_stats_net_single_side_counts_other_as_zero():
    assert build_resource_stats(_raw(net_rx=10.0), disk_baseline=None).net_avg_kbytes_per_s == 10.0


def test_build_resource_stats_sample_sufficiency_min_of_measured():
    stats = build_resource_stats(
        _raw(cpu_p95=50.0, cpu_sufficiency=0.4, mem_p95=50.0, mem_sufficiency=0.9), disk_baseline=None
    )
    assert stats.sample_sufficiency == 0.4


def test_build_resource_stats_sample_sufficiency_ignores_unmeasured_axis():
    stats = build_resource_stats(_raw(cpu_p95=50.0, cpu_sufficiency=0.8, mem_sufficiency=0.1), disk_baseline=None)
    assert stats.sample_sufficiency == 0.8


def test_build_resource_stats_wires_adr0052_signals():
    stats = build_resource_stats(
        _raw(
            mem_total_kb=4 * 1024 * 1024,
            procs_blocked_p95=2.0,
            mem_swap_paging=True,
            disk_await_p95_ms=30.0,
            disk_capacity_runway_days=12.0,
            disk_inode_runway_days=40.0,
            net_retrans_pct=1.5,
            net_drop_pct=0.3,
            history_hours=200.0,
            cpu_burst_ratio=1.4,
            cpu_steal_p95=6.0,
            cpu_percore_p95_max=88.0,
            procs_running_p95=3.0,
            oom_occurred=True,
        ),
        disk_baseline=None,
    )
    assert stats.procs_blocked_p95 == 2.0
    assert stats.procs_running_p95 == 3.0
    assert stats.oom_occurred is True
    assert stats.mem_swap_paging is True
    assert stats.mem_total_mb == 4096
    assert stats.disk_await_p95_ms == 30.0
    assert stats.disk_capacity_runway_days == 12.0
    assert stats.disk_inode_runway_days == 40.0
    assert stats.net_retrans_pct == 1.5
    assert stats.net_drop_pct == 0.3
    assert stats.history_hours == 200.0
    assert stats.cpu_burst_ratio == 1.4
    assert stats.cpu_steal_p95_pct == 6.0
    assert stats.cpu_percore_p95_max == 88.0


def test_build_resource_stats_mem_total_mb_none_when_kb_none():
    assert build_resource_stats(_raw(mem_total_kb=None), disk_baseline=None).mem_total_mb is None


def test_build_resource_stats_utilization_trends_from_slopes():
    r1 = build_resource_stats(_raw(cpu_trend_slope=-0.1, mem_trend_slope=0.5, history_hours=40.0), disk_baseline=None)
    assert r1.cpu_utilization_trend_rising is False
    assert r1.memory_utilization_trend_rising is True
    r2 = build_resource_stats(_raw(cpu_trend_slope=0.05, mem_trend_slope=-1.0, history_hours=40.0), disk_baseline=None)
    assert r2.cpu_utilization_trend_rising is False
    assert r2.memory_utilization_trend_rising is False
    r3 = build_resource_stats(_raw(cpu_trend_slope=0.05, mem_trend_slope=0.5, history_hours=10.0), disk_baseline=None)
    assert r3.cpu_utilization_trend_rising is None
    assert r3.memory_utilization_trend_rising is None
    r4 = build_resource_stats(_raw(history_hours=40.0), disk_baseline=None)
    assert r4.cpu_utilization_trend_rising is None
    assert r4.memory_utilization_trend_rising is None
