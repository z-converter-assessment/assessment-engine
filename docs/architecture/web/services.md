# Web 서비스 계층 모듈

정책: CLAUDE.md #E3 · #E7 (도메인 분류) · #F10 (평가 윈도우). 본 문서는 service 모듈 카탈로그·서비스 분류·Recommendation·대시보드 상단 요약 단일 진실.

| 모듈 | 책임 |
|------|------|
| `query_service.py` | Redis 캐시 + repository 오케스트레이션. SSR/JSON 양 경로에 일관된 ViewModel·Summary 반환 |
| `task_service.py` | Task 발행 (DB INSERT + Redis SET). 트랜잭션 경계 + `IntegrityError` -> `TaskDuplicatePending` 변환. 본 모듈 상단 `HttpZdmPackageResolver` (ZDM 패키지 sha256·size 동적 조회 — install 발행 의존성) |
| `diagnostic_service.py` | 진단 job 발행 (input_hash 계산·INSERT·RabbitMQ publish) + polling 조회 + SSR latest fetch. 추상 `BaseDiagnosticRepository` + `BaseQueryRepository`만 의존 (F4). 트랜잭션 경계 자체 관리 — router는 service만 호출. ADR 0004 + 0010 |
| `mappers/` (sub-package) | Outbound DTO + Detail -> ViewModel 변환 단일 진실 (P2). 11 sub-module — `server.py` / `metric.py` / `attention.py` / `report.py` / `export.py` / `task.py` / `shared.py` (공용 임계 상수 + ReportView Literal + `_DONUT_SEGMENT_DEFS` + `resolve_os_eol`/endoflife 카탈로그 ADR 0031) / `diagnostic.py` (진단 결과 표시 파생 — `to_view` / `to_panel_payload` / `to_history_item`) / `environment_report.py` (환경 보고서 합성) / `report_history.py` (보고서 이력 row) |
| `metrics_calculator.py` | CPU/Disk/Net delta + Mem/Swap 시점값 -> Snapshot. `_is_counter_reset` (boot_time 비교) |
| `cache_serializer.py` | Redis serde — `ServerDetailResponse` / `MetricDashboard`. 역직렬화 후 `enrich_*` 재호출 (idempotent) |
| `unit_converter.py` | KB->GB / sectors->KB/s / usage_pct 단위 변환 |
| `device_filters.py` | 디스크/인터페이스 블랙리스트 필터 (`is_physical_disk`·`is_virtual_disk`·`is_lvm_disk`·`is_partition`·`is_virtual_interface`) + 가상 마운트 필터 + `find_parent_disk` (mount-disk 조인) |
| `service_classifier.py` | 서비스 -> 카테고리 (`web`/`db`/`cache`/`mq`/`container`/`monitor`) + 포트 매핑. 단일 카탈로그(`SERVICE_CATALOG`) 파생 |

진단 deep dive(워커·스케줄러·LLM 토글·diagnostic_jobs): `docs/architecture/diagnostic.md`.

## 서비스 분류 — 3단계 표시 계층

| 단계 | 페이지 | 표시 |
|------|--------|------|
| 목록 | `/servers/` | 카테고리 badge만 (`web`/`db`/...) — 원본 unit 노출 안 함 |
| 상세 | `/servers/{id}` | unit 이름 + matched_ports + 카테고리 badge |
| services 탭 | `/servers/{id}/services` | unit 전체 + sub state + 포트 + 카테고리 |

### 단일 카탈로그 + 다중 신호 분류 (ADR 0032)

`SERVICE_CATALOG`(`CategoryDef` 튜플)이 카테고리별 규약의 단일 진실 — `name_keywords`(unit·comm 공용 substring) / `port_names`(서비스명 -> well-known 포트) / `badge_class`. 파생물은 import 시점 1회 계산: `_NAME_INDEX`(분류 키워드, 옛 `_PATTERNS`) · `_NAME_PORTS`(옛 `SERVICE_PORTS`) · `_PORT_INDEX`(port -> category) · `SERVICE_CATEGORIES`(드롭다운·범례) · `BADGE_CLASS_BY_CATEGORY`(templating filter import). 서비스 추가 = 카탈로그 1곳만 수정.

`classify(unit, listen_ports=None)` — 다중 신호, 정밀도 우선순위:
1. unit 이름 키워드 (최고 정밀 — 소프트웨어 정체성)
2. comm 키워드 (이름 미스매치 흡수 — Windows SCM 이름과 exe basename 불일치 등)
3. listen 포트 (프로토콜 사실관계 — 1433 -> db)

우선순위 근거: 소프트웨어 정체성(name/comm)이 프로토콜(port)보다 카테고리 정밀도가 높다 (haproxy가 5432를 프록시해도 web). port는 name/comm 무정보 시 fallback. `listen_ports` 미제공(목록 화면 경량 SELECT) 시 name 신호만 — 현행 동작 보존.

`classify` 의 comm/port 신호는 `_attributed_ports`(comm~name 또는 name well-known 포트로 unit에 귀속된 포트)에만 적용 — per-unit(services 탭) 표시용이라 multi-service 오분류 방지가 우선. 단 per-unit 분류만으론 이름이 comm과 무관한 opaque 서비스를 못 잡는다(agent 가 services 와 listen_ports 를 잇는 pid join key 미발행 — T15).

호스트 워크로드 union (agent 불변 전제의 최선, ADR 0032):
- `detect_listen_categories(listen_ports)` — listen 소켓을 services unit 과 무관하게 직접 분류(comm 키워드 우선, 없으면 port). listen 소켓의 comm(exe basename)·port 는 지저분한 service 이름과 달리 깨끗·안정(OS·인스턴스명·로케일 무관) 식별자라, "이 호스트가 무슨 워크로드를 listen 하나"를 직접 탐지. Windows opaque SCM 이름(`MSSQL$무엇`)을 1433/`sqlservr` 로 우회.
- `workload_category_counter(services, listen_ports)` — services 이름 분류(인스턴스 카운트) ∪ listen 탐지(이름이 못 잡은 카테고리만 +1, 이중 카운트 회피). role/뱃지/환경 분포 단일 진실.
- 런타임 스택 카테고리(`CategoryDef.single_instance` — 현재 container)는 호스트당 1로 집계. docker+containerd, kubelet+containerd 처럼 1 런타임이 여러 서비스로 떠도 "container 2" 로 부풀리지 않는다 (카운터·목록 뱃지 category_count·detail 뱃지 모두 적용, `SINGLE_INSTANCE_CATEGORIES`). web/db 등 일반 카테고리는 인스턴스 카운트 유지.
- 적용: server detail 뱃지(`enrich_server_detail`) · 환경요약 role 분포(`build_environment_overview`) · `infer_role`(export) · 보고서 mapper(`to_report_row_item`/`build_role_distribution` — `ReportRowRaw` 가 `listen_ports` 보유, 개별 보고서 구동 서비스 차등·role 보강). listen_ports 보유.
- detail 뱃지 포트 표시 = 카테고리 단위 집계 — 각 카테고리 뱃지에 (comm 으로 unit 에 귀속된 포트) + (그 카테고리의 listen 포트, 카테고리당 1회)를 합쳐 붙인다. comm 귀속이 실패하는 워크로드(IIS `W3SVC`<->`System` 의 80/tcp·tcp6)도 카테고리 단위로 80 이 뱃지에 붙음. listen-only 카테고리(services 이름이 못 잡은)는 unit 없는 합성 `ServiceItem`. 뱃지에 귀속된 포트는 "주요 Listen 포트"(`key_listen_ports`)에서 제외, 카테고리 없는 OS 인프라 포트(svchost RPC/SMB/NTP 등)만 거기 남는다. 표시는 캡슐 박스로 카테고리-포트 대응을 한 묶음으로 (detail.html).
- 미적용(name 신호만): 서버 목록 행 뱃지(`ServerSummary` 는 경량 partial SELECT 라 listen_ports 미보유, #C2/E2). 목록 행과 환경요약·보고서 카운트 간 약간의 비대칭은 의도 — `docs/tradeoffs.md` T15.
- 잔존 한계: listen 안 하거나 localhost-only 바인드 워크로드 + opaque 이름은 여전히 미상(union 두 소스 모두 못 잡음). 노이즈는 unknown 통일 노출(E9).

`infer_role(services, listen_ports=None)` = `workload_category_counter` 최빈 카테고리.

주의: 본 절 `classify`는 service_classifier 의 서비스 카테고리 분류. 아래 "Recommendation 분류"의 `recommendation.classify`(USE Method right-sizing)와 다른 함수 — 혼동 금지.

## mount - disk 매핑 (Linux 디바이스 식별 표준)

`device_filters.find_parent_disk(mount_major, mount_minor, disks)`:
- `mount.major == disk.major AND mount.minor == disk.minor` → 디스크 자체 마운트
- `disk.minor + 1..15` → 그 디스크의 partition (SCSI/virtio 관례)
- 가상 (major=0, tmpfs) → None

storage 페이지 mount → disk 매칭 + `_split_disks` (Inventory JSON Export의 `additional_disks.mount_hint`)에 활용.

## 디바이스 필터 정책 — 블랙리스트 (관측성 놓침 방지)

디스크·네트워크 인터페이스는 블랙리스트로 거른다 — 명백한 가상·시스템 디바이스만 제외하고 나머지는 통과. 화이트리스트(알려진 패턴만 허용)는 특이 물리 디바이스(`mpath`·`cciss`·새 NIC naming)를 조용히 놓치는 위험이 있어, 관측성에선 "놓침이 노이즈보다 치명적" 원칙으로 블랙리스트가 정석 (node_exporter `device-exclude` 등 de facto). 보안의 default-deny(화이트)와 반대 방향임에 주의 — 관측성은 "모르는 것도 일단 보여야" 안전.

- `is_physical_disk(name)` = `not (is_virtual_disk OR is_lvm_disk OR is_partition)`. 가상(`loop`/`ram`/`zram`/`fd`/`sr`/`nbd`)·논리(LVM/RAID `dm-`/`md`)·파티션 제외, 나머지(sd/vd/nvme/mmcblk/PhysicalDrive + 특이 컨트롤러) 통과. (과거 화이트리스트 `_PHYS_DISK_RE`에서 전환.)
- `is_virtual_interface(name)` = 보수적 1번 범위만 제외 — `lo`·터널(`sit`/`tunl`/`ip6tnl`/`gre`/`gretap`/`erspan`)·`veth`·`dummy`·`ifb`·`nlmon` + Windows NDIS 필터 드라이버(`-NNNN` suffix). `docker`/`br-`/`bond`/`vlan` 회색지대는 통과(컨테이너·본딩 호스트 정보 손실 방지).

적용 경계 — 저장은 모두 유지, 표시 경계에서만 필터:
- 디스크: `compute_disk_io`(스냅샷) + `to_storage_detail`(인벤토리 물리 디스크) + `query_service._filter_disk_category`(차트 `device_category=phys`).
- 인터페이스: `compute_net_io`(스냅샷) + `query_service.get_metric_chart`(차트 `_NET_METRIC_TYPES`).

IP 필터 보류: `ip_internal`/`ip_external`은 평면 IP 목록만 발행돼(인터페이스 매핑 부재) 가상/물리 구분이 주소 형식만으론 불가 — docker 사설 IP와 물리 사설 IP가 같은 대역(192.168/10/172.16/fd00 ULA). 링크로컬(`fe80::/10`·`169.254/16`)·루프백 정도만 형식 필터 가능하나 이득이 작아, agent가 IP-인터페이스 매핑을 발행하기 전까지 IP는 필터하지 않는다 (인터페이스 IO 필터는 device 이름이 명확해 유지).

## Recommendation 분류 — USE Method 출처

도메인 모듈: `assessment_engine/recommendation.py` (web·diagnostic 양쪽 import). `WINDOW_DAYS=7` 평가 윈도우(#F10)·USE Method 임계값 모두 본 모듈 코드 단일 진실(모듈 상단 명명 상수).

임계값 출처 주석 명시:
- AWS Compute Optimizer: idle (CPU peak <=1%) / over (CPU p95 <=30%, MEM p95 <=50%)
- Azure Advisor: shutdown (CPU p95 <=3%, NET <=2Mbps)
- GCP Recommender: headroom 30%
- Kleinrock 큐잉 이론(1975): under (CPU p95 >=70%)
- Linux page cache: under (MEM p95 >=80%)

UI badge 임계값(`mappers/shared.py` `_USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`)과는 별 도메인 — 시점 사용량 시각 신호 vs 통계 right-sizing 결정.

OS 분기 (원칙 P2/P4 — evidence 기반): right-sizing 분류 단일 진실은 `recommendation.assess(stats) -> Assessment(recommendation, triggers, unmeasured)`이고, `classify`는 분류 enum만 돌려주는 호환 wrapper다. assess는 자원(CPU/Mem/Disk)별로 가진 축을 신호로 모아 under(위험 신호 OR — 하나라도 hit 되면 발화, 누락 0)/over(cpu·mem 이 둘 다 다운사이즈 임계 이하일 때만 — 보수적)/optimal 로 단일 분류를 내고 hit 신호를 근거(triggers)로 동반한다("어떤 데이터로 이 분류"). swap은 Linux page-out(메모리 압박) 신호이나 Windows pagefile은 baseline이라 `recommendation.swap_saturation(os_family, swap_used)` helper가 Windows에서 swap 축을 제외한다. load(CPU run queue)·iowait가 미관측(값 None)이면 `unmeasured`에 기록되고 `is_partial`(=bool(unmeasured))이 confidence 단서가 된다 — 분류 자체는 utilization·capacity로 완결되어 항상 under/over/optimal 결론이 나며("이용률 기준 평가" 표기), cpu_p95·mem_p95가 산출되는 한 "데이터 부족"이 아니다. `insufficient_data`는 utilization(cpu·mem) 둘 다 부재 + under 신호도 없을 때만(신규/표본 부재 — swap 등 saturation 신호가 있으면 util 부재여도 under로 결론). report mapper의 권고(`_build_under_provisioned_reason`)·attention의 capacity 배지(`to_capacity_warning_item`)는 `assess.triggers`를 재사용해 임계 재계산 중복을 제거한다(stats 생성은 `build_resource_stats` 공용). os_family None(unknown)은 Linux로 취급. 분류 명세·근거(USE Method·벤더 임계 출처·한계) 단일 진실은 `docs/architecture/right-sizing.md`, 운영자 임계 카탈로그는 `right_sizing_thresholds.html`.

## 대시보드 상단 요약 — environment_overview + attention

`/servers/` 첫 페이지에서 두 영역으로 표시. environment_overview는 환경 현황·평균·분포(도넛), attention은 즉시 조치 신호 카드. 시간 축은 단일 윈도우(`recommendation.WINDOW_DAYS=7`, #F10). 대시보드는 3 카드섹션(환경 요약 / 환경 자원 평가=활용률+Right-sizing+언더프로비저닝 / 환경 부하 추이+네트워크 토폴로지)으로 분리.

| 시선 | service 메서드 | repo SQL | 시간 축 | 분류 |
|------|----------------|----------|---------|------|
| environment_overview | `get_environment_overview()` | `list_server_ids` + `get_servers` + `environment_utilization(WINDOW_DAYS, end)` + `report_aggregate(WINDOW_DAYS)` + Redis online mget | 7일 USE Method + 7일 평균 활용률 (capacity-weighted) | 자원 합계·역할 분포·활용률 도넛·프로비저닝 분포 도넛 + under_provisioned 호스트 (capacity — `to_capacity_warning_item`, trigger 5종 스왑·CPU·메모리·Load·디스크) |
| attention.gap_warnings | `get_attention_signals` | `metric_gap_warnings(gap_min=5, recent_h=24)` 단일 SQL | 5min~24h 갭 (단기) | "한때 살아있다 끊김" |
| attention.os_eol_warnings | `get_attention_signals` | `report_aggregate(WINDOW_DAYS)` raws + `resolve_os_eol`(endoflife.date 스냅샷, ADR 0031) | EOL 경과 한정 | 지원 종료 OS (Linux distro + Windows Server build) |
| attention.agent_unstable | `get_attention_signals` | `agent_restart_counts_recent(since=now-1h)` SQL (`server_inventory_history` `agent_started_at` DISTINCT-1) | 1h fixed 윈도우 (Redis sliding 대체) | restart_count >= `AGENT_RESTART_ALERT_THRESHOLD` |

운영신호 카드(`AttentionSignals`)는 위 3개뿐 — public `get_attention_signals` 가 내부 `_assemble_attention` 으로 조립. disk·capacity·days_until_full 은 운영신호에서 USE Method right-sizing 으로 이동(중복 회피): capacity(under_provisioned)는 environment_overview, disk capacity/IO 는 `recommendation.classify`, days_until_full 은 보고서 스토리지 컬럼.

설계 결정:
- `list_server_ids()`는 정수 PK만 fetch — `list_servers`(disks JSONB 등 11컬럼) 대비 페이로드 절감 (T8 패턴 동일 적용).
- partition pruning binding 통일: gap SQL의 `recent_hours`가 동적 binding (`(:recent_h * interval '1 hour')`) — service 인자와 SQL 결합을 SQL 본문 hardcode로 묵시화하지 않음 (#F3·#F9).
- 검색·온라인필터 사용 시 environment_overview·attention 자동 격리 — 라우터 `pages.py` 분기 (첫 페이지·검색 없음·필터 없음일 때만 노출).
- ViewModel·mapper 카탈로그: `docs/architecture/web/view-models.md` "대시보드 상단 요약" 절.

## 환경 성능 추이 (live) — `environment_metric_trend` 풀세트

`/servers/environment/metrics` (list_page_router 등록 — server_detail `/{server_id}/metrics`(UUID) 보다 먼저라 'environment' 가 UUID 422 로 안 가고 본 라우트로 잡힘). 전체 환경 대상 10차트 live(시계열). `?ids=public_ids` 면 선택 N대 한정(대시보드 selection 버튼 -> navigate, 제목 "선택 N대 성능 추이"). 실시간 메트릭(현황 모니터링)은 `/servers/environment/realtime` 로 분리 — 시계열 추이와 별개 용도.

| 영역 | service/repo | 비고 |
|------|--------------|------|
| 10차트 | `get_environment_metric_chart(server_ids=None)` -> `environment_metric_trend(server_ids?)` (풀세트 18 metric_type, 3그룹 집계 — `db/repositories.md`) | live fetch `GET /api/servers/environment/metrics-chart`(`ids` 면 N대 resolve), range 토글(기본 15m). 발행/스냅샷 아님. 컨트롤(버킷/구간/앵커/적용)은 카드 밖 좌상단 단일 — 앵커는 '적용' 버튼으로 반영, 구간 select 는 즉시 |

서버 상세 성능 추이(`metrics.js`)와 동일 차트 로직, fetch URL·server_id·device_category 차이만(`environment-metrics.js` — 환경은 dimension 없는 단일선이라 avg only; `data-selection-ids` 있으면 fetch 에 `ids` 전달). 5행2열을 단일 `.perf-merged` 카드로 통합(행=`.perf-row`, `static-assets.md`). 프린트는 base.html @media print 클래스 공용. 페이지 하단 `_reference_link.html`(수치 정의 참고자료 링크).

### 실시간 메트릭 (live 현황) — `/servers/environment/realtime`

`get_environment_realtime(server_ids=None)` -> `build_environment_realtime` (`servers/_environment_realtime.html` partial + `servers/realtime.html` 페이지 wrapper). 현재 평균 활용률 도넛(CPU/메모리/디스크 — capacity-weighted: CPU=Σ(usage%·cores)/Σcores, mem=Σused/Σtotal, disk=Σ(전 mount)used/total, `environment_utilization` 과 동일 정의이며 단순 산술평균 아님) + 자원별 부하 상위 탑5(CPU/메모리/디스크 worst mount/로드 코어대비/스왑 — 서버별 값). 30초 폴링(`realtime.js`)이 partial 만 fragment 교체(`?fragment=realtime`), 갱신 stamp 는 카드 밖 제목 줄(카드 안 `#env-realtime-ts-data` 를 JS 가 읽어 '...초마다 자동 갱신 · 최근 {시각}' 조립). `?ids` 면 선택 N대. 대시보드에서는 이 카드 제거(`get_dashboard_live` realtime 폐기).
