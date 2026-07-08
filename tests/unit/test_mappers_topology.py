"""네트워크 토폴로지 mapper — 구조화 interfaces -> subnet 공동소속 그래프 도출 검증.

build_network_topology 의 핵심 분기 회귀 가드:
  - 같은 network address 공유 -> subnet 노드 + host 엣지
  - 멀티홈(2+ subnet) host 표식
  - 가상망 필터: kind!=physical 제외, 단독 subnet 제외, link-local/host route/prefix0 안전망 제외
  - gateway disambiguation: 같은 CIDR 라도 서로 다른 gateway 면 분리
  - IPv4 only (family=ipv6 제외), address 파싱 실패 흡수
  - isolated_count = 그래프 미포함 호스트
입력은 duck-typed (public_id·hostname·os_family·interfaces) — SimpleNamespace 로 최소 결합.
interfaces 는 구조화 dict [{name, address, prefix, family, kind, gateway}] (agent 공용 iface 분류기).
"""

from types import SimpleNamespace

from assessment_engine.web.services.mappers.topology import build_network_topology


def _iface(cidr: str, kind: str = "physical", gateway: str | None = None) -> dict:
    """CIDR 문자열 -> 구조화 interface dict. family(ipv4/ipv6) 자동 판정, prefix 파싱 불가는 None.

    테스트 편의 헬퍼 — agent 는 이미 구조화된 InterfaceInfo 를 발행하나, 케이스별 주소·kind·gateway 를
    간결히 지정하려고 CIDR 문자열을 dict 로 변환한다.
    """
    addr, _, prefix_s = cidr.partition("/")
    prefix = int(prefix_s) if prefix_s.isdigit() else None
    family = "ipv6" if ":" in addr else "ipv4"
    return {"name": "eth0", "address": addr, "prefix": prefix, "family": family, "kind": kind, "gateway": gateway}


def _host(pid: str, name: str, os_family: str, ifaces: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(public_id=pid, hostname=name, os_family=os_family, interfaces=ifaces)


def _subnet_ids(t) -> list[str]:
    return sorted(e["data"]["id"] for e in t.elements if e["data"].get("kind") == "subnet")


def _edges(t) -> set[tuple[str, str]]:
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
                    "address": "garbage",
                    "prefix": 24,
                    "family": "ipv4",
                    "kind": "physical",
                    "gateway": None,
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
