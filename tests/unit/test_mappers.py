import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from assessment_engine.db.dtos.outbound import (
    MetricGapWarningRaw,
    MountUsageRaw,
    ReportRowRaw,
    ServerDetail,
    ServerSummary,
    StorageWithUsage,
)
from assessment_engine.web.services.mappers.attention import (
    to_gap_warning_item,
)
from assessment_engine.web.services.mappers.host_display import spec_display_line
from assessment_engine.web.services.mappers.os_eol import (
    windows_legacy_version_from_build,
    windows_short_label_from_product_name,
)
from assessment_engine.web.services.mappers.server import (
    _to_disk_item,
    _to_listen_port_item,
    _to_service_item,
    _usage_badge_class,
    _usage_severity,
    build_server_inventory,
    enrich_server_detail,
    infer_role,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
    workload_category_counter,
)


@pytest.mark.parametrize(
    ("pct", "severity"),
    [
        (None, "ok"),
        (0.0, "ok"),
        (50.0, "ok"),
        (74.9, "ok"),
        (75.0, "warn"),
        (89.9, "warn"),
        (90.0, "danger"),
        (100.0, "danger"),
    ],
)
def test_usage_severity(pct: float | None, severity: str):
    assert _usage_severity(pct) == severity


def test_usage_badge_class_none_returns_empty():
    assert _usage_badge_class(None) == ""


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        (50.0, "badge-ok"),
        (80.0, "badge-warn"),
        (95.0, "badge-danger"),
    ],
)
def test_usage_badge_class(pct: float, expected: str):
    assert _usage_badge_class(pct) == expected


def test_to_disk_item_returns_none_for_non_physical():
    assert _to_disk_item({"name": "sda1"}) is None
    assert _to_disk_item({"name": "dm-0"}) is None
    assert _to_disk_item({"name": "loop0"}) is None


def test_to_disk_item_for_physical():
    item = _to_disk_item({"name": "sda", "size_bytes": 10**9, "type": "disk", "kind": "physical"})
    assert item is not None
    assert item.name == "sda"
    assert item.size_gb == 0.93


def test_to_listen_port_item_significant():
    for port in (80, 3389, 8080):
        assert _to_listen_port_item({"proto": "tcp", "addr": "0.0.0.0", "port": port, "uid": 0}).is_significant is True


def test_to_listen_port_item_dynamic_port():
    item = _to_listen_port_item({"proto": "tcp", "addr": "0.0.0.0", "port": 49500, "uid": 1000})
    assert item.is_significant is False


def test_to_service_item_without_listen_ports_has_empty_ports():
    item = _to_service_item({"unit": "nginx.service", "sub": "running"})
    assert item.ports == []
    assert item.category == "web"
    assert item.display_name == "nginx"


def test_to_service_item_with_listen_ports_matches():
    item = _to_service_item(
        {"unit": "nginx.service", "sub": "running"},
        listen_ports=[{"proto": "tcp", "port": 80, "uid": 0, "comm": "nginx"}],
    )
    pairs = {(p.proto, p.port) for p in item.ports}
    assert ("tcp", 80) in pairs


def _summary(**overrides: Any) -> ServerSummary:
    base = ServerSummary(
        id=1,
        public_id="pub-1",
        composite_id="m-1",
        hostname="host",
        os_id="ubuntu",
        os_version="22.04",
        kernel_version=None,
        product_name=None,
        cpu_cores=4,
        mem_total_bytes=8 * 1024**3,
        ip_external=None,
        block_devices=[{"name": "sda", "size_bytes": 100 * 10**9, "type": "disk"}],
        service_categories=[],
        last_seen_at=datetime.now(UTC),
    )
    return dataclasses.replace(base, **overrides)


def test_list_item_storage_total_sum():
    summary = _summary(
        block_devices=[
            {"name": "sda", "size_bytes": 100 * 10**9, "type": "disk"},
            {"name": "sdb", "size_bytes": 50 * 10**9, "type": "disk"},
            {"name": "loop0", "size_bytes": 999 * 10**9, "type": "loop"},
        ]
    )
    item = to_server_list_item(summary)
    assert item.storage_total_gb == 139.7


def test_list_item_badges_from_service_categories():
    summary = _summary(service_categories=["db", "web"])
    item = to_server_list_item(summary)
    assert {s.category for s in item.known_services} == {"web", "db"}
    assert item.show_unknown_badge is False


def test_list_item_no_categories_no_badge():
    summary = _summary(service_categories=[])
    item = to_server_list_item(summary)
    assert item.known_services == []
    assert item.show_unknown_badge is False


def test_list_item_os_display():
    item = to_server_list_item(_summary(os_id="ubuntu", os_version="22.04"))
    assert item.os_display == "ubuntu 22.04"
    item_partial = to_server_list_item(_summary(os_id=None, os_version="22.04"))
    assert item_partial.os_display == "22.04"
    item_none = to_server_list_item(_summary(os_id=None, os_version=None))
    assert item_none.os_display == "-"


@pytest.mark.parametrize(
    ("kernel_version", "expected"),
    [
        ("9600", "2012 R2"),
        ("9200", "2012"),
        ("7601", "2008 R2"),
        ("6003", "2008"),
        ("3790", "2003"),
        ("2195", "2000"),
        ("9200.1234", "2012"),
        ("14393", None),
        ("19045", None),
        ("", None),
        (None, None),
    ],
)
def test_windows_legacy_version_from_build(kernel_version: str | None, expected: str | None):
    assert windows_legacy_version_from_build(kernel_version) == expected


def test_list_item_os_display_windows_legacy_from_build():
    item = to_server_list_item(_summary(os_id="windows", os_version="", kernel_version="9200"))
    assert item.os_display == "windows 2012"
    item_ver = to_server_list_item(_summary(os_id="windows", os_version="22H2", kernel_version="19045"))
    assert item_ver.os_display == "windows 22H2"
    item_2016 = to_server_list_item(_summary(os_id="windows", os_version="", kernel_version="14393"))
    assert item_2016.os_display == "windows"


@pytest.mark.parametrize(
    ("product_name", "expected"),
    [
        ("Windows Server 2019 Standard", "2019"),
        ("Windows Server 2012 R2 Datacenter", "2012 R2"),
        ("Windows Server 2016 Standard Evaluation", "2016"),
        ("Windows Server 2022 Datacenter", "2022"),
        ("Windows Server Datacenter", None),
        ("Windows 10 Pro", "10"),
        ("Windows 11 Enterprise", "11"),
        (None, None),
        ("", None),
    ],
)
def test_windows_short_label_from_product_name(product_name: str | None, expected: str | None):
    assert windows_short_label_from_product_name(product_name) == expected


def test_list_item_os_display_windows_product_name_overrides_display_version():
    item = to_server_list_item(
        _summary(
            os_id="windows",
            os_version="1809",
            kernel_version="17763.4644",
            product_name="Windows Server 2019 Standard",
        )
    )
    assert item.os_display == "windows 2019"
    item_sac = to_server_list_item(
        _summary(
            os_id="windows",
            os_version="1809",
            kernel_version="17763.4644",
            product_name="Windows Server Datacenter",
        )
    )
    assert item_sac.os_display == "windows 1809"
    item_legacy = to_server_list_item(
        _summary(os_id="windows", os_version="", kernel_version="9200", product_name=None)
    )
    assert item_legacy.os_display == "windows 2012"


def _detail(**overrides: Any) -> ServerDetail:
    base = ServerDetail(
        id=1,
        public_id="pub-1",
        agent_id="00000000-0000-4000-8000-000000000001",
        composite_id="m-1",
        machine_id=None,
        hostname="host",
        agent_version="1.0.0",
        os_family=None,
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15.0",
        cpu_cores=4,
        cpu_model="test-cpu",
        cpu_arch="x86_64",
        cpu_bits=64,
        mem_total_bytes=8 * 1024**3,
        boot_time=datetime(2026, 1, 1, tzinfo=UTC),
        agent_started_at=datetime(2026, 1, 1, tzinfo=UTC),
        net_interfaces=[
            {
                "id": "52:54:00:12:34:56",
                "id_type": "mac",
                "name": "eth0",
                "kind": "physical",
                "speed_mbps": 1000,
                "gateway": "10.0.0.254",
                "addresses": [{"address": "10.0.0.1", "prefix": 24, "family": "ipv4"}],
            }
        ],
        ip_external=None,
        block_devices=[{"name": "sda", "size_bytes": 100 * 10**9, "type": "disk"}],
        lvm_vgs=[],
        services=[
            {"unit": "nginx.service", "sub": "running"},
            {"unit": "ssh.service", "sub": "running"},
        ],
        listen_ports=[
            {"proto": "tcp", "addr": "0.0.0.0", "port": 80, "uid": 0, "comm": "nginx"},
            {"proto": "tcp", "addr": "0.0.0.0", "port": 22, "uid": 0, "comm": "sshd"},
            {"proto": "tcp", "addr": "0.0.0.0", "port": 9999, "uid": 0, "comm": "customapp"},
        ],
        last_seen_at=datetime.now(UTC),
    )
    return dataclasses.replace(base, **overrides)


def test_to_server_detail_basic():
    resp = to_server_detail(_detail())
    assert resp.hostname == "host"
    assert len(resp.disks) == 1
    assert resp.disks[0].name == "sda"
    assert resp.disk_total_gb == 93.13
    assert resp.os_display == "ubuntu 22.04"
    assert resp.cpu_display == "test-cpu 4 cores"


def test_to_server_detail_known_services_with_ports():
    resp = to_server_detail(_detail())
    nginx = next(s for s in resp.known_services if s.category == "web")
    assert any(p.port == 80 for p in nginx.ports)


def test_to_server_detail_key_listen_ports_excludes_service_mapped():
    resp = to_server_detail(_detail())
    assert all(lp.port not in (80, 22) for lp in resp.key_listen_ports)
    assert any(lp.port == 9999 for lp in resp.key_listen_ports)


def test_enrich_server_detail_idempotent():
    resp = to_server_detail(_detail())
    sorted_before = list(resp.sorted_services)
    known_before = list(resp.known_services)
    key_ports_before = list(resp.key_listen_ports)

    resp_again = enrich_server_detail(resp)

    assert resp_again is resp
    assert resp.sorted_services == sorted_before
    assert resp.known_services == known_before
    assert resp.key_listen_ports == key_ports_before


def test_workload_counter_listen_only_rescues_opaque_name():
    services = [{"unit": "MyCorpThing", "sub": "running"}]
    listen = [
        {"proto": "tcp", "port": 1433, "comm": "sqlservr"},
        {"proto": "tcp", "port": 22, "comm": "sshd"},
    ]
    assert dict(workload_category_counter(services, listen)) == {"db": 1}


def test_workload_counter_no_double_count():
    services = [{"unit": "nginx.service", "sub": "running"}]
    listen = [
        {"proto": "tcp", "port": 80, "comm": "nginx"},
        {"proto": "tcp", "port": 443, "comm": "nginx"},
    ]
    assert dict(workload_category_counter(services, listen)) == {"web": 1}


def test_workload_counter_container_single_instance():
    services = [
        {"unit": "docker.service", "sub": "running"},
        {"unit": "containerd.service", "sub": "running"},
        {"unit": "nginx.service", "sub": "running"},
        {"unit": "apache2.service", "sub": "running"},
    ]
    counter = dict(workload_category_counter(services, []))
    assert counter == {"container": 1, "web": 2}


def test_enrich_container_single_badge():
    resp = to_server_detail(
        _detail(
            os_family="linux",
            services=[
                {"unit": "docker.service", "sub": "running"},
                {"unit": "containerd.service", "sub": "running"},
            ],
            listen_ports=[],
        )
    )
    containers = [s for s in resp.known_services if s.category == "container"]
    assert len(containers) == 1


def test_workload_counter_listen_port_only_comm_null():
    services = [{"unit": "opaque", "sub": "running"}]
    listen = [{"proto": "tcp", "port": 6379, "comm": None}]
    assert dict(workload_category_counter(services, listen)) == {"cache": 1}


def test_infer_role_union_dominant():
    services = [{"unit": "MSSQL$PROD", "sub": "running"}, {"unit": "W3SVC", "sub": "running"}]
    listen = [{"proto": "tcp", "port": 1433, "comm": "sqlservr"}]
    assert infer_role(services, listen) in {"db", "web"}


def test_enrich_listen_only_synthetic_badge():
    resp = to_server_detail(
        _detail(
            os_family="windows",
            services=[{"unit": "MyCorpThing", "sub": "running"}],
            listen_ports=[{"proto": "tcp", "addr": "0.0.0.0", "port": 1433, "uid": None, "comm": "sqlservr"}],
        )
    )
    db = next(s for s in resp.known_services if s.category == "db")
    assert db.unit == ""
    assert db.display_name == ""
    assert any(p.port == 1433 for p in db.ports)
    assert resp.show_unknown_badge is False


def test_enrich_category_port_aggregation_web_iis():
    resp = to_server_detail(
        _detail(
            os_family="windows",
            services=[{"unit": "W3SVC", "sub": "running"}],
            listen_ports=[
                {"proto": "tcp", "addr": "0.0.0.0", "port": 80, "uid": None, "comm": "System"},
                {"proto": "tcp6", "addr": "::", "port": 80, "uid": None, "comm": "System"},
            ],
        )
    )
    web = next(s for s in resp.known_services if s.category == "web")
    port_pairs = {(p.proto, p.port) for p in web.ports}
    assert ("tcp", 80) in port_pairs
    assert ("tcp6", 80) in port_pairs
    assert all(lp.port != 80 for lp in resp.key_listen_ports)


def test_to_storage_detail_filters_virtual_mounts():
    storage = StorageWithUsage(
        server_id=1,
        public_id="pub-1",
        hostname="h",
        block_devices=[{"name": "sda", "size_bytes": 10**11, "type": "disk"}],
        lvm_vgs=[],
        filesystems=[
            MountUsageRaw(
                mountpoint="/",
                fstype="ext4",
                used_bytes=3 * 10**10,
                free_bytes=2 * 10**10,
                collected_at=datetime.now(UTC),
            ),
            MountUsageRaw(mountpoint="/proc", fstype="proc"),
            MountUsageRaw(mountpoint="/snap/core/123", fstype="squashfs"),
        ],
        inventory_at=datetime.now(UTC),
    )
    resp = to_storage_detail(storage)
    paths = [m.mount for m in resp.mounts]
    assert "/" in paths
    assert "/proc" not in paths
    assert "/snap/core/123" not in paths


def test_to_gap_warning_item_under_provisioned_at_30min():
    now = datetime(2026, 5, 9, 12, 30, tzinfo=UTC)
    metric_ts = now - timedelta(minutes=35)
    raw = MetricGapWarningRaw(
        public_id="pid",
        hostname="h",
        last_metric_at=metric_ts,
    )
    item = to_gap_warning_item(raw, now)
    assert item.badge_class == "attn-active"
    assert item.badge_text == "35분"
    assert item.link_href == "/servers/pid"
    assert item.link_text == "h"
    assert item.meta_at == metric_ts


def test_to_gap_warning_item_right_size_short_gap():
    now = datetime(2026, 5, 9, 12, 30, tzinfo=UTC)
    raw = MetricGapWarningRaw(
        public_id="pid",
        hostname="h",
        last_metric_at=now - timedelta(minutes=10),
    )
    item = to_gap_warning_item(raw, now)
    assert item.badge_class == "attn-active"
    assert item.badge_text == "10분"


def _min_raw(**overrides: Any) -> ReportRowRaw:
    base = ReportRowRaw(
        server_id=1,
        public_id="p1",
        hostname="h",
        os_family="linux",
        os_id="ubuntu",
        os_version="22.04",
        os_codename="jammy",
        kernel_version="5.15",
        net_interfaces=[],
        services=[],
        last_seen_at=None,
        cpu_p95_pct=None,
        cpu_avg_pct=None,
        cpu_peak_pct=None,
        mem_p95_pct=None,
        mem_avg_pct=None,
        mem_peak_pct=None,
    )
    return dataclasses.replace(base, **overrides)


def test_build_server_inventory_enriches_from_raw():
    detail = _detail()
    raw = _min_raw(boot_firmware="uefi", secure_boot=True, edition="Datacenter", timezone="Asia/Seoul")

    snap = build_server_inventory(detail, True, raw)

    assert snap.public_id == detail.public_id
    assert snap.agent_id == detail.agent_id
    assert snap.cpu_arch == detail.cpu_arch
    assert snap.cpu_bits == detail.cpu_bits
    assert snap.boot_firmware == "uefi"
    assert snap.secure_boot is True
    assert snap.os_edition == "Datacenter"
    assert snap.timezone == "Asia/Seoul"


def test_spec_display_line_missing_values_show_dash():
    assert spec_display_line(None, None, None) == "— · — · —"
    assert spec_display_line(4, None, []) == "4코어 · — · —"


def test_build_server_inventory_raw_none_leaves_reproduction_fields_none():
    snap = build_server_inventory(_detail(), False, None)

    assert snap.boot_firmware is None
    assert snap.secure_boot is None
    assert snap.os_edition is None
    assert snap.timezone is None
    assert snap.hostname == "host"
