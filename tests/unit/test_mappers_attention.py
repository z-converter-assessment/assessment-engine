from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from assessment_engine.domain import right_sizing
from assessment_engine.web.services.mappers.attention import (
    _NET_CONGESTED_COLOR,
    _UTIL_COLOR_GAUGE,
    _UTIL_COLOR_NONE,
    _UTIL_DONUT_CIRC,
    build_action_targets,
    build_environment_realtime,
    to_capacity_warning_item,
)
from tests.builders import report_row_raw

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw
    from assessment_engine.json_types import JsonObject

_NOW = datetime(2026, 5, 12, tzinfo=UTC)


def _snap(hostname: str, public_id: str, **kw: Any) -> JsonObject:
    d = {"hostname": hostname, "public_id": public_id}
    d.update(kw)
    return d


def _raw(**kw: Any) -> ReportRowRaw:
    return report_row_raw(net_interfaces=[], **kw)


def _two_snaps() -> list[JsonObject]:
    return [
        _snap(
            "h1",
            "p1",
            cpu_pct=50.0,
            mem_pct=50.0,
            disk_pct=30.0,
            cpu_cores=2,
            mem_used_bytes=1e9,
            mem_total_bytes=2e9,
            fs_used_gb=30.0,
            fs_total_gb=100.0,
            cpu_queue_value=1.5,
            cpu_queue_threshold=1.0,
            cpu_queue_crossed=True,
            disk_signal_value=10.0,
            disk_signal_threshold=20.0,
            disk_signal_kind="await",
            disk_signal_crossed=False,
            disk_util_pct=40.0,
            mem_pressure=True,
        ),
        _snap(
            "h2",
            "p2",
            cpu_pct=80.0,
            mem_pct=75.0,
            disk_pct=90.0,
            cpu_cores=8,
            mem_used_bytes=3e9,
            mem_total_bytes=4e9,
            fs_used_gb=90.0,
            fs_total_gb=100.0,
            cpu_queue_value=0.2,
            cpu_queue_threshold=1.0,
            cpu_queue_crossed=False,
            disk_signal_value=40.0,
            disk_signal_threshold=20.0,
            disk_signal_kind="await",
            disk_signal_crossed=True,
            disk_util_pct=80.0,
            mem_pressure=False,
        ),
    ]


def test_realtime_cpu_capacity_weighted_not_arithmetic():
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    cpu_bar = r.utilization[0]
    assert cpu_bar.label == "CPU"
    assert cpu_bar.pct == 74.0
    assert cpu_bar.bar_color == _UTIL_COLOR_GAUGE
    assert cpu_bar.dash_length == 74.0 / 100.0 * _UTIL_DONUT_CIRC


def test_realtime_mem_disk_are_used_over_total_ratio():
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    assert r.utilization[1].label == "메모리"
    assert r.utilization[1].pct == 66.7
    assert len(r.utilization) == 2


def test_realtime_online_offline_sample_size():
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    assert r.total == 5
    assert r.online == 2
    assert r.offline == 3
    assert r.sample_size == 2
    assert r.last_collected_at == _NOW


def test_realtime_saturation_counts():
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    labels = {d.label: (d.count, d.total) for d in r.saturation_donuts}
    assert labels["페이징"] == (1, 2)
    assert labels["디스크 I/O 조사 신호"] == (1, 2)
    assert labels["네트워크 혼잡"] == (0, 2)


def test_realtime_network_congestion_donut_counts_flagged_hosts():
    snaps = [
        _snap("h1", "p1", cpu_cores=1, net_congested=True),
        _snap("h2", "p2", cpu_cores=1, net_congested=False),
        _snap("h3", "p3", cpu_cores=1),
    ]
    r = build_environment_realtime(total=3, online=3, snapshots=snaps, last_collected_at=_NOW)
    labels = {d.label: (d.count, d.total) for d in r.saturation_donuts}
    assert labels["네트워크 혼잡"] == (1, 3)


def test_realtime_load_rows_hostname_sorted_with_all_axes():
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    assert [row.hostname for row in r.load_rows] == ["h1", "h2"]
    h2 = r.load_rows[1]
    assert h2.cpu.value == 80.0
    assert h2.cpu.display == "80.0%"
    assert h2.mem.value == 75.0
    assert h2.mem.display == "75.0%"
    assert h2.run_queue.value == 0.2
    assert h2.run_queue.display == "L 0.20 / 1"
    assert h2.disk_util.value == 80.0
    assert h2.disk_util.display == "80%"


def test_realtime_load_rows_paging_os_tagged_run_queue_not():
    snaps = [
        _snap("lin", "pl", os_family="linux", cpu_queue_value=0.8, cpu_queue_threshold=1.0, paging_rate=3.0),
        _snap("win", "pw", os_family="windows", cpu_queue_value=1.1, cpu_queue_threshold=2.0, paging_rate=25.0),
        _snap("none", "pn", cpu_queue_value=0.5, cpu_queue_threshold=1.0, paging_rate=1.0),
    ]
    r = build_environment_realtime(total=3, online=3, snapshots=snaps, last_collected_at=_NOW)
    rows = {row.hostname: row for row in r.load_rows}
    assert rows["lin"].run_queue.display == "L 0.80 / 1"
    assert rows["lin"].paging.display == "L 3.00/s"
    assert rows["win"].run_queue.display == "W 1.10 / 2"
    assert rows["win"].paging.display == "W 25.00/s"
    assert rows["none"].run_queue.display == "L 0.50 / 1"
    assert rows["none"].paging.display == "L 1.00/s"
    assert rows["win"].run_queue.value == 1.1


def test_realtime_load_rows_paging_shows_small_nonzero_rate():
    snaps = [_snap("h1", "p1", os_family="linux", paging_rate=0.03, mem_pressure=True)]
    r = build_environment_realtime(total=1, online=1, snapshots=snaps, last_collected_at=_NOW)
    assert r.load_rows[0].paging.display == "L 0.03/s"
    labels = {d.label: d.count for d in r.saturation_donuts}
    assert labels["페이징"] == 1


def test_realtime_load_rows_threshold_exceeded_cells_get_congested_color():
    r = build_environment_realtime(total=5, online=2, snapshots=_two_snaps(), last_collected_at=_NOW)
    h1, h2 = r.load_rows[0], r.load_rows[1]
    assert h1.run_queue.color == _NET_CONGESTED_COLOR
    assert h1.disk_io.color == ""
    assert h2.run_queue.color == ""
    assert h2.disk_io.color == _NET_CONGESTED_COLOR
    assert h1.cpu.color == ""
    assert h1.disk_util.color == ""

    paging_snaps = [
        _snap("pressured", "pp", paging_rate=5.0, mem_pressure=True),
        _snap("normal", "pn", paging_rate=0.0, mem_pressure=False),
    ]
    pr = build_environment_realtime(total=2, online=2, snapshots=paging_snaps, last_collected_at=_NOW)
    rows = {row.hostname: row for row in pr.load_rows}
    assert rows["pressured"].paging.color == _NET_CONGESTED_COLOR
    assert rows["normal"].paging.color == ""


def test_realtime_load_rows_network_shows_congestion_verdict_not_throughput():
    snaps = [
        _snap("congested", "pc", net_kbps=999.0, net_congested=True),
        _snap("ok", "po", net_kbps=1.0, net_congested=False),
    ]
    r = build_environment_realtime(total=2, online=2, snapshots=snaps, last_collected_at=_NOW)
    rows = {row.hostname: row for row in r.load_rows}
    assert rows["congested"].network.display == "혼잡"
    assert rows["congested"].network.color == _NET_CONGESTED_COLOR
    assert rows["ok"].network.display == "정상"
    assert rows["ok"].network.color == ""


def test_realtime_load_rows_keeps_every_snapshot_including_none():
    snaps = [
        _snap("a", "pa", cpu_pct=10.0, cpu_cores=1),
        _snap("b", "pb", cpu_pct=None, cpu_cores=4),
        _snap("c", "pc", cpu_pct=90.0, cpu_cores=1),
    ]
    r = build_environment_realtime(total=3, online=3, snapshots=snaps, last_collected_at=None)
    assert r.utilization[0].pct == 50.0
    assert [row.hostname for row in r.load_rows] == ["a", "b", "c"]
    b_row = next(row for row in r.load_rows if row.hostname == "b")
    assert b_row.cpu.value is None
    assert b_row.cpu.display == "—"
    assert r.sample_size == 3


def test_realtime_empty_snapshots_none_avgs_and_no_rows():
    r = build_environment_realtime(total=3, online=0, snapshots=[], last_collected_at=None)
    for bar in r.utilization:
        assert bar.pct is None
        assert bar.bar_color == _UTIL_COLOR_NONE
        assert bar.dash_length == 0.0
    assert r.sample_size == 0
    assert r.offline == 3
    assert r.load_rows == []
    assert all(d.count == 0 and d.total == 0 for d in r.saturation_donuts)
    assert all(d.dash_length == 0.0 for d in r.saturation_donuts)


def _classified_raws() -> list[ReportRowRaw]:
    return [
        _raw(hostname="x", public_id="px"),
        _raw(hostname="op", public_id="pop", cpu_p95_pct=50.0, mem_p95_pct=85.0),
        _raw(hostname="i", public_id="pi", cpu_p95_pct=2.0, net_rx_kbps=0.0, net_tx_kbps=0.0),
        _raw(hostname="o", public_id="po", cpu_p95_pct=20.0, mem_p95_pct=30.0),
        _raw(hostname="u", public_id="pu", mem_p95_pct=92.0, mem_swap_paging=True),
    ]


def test_action_targets_sorted_by_classification_order():
    at = build_action_targets(_classified_raws())
    assert [h.classification for h in at.hosts] == [
        "under_provisioned",
        "over_provisioned",
        "idle",
        "optimal",
        "insufficient_data",
    ]
    assert [h.classification_rank for h in at.hosts] == [0, 1, 2, 3, 4]


def test_action_targets_severity_then_hostname_tiebreak():
    raws = [
        _raw(hostname="zebra", public_id="pz", cpu_p95_pct=20.0, mem_p95_pct=30.0),
        _raw(hostname="alpha", public_id="pa", cpu_p95_pct=20.0, mem_p95_pct=30.0),
    ]
    at = build_action_targets(raws)
    assert all(h.classification == "over_provisioned" for h in at.hosts)
    assert [h.hostname for h in at.hosts] == ["alpha", "zebra"]


def test_action_targets_counts():
    at = build_action_targets(_classified_raws())
    assert at.total == 5
    assert at.under_count == 1


def test_action_targets_efficiency_aggregates_over_and_idle_only():
    at = build_action_targets(_classified_raws())
    assert at.efficiency_count == 2
    assert at.efficiency_vcpus == 4
    assert at.efficiency_memory_gb == 4.0
    assert at.efficiency_disk_gb == 93


def test_action_targets_empty_raws():
    at = build_action_targets([])
    assert at.hosts == []
    assert at.total == 0
    assert at.under_count == 0
    assert at.efficiency_count == 0
    assert at.efficiency_vcpus == 0
    assert at.efficiency_memory_gb == 0.0
    assert at.efficiency_disk_gb == 0


def test_action_targets_reuses_capacity_warning_classification():
    raw = _raw(hostname="u", public_id="pu", mem_p95_pct=92.0, mem_swap_paging=True)
    direct = to_capacity_warning_item(raw)
    at = build_action_targets([raw])
    assert at.hosts[0].classification == direct.classification
    assert at.hosts[0].severity_score == direct.severity_score
    assert at.hosts[0].classification == "under_provisioned"
    assert right_sizing.CLASSIFICATION_ORDER[at.hosts[0].classification] == 0


def test_capacity_warning_item_disk_io_status_symmetric_with_network():
    raw = _raw(hostname="h", public_id="ph", cpu_p95_pct=50.0, mem_p95_pct=85.0, disk_await_p95_ms=25.0)
    item = to_capacity_warning_item(raw)
    assert item.classification == "optimal"
    assert item.disk_io_status_label == "I/O 병목"
    assert item.disk_io_status_color == _NET_CONGESTED_COLOR


def test_capacity_warning_item_disk_io_status_unmeasured_by_default():
    item = to_capacity_warning_item(_raw())
    assert item.disk_io_status_label == "미측정"
    assert item.disk_io_status_color == ""


def test_capacity_warning_item_spec_display_matches_server_list_formula():
    item = to_capacity_warning_item(_raw())
    assert item.spec_display == "2코어 · 2.0GB · 47GB"
