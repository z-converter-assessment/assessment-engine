"""네트워크 토폴로지 mapper — ip_internal CIDR -> subnet 공동소속 그래프 도출 검증.

build_network_topology 의 핵심 분기 회귀 가드:
  - 같은 network address 공유 -> subnet 노드 + host 엣지
  - 멀티홈(2+ subnet) host 표식
  - 가상망 필터 3종: 동일 host IP 중복(docker 브리지), 단독 subnet, link-local/host route/prefix0
  - IPv4 only (IPv6 제외), CIDR 파싱 실패 흡수
  - isolated_count = 그래프 미포함 호스트
입력은 duck-typed (public_id·hostname·os_family·ip_internal) — SimpleNamespace 로 최소 결합.
"""

from types import SimpleNamespace

from assessment_engine.web.services.mappers.topology import build_network_topology


def _host(pid: str, name: str, os_family: str, ips: list[str]) -> SimpleNamespace:
    return SimpleNamespace(public_id=pid, hostname=name, os_family=os_family, ip_internal=ips)


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
        _host("a", "hostA", "linux", ["10.0.1.10/24"]),
        _host("b", "hostB", "linux", ["10.0.1.11/24"]),
    ]
    t = build_network_topology(hosts)
    assert t.has_data is True
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    assert t.subnet_count == 1
    assert t.host_count == 2
    assert _edges(t) == {("host:a", "subnet:10.0.1.0/24"), ("host:b", "subnet:10.0.1.0/24")}


def test_multi_homed_host_spans_two_subnets():
    hosts = [
        _host("a", "hostA", "linux", ["10.0.1.10/24"]),
        _host("c", "hostC", "windows", ["10.0.1.12/24", "10.0.2.5/24"]),
        _host("d", "hostD", "linux", ["10.0.2.6/24"]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24", "subnet:10.0.2.0/24"]
    # host c 가 두 서브넷에 걸침 — 노드 플래그(폐기) 대신 엣지로 검증
    assert ("host:c", "subnet:10.0.1.0/24") in _edges(t)
    assert ("host:c", "subnet:10.0.2.0/24") in _edges(t)
    assert ("host:a", "subnet:10.0.2.0/24") not in _edges(t)
    assert t.multi_homed_count == 1


def test_docker_bridge_dropped_by_duplicate_host_ip():
    # docker0: 두 호스트 모두 게이트웨이 .1 을 주장 -> host-local 가상 -> 제외.
    hosts = [
        _host("a", "hostA", "linux", ["10.0.1.10/24", "172.17.0.1/16"]),
        _host("b", "hostB", "linux", ["10.0.1.11/24", "172.17.0.1/16"]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    assert "subnet:172.17.0.0/16" not in _subnet_ids(t)


def test_singleton_subnet_dropped_and_host_isolated():
    hosts = [
        _host("a", "hostA", "linux", ["10.0.1.10/24"]),
        _host("b", "hostB", "linux", ["10.0.1.11/24"]),
        _host("e", "hostE", "linux", ["192.168.50.9/24"]),  # 단독 -> isolated
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    assert t.host_count == 2
    assert t.isolated_count == 1


def test_ipv6_and_link_local_and_garbage_excluded():
    hosts = [
        _host("a", "hostA", "linux", ["10.0.1.10/24"]),
        _host("b", "hostB", "linux", ["10.0.1.11/24"]),
        _host("c", "hostC", "windows", ["10.0.1.12/24", "fe80::1/64"]),  # IPv6 link-local 제외
        _host("f", "hostF", "linux", ["169.254.1.1/16", "garbage", "10.0.1.99/32"]),  # 전부 제외 -> isolated
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
    # hostC 는 IPv4 로 그래프 포함, IPv6 는 subnet 노드 미생성
    assert all(":" not in e["data"]["label"] for e in t.elements if e["data"].get("kind") == "subnet")
    assert t.isolated_count == 1  # hostF


def test_prefix_zero_excluded():
    # netmask 부재 폴백 prefix 0 (0.0.0.0/0) -> 전역 합쳐짐 방지 위해 제외.
    hosts = [
        _host("a", "hostA", "linux", ["10.0.1.10/0"]),
        _host("b", "hostB", "linux", ["10.0.1.11/0"]),
    ]
    t = build_network_topology(hosts)
    assert t.has_data is False


def test_ipaddr_value_attribute_accepted():
    # ip_internal 이 enrich 후 IpAddr(value=...) 라도 _cidr_str 가 흡수.
    ipaddr_a = SimpleNamespace(value="10.0.1.10/24", is_ipv4=True)
    ipaddr_b = SimpleNamespace(value="10.0.1.11/24", is_ipv4=True)
    hosts = [
        _host("a", "hostA", "linux", [ipaddr_a]),
        _host("b", "hostB", "linux", [ipaddr_b]),
    ]
    t = build_network_topology(hosts)
    assert _subnet_ids(t) == ["subnet:10.0.1.0/24"]
