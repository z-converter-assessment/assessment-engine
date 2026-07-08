"""네트워크 토폴로지 ViewModel — 대시보드 '네트워크 토폴로지' 카드 (P2 precompute).

호스트별 물리 인터페이스에서 도출한 L3 subnet 공동소속 그래프.
조립·필터·파싱은 mappers/topology.build_network_topology 단일 진실 (본 dataclass 는 결과 형태만 정의).

elements: Cytoscape.js elements 형식(`{"data": {...}}` 리스트)으로 mapper 가 precompute.
  - 그래프 노드/엣지 조립은 결정론적 표현 변환이라 mapper(P2)에서 굳혀 둔다. 템플릿은 `| tojson`,
    network-topology.js 는 레이아웃·스타일·클릭 바인딩만(P4: 라이브러리 옵션 조립·시각화).
  - subnet 노드 data: {id "subnet:<net>", label <net>, kind "subnet", hostCount}
  - host 노드 data:   {id "host:<public_id>", label <hostname>, kind "host", publicId, osFamily}
                      classes "collapsed" — 집계 뷰 초기 숨김(서브넷 노드 클릭 시 펼침)
  - edge data:        {source "host:<public_id>", target "subnet:<net>"}, classes "collapsed"
"""

from dataclasses import dataclass, field


@dataclass
class SubnetHost:
    hostname: str  # display
    ip: str  # 해당 서브넷에서 호스트가 주장한 IP (raw, "10.0.1.15")
    os_family: str  # linux/windows/unknown — 표시용
    public_id: str  # 상세 링크 (#E4)
    roles: list[str] = field(default_factory=list)  # 워크로드 카테고리(service_categories, E7) — 서브넷별 app tier


@dataclass
class SubnetGroup:
    net_key: str  # network address ("10.0.1.0/24")
    host_count: int = 0  # 본 서브넷 호스트 수 (보고서 서브넷 요약 표 — len(hosts) precompute, P3)
    hosts: list[SubnetHost] = field(default_factory=list)  # 본 서브넷 소속 호스트 (IP 표시)


@dataclass
class NetworkTopology:
    elements: list[dict]  # Cytoscape elements (노드 + 엣지) — mapper precompute
    subnet_count: int  # 표시된 공유 서브넷(세그먼트) 수
    host_count: int  # 그래프에 포함된 호스트 수 (1개+ 공유 서브넷 소속)
    multi_homed_count: int  # 2개+ 서브넷에 걸친 호스트 수 (라우팅/브리지 지점)
    isolated_count: int  # 공유 서브넷에 들지 못한 호스트 수 (단독·가상망만 보유)
    subnets: list[SubnetGroup] = field(default_factory=list)  # 서브넷별 소속 서버 목록 카드용
    caveats: list[str] = field(default_factory=list)  # 카드 캡션 — 추론 한계 정직 노출 (#E9)
    has_data: bool = False  # 표시할 공유 서브넷 존재 여부 — False 면 템플릿 empty_state
