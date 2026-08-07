"""네트워크 토폴로지 ViewModel — 호스트별 물리 인터페이스에서 도출한 L3 subnet 공동소속 그래프.

조립·필터·파싱은 `mappers/topology.build_network_topology` 단일 진실 (여기는 결과 형태만 정의).
elements 형식(노드·엣지 data 표)은 `docs/reference/web/view-models.md` "네트워크 토폴로지 그래프 elements" 절.
"""

from dataclasses import dataclass, field

from assessment_engine.json_types import JsonObject


@dataclass
class SubnetHost:
    hostname: str
    ip: str  # 해당 서브넷에서 호스트가 주장한 IP (실측 아님)
    os_family: str
    public_id: str
    roles: list[str] = field(default_factory=list[str])  # 시그니처 워크로드 카테고리 — 서브넷별 app tier
    origin: str | None = None  # dhcp/static — 고정 IP 서버 식별
    mtu: int | None = None  # 같은 서브넷 내 불일치 발견용
    speed_mbps: int | None = None  # 대역폭 병목 후보 식별용
    is_online: bool = False
    multi_homed: bool = False  # 2+ 서브넷에 걸친 호스트 (브리지/라우터 후보)


@dataclass
class SubnetGroup:
    net_key: str  # network address ("10.0.1.0/24")
    host_count: int = 0  # len(hosts) precompute — 템플릿이 계산하지 않게
    gateway: str | None = None  # 서브넷당 1개로 mapper 가 이미 추린 대표 게이트웨이
    hosts: list[SubnetHost] = field(default_factory=list[SubnetHost])


@dataclass
class NetworkTopology:
    elements: list[JsonObject]  # Cytoscape elements (노드 + 엣지) — mapper precompute
    subnet_count: int  # 표시된 공유 서브넷(세그먼트) 수
    host_count: int  # 1개+ 공유 서브넷에 속해 그래프에 포함된 호스트 수
    multi_homed_count: int  # 2개+ 서브넷에 걸친 호스트 수 (라우팅/브리지 지점)
    isolated_count: int  # 공유 서브넷에 들지 못한 호스트 수 (단독·가상망만 보유)
    router_count: int = 0  # 공유 게이트웨이(2+ 서브넷) 라우터 노드 수 — 0 이면 라우터 범례 미노출
    subnets: list[SubnetGroup] = field(default_factory=list[SubnetGroup])
    caveats: list[str] = field(default_factory=list[str])  # 카드 캡션 — 추론 한계 정직 노출
    has_data: bool = False  # False 면 템플릿 empty_state
