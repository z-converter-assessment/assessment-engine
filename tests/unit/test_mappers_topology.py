"""네트워크 토폴로지 mapper — 구조화 interfaces -> subnet 공동소속 그래프 도출 검증.

build_network_topology 의 핵심 분기 회귀 가드:
  - 같은 network address 공유 -> subnet 노드 + host 엣지
  - 멀티홈(2+ subnet) host 표식
  - 가상망 필터: 집계 단위(physical·bond_master)만 채택 (bridge/veth/bond_member 등 제외),
    단독 subnet 제외, link-local/host route/prefix0 안전망 제외
  - gateway disambiguation: 같은 CIDR 라도 서로 다른 gateway 면 분리
  - IPv4 only (family=ipv6 제외), address 파싱 실패 흡수
  - isolated_count = 그래프 미포함 호스트
입력은 duck-typed (public_id·hostname·os_family·net_interfaces) — SimpleNamespace 로 최소 결합 (wire).
net_interfaces 는 구조화 dict [{name, kind, gateway, addresses:[{address, prefix, family}]}]
(agent 공용 iface 분류기 — 주소는 인터페이스별 addresses 리스트에 nested, gateway 는 인터페이스 레벨).
"""

from types import SimpleNamespace
from typing import cast

from assessment_engine.db.dtos.outbound import ServerDetail
from assessment_engine.json_types import JsonObject
from assessment_engine.web.services.mappers.topology import build_network_topology
from assessment_engine.web.view_models.topology import NetworkTopology


def _iface(cidr: str, kind: str = "physical", gateway: str | None = None) -> JsonObject:
    """CIDR 문자열 -> 구조화 net_interface dict. family(ipv4/ipv6) 자동 판정, prefix 파싱 불가는 None.

    테스트 편의 헬퍼 — agent 는 이미 구조화된 InterfaceInfo 를 발행하나, 케이스별 주소·kind·gateway 를
    간결히 지정하려고 CIDR 문자열을 dict 로 변환한다. 주소는 인터페이스별 addresses 리스트에 nested 로
    담고(주소별 family/prefix), gateway·kind 는 인터페이스 레벨.
    """
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
    """build_network_topology 가 읽는 축만 가진 대역 — public_id·hostname·os_family·net_interfaces."""
    return cast(ServerDetail, SimpleNamespace(public_id=pid, hostname=name, os_family=os_family, net_interfaces=ifaces))


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
    # host c 가 두 서브넷에 걸침 — 노드 플래그(폐기) 대신 엣지로 검증
    assert ("host:c", "subnet:10.0.1.0/24") in _edges(t)
    assert ("host:c", "subnet:10.0.2.0/24") in _edges(t)
    assert ("host:a", "subnet:10.0.2.0/24") not in _edges(t)
    assert t.multi_homed_count == 1


def test_virtual_interface_dropped_by_kind():
    # docker0(kind=bridge)는 가상 인터페이스라 kind 태그로 직접 제외 (physical 만 채택).
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24"), _iface("172.17.0.1/16", kind="bridge")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24"), _iface("172.17.0.1/16", kind="bridge")]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    assert "subnet:172.17.0.0/16" not in _subnet_ids(t)


def test_bond_master_included():
    # 본딩 호스트 — IP 는 bond_master(bond0)에 실림. bond_master 는 집계 단위라 토폴로지 포함 (net_io 집계와 정합).
    hosts = [
        _host("a", "bondA", "linux", [_iface("10.0.2.10/24", kind="bond_master")]),
        _host("b", "bondB", "linux", [_iface("10.0.2.11/24", kind="bond_master")]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.2.0/24"]


def test_bond_member_excluded_by_kind():
    # bond_member(물리 leg)는 bond_master 가 집계 단위라 이중집계 회피로 제외 — 그래프에 안 나타남.
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
        _host("e", "hostE", "linux", [_iface("192.168.50.9/24")]),  # 단독 -> isolated
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    assert t.host_count == 2
    assert t.isolated_count == 1


def test_ipv6_and_link_local_and_garbage_excluded():
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24")]),
        _host("c", "hostC", "windows", [_iface("10.0.1.12/24"), _iface("fe80::1/64")]),  # IPv6 family 제외
        _host(
            "f",
            "hostF",
            "linux",
            [
                _iface("169.254.1.1/16"),  # link-local 안전망 제외
                # address 파싱 실패 (prefix 있어 ip_interface try/except 분기 진입) -> 흡수
                {
                    "name": "x",
                    "kind": "physical",
                    "gateway": None,
                    "addresses": [{"address": "garbage", "prefix": 24, "family": "ipv4"}],
                },
                _iface("10.0.1.99/32"),  # host route(/32) 제외
            ],
        ),  # 전부 제외 -> isolated
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    # hostC 는 IPv4 로 그래프 포함, IPv6 는 subnet 노드 미생성
    assert all(":" not in e["data"]["label"] for e in t.elements if e["data"].get("kind") == "subnet")
    assert t.isolated_count == 1  # hostF


def test_prefix_zero_excluded():
    # netmask 부재 폴백 prefix 0 (0.0.0.0/0) -> 전역 합쳐짐 방지 위해 제외.
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/0")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/0")]),
    ]
    t = build_network_topology(hosts)
    assert t.has_data is False


def test_gateway_disambiguates_overlapping_subnet():
    # 같은 CIDR(10.0.1.0/24) 라도 서로 다른 non-null gateway 면 다른 물리망으로 분리
    # (사설 대역 중복 오병합 방지). gateway 별 그룹 각 2대+ 라 둘 다 생존.
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


def _host_roles(
    pid: str, name: str, os_family: str, ifaces: list[JsonObject], roles: list[str] | None
) -> ServerDetail:
    """service_categories(E7) 를 실은 대역 — _host 는 해당 속성이 없어 roles 테스트용 별도 구성."""
    return cast(
        ServerDetail,
        SimpleNamespace(
            public_id=pid,
            hostname=name,
            os_family=os_family,
            net_interfaces=ifaces,
            service_categories=roles,
        ),
    )


def test_subnets_card_lists_member_hosts_with_ip_and_meta():
    # subnets 카드: 그래프와 별개로 서브넷별 소속 서버 목록(hostname·IP·os_family·public_id) 제공.
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
    # 서브넷 내 호스트는 IP 숫자 오름차순 (_subnet_host_sort_key). 입력 역순이어도 정렬.
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.200/24")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.9/24")]),
        _host("c", "hostC", "linux", [_iface("10.0.1.50/24")]),
    ]
    t = build_network_topology(hosts)
    grp = t.subnets[0]
    assert [sh.ip for sh in grp.hosts] == ["10.0.1.9", "10.0.1.50", "10.0.1.200"]


def test_subnet_label_uses_gateway_disambiguated_net_key():
    # gateway 분리 시 SubnetGroup.net_key 는 CIDR 이 아닌 표시 라벨("... (via gw)").
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
    # roles = sorted(service_categories) — host 노드 data 와 SubnetHost 양쪽에 실림.
    hosts = [
        _host_roles("a", "hostA", "linux", [_iface("10.0.1.10/24")], ["web", "db"]),
        _host_roles("b", "hostB", "linux", [_iface("10.0.1.11/24")], ["cache"]),
    ]
    t = build_network_topology(hosts)
    node_roles = {
        e["data"]["publicId"]: e["data"]["roles"] for e in t.elements if e["data"].get("kind") == "host"
    }
    assert node_roles["a"] == ["db", "web"]  # 정렬됨
    assert node_roles["b"] == ["cache"]
    sh_roles = {sh.public_id: sh.roles for sh in t.subnets[0].hosts}
    assert sh_roles["a"] == ["db", "web"]
    assert sh_roles["b"] == ["cache"]


def test_roles_filtered_to_signature_workloads():
    # 토폴로지 뱃지 = 시그니처 워크로드만(환경 개요 도넛·서버 목록 뱃지와 동일 기준).
    # file·mail·infra·remote 등 baseline·관리 카테고리는 제외 — 시그니처 없으면 빈 리스트.
    hosts = [
        _host_roles("a", "hostA", "linux", [_iface("10.0.1.10/24")], ["web", "file", "remote", "db"]),
        _host_roles("b", "hostB", "linux", [_iface("10.0.1.11/24")], ["mail", "infra", "remote"]),
    ]
    t = build_network_topology(hosts)
    node_roles = {
        e["data"]["publicId"]: e["data"]["roles"] for e in t.elements if e["data"].get("kind") == "host"
    }
    assert node_roles["a"] == ["db", "web"]  # file·remote 제외, 시그니처만 정렬
    assert node_roles["b"] == []  # 시그니처 0 -> 빈 리스트


def test_roles_default_empty_when_service_categories_absent():
    # service_categories 속성 없거나 None 이면 roles 빈 리스트 (getattr 폴백).
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24")]),
        _host_roles("b", "hostB", "linux", [_iface("10.0.1.11/24")], None),
    ]
    t = build_network_topology(hosts)
    node_roles = {
        e["data"]["publicId"]: e["data"]["roles"] for e in t.elements if e["data"].get("kind") == "host"
    }
    assert node_roles["a"] == []
    assert node_roles["b"] == []
    assert all(sh.roles == [] for sh in t.subnets[0].hosts)


def test_caveats_exposed_on_topology():
    # 추론 한계 caveats 는 데이터 유무와 무관하게 3건 노출 (#E9 정직 노출).
    t = build_network_topology([])
    assert len(t.caveats) == 3
    assert all(isinstance(c, str) and c for c in t.caveats)


def test_null_gateway_host_excluded_from_ambiguous_subnet():
    # 서브넷에 non-null gateway 2+ (모호) 면 gateway 미제공 호스트는 귀속 불가라 제외 -> isolated.
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24", gateway="10.0.1.1")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24", gateway="10.0.1.1")]),
        _host("c", "hostC", "linux", [_iface("10.0.1.12/24", gateway="10.0.1.254")]),
        _host("d", "hostD", "linux", [_iface("10.0.1.13/24", gateway="10.0.1.254")]),
        _host("e", "hostE", "linux", [_iface("10.0.1.20/24")]),  # gateway None -> 모호 서브넷서 제외
    ]
    t = build_network_topology(hosts)
    graph_pids = {e["data"]["publicId"] for e in t.elements if e["data"].get("kind") == "host"}
    assert "e" not in graph_pids
    assert t.host_count == 4
    assert t.isolated_count == 1


def test_null_gateway_host_joins_single_gateway_subnet():
    # gateway 1종만 있는 서브넷엔 null gateway 호스트도 합류 (모호하지 않음).
    hosts = [
        _host("a", "hostA", "linux", [_iface("10.0.1.10/24", gateway="10.0.1.1")]),
        _host("b", "hostB", "linux", [_iface("10.0.1.11/24")]),  # gateway None
    ]
    t = build_network_topology(hosts)
    assert t.host_count == 2
    assert t.subnet_count == 1
    assert t.isolated_count == 0


# ─── 3계층 재설계 — 게이트웨이 라우터 노드 · 멀티홈 · SubnetHost 상세 필드 ──────


def _rich_iface(
    name: str,
    cidr: str,
    gateway: str | None,
    origin: str = "dhcp",
    mac: str = "fa:16:3e:00:00:01",
    mtu: int = 1450,
) -> JsonObject:
    """툴팁·표 필드까지 담은 net_interface — name/id(mac)/mtu/gateway/addresses.origin."""
    addr, _, prefix_s = cidr.partition("/")
    return {
        "name": name, "id": mac, "id_type": "mac", "kind": "physical", "mtu": mtu, "gateway": gateway,
        "addresses": [{"address": addr, "prefix": int(prefix_s), "family": "ipv4", "origin": origin}],
    }


def _gateways(t: NetworkTopology) -> dict[str, int]:
    return {e["data"]["label"]: e["data"]["subnetCount"] for e in t.elements if e["data"].get("kind") == "gateway"}


def _route_edges(t: NetworkTopology) -> set[tuple[str, str]]:
    return {(e["data"]["source"], e["data"]["target"]) for e in t.elements if e["data"].get("kind") == "route"}


def test_single_subnet_gateway_not_promoted_to_router_node():
    # 서브넷당 1:1 gateway 는 유추 가능(대개 .1)이라 라우터 노드로 안 띄운다 — subnet data·표에만 노출.
    hosts = [
        _host("a", "hostA", "linux", [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1")]),
        _host("b", "hostB", "linux", [_rich_iface("ens3", "10.0.1.11/24", "10.0.1.1")]),
    ]
    t = build_network_topology(hosts)
    assert _gateways(t) == {}  # 공유 아님 -> 라우터 노드 없음
    assert _route_edges(t) == set()
    assert t.router_count == 0  # 범례 미노출
    sn = next(e["data"] for e in t.elements if e["data"].get("kind") == "subnet")
    assert sn["gateway"] == "10.0.1.1"  # subnet data 엔 여전히 실림(툴팁·표용)


def test_shared_gateway_groups_subnets_under_one_router():
    # 서로 다른 두 서브넷이 같은 gateway 를 쓰면 한 라우터 노드(subnetCount=2)로 묶여 라우팅 계층이 드러남.
    hosts = [
        _host("a", "hostA", "linux", [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1")]),
        _host("b", "hostB", "linux", [_rich_iface("ens3", "10.0.1.11/24", "10.0.1.1")]),
        _host("c", "hostC", "linux", [_rich_iface("ens4", "10.0.9.10/24", "10.0.1.1")]),
        _host("d", "hostD", "linux", [_rich_iface("ens4", "10.0.9.11/24", "10.0.1.1")]),
    ]
    t = build_network_topology(hosts)
    assert _gateways(t) == {"10.0.1.1": 2}
    assert t.router_count == 1  # 공유 게이트웨이 1개 -> 라우터 범례 노출
    assert ("gw:10.0.1.1", "subnet:10.0.1.0/24") in _route_edges(t)
    assert ("gw:10.0.1.1", "subnet:10.0.9.0/24") in _route_edges(t)


def test_multi_homed_host_flagged_in_node_and_subnet_host():
    hosts = [
        _host("a", "hostA", "linux",
              [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1"), _rich_iface("ens4", "10.0.2.10/24", "10.0.2.1")]),
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
    assert len(a_rows) == 2 and all(h.multi_homed for h in a_rows)


def test_subnet_host_carries_mtu_speed_origin_and_group_gateway():
    """SubnetHost 는 iface 이름·per-host gateway 대신 mtu/speed_mbps/origin — 게이트웨이는

    SubnetGroup(서브넷당 1개, disambiguation 완료)으로 이동해 행마다 반복 안 함."""
    hosts = [
        _host("a", "hostA", "linux", [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1", origin="static")]),
        _host("b", "hostB", "linux", [_rich_iface("eth0", "10.0.1.11/24", "10.0.1.1")]),
    ]
    t = build_network_topology(hosts)
    row_a = next(h for sn in t.subnets for h in sn.hosts if h.public_id == "a")
    assert row_a.mtu == 1450  # _rich_iface 기본값
    assert row_a.speed_mbps is None  # _rich_iface 는 speed_mbps 미설정
    assert row_a.origin == "static"
    assert t.subnets[0].gateway == "10.0.1.1"


def test_subnet_host_online_status_from_online_by_id():
    """online_by_id(내부 id -> bool) 로 SubnetHost.is_online 채움 — 미전달/미매칭은 기본 False."""
    hosts = [
        cast(ServerDetail, SimpleNamespace(id=1, public_id="a", hostname="hostA", os_family="linux",
                                           net_interfaces=[_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1")])),
        cast(ServerDetail, SimpleNamespace(id=2, public_id="b", hostname="hostB", os_family="linux",
                                           net_interfaces=[_rich_iface("eth0", "10.0.1.11/24", "10.0.1.1")])),
    ]
    t = build_network_topology(hosts, online_by_id={1: True})
    by_pid = {h.public_id: h for sn in t.subnets for h in sn.hosts}
    assert by_pid["a"].is_online is True
    assert by_pid["b"].is_online is False  # id=2 미매칭 -> False

    t_none = build_network_topology(hosts)  # online_by_id 미전달
    assert all(not h.is_online for sn in t_none.subnets for h in sn.hosts)


def test_host_node_ifaces_tooltip_payload():
    hosts = [
        _host("a", "hostA", "linux",
              [_rich_iface("ens3", "10.0.1.10/24", "10.0.1.1", mac="fa:16:3e:ab:cd:ef", mtu=9000)]),
        _host("b", "hostB", "linux", [_rich_iface("ens3", "10.0.1.11/24", "10.0.1.1")]),
    ]
    t = build_network_topology(hosts)
    host_a = next(e["data"] for e in t.elements if e["data"].get("id") == "host:a")
    assert host_a["ifaces"] == [{"name": "ens3", "mac": "fa:16:3e:ab:cd:ef", "mtu": 9000, "gateway": "10.0.1.1"}]
