# Web 서비스 계층 모듈

정책: AGENTS.md #E3 · #E7 (도메인 분류) · #F10 (평가 윈도우). 본 문서는 service 모듈 카탈로그·서비스 분류·Recommendation·환경 개요 상단 요약 단일 진실.

| 모듈 | 책임 |
|------|------|
| `query/` 패키지 (`service.py` 결합 + 도메인 mixin) | Redis 캐시 + repository 오케스트레이션. SSR/JSON 양 경로에 일관된 ViewModel·Summary 반환. `QueryService` 는 도메인 mixin(server·metric·attention·environment·report·task)을 결합해 repo 계층 `db/repositories/query/` 와 동형이고, 공유 helper 는 `query/_base.py` |
| `task_service.py` | Task 발행 (DB INSERT + Redis SET). 트랜잭션 경계 + `IntegrityError` -> `TaskDuplicatePendingError` 변환. `HttpZdmPackageResolver` (ZDM 패키지 sha256·size 동적 조회)가 install 발행 의존성 |
| `mappers/` (sub-package) | Outbound DTO + Detail -> ViewModel 변환 단일 진실 (P2). 모듈 목록은 아래, ViewModel 별 파생 필드는 `docs/reference/web/view-models.md` |
| `diagnostic_service.py` | 보고서 발행 enqueue(비동기 parent job) + 동기 저장(워커 child 경로) + job 상태 전이(claim/finish/recover) + 발행 이력. `DiagnosticRepository` protocol 만 의존 |
| `report/generator.py` | pending job -> 보고서 ViewModel 생성 디스패치 (워커·발행 경로 공유 단일 진실). 보고서 교차참조 helper `attention_for_host`/`attention_by_host` 정의 |
| `report/result.py` | `diagnostic_jobs.result` JSONB 구조·키 상수 + 조립 helper. serializer 와 나눈 이유는 의존 방향 — 여기는 ViewModel 을 import 하지 않는다 |
| `report/serializer.py` | 발행 시점 정적 스냅샷 serde — ViewModel <-> `diagnostic_jobs.result` JSONB |
| `cache_serializer.py` | Redis serde — `ServerDetailResponse` / `MetricDashboard`. 역직렬화 후 `enrich_*` 재호출 (idempotent) |
| `serialization.py` | `cache_serializer`·`report/serializer` 공용 직렬화 계약 (datetime -> ISO, dataclass -> dict) |
| `unit_converter.py` | KB->GB / sectors->KB/s / usage_pct 단위 변환 |
| `device_filters.py` | block_device `type`·fstype·net_interface `kind` 기반 계층 술어 단일 진실 — 규약은 아래 "디바이스 필터 정책" 절 |
| `service_classifier.py` (도메인 `assessment_engine/domain/`, web·consumer 공용 — `right_sizing.py` 동급) | 서비스 -> 카테고리 + 포트 귀속 + 카테고리 집합 사전계산(`compute_service_categories`, ingest 가 `service_categories` 저장) — 규약은 아래 "서비스 분류" 절 |

`mappers/` 모듈 중 파일명으로 담당이 드러나지 않는 것들 — `constants.py` = 공용 임계·색·라벨·정렬 순서 값 카탈로그, `assessment_display.py` = right_sizing 판정 -> 화면 형식화·API raw numeric·신뢰도 노트, `resource_stats.py` = `ReportRowRaw` -> 도메인 입력 어댑터(표시 파생 0), `os_eol.py` = endoflife 스냅샷 카탈로그 + 지원 단계 판정, `service_reference.py` = `/reference` 서비스 뱃지 범례, JSON API 응답 매퍼 3종(`api_reference.py`·`assessment_api.py`·`right_sizing_api.py`) = E6 타입계약 원천(분류·근거는 도메인 단일 진실 재사용).

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

모호한 경계는 못박아 둔다.

| 케이스 | 규칙 | 예 |
|--------|------|-----|
| 프록시 | 리버스·LB(inbound, 앱 앞단) = `web` / 포워드·egress = `infra` / 캐싱이 주목적 = `cache` | haproxy·traefik = web · squid = infra · varnish = cache |
| 시계열 | 범용 TSDB = `db` / 관측 전용 설계 = `monitor` | influxdb = db · prometheus·victoriametrics·loki = monitor |
| 검색엔진 | 저장이 본질이라 `db` (ELK 로그 용도여도) | elasticsearch = db · 오브젝트 스토리지 minio = file |
| 디렉토리 | 인증·디렉토리 인프라라 `infra` (계층 db 로 보지 않음) | slapd = infra |

카탈로그의 카테고리 정의 순서가 곧 분류 우선순위다 (cross-category 첫 매칭 우선). 순서 자체는 `SERVICE_CATALOG` 가 갖는다 — 위 표는 각 카테고리의 범위만 정하고 순서를 뜻하지 않는다.

### 단일 카탈로그 + 다중 신호 분류

`SERVICE_CATALOG`(`CategoryDef` 튜플)이 카테고리별 규약의 단일 진실 — `name_keywords`(unit·comm 공용 substring) / `port_names`(서비스명 -> well-known 포트) / `badge_class`. 분류 인덱스·드롭다운 목록·뱃지 CSS 맵은 모두 이 카탈로그에서 import 시점 1회 파생하므로 서비스 추가는 카탈로그 1곳만 수정한다.

`classify_service(unit, listen_ports=None, pid=None)` — 다중 신호, 정밀도 우선순위:
1. unit 이름 키워드 (최고 정밀 — 소프트웨어 정체성)
2. comm 키워드 (이름 미스매치 흡수 — Windows SCM 이름과 exe basename 불일치 등)
3. listen 포트 (프로토콜 사실관계 — 1433 -> db)

소프트웨어 정체성(name/comm)이 프로토콜(port)보다 카테고리 정밀도가 높아 이 순서다 — haproxy 가 5432 를 프록시해도 web 이다. `listen_ports` 미제공 호출(per-unit 상세 표시 등)은 name 신호만 쓰고, 목록 뱃지는 `classify_service` 를 직접 부르지 않고 ingest 사전계산 `service_categories` 를 소비한다.

comm/port 신호는 그 unit 에 귀속된 포트에만 적용한다 — 호스트 전체 포트로 unit 을 분류하면 multi-service 호스트에서 오분류가 난다. pid 가 없는 구간에 한해 `comm~name` substring -> name well-known 포트 순 fallback 이라, 그 구간에서는 이름이 comm 과 무관한 opaque 서비스를 per-unit 으로 못 잡는다 (T15).

#### 포트 귀속 규칙 (`matched_ports`) — 전 화면 공유 단일 규칙

- 포트에 소유 pid 가 있으면 그 pid 의 unit 에만 귀속한다. `.service`/`.socket` 이나 동일 comm unit 간 이중 귀속을 막는다 — 22 는 `ssh.service` 에만 붙고 `sshd-unix-local.socket` 에는 안 붙는다.
- pid 가 없는 포트(소켓 액티베이션·비-systemd)는 소유 프로세스가 없으므로 unit 카테고리(`classify_service`)와 같은 카테고리의 포트만 폴백 귀속하고, 카테고리 없는(unknown) unit 에는 붙이지 않는다 — 68 DHCP 등 OS 내부 포트 노이즈 제거.
- 동일 포트라도 proto(tcp/tcp6/udp)가 다르면 별도 항목이다.

### 호스트 워크로드 union

per-unit 분류가 못 잡는 구간을 listen 소켓 직접 분류로 보완한다. 보완 범위와 잔존 한계는 `docs/explanation/tradeoffs.md` T15.

- `detect_listen_categories(listen_ports)` — listen 소켓을 services unit 과 무관하게 직접 분류(comm 키워드 우선, 없으면 port). listen 소켓의 comm(exe basename)·port 는 지저분한 service 이름과 달리 OS·인스턴스명·로케일에 무관한 안정 식별자라, "이 호스트가 무슨 워크로드를 listen 하나"를 직접 탐지한다.
- `workload_category_counter(services, listen_ports)` — services 이름 분류(인스턴스 카운트)와 listen 탐지의 합집합(이름이 못 잡은 카테고리만 +1, 이중 카운트 회피). role/뱃지/환경 분포 단일 진실.
- baseline 제외 — `is_baseline_service`/`is_baseline_socket` 로 OS 기본·관리 서비스(SSH·RDP·NTP·RPC + `systemd-` 자체 유닛)를 특징 워크로드 집계에서 뺀다. 거의 전 호스트에 있어 "이 서버가 무슨 서버인가"를 구분하지 못하고, 포트 문맥 classify 가 systemd 소켓을 엉뚱한 카테고리로 끌어들이는 노이즈도 차단한다. `remote`(SSH·RDP)는 전부 baseline 이라 환경 집계엔 항상 0(서버 상세 live classify 만 노출). `workload_category_counter`·`compute_service_categories`·`workload_services_by_category` 공통 기준.
- `workload_services_by_category(services, listen_ports)` — 위와 동일 기준(baseline·unknown 제외)으로 카테고리별 특징 서비스명 목록. 환경/N대 보고서 "서비스 구성" 카드 breakdown 이 total_count(카테고리 distinct 호스트)와 같은 소스를 써 정합하고, 포트로만 탐지돼 이름 미상인 호스트는 "(포트 탐지)"로 합산한다.
- 런타임 스택 카테고리(`CategoryDef.single_instance` — 현재 container)는 호스트당 1로 집계. docker+containerd, kubelet+containerd 처럼 1 런타임이 여러 서비스로 떠도 "container 2" 로 부풀리지 않는다 (카운터·목록 뱃지 category_count·detail 뱃지 모두 적용). web/db 등 일반 카테고리는 인스턴스 카운트 유지.
- 적용: server detail 뱃지(`enrich_server_detail`) · 환경 개요 주요 워크로드 도넛(`build_environment_overview` — 시그니처 카테고리만) · `infer_role`(export) · 보고서 mapper(`to_report_row_item`/`build_role_distribution`, `ReportRowRaw` 가 `listen_ports` 보유).
- detail 뱃지 포트 표시 = 카테고리 단위 집계 — 각 카테고리 뱃지에 (그 카테고리 unit 들에 귀속된 포트) + (그 카테고리의 listen 포트, 카테고리당 1회)를 합쳐 붙인다. unit 귀속이 실패하는 워크로드(IIS `W3SVC` <-> `System` 의 80/tcp·tcp6)도 카테고리 단위로 80 이 뱃지에 붙는다. listen-only 카테고리(services 이름이 못 잡은)는 unit 없는 합성 `ServiceItem`. 뱃지에 귀속된 포트는 "주요 Listen 포트"(`key_listen_ports`)에서 제외하고, 카테고리 없는 OS 인프라 포트(svchost RPC/SMB/NTP 등)만 거기 남는다.
- 목록 행 뱃지: ingest 사전계산 `service_categories`(text[]) 소비 — `ServerSummary` 가 services JSONB·listen_ports 재로드 없이(경량 partial SELECT, #C2/E2) 상세·환경요약·보고서와 동일 카테고리 집합. 이름·comm·포트 어느 신호로 식별되든 일치(화면 간 비대칭 0, 행별 재분류 0).

`infer_role(services, listen_ports=None)` = `workload_category_counter` 최빈 카테고리.

## 디바이스 계층 — block_devices[] 평면 DAG (parent-by-id)

노드 정의와 parent 체인 조인 규약은 `docs/reference/contracts/agent-data.md` 가 갖는다. web 의 소비 규칙은 하나 — 정적 토폴로지(무엇이 존재)와 동적 사용량(server_filesystem, 얼마나 찼나)을 분리해 모든 소비처(용량·상세·export·토폴로지)가 같은 술어를 공유한다. 다중 부모(RAID span·striped VG)를 디스크별 그룹으로 펴는 것은 렌더 결정이고 `view-models.md` StorageNode 가 소유한다.

### 측정 원칙 — 무엇을 어느 계층에서 재나

Windows·Linux 통일이고 모든 화면이 이 귀속을 따른다. 소비처가 계층을 바꿔 재는 것은 금지 — 같은 질문은 같은 계층에서 답한다.

| 축 | 계층 | 근거 |
|----|------|------|
| 배정 용량 · 레이아웃 루트 · 디바이스 특성(rotational·sector_size·serial) | 물리 디스크 | 마운트 안 된 공간 누락·이중계산 회피 |
| 파일시스템 용량 | 마운트된 데이터 볼륨 (Linux part/LV, Windows volume) | 실제로 쓸 수 있는 공간이 마운트 단위 |
| 사용량 (bytes·inode 2축) | 파일시스템(마운트) | fullness 는 파일시스템 속성이라 raw 디스크는 채우는 대상이 아니고, inode 고갈은 bytes 가 여유해도 쓰기가 실패하므로 별도 축 |
| I/O (IOPS·처리량·await 포화) | 물리 디스크 | LV/파티션 통과분 이중집계 회피 |
| 확장 여력 | `lvm_vgs.free_bytes`(VG 미할당) + 물리 디스크 미파티션 갭(배정 - 자식 합) | 두 원천이 서로 다른 공간이라 둘 다 세야 "디스크를 더 안 붙이고 늘릴 수 있는 양"이 나옴 |

## 디바이스 필터 정책 — block_device `type` · fstype · net `kind`

디스크·마운트·인터페이스의 물리/논리/데이터/가상 판정은 계층별 원본 필드로 한다 (payload 계약 #B). 엔진은 이름 정규식·major 추론 없이 필드로만 판정하고, 술어 본문이 한 줄짜리 함수라 `device_filters` 가 곧 정의다. 판정에 필요한 규약만 여기 남긴다 — `fstype` 이 None(미상)이면 데이터 볼륨으로 포함하고(df 관례), 인터페이스는 물리 NIC 와 bond_master 만 통과시킨다(bond_member 이중 집계 회피). 계층 축이 갈리는 것도 계약이다 — block_device 는 `type`, net_interface 는 `kind`. 같은 경계의 SQL 등가는 `db/repositories/query/types.py` 가 갖고 동기화 의무는 `docs/reference/db/repositories.md`.

적용 경계 — 저장은 모두 유지, 표시 경계에서만 필터:
- 디스크: `compute_disk_io`(스냅샷) + `to_storage_detail`(인벤토리 물리 디스크). I/O rate 차트는 물리 device 스코프 — 집계 계층(`server_disk_io_5m` cagg) 사전필터.
- 인터페이스: `compute_net_io`(스냅샷). 차트·집계의 물리 인터페이스 선별은 표시 계층이 아니라 repo SQL 이 한다.
- 마운트: `mappers/server.py`·`metric_dashboard.py` 가 파이썬 경계, 나머지는 repo SQL.

IP 는 필터하지 않는다. `ip_internal`/`ip_external` 이 인터페이스 매핑 없는 평면 목록이라 docker 사설 IP 와 물리 사설 IP 가 같은 대역(192.168/10/172.16/fd00 ULA)이고, 주소 형식만으로는 가상/물리를 가를 수 없다. agent 가 IP-인터페이스 매핑을 발행하면 재검토한다.

## Recommendation 분류 — USE Method 출처

도메인 모듈: `assessment_engine/domain/right_sizing.py` (web·diagnostic 양쪽 import). `WINDOW_DAYS` 평가 윈도우(#F10)·USE Method 임계값 모두 본 모듈 코드 단일 진실.

분류(5분류·판정 순서·합성 규칙·OS 분기) 명세는 `docs/reference/right-sizing.md`, 임계 수치·벤더 출처는 `docs/reference/right-sizing-thresholds.md`, 운영자 카탈로그는 `/reference` 임계값 페이지가 갖는다. web 계층 책임은 소비만 (P2/P4):
- 표시 계층은 임계를 다시 계산하지 않는다. 분류 결과의 triggers 와 os-aware helper 를 그대로 받아 한국어 라벨로만 바꾼다.
- `unmeasured` -> `is_partial`(=bool(unmeasured)) 을 ViewModel precompute, 템플릿이 "포화 수치 미관측" confidence 마커로 노출.

## 환경 개요 상단 요약 — environment_overview (`/`)

환경 개요(`/`)가 노출하는 ViewModel 과 화면 축은 `docs/reference/web/view-models.md` 가 갖는다. 라이브 운영 현황은 별도 `/environment/realtime` 이다. 홈 카드 레이아웃은 `docs/explanation/products/dashboard.md`.

가변 윈도우·앵커로 적정성을 따로 보는 전용 페이지는 `/environment/assessment`(`get_environment_assessment(time_range, anchor)`) — 개요 조립부 `assemble_overview` 를 attention/trend 제외 경량 재사용하고, 서버별 표는 `build_action_targets` 로 전 서버·전 분류를 절단 없이 산출한다. 자원 부족 상세 표의 소유는 이 페이지다.

| 영역 | service 메서드 | repo SQL | 시간 축 | 분류 |
|------|----------------|----------|---------|------|
| environment_overview | `get_dashboard_overview()` | `list_server_ids` + `get_servers` + `get_environment_utilization(WINDOW_DAYS, end)` + `get_report_aggregate(WINDOW_DAYS)` + `get_fleet_error_summary` + Redis online mget | 창 USE Method + 창 평균 활용률 (capacity-weighted) | `assemble_overview`: `build_resource_stats` -> `rollup_host().recommendation`(프로비저닝 도넛) + `is_cpu_saturated`·`is_memory_saturated`·`is_disk_io_saturated`·`assess_network`(포화 4도넛) + `to_capacity_warning_item`(under_provisioned 상세, os-aware triggers) |
| get_attention_signals (보고서 교차참조) | `get_attention_signals` | gap `get_metric_gap_warnings` + os_eol `get_report_aggregate` raws `resolve_os_eol` + agent `get_agent_restart_counts_recent` | 신호별 상이 | gap "한때 살아있다 끊김" / os_eol 무상 보안 패치가 끊긴 OS(Linux distro + Windows build) / agent restart_count >= `agent_restart_alert_threshold`(WebSettings) |

운영신호 카탈로그(`AttentionSignals`)는 위 3개 — public `get_attention_signals` 가 내부 `_assemble_attention` 으로 조립, 보고서 `attention_for_host` 로 소비. 중복 회피 분리 소유: capacity(under_provisioned)는 environment_overview, days_until_full 은 보고서 스토리지 컬럼.

설계 결정:
- `list_server_ids()`는 정수 PK만 fetch — `list_servers`(disks JSONB 등) 대비 페이로드 절감 (T8 패턴 동일 적용).
- partition pruning binding 통일: gap SQL의 `recent_hours`가 동적 binding — service 인자와 SQL 결합을 SQL 본문 hardcode로 묵시화하지 않음 (#F3·#F9).
- 포화 도넛은 분자와 분모의 원천이 다르다 — 분자(cpu/mem/disk/net 발화 호스트 수)는 `get_report_aggregate` 결과(cagg 버킷)를 순회해 세고, 분모(`util.sample_size`)는 raw 테이블 기준이다. raw 보존 기간이 `WINDOW_DAYS` 보다 짧은 구성에서는 분자가 분모를 넘어 비율이 100% 를 초과할 수 있어 `assemble_overview` 가 분모를 최대 분자 이상으로 클램프한다. 원천을 맞추기 전까지 유지해야 하는 방어이므로 제거 금지.
- ViewModel·mapper 카탈로그: `docs/reference/web/view-models.md` "환경 개요 상단 요약" 절.

## 환경 성능 추이 (live) — `get_metric_trend` 풀세트

`/environment/metrics` (환경 단위 `/environment` 그룹). 전체 환경 대상 8차트 live(시계열, 4자원 카드 CPU2·메모리2·스토리지2·네트워크2). `?ids=public_ids` 면 선택 N대 한정(목록 selection 버튼 -> navigate, 제목 "선택 N대 성능 추이"). 실시간 현황(모니터링)은 `/environment/realtime` 로 분리 — 시계열 추이와 별개 용도.

| 영역 | service/repo | 비고 |
|------|--------------|------|
| 8차트 | `get_environment_metric_chart(server_ids=None)` -> `get_metric_trend(server_ids?, collapse=True)` (`EnvironmentMetricType` 카탈로그) | live fetch `GET /api/servers/environment/metrics-chart`(`ids` 면 N대 resolve), range 토글(기본 15m). 발행/스냅샷 아님. 컨트롤(버킷/구간/앵커/적용)은 카드 밖 좌상단 단일 — 앵커는 '적용' 버튼으로 반영, 구간 select 는 즉시 |

서버 상세 성능 추이(`metrics.js`, 9차트 4자원 카드 CPU2·메모리3·스토리지2·네트워크2)와 동일 함수(`get_metric_trend`)를 쓴다 — 환경은 `collapse=True`(dimension 합산 단일선), 상세는 `collapse=False, server_ids=[1대]`(device/iface/mount 보존, 단 스토리지 용량 추이만 전체+마운트별 둘 다 fetch). `server_ids=[1대]` 는 per_ts 합산 대상이 1서버라 시점값=그 서버값이므로 환경 선택 1대와 서버 상세가 같은 값이 된다. `data-selection-ids` 가 있으면 fetch 에 `ids` 를 전달한다.

서버 상세 성능 추이(`metrics.js`)·자원별 상세 탭(`cpu.js`/`memory.js`/`storage.js`/`network.js`)·환경 성능 추이 세 스코프가 신호 카탈로그(CPU 사용률+실행 큐, 메모리 사용률+구성+압박 여부, 스토리지 처리량+용량, 네트워크 I/O+이상 여부)와 레이아웃(`.perf-grid`/`.perf-item`, 화면 2열·인쇄도 2열 portrait 1페이지, `static-assets.md` "차트 컨트롤" 절)을 공유한다. 어느 축을 넣고 뺐는지와 그 근거는 `docs/explanation/products/dashboard.md` 가 갖는다.

세 스코프의 표현 단위만 다르다. 환경은 CPU 고사용률과 나머지 신호의 서버 수(count, `cpu.high_utilization_hosts`/`mem.paging_pressure_hosts`/`disk.saturation_hosts`/`net.congested_hosts`)를 표시한다. 서버 상세는 단일 시계열이라 실행 큐가 연속값(`cpu.run_queue`), 메모리 압박과 네트워크 이상이 이진 0/1(`mem.paging_pressure`/`net.congested`)이다. 스토리지는 양 스코프 모두 사용률(`fs.usage_percent`, capacity-weighted)로 표시하고, y축만 환경이 100% 고정·서버 상세가 마운트별 밴드 차이를 흡수하려 하단 0%만 고정한다. 페이지 하단은 `_reference_link.html` 푸터.

### 실시간 현황 (live) — `/environment/realtime`

`get_environment_realtime(server_ids=None)` -> `build_environment_realtime` (`servers/_environment_realtime.html` partial + `servers/realtime.html` 페이지 wrapper). `?ids` 면 선택 N대. 화면 구성·축 선정 근거·polling 주기·갱신 표기는 `docs/explanation/products/dashboard.md` "실시간 현황" 절 단일 진실.

측정 의미론은 여기가 소유한다. 이용률 도넛은 capacity-weighted 라 단순 산술평균이 아니고 `get_environment_utilization` 과 같은 정의다 (CPU = sum(usage%·cores)/sum(cores), mem = sum(used)/sum(total)). 디스크 축 둘은 물리 disk 만 본다(`_PHYS_DISK_SQL_FILTER` fail-closed) — Windows `aggregate:system` 같은 합성 pseudo-device 를 섞으면 진짜 물리 device 의 카운터 이상이 그 값에 은폐된다. 둘 다 ops 델타 > 0 을 요구하는데, 연산 0건인데 io_time 만 증가하면 구세대 virtio 의 phantom busy 카운터라 미측정으로 둔다. 응답지연은 저활동 device(`DISKIO_UTIL_MIN` 미만)도 미측정이다.
