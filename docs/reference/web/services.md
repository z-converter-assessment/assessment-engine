# Web 서비스 계층 모듈

정책: CLAUDE.md #E3 · #E7 (도메인 분류) · #F10 (평가 윈도우). 본 문서는 service 모듈 카탈로그·서비스 분류·Recommendation·환경 개요 상단 요약 단일 진실.

| 모듈 | 책임 |
|------|------|
| `query_service.py` (+ `query/` 패키지) | Redis 캐시 + repository 오케스트레이션. SSR/JSON 양 경로에 일관된 ViewModel·Summary 반환. `QueryService` 는 6 도메인 mixin (`query/` 하위 server·metric·attention·environment·report·task) 을 multiple inheritance 로 결합 — repo 계층 `db/repositories/query/` 와 동형. 공유 helper(`_online_map`·`_inject_net_baseline`)는 `query/_base.py` |
| `task_service.py` | Task 발행 (DB INSERT + Redis SET). 트랜잭션 경계 + `IntegrityError` -> `TaskDuplicatePending` 변환. 본 모듈 상단 `HttpZdmPackageResolver` (ZDM 패키지 sha256·size 동적 조회 — install 발행 의존성) |
| `mappers/` (sub-package) | Outbound DTO + Detail -> ViewModel 변환 단일 진실 (P2). 12 sub-module — `server.py`(상세·목록 ViewModel + `infer_role`) / `metric.py` / `attention.py` / `report.py` / `task.py` / `shared.py` (공용 임계 상수 + ReportView Literal + `_DONUT_SEGMENT_DEFS` + `UTIL_GAUGE_COLOR` + `spec_display_line`(정적 사양 한 줄) + `_eol_info`(경계 3개 -> 지원 단계 4상태 판정)/`resolve_os_eol`·`lookup_os_eol`/endoflife 카탈로그) / `environment_report.py` (환경 보고서 합성) / `report_history.py` (보고서 이력 row) / `topology.py` (네트워크 토폴로지 — Cytoscape 집계 그래프 elements + 서브넷별 서버 목록 `SubnetGroup`) / JSON API 응답 매퍼 3종 (`api_reference.py` OpenAPI 스펙 -> API 목록 · `assessment_api.py` `/api/assessment` per-server 계약 dict · `right_sizing_api.py` per-server 프로비저닝 판정 dict — E6 타입계약 원천, 분류·근거는 도메인 단일 진실(`rollup_host` 등) 재사용) |
| `metrics_calculator.py` | CPU/Disk/Net delta + Mem/Swap 시점값 -> Snapshot. reset 판정은 `boot_time.is_counter_reset`(공용 도메인 모듈, 지터 허용 비교) 경유 — 재부팅 구간 delta 는 None |
| `diagnostic_service.py` | 보고서 발행 enqueue(비동기 parent job) + 동기 저장(워커 child 경로) + job 상태 전이(claim/finish/recover) + 발행 이력. 추상 `BaseDiagnosticRepository`만 의존 |
| `report_generator.py` | pending job -> 보고서 ViewModel 생성 디스패치 (워커·발행 경로 공유 단일 진실). 보고서 교차참조 helper `attention_for_host`/`attention_by_host` 정의 |
| `report_serializer.py` | 발행 시점 정적 스냅샷 serde — ViewModel <-> `diagnostic_jobs.result` JSONB |
| `cache_serializer.py` | Redis serde — `ServerDetailResponse` / `MetricDashboard`. 역직렬화 후 `enrich_*` 재호출 (idempotent) |
| `serialization_util.py` | `cache_serializer`·`report_serializer` 공용 직렬화 계약 (datetime -> ISO, dataclass -> dict) |
| `unit_converter.py` | KB->GB / sectors->KB/s / usage_pct 단위 변환 |
| `device_filters.py` | block_device `type`·fstype·net_interface `kind` 기반 계층 술어 단일 진실 — `is_physical_disk`(type=="disk")·`is_lvm_disk`(lvm/raid/crypt/mpath/dynamic)·`is_partition`(=="part")·`is_swap`(=="swap")·`is_virtual_interface`(kind not in physical/bond_master)·`is_data_volume`(fstype·mountpoint — 가상fs·/boot 제외) + `disk_total_bytes`/`swap_total_bytes` (block_device type 합산 단일 산식 — Windows PhysicalDrive 도 type=disk 발행이라 양 OS 공통, fallback 없음). 부모-자식 조인은 노드 `parent`(부모 id) |
| `service_classifier.py` (도메인 `assessment_engine/`, web·consumer 공용 — `recommendation.py` 동급) | 서비스 -> 카테고리 (`web`/`db`/`cache`/`mq`/`container`/`monitor`/`remote`/`file`/`mail`/`infra` — 원칙·경계 규칙은 아래 "카테고리 경계" 절) + 포트 매핑 + 카테고리 집합 사전계산(`compute_service_categories`, ingest 가 `service_categories` 저장). 단일 카탈로그(`SERVICE_CATALOG`) 파생. `MatchedPort` 정의(web view_model re-export) |

## 서비스 분류 — 3단계 표시 계층

| 단계 | 페이지 | 표시 |
|------|--------|------|
| 목록 | `/servers` | 카테고리 chip (ingest 사전계산 `service_categories` 집합) — 원본 unit·개수 노출 안 함 |
| 상세 | `/servers/{id}` | unit 이름 + matched_ports + 카테고리 badge |
| services 탭 | `/servers/{id}/services` | unit 전체 + sub state + 포트 + 카테고리 |

### 카테고리 경계

분류 축은 하나다 — "이 호스트에서의 주 역할(배포 목적)". 카테고리는 상호배타라 한 서비스가 정확히 하나에 든다. 겸업(예: 프록시가 캐시도 함)이면 그 소프트웨어의 존재 이유로 tie-break 해 경계를 재현 가능하게 만든다.

| 카테고리 | 범위 |
|----------|------|
| `web` | 앱·웹 서빙 + 리버스 프록시·로드밸런서 (앱 트래픽 앞단 edge) |
| `db` | 범용 데이터 저장소 (RDBMS·NoSQL·범용 TSDB·검색엔진) |
| `cache` | 인메모리·휘발 캐시·HTTP 가속 |
| `mq` | 메시지 브로커 |
| `mail` | 메일 서버 (SMTP/IMAP/POP3) |
| `file` | 파일·오브젝트·블록 스토리지 공유 |
| `remote` | 원격 접속·관리 (관리 표면 — 대개 전 호스트) |
| `infra` | 네트워크 인프라 (DNS·DHCP·NTP·디렉토리·SNMP·포워드 프록시·HA) |
| `monitor` | 관측 전용 도구 (수집·저장·시각화·알림) |
| `container` | 컨테이너 런타임·오케스트레이션 (호스트당 1) |

모호한 경계는 못박아 둔다 — 원칙이 없으면 판단이 자의적으로 보인다.

| 케이스 | 규칙 | 예 |
|--------|------|-----|
| 프록시 | 리버스·LB(inbound, 앱 앞단) = `web` / 포워드·egress = `infra` / 캐싱이 주목적 = `cache` | haproxy·traefik = web · squid = infra · varnish = cache |
| 시계열 | 범용 TSDB = `db` / 관측 전용 설계 = `monitor` | influxdb = db · prometheus·victoriametrics·loki = monitor |
| 검색엔진 | 저장이 본질이라 `db` (ELK 로그 용도여도) | elasticsearch = db · 오브젝트 스토리지 minio = file |
| 디렉토리 | 인증·디렉토리 인프라라 `infra` (계층 db 로 보지 않음) | ldap·slapd = infra |

카탈로그의 카테고리 순서가 곧 분류 우선순위다 (cross-category 첫 매칭 우선) — `web` -> `db` -> ... 순서를 유지한다.

### 단일 카탈로그 + 다중 신호 분류

`SERVICE_CATALOG`(`CategoryDef` 튜플)이 카테고리별 규약의 단일 진실 — `name_keywords`(unit·comm 공용 substring) / `port_names`(서비스명 -> well-known 포트) / `badge_class`. 파생물은 import 시점 1회 계산: `_NAME_INDEX`(분류 키워드) · `_NAME_PORTS`(서비스명 -> 포트) · `_PORT_INDEX`(port -> category) · `SERVICE_CATEGORIES`(드롭다운·범례) · `BADGE_CLASS_BY_CATEGORY`(templating filter import). 서비스 추가 = 카탈로그 1곳만 수정.

`classify(unit, listen_ports=None)` — 다중 신호, 정밀도 우선순위:
1. unit 이름 키워드 (최고 정밀 — 소프트웨어 정체성)
2. comm 키워드 (이름 미스매치 흡수 — Windows SCM 이름과 exe basename 불일치 등)
3. listen 포트 (프로토콜 사실관계 — 1433 -> db)

우선순위 근거: 소프트웨어 정체성(name/comm)이 프로토콜(port)보다 카테고리 정밀도가 높다 (haproxy가 5432를 프록시해도 web). port는 name/comm 무정보 시 fallback. `listen_ports` 미제공 시 name 신호만 (per-unit 상세 표시 등 listen_ports 없는 호출). 목록 뱃지는 `classify` 직접 호출이 아니라 ingest 사전계산 `service_categories` 소비.

`classify` 의 comm/port 신호는 `_attributed_ports`(comm~name 또는 name well-known 포트로 unit에 귀속된 포트)에만 적용 — per-unit(services 탭) 표시용이라 multi-service 오분류 방지가 우선. 단 per-unit 분류만으론 이름이 comm과 무관한 opaque 서비스를 못 잡는다(agent 가 services 와 listen_ports 를 잇는 pid join key 미발행 — T15).

호스트 워크로드 union (agent 불변 전제의 최선):
- `detect_listen_categories(listen_ports)` — listen 소켓을 services unit 과 무관하게 직접 분류(comm 키워드 우선, 없으면 port). listen 소켓의 comm(exe basename)·port 는 지저분한 service 이름과 달리 깨끗·안정(OS·인스턴스명·로케일 무관) 식별자라, "이 호스트가 무슨 워크로드를 listen 하나"를 직접 탐지. Windows opaque SCM 이름(`MSSQL$무엇`)을 1433/`sqlservr` 로 우회.
- `workload_category_counter(services, listen_ports)` — services 이름 분류(인스턴스 카운트) ∪ listen 탐지(이름이 못 잡은 카테고리만 +1, 이중 카운트 회피). role/뱃지/환경 분포 단일 진실.
- baseline 제외 — `is_baseline_service`/`is_baseline_socket`(`_BASELINE_KEYWORDS`) 로 OS 기본·관리 서비스(SSH·RDP·NTP·RPC + `systemd-` 자체 유닛)를 특징 워크로드 집계에서 뺀다. 거의 전 호스트에 있어 "이 서버가 무슨 서버인가"를 구분하지 못하고, 포트 문맥 classify 가 systemd 소켓을 엉뚱한 카테고리로 끌어들이는 노이즈도 차단. `remote`(SSH·RDP) 는 전부 baseline 이라 환경 집계엔 항상 0(서버 상세 live classify 만 노출). `workload_category_counter`·`compute_service_categories`·`workload_services_by_category` 공통 기준.
- `workload_services_by_category(services, listen_ports)` — 위와 동일 기준(baseline·unknown 제외)으로 카테고리별 특징 서비스명 목록. 환경/N대 보고서 "서비스 구성" 카드 breakdown 이 total_count(카테고리 distinct 호스트)와 같은 소스를 써 정합하고, 포트로만 탐지돼 이름 미상인 호스트는 "(포트 탐지)"로 합산.
- 런타임 스택 카테고리(`CategoryDef.single_instance` — 현재 container)는 호스트당 1로 집계. docker+containerd, kubelet+containerd 처럼 1 런타임이 여러 서비스로 떠도 "container 2" 로 부풀리지 않는다 (카운터·목록 뱃지 category_count·detail 뱃지 모두 적용, `SINGLE_INSTANCE_CATEGORIES`). web/db 등 일반 카테고리는 인스턴스 카운트 유지.
- 적용: server detail 뱃지(`enrich_server_detail`) · 환경 개요 주요 워크로드 도넛(`build_environment_overview` — 시그니처 카테고리만) · `infer_role`(export) · 보고서 mapper(`to_report_row_item`/`build_role_distribution` — `ReportRowRaw` 가 `listen_ports` 보유, 개별 보고서 구동 서비스 차등·role 보강). listen_ports 보유.
- detail 뱃지 포트 표시 = 카테고리 단위 집계 — 각 카테고리 뱃지에 (comm 으로 unit 에 귀속된 포트) + (그 카테고리의 listen 포트, 카테고리당 1회)를 합쳐 붙인다. comm 귀속이 실패하는 워크로드(IIS `W3SVC`<->`System` 의 80/tcp·tcp6)도 카테고리 단위로 80 이 뱃지에 붙음. listen-only 카테고리(services 이름이 못 잡은)는 unit 없는 합성 `ServiceItem`. 뱃지에 귀속된 포트는 "주요 Listen 포트"(`key_listen_ports`)에서 제외, 카테고리 없는 OS 인프라 포트(svchost RPC/SMB/NTP 등)만 거기 남는다. 표시는 캡슐 박스로 카테고리-포트 대응을 한 묶음으로 (detail.html).
- 목록 행 뱃지: ingest 사전계산 `service_categories`(text[]) 소비 — `ServerSummary` 가 services JSONB·listen_ports 재로드 없이(경량 partial SELECT, #C2/E2) 상세·환경요약·보고서와 동일 카테고리 집합. 이름·comm·포트 어느 신호로 식별되든 일치(화면 간 비대칭 0, 행별 재분류 0).
- 잔존 한계: listen 안 하거나 localhost-only 바인드 워크로드 + opaque 이름은 여전히 미상(union 두 소스 모두 못 잡음). 노이즈는 unknown 통일 노출(E9).

`infer_role(services, listen_ports=None)` = `workload_category_counter` 최빈 카테고리.

주의: 본 절 `classify`는 service_classifier 의 서비스 카테고리 분류. 아래 "Recommendation 분류"의 `classify_host`/`rollup_host`(USE Method right-sizing)와 다른 함수 — 혼동 금지.

## 디바이스 계층 — block_devices[] 평면 DAG (parent-by-id)

스토리지 토폴로지는 agent `block_devices[]` 평면 그래프(lsblk 정석) — 노드 `{name, type, size_bytes, fstype, mountpoint, parent, id, id_type}`. 부모-자식(disk->part->lvm->crypt->fs)은 노드 `parent`(부모 id) 체인 조인. 다중 부모(RAID span·striped VG)는 디스크별 그룹으로 노출. 정적 토폴로지(무엇이 존재)와 동적 사용량(server_filesystem, 얼마나 찼나)을 분리해 모든 소비처(용량·상세·export·토폴로지)가 같은 술어를 공유한다.

## 디바이스 필터 정책 — block_device `type` · fstype · net `kind`

디스크·마운트·인터페이스의 물리/논리/데이터/가상 판정은 계층별 원본 필드로 한다 (payload 계약 #B). 엔진은 이름 정규식·major 추론 없이 필드로만 판정 — 화면·집계·용량 단일 기준. `device_filters` 단일 진실:

- `is_physical_disk(type)` = `type=="disk"` (PhysicalDrive/sd/nvme/vd. partition/lvm/raid/swap 제외).
- `is_lvm_disk(type)` = `type in ("lvm","raid","crypt","mpath","dynamic")` — 물리 부재(Windows dynamic 등) 시 disk 차트 fallback 차원.
- `is_partition(type)` = `type=="part"`. `is_swap(type)` = `type=="swap"` (v2 는 swap 을 block_device 노드로 표현).
- `is_data_volume(fstype, mountpoint)` = 가상 fs(`VIRTUAL_FSTYPES`) 아니고 mountpoint 가 `/boot` 아님 — fstype None(미상)은 데이터로 포함(df 관례).
- `is_virtual_interface(kind)` = `kind not in ("physical","bond_master")` — 물리 NIC + bond_master 만 통과(bond_member 이중 집계 회피). net_interface 만 `kind` 유지(block_device 는 `type`).
- 집계 SQL 투영은 `types._DATA_VOLUME_SQL_FILTER`(raw 테이블) 와 `types._DATA_VOLUME_CAGG_FILTER`(cagg) — 둘 다 `device_filters.is_data_volume` 의 SQL 등가다. 변경 시 셋을 함께 맞춘다.

적용 경계 — 저장은 모두 유지, 표시 경계에서만 필터:
- 디스크: `compute_disk_io`(스냅샷) + `to_storage_detail`(인벤토리 물리 디스크). I/O rate 차트는 물리 device 스코프 — 집계 계층(`server_disk_io_5m` cagg) 사전필터.
- 인터페이스: `compute_net_io`(스냅샷) + repo SQL `types._PHYS_IFACE_SQL_FILTER`(차트·집계).
- 마운트: `mappers/server.py`·`metrics_calculator.py`(파이썬 경계) + `types._DATA_VOLUME_SQL_FILTER`(raw 집계 SQL — `query/metric.py`)·`types._DATA_VOLUME_CAGG_FILTER`(cagg 조회 — `query/report.py`).

IP 필터 보류: `ip_internal`/`ip_external`은 평면 IP 목록만 발행돼(인터페이스 매핑 부재) 가상/물리 구분이 주소 형식만으론 불가 — docker 사설 IP와 물리 사설 IP가 같은 대역(192.168/10/172.16/fd00 ULA). 링크로컬(`fe80::/10`·`169.254/16`)·루프백 정도만 형식 필터 가능하나 이득이 작아, agent가 IP-인터페이스 매핑을 발행하기 전까지 IP는 필터하지 않는다 (인터페이스 IO 필터는 device 이름이 명확해 유지).

## Recommendation 분류 — USE Method 출처

도메인 모듈: `assessment_engine/recommendation.py` (web·diagnostic 양쪽 import). `WINDOW_DAYS=14` 평가 윈도우(#F10)·USE Method 임계값 모두 본 모듈 코드 단일 진실(모듈 상단 명명 상수).

UI badge 임계값(`mappers/shared.py` `_USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`)과는 별 도메인 — 시점 사용량 시각 신호 vs 통계 right-sizing 결정.

right-sizing 분류(5분류·판정 순서·합성 규칙·OS 분기·벤더 임계 출처)의 명세 단일 진실은 `docs/reference/right-sizing.md`, 운영자 임계 카탈로그는 `right_sizing_thresholds.html`. web 계층 책임은 소비만 (P2/P4):
- 분류 = `recommendation.rollup_host(stats) -> HostAssessment`(자원 5개 per-resource + 근본원인). 배지 = `classify_host`. report 진단(`_build_diagnosis`, host.resources 상태·trigger 파생)·권고(`under_prescription(host)`)·attention 자원 부족 카드(`to_capacity_warning_item`, 발화 원인 `active_causes`)가 host.resources triggers·os-aware helper 를 재사용해 한국어 표시로 변환한다(임계 재계산 금지, stats 생성은 `build_resource_stats` 공용). 네트워크 혼잡은 host under 아닌 별도 `network_congested` 플래그.
- `unmeasured` -> `is_partial`(=bool(unmeasured)) 을 ViewModel precompute, 템플릿이 "포화 수치 미관측" confidence 마커로 노출.

## 환경 개요 상단 요약 — environment_overview (`/`)

환경 개요(`/`)는 `EnvironmentOverview` 만 노출 — 환경 요약 KPI(OS 지원 4상태 포함)·주요 워크로드·자원 적정성 막대·자원 이용/포화 도넛·시스템 에러. 운영신호 3 카탈로그(`AttentionSignals` — 통신끊김/OS지원종료/에이전트재시작)는 독립 라이브 카드로 렌더되지 않고 보고서 경로에서만 소비된다 (#E9, view-models.md "환경 개요 상단 요약" 절). 라이브 운영 현황은 별도 `/environment/realtime`(`get_environment_realtime` — 서버별 순간 스냅샷, `realtime.js` 30초 fragment 폴링은 본 문서 "실시간 현황" 절). 평균 활용률·자원 적정성 현황은 `recommendation.WINDOW_DAYS`(14일, #F10) 윈도우 — 분류와 한 창 통일. 홈 카드 레이아웃은 `docs/explanation/products/dashboard.md` 단일 진실. 가변 윈도우·앵커로 적정성을 따로 보는 전용 페이지는 `/environment/assessment` (`get_environment_assessment(time_range, anchor)` — 개요 조립부 `_assemble_overview` 를 attention/trend 제외 경량 재사용, 서버별 표는 `build_action_targets` 로 전 서버·전 분류를 절단 없이 산출). 자원 부족 상세 표의 소유는 `/environment/assessment`.

| 영역 | service 메서드 | repo SQL | 시간 축 | 분류 |
|------|----------------|----------|---------|------|
| environment_overview | `get_dashboard_overview()` | `list_server_ids` + `get_servers` + `environment_utilization(WINDOW_DAYS, end)` + `report_aggregate(WINDOW_DAYS)` + `fleet_error_summary` + Redis online mget | 14일 USE Method + 14일 평균 활용률 (capacity-weighted) | `_assemble_overview`: `build_resource_stats` -> `classify_host`(프로비저닝 도넛) + `cpu/mem/disk_io_saturated`·`assess_network`(포화 4도넛) + `to_capacity_warning_item`(under_provisioned 상세, os-aware triggers) |
| get_attention_signals (보고서 교차참조) | `get_attention_signals` | gap `metric_gap_warnings(gap_min=5, recent_h=24)` + os_eol `report_aggregate` raws `resolve_os_eol` + agent `agent_restart_counts_recent(now-1h)` | 신호별 상이 | gap "한때 살아있다 끊김" / os_eol 무상 보안 패치가 끊긴 OS(Linux distro + Windows build) / agent restart_count >= `agent_restart_alert_threshold`(WebSettings, 기본 3) |

운영신호 카탈로그(`AttentionSignals`)는 위 3개 — public `get_attention_signals` 가 내부 `_assemble_attention` 으로 조립, 보고서 `attention_for_host` 로 소비. 중복 회피 분리 소유: capacity(under_provisioned)는 environment_overview, days_until_full 은 보고서 스토리지 컬럼.

설계 결정:
- `list_server_ids()`는 정수 PK만 fetch — `list_servers`(disks JSONB 등) 대비 페이로드 절감 (T8 패턴 동일 적용).
- partition pruning binding 통일: gap SQL의 `recent_hours`가 동적 binding (`(:recent_h * interval '1 hour')`) — service 인자와 SQL 결합을 SQL 본문 hardcode로 묵시화하지 않음 (#F3·#F9).
- ViewModel·mapper 카탈로그: `docs/reference/web/view-models.md` "환경 개요 상단 요약" 절.

## 환경 성능 추이 (live) — `metric_trend` 풀세트

`/environment/metrics` (환경 단위 `/environment` 그룹). 전체 환경 대상 8차트 live(시계열, 4자원 카드 CPU2·메모리2·스토리지2·네트워크2). `?ids=public_ids` 면 선택 N대 한정(목록 selection 버튼 -> navigate, 제목 "선택 N대 성능 추이"). 실시간 현황(모니터링)은 `/environment/realtime` 로 분리 — 시계열 추이와 별개 용도.

| 영역 | service/repo | 비고 |
|------|--------------|------|
| 8차트 | `get_environment_metric_chart(server_ids=None)` -> `metric_trend(server_ids?, collapse=True)` (`EnvironmentMetricType` 9종 부분집합, 전체 `MetricType` 30종 중 — `db/repositories.md`) | live fetch `GET /api/servers/environment/metrics-chart`(`ids` 면 N대 resolve), range 토글(기본 15m). 발행/스냅샷 아님. 컨트롤(버킷/구간/앵커/적용)은 카드 밖 좌상단 단일 — 앵커는 '적용' 버튼으로 반영, 구간 select 는 즉시 |

서버 상세 성능 추이(`metrics.js`, 9차트 4자원 카드 CPU2·메모리3·스토리지2·네트워크2)와 동일 함수(`metric_trend`) — 환경은 `collapse=True`(dimension 합산 단일선), 상세는 `collapse=False, server_ids=[1대]`(device/iface/mount 보존, 단 스토리지 용량 추이만 전체+마운트별 둘 다 fetch). server_ids=[1대] 는 per_ts 합산 대상이 1서버라 시점값=그 서버값 -> 환경 선택 1대 = 서버 상세 동일. `data-selection-ids` 있으면 fetch 에 `ids` 전달. 서버 상세 성능 추이(`metrics.js`)·자원별 상세 탭(`cpu.js`/`memory.js`/`storage.js`/`network.js`)·환경 성능 추이 세 스코프가 신호 카탈로그(CPU 사용률+실행 큐, 메모리 사용률+구성+압박 여부, 스토리지 처리량+용량, 네트워크 I/O+이상 여부)와 레이아웃(`.perf-grid`/`.perf-item`, 화면 2열/인쇄도 2열 portrait 1페이지, `static-assets.md` "차트 컨트롤" 절) 단일 진실 공유 — CPU 분류(User/System/I·O Wait/Nice)·CPU/메모리/디스크 PSI·디스크 IOPS·await·네트워크 PPS·TCP 재전송율·패킷 드롭율은 세 스코프 모두에서 제외됐다(이기종 장치 합산은 비교 기준선 없어 해석 불가, PSI 는 Windows 미발행이라 "환경/서버 상세 모아보기" 단위에서 오인 소지, 존재 판정성 신호는 이진/카운트로 통일이 더 이해하기 쉬움). 환경 스코프만 존재하는 추가 차이: CPU 실행 큐·메모리 페이징 압박·디스크 I/O·네트워크 이상은 "판정 crossing 서버 수"(count, `cpu.saturation_hosts`/`mem.paging_pressure_hosts`/`disk.saturation_hosts`/`net.congested_hosts`)인 반면 서버 상세(개별 탭·`metrics.js` 공통)는 서버 1대 단일 시계열이라 실행 큐는 연속값 그대로(`cpu.run_queue`), 메모리 압박·네트워크 이상은 이진 0/1(`mem.paging_pressure`/`net.congested`) — 스코프에 맞는 표현 단위만 다르고 원자료·임계·판정 로직(recommendation.mem_pressure_active/assess_network)은 동일. 스토리지는 사용률(`fs.usage_percent`, capacity-weighted Σused/Σ(used+free)*100)로 표시 — 절대 총량(GB)은 서버마다 프로비저닝 용량이 제각각이라 위험도를 못 읽어 CPU·메모리와 같은 0~100% 척도로 통일(환경은 y축 100% 고정, 서버 상세는 마운트별 밴드 차이 흡수 위해 하단 0%만 고정하고 상단 자동). 페이지 하단은 `_reference_link.html` 푸터(내용은 `docs/reference/web/routers.md` `/reference` 행).

### 실시간 현황 (live) — `/environment/realtime`

`get_environment_realtime(server_ids=None)` -> `build_environment_realtime` (`servers/_environment_realtime.html` partial + `servers/realtime.html` 페이지 wrapper). 이용률 도넛 2(CPU·메모리 — capacity-weighted: CPU=sum(usage%·cores)/sum(cores), mem=sum(used)/sum(total), `environment_utilization` 과 동일 정의이며 단순 산술평균 아님. 디스크 용량(fill%)은 느린 누적 축이라 실시간 신호에서 제외, 디스크 I/O 이용률은 장치 종류별 신뢰도 편차라(SSD/NVMe 병렬 처리, `right-sizing-thresholds.md` "Disk IO" 절 Gregg 근거) 환경 평균 도넛으로 안 묶음) + 신호 도넛 4(실행 큐 임계·페이징·디스크 응답지연 임계·네트워크 혼잡 — 순간 단일신호 임계 초과 호스트 수/표본, dual-gate 포화와 다른 정의) + 서버별 실시간 부하 sortable-table(`RealtimeLoadRow` — CPU·메모리 이용률/실행 큐/페이징/디스크 이용률/디스크 응답지연/네트워크 7축을 호스트당 1행, top-N 절단 없이 전체 노출, 서버 목록과 동일 칼럼 클릭 정렬 + 20개 초과 더보기/접기 관례. 디스크 이용률(Utilization, `disk_io_util_pct`, 도넛 없이 표 전용 raw 값·판정 없음)·응답지연(Saturation, `disk_sat_index`)은 USE Method상 별개 축 — 둘 다 물리 disk only(`_PHYS_DISK_SQL_FILTER` fail-closed, Windows `aggregate:system` 같은 합성 pseudo-device 제외 — 안 그러면 진짜 물리 device 카운터 이상이 pseudo-device 값으로 은폐됨) + ops 델타 > 0 요구(await 와 동일 원칙, 연산 0건인데 io_time 만 증가하면 구세대 virtio phantom busy 카운터 오탐이라 미측정), 응답지연은 추가로 저활동 device(`RS_DISKIO_UTIL_MIN` 미만)면 미측정("—"). 페이징은 소수점 2자리 표시 의무(Linux 임계 "> 0"이라 정수 반올림하면 0.03/s 같은 실측이 "0"으로 묻혀 페이징 신호 도넛 카운트와 표 값이 안 맞아 보임). 네트워크는 처리량(kbps) 아닌 혼잡 판정(`net_signal_active`, 네트워크 혼잡 도넛과 동일 신호원 — 재전송·드롭·conntrack) 결과만 "정상"/"혼잡"(빨강 강조)로 표시 — 처리량은 판정 대상과 다른 원자료라 칼럼에서 제외). `realtime.js` 가 30초 주기 `?fragment=realtime` polling 후 `#rt-mount` swap(P3 정공, 정렬/더보기 클릭 위임은 안 바뀌는 mount 자체에 걸어 swap 후에도 유지). `?ids` 면 선택 N대.
