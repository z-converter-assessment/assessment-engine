"""네트워크 토폴로지 mapper (P2) — 호스트별 물리 인터페이스에서 L3 subnet 공동소속 그래프 도출.

Repository 는 interfaces 를 구조화 dict(name/address/prefix/family/kind)로 보존(P1). 본 mapper 가
subnet 그룹핑·가상망 필터·노드/엣지 조립을 단일 책임으로 수행(P2).

토폴로지 의미: agent 는 인터페이스 IP+prefix 만 발행한다 (LLDP/ARP/traceroute 같은 실측 인접 정보 없음).
유일하게 도출 가능한 관계 = "두 호스트가 같은 network address(IP & prefix)면 같은 브로드캐스트 도메인"이라는
추론이다. 실측 reachability 가 아니다 — VLAN/방화벽 격리 미반영, RFC1918 중복이면 별개 네트워크를 한
서브넷으로 오병합할 수 있다 (caveats 로 정직 노출).

가상망 제거: interface `kind` 태그(agent 공용 분류기)로 물리(physical)만 채택 — docker0/veth/bridge/tunnel/
loopback 은 kind 로 직접 제외(과거 host-local IP 중복 휴리스틱 불요). link-local·prefix 0/32 은 안전망으로 추가 제외.
2대 미만 단독 서브넷은 inter-host 토폴로지에 무의미해 제외.

IPv4 only (v1): 그래프는 physical IPv4 만. IPv6 는 family 로 제외.

명세·근거 단일 진실은 본 모듈 docstring + view_models/topology.NetworkTopology.
"""

import ipaddress
from collections import defaultdict

from assessment_engine.web.view_models.topology import NetworkTopology, SubnetGroup, SubnetHost

_CAVEATS = [
    "추론 토폴로지 — 같은 서브넷(IP·prefix) 공유 기준이며, 실제 통신 가능 여부(방화벽·VLAN 격리)는 반영하지 않습니다.",
    "물리 인터페이스(kind=physical)의 IPv4 주소만 사용합니다 — 가상 네트워크(docker·bridge·veth 등)와 IPv6는 제외했습니다.",
    "사설 IP 대역 중복은 게이트웨이가 다르면 분리하나, 게이트웨이 미제공(구형 OS) 호스트는 한 서브넷으로 합쳐 보일 수 있습니다.",
]


def _subnet_host_sort_key(host):
    """서브넷 내 호스트 정렬 키 — IP 숫자 오름차순, 파싱 불가(빈 IP)는 후순위."""
    try:
        return (0, int(ipaddress.ip_address(host.ip)))
    except ValueError:
        return (1, 0)


def build_network_topology(hosts) -> NetworkTopology:
    """hosts: ServerDetail/DTO 리스트 (public_id·hostname·os_family·interfaces 사용).

    interfaces 는 구조화 dict 리스트 [{name, address, prefix, family, kind}]. 물리(kind=physical) IPv4 만 채택.
    """
    # subnet CIDR -> [(pid, ip, gateway)]. 한 호스트가 같은 서브넷에 여러 IP 면 멤버십 1회만.
    subnet_members: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
    host_meta: dict[str, tuple[str, str]] = {}  # public_id -> (hostname, os_family)

    for h in hosts:
        pid = str(h.public_id)
        host_meta[pid] = (h.hostname, h.os_family or "unknown")
        seen_nets: set[str] = set()
        for iface_info in h.interfaces or []:
            if iface_info.get("kind") != "physical":
                continue  # 가상(bridge/veth/tunnel/loopback 등) 제외 — agent kind 태그 단일 신호
            if iface_info.get("family") != "ipv4":
                continue  # IPv4 v1 (IPv6 은 그래프 제외)
            addr = iface_info.get("address")
            prefix = iface_info.get("prefix")
            if addr is None or prefix is None:
                continue
            try:
                iface = ipaddress.ip_interface(f"{addr}/{prefix}")
            except ValueError:
                continue
            ip = iface.ip
            if ip.is_loopback or ip.is_link_local:
                continue  # (a) 안전망 — 루프백 / 169.254 APIPA
            if prefix == 0 or prefix >= 32:
                continue  # (a) netmask 부재 / host route
            subnet = str(iface.network)  # "10.0.1.0/24"
            if subnet in seen_nets:
                continue
            seen_nets.add(subnet)
            subnet_members[subnet].append((pid, str(ip), iface_info.get("gateway")))

    # gateway disambiguation + 단독 서브넷 필터. 한 서브넷에 서로 다른 non-null gateway 2+ 면 다른 물리망으로
    # 간주해 gateway 별로 분리(사설 대역 중복 오병합 방지). null gateway 는 gateway 1개뿐인 서브넷엔 합류,
    # 모호(2+)한 서브넷에선 귀속 불가라 제외. 가상망은 kind 로 이미 제외됨.
    surviving: dict[str, list[str]] = {}         # net_key -> pids
    seg_pid_ip: dict[str, dict[str, str]] = {}   # net_key -> {pid: ip}
    net_cidr: dict[str, str] = {}                # net_key -> subnet CIDR (정렬용)
    net_label: dict[str, str] = {}               # net_key -> 표시 라벨
    for subnet, members in subnet_members.items():
        gws = {gw for (_, _, gw) in members if gw}
        if len(gws) >= 2:
            groups = [
                (f"{subnet}#{gw}", f"{subnet} (via {gw})", [(p, i) for (p, i, g) in members if g == gw])
                for gw in sorted(gws)
            ]
        else:
            groups = [(subnet, subnet, [(p, i) for (p, i, _) in members])]
        for net_key, label, gm in groups:
            pids = sorted({p for (p, _) in gm})
            if len(pids) < 2:
                continue  # 단독 서브넷 -> inter-host 토폴로지 무의미
            surviving[net_key] = pids
            seg_pid_ip[net_key] = {p: i for (p, i) in gm}
            net_cidr[net_key] = subnet
            net_label[net_key] = label

    host_subnet_count: dict[str, int] = defaultdict(int)
    for pids in surviving.values():
        for pid in pids:
            host_subnet_count[pid] += 1

    ordered_nets = sorted(surviving, key=lambda k: ipaddress.ip_network(net_cidr[k]))  # 서브넷 주소 오름차순

    # 집계 뷰: subnet 노드만 기본 표시(hostCount 라벨), host 노드/엣지는 "collapsed" 로 시작 ->
    # network-topology.js 가 subnet 노드 클릭 시 해당 호스트를 펼친다 (대규모 호스트 hairball 회피).
    elements: list[dict] = []
    graph_hosts: set[str] = set()
    edges: list[tuple[str, str]] = []
    for net_key in ordered_nets:
        elements.append(
            {
                "data": {
                    "id": f"subnet:{net_key}",
                    "label": net_label[net_key],
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
    for net_key in ordered_nets:
        pid_ip = seg_pid_ip[net_key]
        hosts_list = []
        for pid in surviving[net_key]:
            hostname, os_family = host_meta[pid]
            hosts_list.append(
                SubnetHost(hostname=hostname, ip=pid_ip.get(pid, ""), os_family=os_family, public_id=pid)
            )
        hosts_list.sort(key=_subnet_host_sort_key)  # 서브넷 내 IP 숫자 오름차순
        subnets.append(SubnetGroup(net_key=net_label[net_key], host_count=len(hosts_list), hosts=hosts_list))

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
