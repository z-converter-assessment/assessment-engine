"""네트워크 토폴로지 mapper (P2) — 호스트별 집계 대상 인터페이스(physical·bond_master)에서 L3 subnet 그래프 도출.

Repository 는 net_interfaces 를 구조화 dict(kind/gateway/addresses[{address,prefix,family}])로 보존(P1). 본
mapper 가 subnet 그룹핑·가상망 필터·노드/엣지 조립을 단일 책임으로 수행(P2).

토폴로지 의미: agent 는 인터페이스 IP+prefix 만 발행한다 (LLDP/ARP/traceroute 같은 실측 인접 정보 없음).
유일하게 도출 가능한 관계 = "두 호스트가 같은 network address(IP & prefix)면 같은 브로드캐스트 도메인"이라는
추론이다. 실측 reachability 가 아니다 — VLAN/방화벽 격리 미반영, RFC1918 중복이면 별개 네트워크를 한
서브넷으로 오병합할 수 있다 (caveats 로 정직 노출).

집계 대상 필터: interface `kind` 태그로 physical + bond_master 만 채택 — 본딩 호스트는 IP 가 bond_master(bond0)에
실려 포함. docker0/veth/bridge/vlan/tunnel/loopback/bond_member 는 제외. link-local·prefix 0/32 은 안전망으로 추가 제외.
2대 미만 단독 서브넷은 inter-host 토폴로지에 무의미해 제외.

IPv4 only: 그래프는 physical+bond_master 인터페이스의 IPv4 주소만. IPv6 는 family 로 제외.

명세·근거 단일 진실은 본 모듈 docstring + view_models/topology.NetworkTopology.
"""

import ipaddress
from collections import defaultdict

from assessment_engine.service_classifier import SIGNATURE_CATEGORIES
from assessment_engine.web.services.device_filters import is_virtual_interface
from assessment_engine.web.view_models.topology import NetworkTopology, SubnetGroup, SubnetHost

_CAVEATS = [
    "추론 토폴로지 — 같은 서브넷(IP·prefix) 공유 기준이며, 실제 통신 가능 여부(방화벽·VLAN 격리)는 반영하지 않습니다.",
    "물리·본딩(bond) 인터페이스 IPv4 주소만 사용합니다 — 가상망(docker·bridge·veth·vlan)·IPv6는 제외했습니다.",
    "사설 IP 대역 중복은 게이트웨이가 다르면 분리하나, 게이트웨이 미제공(구형 OS) 호스트는 한 서브넷으로 합쳐 보일 수 있습니다.",
]


def _subnet_host_sort_key(host):
    """서브넷 내 호스트 정렬 키 — IP 숫자 오름차순, 파싱 불가(빈 IP)는 후순위."""
    try:
        return (0, int(ipaddress.ip_address(host.ip)))
    except ValueError:
        return (1, 0)


def build_network_topology(hosts) -> NetworkTopology:
    """hosts: ServerDetail/DTO 리스트 (public_id·hostname·os_family·net_interfaces 사용).

    net_interfaces 는 [{kind, gateway, addresses:[{address, prefix, family}]}]. physical+bond_master 의 IPv4 주소만 채택.
    """
    # subnet CIDR -> [(pid, ip, gateway)]. 한 호스트가 같은 서브넷에 여러 IP 면 멤버십 1회만.
    subnet_members: dict[str, list[tuple[str, str, str | None]]] = defaultdict(list)
    host_meta: dict[str, tuple[str, str]] = {}  # public_id -> (hostname, os_family)
    host_roles: dict[str, list[str]] = {}  # public_id -> 주요 워크로드 카테고리(시그니처만, 환경 개요 도넛·서버 목록 뱃지와 동일 기준)

    for h in hosts:
        pid = str(h.public_id)
        host_meta[pid] = (h.hostname, h.os_family or "unknown")
        # 시그니처 워크로드만(SIGNATURE_CATEGORIES) — file·mail·infra·remote 등 baseline·관리는 토폴로지 뱃지 노이즈라 제외.
        host_roles[pid] = sorted(
            c for c in (getattr(h, "service_categories", None) or []) if c in SIGNATURE_CATEGORIES
        )
        seen_nets: set[str] = set()  # 호스트 스코프 — 여러 인터페이스/주소가 같은 서브넷 잡아도 멤버십 1회
        for iface_info in h.net_interfaces or []:
            if is_virtual_interface(iface_info.get("kind")):
                continue  # 집계 단위(physical·bond_master)만 — bridge/veth/vlan/tunnel/loopback/bond_member 제외
            gateway = iface_info.get("gateway")  # gateway 는 인터페이스 레벨 (주소별 아님)
            for a in iface_info.get("addresses") or []:
                if a.get("family") != "ipv4":
                    continue  # IPv4 only (IPv6 은 그래프 제외)
                addr = a.get("address")
                prefix = a.get("prefix")
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
                subnet_members[subnet].append((pid, str(ip), gateway))

    # gateway disambiguation + 단독 서브넷 필터. 한 서브넷에 서로 다른 non-null gateway 2+ 면 다른 물리망으로
    # 간주해 gateway 별로 분리(사설 대역 중복 오병합 방지). null gateway 는 gateway 1개뿐인 서브넷엔 합류,
    # 모호(2+)한 서브넷에선 귀속 불가라 제외. 가상망은 kind 로 이미 제외됨.
    surviving: dict[str, list[str]] = {}  # net_key -> pids
    seg_pid_ip: dict[str, dict[str, str]] = {}  # net_key -> {pid: ip}
    net_cidr: dict[str, str] = {}  # net_key -> subnet CIDR (정렬용)
    net_label: dict[str, str] = {}  # net_key -> 표시 라벨
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
                    "roles": host_roles.get(pid, []),  # 워크로드 카테고리 — 노드 tooltip·색 (app tier)
                },
                "classes": "collapsed",
            }
        )

    for pid, net_key in edges:
        elements.append({"data": {"source": f"host:{pid}", "target": f"subnet:{net_key}"}, "classes": "collapsed"})

    # 서브넷별 소속 서버 목록 (IP 표시) — 그래프와 별개 카드. net_members 에서 pid->ip 복원.
    subnets: list[SubnetGroup] = []
    for net_key in ordered_nets:
        pid_ip = seg_pid_ip[net_key]
        hosts_list = []
        for pid in surviving[net_key]:
            hostname, os_family = host_meta[pid]
            hosts_list.append(
                SubnetHost(
                    hostname=hostname,
                    ip=pid_ip.get(pid, ""),
                    os_family=os_family,
                    public_id=pid,
                    roles=host_roles.get(pid, []),
                )
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
