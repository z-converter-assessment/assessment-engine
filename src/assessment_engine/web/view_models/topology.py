"""네트워크 토폴로지 ViewModel — 호스트별 물리 인터페이스에서 도출한 L3 subnet 공동소속 그래프.

조립·필터·파싱은 `mappers/topology.build_network_topology` 단일 진실 (여기는 결과 형태만 정의).
elements 형식(노드·엣지 data 표)은 `docs/reference/web/view-models.md` "네트워크 토폴로지 그래프 elements" 절.
"""

from dataclasses import dataclass, field

from assessment_engine.json_types import JsonObject


@dataclass
class SubnetHost:
    hostname: str
    ip: str
    os_family: str
    public_id: str
    roles: list[str] = field(default_factory=list[str])
    origin: str | None = None
    mtu: int | None = None
    speed_mbps: int | None = None
    is_online: bool = False
    multi_homed: bool = False


@dataclass
class SubnetGroup:
    net_key: str
    host_count: int = 0
    gateway: str | None = None
    hosts: list[SubnetHost] = field(default_factory=list[SubnetHost])


@dataclass
class NetworkTopology:
    elements: list[JsonObject]
    subnet_count: int
    host_count: int
    multi_homed_count: int
    isolated_count: int
    router_count: int = 0  # 공유 게이트웨이(2+ 서브넷) 라우터 노드 수 — 0 이면 라우터 범례 미노출
    subnets: list[SubnetGroup] = field(default_factory=list[SubnetGroup])
    caveats: list[str] = field(default_factory=list[str])  # 카드 캡션 — 추론 한계 정직 노출
    has_data: bool = False
