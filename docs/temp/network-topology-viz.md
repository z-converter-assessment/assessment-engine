# 네트워크 토폴로지 시각화 설계 (서브넷-호스트 관계)

> 상태: 설계 초안 (미구현). 외부 공유용 self-contained 문서.
> 결정: 시각화 라이브러리 = Cytoscape.js / 형태 = Bipartite(서브넷 허브). 2026-05-30.
> 전제: agent 가 NIC 별 `IP/prefix`(CIDR, 예 `192.168.1.10/24`) 를 추가 수집해 발행하기로 대략 합의된 상황.

---

## 1. 목적

여러 호스트(VM)가 각자 여러 NIC 를 갖고, NIC 마다 서로 다른 서브넷의 IP 를 할당받는다.
"같은 서브넷에 속한 호스트"라는 공통분모를 시각적으로 드러내, 운영자가
- 어떤 호스트들이 같은 L2/L3 세그먼트에 묶여 있는지
- 멀티홈(NIC 2개+) 호스트가 어떤 서브넷들을 연결하는지(게이트웨이·브리지 후보)
- 고립된 서브넷·단일 호스트 서브넷
를 한눈에 파악하게 한다.

## 2. 왜 Bipartite(서브넷 허브) 인가

서브넷-호스트 관계는 본질적으로 2-mode(bipartite) 그래프다.
- 한쪽 노드 집합 = 서브넷(CIDR network address)
- 다른쪽 노드 집합 = 호스트
- 엣지 = "이 호스트의 NIC 가 이 서브넷에 IP 를 갖는다" (엣지 라벨 = IP)

```
        [ 192.168.1.0/24 ]                 [ 10.0.5.0/24 ]
          /     |      \                       |      \
   .1.10/      .1.11    \.1.12          .5.20 /        \ .5.21
   hostA      hostB      hostC ----------------+        hostD
                          (multihomed: NIC0 -> .1.0/24, NIC1 -> 10.0.5.0/24)
```

대안 대비 우위:
- 서브넷 그룹 박스(compound) 방식은 호스트가 부모 박스 1개에만 속할 수 있어 멀티홈을
  표현 못 한다. Bipartite 는 호스트가 여러 서브넷 허브에 엣지를 그어 멀티홈을 자연 표현.
- 호스트 mesh(같은 서브넷 호스트끼리 직접 연결) 방식은 서브넷 식별이 약하고, 한 서브넷에
  N 호스트면 엣지가 N*(N-1)/2 로 폭증한다. 허브 방식은 엣지가 NIC 수에 선형.

## 3. 시각화 도구 = Cytoscape.js

- 그래프 이론 특화 — 노드/엣지 1급 모델, 풍부한 레이아웃.
- 레이아웃 `fcose`(force-directed, 클러스터 분리 우수) 로 서브넷별 호스트 군집이 자동 분리.
- 인터랙티브 — 줌·팬·드래그·hover tooltip·노드 선택 하이라이트.
- 대규모(수백 노드) 대응. 향후 호스트 증가에도 확장.
- HTML + JS 외부 라이브러리(CDN 또는 번들). 본 엔진의 JS 외부화 규약과 정합(별도 `.js`).

대안: vis-network(물리 내장, 코드 적음) 로도 동일 bipartite 가능 — 빠른 프로토타입이
우선이면 후보. 단 레이아웃 커스텀·대규모는 Cytoscape 가 우위라 본 설계는 Cytoscape 채택.

## 4. 필요 payload 정의 (agent -> engine)

현재 inventory payload 에는 `ip_internal`(IP 문자열 리스트)만 있고 NIC-IP-서브넷 매핑이 없다.
서브넷 도출을 위해 NIC 별 `IP/prefix`(CIDR) 가 필요하다.

제안 payload 필드 (inventory 메시지에 추가):

```json
{
  "network_interfaces": [
    {
      "name": "eth0",
      "addresses": [
        { "ip": "192.168.1.10", "cidr": "192.168.1.10/24", "family": "ipv4" }
      ]
    },
    {
      "name": "eth1",
      "addresses": [
        { "ip": "10.0.5.20", "cidr": "10.0.5.20/24", "family": "ipv4" }
      ]
    }
  ]
}
```

필드 규약:
- `name` : NIC 이름 (eth0, ens192, Ethernet0 등). 멀티홈 식별 단위.
- `addresses[].ip` : 호스트 주소 (xxx.xxx.xxx.xxx).
- `addresses[].cidr` : `IP/prefix` 표기 (예 `192.168.1.10/24`). prefix 로 서브넷 network
   address 도출. 합의된 "xxx.xxx.xxx.xxx/xx" 형식이 여기에 해당.
- `addresses[].family` : `ipv4` | `ipv6`. 초기 구현은 ipv4 만 시각화, ipv6 는 수집·보존만.
- loopback(127.0.0.0/8, ::1) · link-local(169.254/16, fe80::/10) 은 시각화에서 제외
  (engine 필터). 수집 단계에서 넣어도 무방.

서브넷 도출(engine 측):
- `cidr = 192.168.1.10/24` -> Python `ipaddress.ip_interface(cidr).network` -> `192.168.1.0/24`.
- 서브넷 노드 id = network address 문자열(`192.168.1.0/24`).
- 같은 network 를 가진 모든 호스트 NIC 가 그 서브넷 노드에 연결.

## 5. 데이터 흐름 (#B 계약 + #F9 체크리스트 대상)

미구현. 실제 진행 시 아래 체인 동시 갱신 필요(엔진 규약 #F9 "inventory 컬럼 추가" 류):

```
agent payload (network_interfaces)
  -> consumer inbound schema (Pydantic, extra=ignore 유지)
  -> Inbound DTO + handler 매핑
  -> DB 저장: 신규 테이블 server_network_interface
       (server_id FK, nic_name, ip, prefix, family, collected_at)
       또는 inventory JSONB 컬럼(network_interfaces) — 정규화 필요성에 따라 결정
  -> Query repo: 전 서버 NIC-CIDR 조회 (raw)
  -> Service/mapper: CIDR -> network address 도출, 서브넷별 그룹핑 (P2)
  -> ViewModel: { subnets:[{cidr,hosts:[...]}], hosts:[...], edges:[{host,subnet,ip,nic}] }
  -> JSON API (예: GET /api/network/topology) — Cytoscape elements 직접 소비 형태
  -> 페이지(SSR shell) + 외부화 .js (Cytoscape 초기화·렌더)
```

저장 모델 선택지(추후 결정):
- 정규화 테이블 `server_network_interface` : NIC 다중·시계열 변화 추적 용이. 조인 비용.
- inventory JSONB `network_interfaces` 컬럼 : 단순, 최신 스냅샷만. 서브넷 집계는 앱 레이어.
초기 구현은 "최신 인벤토리 스냅샷의 서브넷 관계" 표시가 목적이라 JSONB 컬럼이 가벼움.
시계열(서브넷 멤버십 변화 이력)이 필요해지면 정규화 테이블로 승격.

## 6. Cytoscape elements 모델

```
nodes:
  - { data: { id: "subnet:192.168.1.0/24", label: "192.168.1.0/24",
              type: "subnet", host_count: 3 } }
  - { data: { id: "host:<public_id>", label: "app-server-01",
              type: "host", os_family: "linux", online: true } }
edges:
  - { data: { id: "nic:<public_id>:eth0:192.168.1.0/24",
              source: "host:<public_id>", target: "subnet:192.168.1.0/24",
              ip: "192.168.1.10", nic: "eth0" } }
```

스타일 규약(엔진 표시 표준과 색 일관, 예시):
- subnet 노드 : 큰 원/사각, 라벨 = CIDR, 크기를 host_count 비례(공통분모 강조).
- host 노드 : 작은 원, online/offline 색 구분(엔진 status dot 색과 동일 hex).
- edge : 가는 선, hover 시 IP·NIC 라벨 표시. 멀티홈 호스트는 자연히 여러 edge.
- 멀티홈 강조 : degree(연결 서브넷 수) >= 2 인 host 노드 테두리 강조.

레이아웃 : `fcose` (animate, nodeRepulsion 조정으로 서브넷 군집 분리).
인터랙션 : 노드 클릭 -> 해당 호스트 상세(`/servers/{public_id}`) 링크, 서브넷 클릭 ->
그 서브넷 멤버 호스트 하이라이트.

## 7. 구현 단계 (추후)

1. agent payload 확장 합의 확정(`network_interfaces` 필드·CIDR 형식) — 양측 계약.
2. consumer schema + inbound DTO + handler + DB(JSONB 컬럼 또는 신규 테이블) + Alembic.
3. Query repo(NIC-CIDR raw) + Service/mapper(서브넷 도출·그룹핑) + ViewModel.
4. JSON API(`/api/network/topology`) — Cytoscape elements 형태 응답.
5. 페이지(SSR shell) + 외부화 `.js`(Cytoscape 초기화) + 신규 의존성(Cytoscape.js) 도입 검토.
6. loopback/link-local 필터, ipv4 우선, 멀티홈 강조, 빈 상태(서브넷 0) placeholder.

## 8. 한계·메모

- ipv6 는 초기 시각화 제외(수집·보존만). dual-stack 표현은 후속.
- 서브넷 = L3 CIDR network 기준. 동일 CIDR 이 물리적으로 다른 L2 일 가능성(중복 사설망)은
  본 시각화 범위 밖 — VLAN/세그먼트 식별 메타가 추가되면 보강.
- 대규모(수천 노드)에서 fcose 비용 — 서브넷 단위 필터·페이지네이션 고려.
