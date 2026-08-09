from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from assessment_engine.domain import right_sizing
from assessment_engine.web.services.mappers.right_sizing_api import build_right_sizing_entry
from assessment_engine.web.view_models.right_sizing_api import RightSizingServer
from tests.builders import report_row_raw

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ReportRowRaw

_SERVER_SCHEMA = TypeAdapter(RightSizingServer)

_NOW = datetime(2026, 5, 12, tzinfo=UTC)


def _raw(
    *,
    server_id: int = 1,
    public_id: str = "a",
    hostname: str = "h",
    os_family: str | None = None,
    os_id: str = "ubuntu",
    os_version: str = "22.04",
    cpu_p95: float | None = None,
    cpu_peak: float | None = None,
    mem_p95: float | None = None,
    cpu_cores: int | None = 2,
    mem_total_bytes: int | None = 2 * 1024**3,
    procs_running_p95: float | None = None,
    mem_swap_paging: bool | None = False,
    disk_await_p95_ms: float | None = None,
    cpu_sufficiency: float | None = None,
    mem_sufficiency: float | None = None,
    net_rx_kbps: float | None = None,
    net_tx_kbps: float | None = None,
    net_retrans_pct: float | None = None,
    net_drop_pct: float | None = None,
) -> ReportRowRaw:
    return report_row_raw(
        server_id=server_id,
        public_id=public_id,
        hostname=hostname,
        os_family=os_family,
        os_id=os_id,
        os_version=os_version,
        cpu_p95_pct=cpu_p95,
        cpu_peak_pct=cpu_peak,
        mem_p95_pct=mem_p95,
        cpu_cores=cpu_cores,
        mem_total_bytes=mem_total_bytes,
        procs_running_p95=procs_running_p95,
        mem_swap_paging=mem_swap_paging,
        disk_await_p95_ms=disk_await_p95_ms,
        cpu_sufficiency=cpu_sufficiency,
        mem_sufficiency=mem_sufficiency,
        net_rx_kbps=net_rx_kbps,
        net_tx_kbps=net_tx_kbps,
        net_retrans_pct=net_retrans_pct,
        net_drop_pct=net_drop_pct,
        block_devices=None,
        boot_time=None,
    )


def _under_mem_root():
    return _raw(
        os_family="linux",
        cpu_p95=88.0,
        mem_p95=94.0,
        cpu_cores=4,
        procs_running_p95=6.0,
        mem_swap_paging=True,
        disk_await_p95_ms=25.0,
        mem_total_bytes=8 * 1024**3,
    )


def test_saturation_is_raw_numeric_not_display_string():
    e = build_right_sizing_entry(
        _raw(os_family="linux", cpu_p95=75.0, cpu_cores=4, procs_running_p95=6.0), is_online=True
    )
    sat = e["resources"]["cpu"]["saturation"]
    assert isinstance(sat["value"], (int, float))
    assert sat["value"] == 1.5
    assert isinstance(sat["threshold"], (int, float))
    assert sat["threshold"] == 1.0
    assert sat["unit"] == "per_core"
    assert sat["measured"] is True
    assert sat["saturated"] is True
    assert sat["signal"] == "run queue (procs_running)/core"


def test_disk_io_saturation_await_numeric():
    e = build_right_sizing_entry(_raw(os_family="linux", disk_await_p95_ms=30.0), is_online=True)
    sat = e["resources"]["disk"]["io"]["saturation"]
    assert sat["value"] == 30.0
    assert sat["threshold"] == 20
    assert sat["unit"] == "ms"
    assert sat["saturated"] is True


def test_saturation_unmeasured_is_null():
    e = build_right_sizing_entry(_raw(os_family="windows", cpu_p95=40.0, cpu_cores=4), is_online=True)
    sat = e["resources"]["cpu"]["saturation"]
    assert sat["value"] is None
    assert sat["measured"] is False


def test_recommendation_structure_independent_actions_despite_causal_link():
    e = build_right_sizing_entry(_under_mem_root(), is_online=True)
    assert e["classification"] == "under_provisioned"
    rec = e["recommendation"]
    assert set(rec) == {"summary", "kind", "actions", "suppressed"}
    assert rec["kind"] == "provision"
    action_res = {a["resource"] for a in rec["actions"]}
    assert action_res == {"memory", "cpu", "disk_io"}
    assert rec["suppressed"] == []


def test_action_target_is_typed_int():
    e = build_right_sizing_entry(_under_mem_root(), is_online=True)
    mem = next(a for a in e["recommendation"]["actions"] if a["resource"] == "memory")
    assert mem["op"] == "increase"
    assert isinstance(mem["target_mb"], int)
    assert mem["target_mb"] > 0


def test_independent_under_actions_all_present():
    e = build_right_sizing_entry(
        _raw(os_family="linux", cpu_p95=88.0, cpu_cores=4, disk_await_p95_ms=25.0, mem_p95=50.0),
        is_online=True,
    )
    rec = e["recommendation"]
    action_res = {a["resource"] for a in rec["actions"]}
    assert "cpu" in action_res
    assert "disk_io" in action_res
    assert rec["suppressed"] == []
    tier = next(a for a in rec["actions"] if a["resource"] == "disk_io")
    assert tier["op"] == "tier_up"
    assert "target_gb" not in tier


def test_optimal_maintain_no_actions():
    e = build_right_sizing_entry(
        _raw(os_family="linux", cpu_p95=65.0, mem_p95=85.0, cpu_cores=4, procs_running_p95=1.0),
        is_online=True,
    )
    rec = e["recommendation"]
    assert rec["kind"] == "maintain"
    assert rec["actions"] == []
    assert rec["suppressed"] == []


def test_network_signals_numeric_and_congested():
    e = build_right_sizing_entry(
        _raw(
            os_family="linux",
            cpu_p95=50.0,
            cpu_cores=4,
            net_rx_kbps=right_sizing.NET_MIN_TRAFFIC_KBPS,
            net_retrans_pct=2.0,
            net_drop_pct=0.0,
        ),
        is_online=True,
    )
    net = e["network"]
    assert net["status"] == "congested"
    assert net["congested"] is True
    rt = net["signals"]["retransmit_pct"]
    assert rt["value"] == 2.0
    assert rt["threshold"] == 1.0
    assert rt["exceeded"] is True
    assert net["signals"]["drop_pct"]["exceeded"] is False


def test_network_rate_signal_is_deferred_when_traffic_is_low():
    e = build_right_sizing_entry(
        _raw(net_rx_kbps=right_sizing.NET_MIN_TRAFFIC_KBPS - 1, net_retrans_pct=2.0), is_online=True
    )
    net = e["network"]
    assert net["status"] == "quality_ok"
    assert net["signals"]["retransmit_pct"]["measured"] is True
    assert net["signals"]["retransmit_pct"]["exceeded"] is None


def test_downsize_recommendation_observes_when_sample_coverage_is_low():
    e = build_right_sizing_entry(
        _raw(
            os_family="linux",
            cpu_p95=10.0,
            mem_p95=70.0,
            cpu_cores=8,
            procs_running_p95=0.5,
            net_rx_kbps=500.0,
            cpu_sufficiency=0.4,
            mem_sufficiency=0.4,
        ),
        is_online=True,
    )
    rec = e["recommendation"]
    assert e["classification"] == "over_provisioned"
    assert rec["summary"] == "관찰 지속"
    assert rec["kind"] == "observe"
    assert rec["actions"] == []


def test_hostname_ambiguous_flag_passthrough():
    e = build_right_sizing_entry(_raw(os_family="linux"), is_online=True, hostname_ambiguous=True)
    assert e["hostname_ambiguous"] is True
    e2 = build_right_sizing_entry(_raw(os_family="linux"), is_online=True)
    assert e2["hostname_ambiguous"] is False


def test_current_resource_values_paired_with_target():
    e = build_right_sizing_entry(_under_mem_root(), is_online=True)
    r = e["resources"]
    assert r["cpu"]["current_cores"] == 4
    assert r["memory"]["current_mb"] == 8192
    assert "current_gb" in r["disk"]["capacity"]


def test_classification_and_labels_present():
    e = build_right_sizing_entry(_under_mem_root(), is_online=True)
    assert e["classification"] == "under_provisioned"
    assert isinstance(e["classification_label"], str)
    assert e["classification_label"]
    assert "메모리" in (e["root_cause"] or "")


def test_evidence_labels_no_raw_enum_leak():
    from assessment_engine.domain.right_sizing import TRIGGER_LABEL_KO
    from assessment_engine.web.services.mappers.right_sizing_api import _evidence_labels

    all_triggers = list(TRIGGER_LABEL_KO)
    labels = _evidence_labels(all_triggers)
    leaked = [k for k, lbl in zip(all_triggers, labels, strict=True) if k == lbl]
    assert not leaked, f"raw enum 누출: {leaked}"
    assert "mem_oom" not in labels
    assert "net_retrans" not in labels


def test_entry_matches_response_schema():
    scenarios = [
        _raw(os_family="linux", cpu_p95=75.0, cpu_cores=4, procs_running_p95=6.0),
        _raw(os_family="windows", cpu_p95=40.0, cpu_cores=4),
        _raw(os_family="linux", disk_await_p95_ms=30.0),
        _raw(os_family="linux", mem_p95=92.0, mem_total_bytes=8 * 1024**3),
    ]
    for raw in scenarios:
        entry = build_right_sizing_entry(raw, is_online=True)
        _SERVER_SCHEMA.validate_python(entry)
