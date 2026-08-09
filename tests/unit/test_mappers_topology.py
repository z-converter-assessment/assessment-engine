from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from assessment_engine.web.services.mappers.topology import build_network_topology

if TYPE_CHECKING:
    from assessment_engine.db.dtos.outbound import ServerDetail
    from assessment_engine.json_types import JsonObject
    from assessment_engine.web.view_models.topology import NetworkTopology


def _iface(cidr: str, kind: str = "physical", gateway: str | None = None) -> JsonObject:
    addr, _, prefix_s = cidr.partition("/")
    prefix = int(prefix_s) if prefix_s.isdigit() else None
    family = "ipv6" if ":" in addr else "ipv4"
    return {
        "name": "eth0",
        "kind": kind,
        "gateway": gateway,
        "addresses": [{"address": addr, "prefix": prefix, "family": family}],
    }


def _host(pid: str, name: str, os_family: str, ifaces: list[JsonObject]) -> ServerDetail:
    return cast(
        "ServerDetail", SimpleNamespace(public_id=pid, hostname=name, os_family=os_family, net_interfaces=ifaces)
    )


def _subnet_ids(t: NetworkTopology) -> list[str]:
    return sorted(e["data"]["id"] for e in t.elements if e["data"].get("kind") == "subnet")


def _edges(t: NetworkTopology) -> set[tuple[str, str]]:
    return {(e["data"]["source"], e["data"]["target"]) for e in t.elements if "source" in e["data"]}


def test_empty_hosts():
    t = build_network_topology([])
    assert t.has_data is False
    assert t.elements == []
    assert t.subnet_count == 0
    assert t.host_count == 0
    assert t.isolated_count == 0


def test_shared_subnet_forms_hub_and_edges():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24")]),
    ]
    t = build_network_topology(hosts)
    assert t.has_data is True
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    assert t.subnet_count == 1
    assert t.host_count == 2
    assert _edges(t) == {("host:a", "subnet:10.0.1.0/24"), ("host:b", "subnet:10.0.1.0/24")}


def test_multi_homed_host_spans_two_subnets():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24")]),
        _host("c", "hostC", "windows", [_iface("10.0.1.12/24"), _iface("10.0.2.5/24")]),
        _host("d", "hostD", "linux", [_iface("10.0.2.6/24")]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24", "subnet:10.0.2.0/24"]
    assert ("host:c", "subnet:10.0.1.0/24") in _edges(t)
    assert ("host:c", "subnet:10.0.2.0/24") in _edges(t)
    assert ("host:a", "subnet:10.0.2.0/24") not in _edges(t)
    assert t.multi_homed_count == 1


def test_virtual_interface_dropped_by_kind():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24"), _iface("172.17.0.1/16", kind="bridge")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24"), _iface("172.17.0.1/16", kind="bridge")]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    assert "subnet:172.17.0.0/16" not in _subnet_ids(t)


def test_bond_master_included():
    hosts = [
        _host("a", "bondA", "linux", [_iface("10.0.2.10/24", kind="bond_master")]),
        _host("b", "bondB", "linux", [_iface("10.0.2.11/24", kind="bond_master")]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.2.0/24"]


def test_bond_member_excluded_by_kind():
    hosts = [
        _host("a", "bondA", "linux", [_iface("10.0.3.10/24", kind="bond_member")]),
        _host("b", "bondB", "linux", [_iface("10.0.3.11/24", kind="bond_member")]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == []


def test_singleton_subnet_dropped_and_host_isolated():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24")]),
        _host("e", "hostE", "linux", [_iface("192.168.50.9/24")]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    assert t.host_count == 2
    assert t.isolated_count == 1


def test_ipv6_and_link_local_and_garbage_excluded():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24")]),
        _host("c", "hostC", "windows", [_iface("10.0.1.12/24"), _iface("fe80::1/64")]),
        _host(
            "f",
            "hostF",
            "linux",
            [
                _iface("169.254.1.1/16"),
                {
                    "name": "x",
                    "kind": "physical",
                    "gateway": None,
                    "addresses": [{"address": "garbage", "prefix": 24, "family": "ipv4"}],
                },
                _iface("10.0.1.99/32"),
            ],
        ),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    assert all(":" not in e["data"]["label"] for e in t.elements if e["data"].get("kind") == "subnet")
    assert t.isolated_count == 1


def test_prefix_zero_excluded():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/0")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/0")]),
    ]
    t = build_network_topology(hosts)
    assert t.has_data is False


def test_gateway_disambiguates_overlapping_subnet():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24", gateway="10.0.1.1")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24", gateway="10.0.1.1")]),
        _host("c", "hostC", "linux", [_iface("10.0.1.12/24", gateway="10.0.1.254")]),
        _host("d", "hostD", "linux", [_iface("10.0.1.13/24", gateway="10.0.1.254")]),
    ]
    t = build_network_topology(hosts)
    labels = sorted(e["data"]["label"] for e in t.elements if e["data"].get("kind") == "subnet")
    assert labels == ["10.0.1.0/24 (via 10.0.1.1)", "10.0.1.0/24 (via 10.0.1.254)"]
    assert t.subnet_count == 2
    assert t.host_count == 4


def _host_roles(pid: str, name: str, os_family: str, ifaces: list[JsonObject], roles: list[str] | None) -> ServerDetail:
    return cast(
        "ServerDetail",
        SimpleNamespace(
            public_id=pid,
            hostname=name,
            os_family=os_family,
            net_interfaces=ifaces,
            service_categories=roles,
        ),
    )


def test_subnets_card_lists_member_hosts_with_ip_and_meta():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24")]),
        _host("b", "hostB", "windows", [_iface("10.0.1.11/24")]),
    ]
    t = build_network_topology(hosts)
    assert len(t.subnets) == 1
    grp = t.subnets[0]
    assert grp.net_key == "10.0.1.0/24"
    assert grp.host_count == 2
    by_pid = {sh.public_id: sh for sh in grp.hosts}
    assert by_pid["a"].hostname == "hostA"
    assert by_pid["a"].ip == "10.0.1.10"
    assert by_pid["a"].os_family == "linux"
    assert by_pid["b"].os_family == "windows"
    assert by_pid["b"].ip == "10.0.1.11"


def test_subnet_hosts_sorted_by_numeric_ip_ascending():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.200/24")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.9/24")]),
        _host("c", "hostC", "linux", [_iface("10.0.1.50/24")]),
    ]
    t = build_network_topology(hosts)
    grp = t.subnets[0]
    assert [sh.ip for sh in grp.hosts] == ["10.0.1.9", "10.0.1.50", "10.0.1.200"]


def test_subnet_label_uses_gateway_disambiguated_net_key():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24", gateway="10.0.1.1")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24", gateway="10.0.1.1")]),
        _host("c", "hostC", "linux", [_iface("10.0.1.12/24", gateway="10.0.1.254")]),
        _host("d", "hostD", "linux", [_iface("10.0.1.13/24", gateway="10.0.1.254")]),
    ]
    t = build_network_topology(hosts)
    assert sorted(g.net_key for g in t.subnets) == [
        "10.0.1.0/24 (via 10.0.1.1)",
        "10.0.1.0/24 (via 10.0.1.254)",
    ]


def test_roles_from_service_categories_sorted_in_node_and_subnet_host():
    hosts = [
        _host_roles("a", "hostA", "linux", [_iface("10.0.1.10/24")], ["web", "db"]),
        _host_roles("b", "hostB", "linux", [_iface("10.0.1.11/24")], ["cache"]),
    ]
    t = build_network_topology(hosts)
    node_roles = {e["data"]["publicId"]: e["data"]["roles"] for e in t.elements if e["data"].get("kind") == "host"}
    assert node_roles["a"] == ["db", "web"]
    assert node_roles["b"] == ["cache"]
    sh_roles = {sh.public_id: sh.roles for sh in t.subnets[0].hosts}
    assert sh_roles["a"] == ["db", "web"]
    assert sh_roles["b"] == ["cache"]


def test_roles_filtered_to_signature_workloads():
    hosts = [
        _host_roles("a", "hostA", "linux", [_iface("10.0.1.10/24")], ["web", "file", "remote", "db"]),
        _host_roles("b", "hostB", "linux", [_iface("10.0.1.11/24")], ["mail", "infra", "remote"]),
    ]
    t = build_network_topology(hosts)
    node_roles = {e["data"]["publicId"]: e["data"]["roles"] for e in t.elements if e["data"].get("kind") == "host"}
    assert node_roles["a"] == ["db", "web"]
    assert node_roles["b"] == []


def test_roles_default_empty_when_service_categories_absent():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24")]),
        _host_roles("b", "hostB", "linux", [_iface("10.0.1.11/24")], None),
    ]
    t = build_network_topology(hosts)
    node_roles = {e["data"]["publicId"]: e["data"]["roles"] for e in t.elements if e["data"].get("kind") == "host"}
    assert node_roles["a"] == []
    assert node_roles["b"] == []
    assert all(sh.roles == [] for sh in t.subnets[0].hosts)


def test_caveats_exposed_on_topology():
    t = build_network_topology([])
    assert len(t.caveats) == 3
    assert all(isinstance(c, str) and c for c in t.caveats)


def test_null_gateway_host_excluded_from_ambiguous_subnet():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24", gateway="10.0.1.1")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24", gateway="10.0.1.1")]),
        _host("c", "hostC", "linux", [_iface("10.0.1.12/24", gateway="10.0.1.254")]),
        _host("d", "hostD", "linux", [_iface("10.0.1.13/24", gateway="10.0.1.254")]),
        _host("e", "hostE", "linux", [_iface("10.0.1.20/24")]),
    ]
    t = build_network_topology(hosts)
    graph_pids = {e["data"]["publicId"] for e in t.elements if e["data"].get("kind") == "host"}
    assert "e" not in graph_pids
    assert t.host_count == 4
    assert t.isolated_count == 1


def test_null_gateway_host_joins_single_gateway_subnet():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24", gateway="10.0.1.1")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24")]),
    ]
    t = build_network_topology(hosts)
    assert t.host_count == 2
    assert t.subnet_count == 1
    assert t.isolated_count == 0


def _rich_iface(
    name: str,
    cidr: str,
    gateway: str | None,
    origin: str = "dhcp",
    mac: str = "fa:16:3e:00:00:01",
    mtu: int = 1450,
) -> JsonObject:
    addr, _, prefix_s = cidr.partition("/")
    return {
        "name": name,
        "id": mac,
        "id_type": "mac",
        "kind": "physical",
        "mtu": mtu,
        "gateway": gateway,
        "addresses": [{"address": addr, "prefix": int(prefix_s), "family": "ipv4", "origin": origin}],
    }


def _gateways(t: NetworkTopology) -> dict[str, int]:
    return {e["data"]["label"]: e["data"]["subnetCount"] for e in t.elements if e["data"].get("kind") == "gateway"}


def _route_edges(t: NetworkTopology) -> set[tuple[str, str]]:
    return {(e["data"]["source"], e["data"]["target"]) for e in t.elements if e["data"].get("kind") == "route"}


def test_single_subnet_gateway_not_promoted_to_router_node():
    hosts = [
        _host("a", "hostA", "linux", [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1")]),
        _host("b", "hostB", "linux", [_rich_iface("ens3", "10.0.1.11/24", "10.0.1.1")]),
    ]
    t = build_network_topology(hosts)
    assert _gateways(t) == {}
    assert _route_edges(t) == set()
    assert t.router_count == 0
    sn = next(e["data"] for e in t.elements if e["data"].get("kind") == "subnet")
    assert sn["gateway"] == "10.0.1.1"


def test_shared_gateway_groups_subnets_under_one_router():
    hosts = [
        _host("a", "hostA", "linux", [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1")]),
        _host("b", "hostB", "linux", [_rich_iface("ens3", "10.0.1.11/24", "10.0.1.1")]),
        _host("c", "hostC", "linux", [_rich_iface("ens4", "10.0.9.10/24", "10.0.1.1")]),
        _host("d", "hostD", "linux", [_rich_iface("ens4", "10.0.9.11/24", "10.0.1.1")]),
    ]
    t = build_network_topology(hosts)
    assert _gateways(t) == {"10.0.1.1": 2}
    assert t.router_count == 1
    assert ("gw:10.0.1.1", "subnet:10.0.1.0/24") in _route_edges(t)
    assert ("gw:10.0.1.1", "subnet:10.0.9.0/24") in _route_edges(t)


def test_multi_homed_host_flagged_in_node_and_subnet_host():
    hosts = [
        _host(
            "a",
            "hostA",
            "linux",
            [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1"), _rich_iface("ens4", "10.0.2.10/24", "10.0.2.1")],
        ),
        _host("b", "hostB", "linux", [_rich_iface("ens3", "10.0.1.11/24", "10.0.1.1")]),
        _host("c", "hostC", "linux", [_rich_iface("ens3", "10.0.2.11/24", "10.0.2.1")]),
    ]
    t = build_network_topology(hosts)
    assert t.multi_homed_count == 1
    host_a = next(e["data"] for e in t.elements if e["data"].get("id") == "host:a")
    host_b = next(e["data"] for e in t.elements if e["data"].get("id") == "host:b")
    assert host_a["multiHomed"] is True
    assert host_b["multiHomed"] is False
    a_rows = [h for sn in t.subnets for h in sn.hosts if h.public_id == "a"]
    assert len(a_rows) == 2
    assert all(h.multi_homed for h in a_rows)


def test_subnet_host_carries_mtu_speed_origin_and_group_gateway():
    hosts = [
        _host("a", "hostA", "linux", [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1", origin="static")]),
        _host("b", "hostB", "linux", [_rich_iface("eth0", "10.0.1.11/24", "10.0.1.1")]),
    ]
    t = build_network_topology(hosts)
    row_a = next(h for sn in t.subnets for h in sn.hosts if h.public_id == "a")
    assert row_a.mtu == 1450
    assert row_a.speed_mbps is None
    assert row_a.origin == "static"
    assert t.subnets[0].gateway == "10.0.1.1"


def test_subnet_host_online_status_from_online_by_id():
    hosts = [
        cast(
            "ServerDetail",
            SimpleNamespace(
                id=1,
                public_id="a",
                hostname="hostA",
                os_family="linux",
                net_interfaces=[_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1")],
            ),
        ),
        cast(
            "ServerDetail",
            SimpleNamespace(
                id=2,
                public_id="b",
                hostname="hostB",
                os_family="linux",
                net_interfaces=[_rich_iface("eth0", "10.0.1.11/24", "10.0.1.1")],
            ),
        ),
    ]
    t = build_network_topology(hosts, online_by_id={1: True})
    by_pid = {h.public_id: h for sn in t.subnets for h in sn.hosts}
    assert by_pid["a"].is_online is True
    assert by_pid["b"].is_online is False

    t_none = build_network_topology(hosts)
    assert all(not h.is_online for sn in t_none.subnets for h in sn.hosts)


def test_host_node_ifaces_tooltip_payload():
    hosts = [
        _host(
            "a", "hostA", "linux", [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1", mac="fa:16:3e:ab:cd:ef", mtu=9000)]
        ),
        _host("b", "hostB", "linux", [_rich_iface("ens3", "10.0.1.11/24", "10.0.1.1")]),
    ]
    t = build_network_topology(hosts)
    host_a = next(e["data"] for e in t.elements if e["data"].get("id") == "host:a")
    assert host_a["ifaces"] == [{"name": "ens3", "mac": "fa:16:3e:ab:cd:ef", "mtu": 9000, "gateway": "10.0.1.1"}]
