"""네트워크 토폴로지 mapper (P2) — 호스트별 ip_internal CIDR 에서 L3 subnet 공동소속 그래프 도출.

Repository 는 ip_internal 을 raw CIDR 문자열(agent payload v3.4+, 예 "10.0.1.15/24")로 보존(P1).
본 mapper 가 파싱·subnet 그룹핑·가상망 필터·노드/엣지 조립을 단일 책임으로 수행(P2).

토폴로지 의미: agent 는 인터페이스 IP+prefix 만 발행한다 (LLDP/ARP/traceroute 같은 실측 인접 정보 없음).
유일하게 도출 가능한 관계 = "두 호스트가 같은 network address(IP & prefix)면 같은 브로드캐스트 도메인"이라는
추론이다. 실측 reachability 가 아니다 — VLAN/방화벽 격리 미반영, RFC1918 중복이면 별개 네트워크를 한
서브넷으로 오병합할 수 있다 (caveats 로 정직 노출).

가상망 제거 (engine-side, agent 무수정 제약): ip_internal 에 인터페이스 이름이 없어 docker0/virbr0 를
이름으로 판별할 수 없다. 대신 세 단계 필터로 호스트-로컬 가상 브리지를 흡수한다:
  (a) link-local(169.254)·host route(prefix 32)·netmask 부재(prefix 0) 제외.
  (b) 동일 network 안에서 2대+ 호스트가 같은 host IP 를 주장하면 host-local 가상으로 간주 — docker0 는 모든
      호스트가 게이트웨이 .1 을 주장하므로 결정론적 신호 (실제 LAN 은 IP 중복 불가).
  (c) 2대 미만 단독 서브넷 제외 — inter-host 토폴로지에 무의미하고, 단독 docker 호스트의 브리지도 흡수.

IPv4 only (v1): Linux agent 가 IPv6 미발행이라 혼합 시 Windows 편향. IPv6(특히 fe80 link-local)은 (a)에서 제외.

명세·근거 단일 진실은 본 모듈 docstring + view_models/topology.NetworkTopology.
"""

import ipaddress
from collections import defaultdict

from assessment_engine.web.view_models.topology import NetworkTopology, SubnetGroup, SubnetHost

_CAVEATS = [
    "추론 토폴로지 — 같은 서브넷(IP·prefix) 공유 기준이며, 실제 통신 가능 여부(방화벽·VLAN 격리)는 반영하지 않습니다.",
    "docker·libvirt 등 호스트 내부 가상 네트워크와 IPv6 주소는 제외했습니다.",
    "사설 IP 대역이 서로 다른 네트워크에서 중복되면 한 서브넷으로 합쳐 보일 수 있습니다.",
]


def _cidr_str(item) -> str:
    """ip_internal 항목 정규화 — 단계에 따라 raw CIDR str 또는 IpAddr(value) 둘 다 수용."""
    return item.value if hasattr(item, "value") else item


def build_network_topology(hosts) -> NetworkTopology:
    """hosts: ServerDetail/DTO 리스트 (public_id·hostname·os_family·ip_internal 사용).

    ip_internal 은 raw CIDR 문자열 리스트 (get_servers 단계는 enrich 전이라 str). IpAddr 도 방어 수용.
    """
    # net_key -> [(public_id, host_ip_str)] — 한 호스트가 같은 서브넷에 여러 IP 면 멤버십 1회만.
    net_members: dict[str, list[tuple[str, str]]] = defaultdict(list)
    host_meta: dict[str, tuple[str, str]] = {}  # public_id -> (hostname, os_family)

    for h in hosts:
        pid = str(h.public_id)
        host_meta[pid] = (h.hostname, h.os_family or "unknown")
        seen_nets: set[str] = set()
        for raw in h.ip_internal or []:
            try:
                iface = ipaddress.ip_interface(_cidr_str(raw))
            except ValueError:
                continue
            if iface.version != 4:
                continue  # IPv4 v1
            ip = iface.ip
            if ip.is_loopback or ip.is_link_local:
                continue  # (a) 169.254 APIPA / 루프백 — 세그먼트 아님
            prefix = iface.network.prefixlen
            if prefix == 0 or prefix >= 32:
                continue  # (a) netmask 부재 폴백 / host route
            net_key = str(iface.network)  # "10.0.1.0/24"
            if net_key in seen_nets:
                continue
            seen_nets.add(net_key)
            net_members[net_key].append((pid, str(ip)))

    # 가상망/단독 필터 -> 살아남은 서브넷별 호스트 목록.
    surviving: dict[str, list[str]] = {}
    for net_key, members in net_members.items():
        ips = [m[1] for m in members]
        if len(set(ips)) < len(ips):
            continue  # (b) 동일 host IP 중복 -> host-local 가상 브리지
        pids = sorted({m[0] for m in members})
        if len(pids) < 2:
            continue  # (c) 단독 서브넷 -> inter-host 토폴로지 무의미
        surviving[net_key] = pids

    host_subnet_count: dict[str, int] = defaultdict(int)
    for pids in surviving.values():
        for pid in pids:
            host_subnet_count[pid] += 1

    # 집계 뷰: subnet 노드만 기본 표시(hostCount 라벨), host 노드/엣지는 "collapsed" 로 시작 ->
    # network-topology.js 가 subnet 노드 클릭 시 해당 호스트를 펼친다 (대규모 호스트 hairball 회피).
    elements: list[dict] = []
    graph_hosts: set[str] = set()
    edges: list[tuple[str, str]] = []
    for net_key in sorted(surviving):
        elements.append(
            {
                "data": {
                    "id": f"subnet:{net_key}",
                    "label": net_key,
                    "kind": "subnet",
                    "hostCount": len(surviving[net_key]),
                }
            }
        )
        for pid in surviving[net_key]:
            graph_hosts.add(pid)
            edges.append((pid, net_key))

    for pid in sorted(graph_hosts):
        hostname, os_family = host_meta[pid]
        elements.append(
            {
                "data": {
                    "id": f"host:{pid}",
                    "label": hostname,
                    "kind": "host",
                    "publicId": pid,
                    "osFamily": os_family,
                },
                "classes": "collapsed",
            }
        )

    for pid, net_key in edges:
        elements.append(
            {"data": {"source": f"host:{pid}", "target": f"subnet:{net_key}"}, "classes": "collapsed"}
        )

    # 서브넷별 소속 서버 목록 (IP 표시) — 그래프와 별개 카드. net_members 에서 pid->ip 복원.
    subnets: list[SubnetGroup] = []
    for net_key in sorted(surviving):
        pid_ip = {pid: ip for pid, ip in net_members[net_key]}
        hosts_list = []
        for pid in surviving[net_key]:
            hostname, os_family = host_meta[pid]
            hosts_list.append(
                SubnetHost(hostname=hostname, ip=pid_ip.get(pid, ""), os_family=os_family, public_id=pid)
            )
        subnets.append(SubnetGroup(net_key=net_key, host_count=len(hosts_list), hosts=hosts_list))

    multi_homed_count = sum(1 for pid in graph_hosts if host_subnet_count[pid] >= 2)
    isolated_count = len(host_meta) - len(graph_hosts)

    return NetworkTopology(
        elements=elements,
        subnet_count=len(surviving),
        host_count=len(graph_hosts),
        multi_homed_count=multi_homed_count,
        isolated_count=isolated_count,
        subnets=subnets,
        caveats=_CAVEATS,
        has_data=bool(surviving),
    )
