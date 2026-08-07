# Repository 계층

정책: CLAUDE.md #C2 · #F4. 인터페이스 규칙(Protocol·이름·상속 안 함)은 `docs/guides/conventions.md` 3절이 갖는다.

인터페이스는 셋이다 — `CollectRepository`(Consumer) / `QueryRepository`(Web) / `DiagnosticRepository`(보고서 발행·diagnostic_jobs 스냅샷). 구현 import 는 composition root 셋(`web/deps.py` · `consumer/main.py` · `worker/main.py`)만 한다.

## Collect 계층 — `CollectRepository` (Consumer)

| 메서드 | 설명 |
|--------|------|
| `find_server_id(agent_id) -> int \| None` | `agent_id` 단일 키로 server_id 조회 (#C1) |
| `upsert_server(data) -> int` | `agent_id` UNIQUE 기준 ON CONFLICT DO UPDATE. 변경 감지 시 history append |
| `ensure_server_id(agent_id, fallback) -> tuple[int, bool]` | find → 없으면 placeholder INSERT. metrics 핸들러 auto-register. 단일 키 (#C1) |
| `record_metrics(server_id, data) -> MetricInsertResult` | host 집계 + 자식 6개 = 시계열 7테이블 INSERT. 테이블별 행 수 반환 |
| `create_task(data) -> str` | tasks INSERT. public_id(UUID) 반환 |
| `complete_task(data) -> bool` | task.result handler — status / completed_at / failure_reason / exit_code / signal_no / task_policy / duration_ms / stdout_tail / stderr_tail UPDATE |
| `expire_overdue_tasks(server_ids) -> int` | deadline 경과 pending(install) 을 failure(timeout) 로 전이 — 발행 직전 호출 |
| `find_pending_deadline_servers(server_ids) -> list[int]` | deadline 안 지난 활성 pending 보유 server_id — 발행 all-or-nothing 사전 검증 |
| `expire_all_overdue_tasks() -> int` | server_ids 무필터 전역 timeout 전이 — worker reaper 루프 (F11) |

### 구현 디테일

- `upsert_server`: `pg_insert ... on_conflict_do_update`. values·set_ dict는 한 번 만들어 재사용 (컬럼 추가 시 한 곳만 수정). `agent_id` UNIQUE 키는 set_ 제외 (composite_id·machine_id 는 set_ 포함 — 최신 감사값 표시). `service_categories`(ingest 사전계산)도 set_ 포함. agent_id 가 부팅 무관 불변이라 별도 호스트 재연결 로직 없이 동일 agent_id 가 같은 행을 잡는다.
- `ensure_server_id`: `_insert_placeholder_server`는 `ON CONFLICT DO NOTHING` (placeholder가 진짜 inventory 덮어쓰는 race 방지)
- `record_metrics`: 7테이블 모두 `pg_insert(...).on_conflict_do_nothing(index_elements=...)` — 자연키 UNIQUE 가 충돌을 흡수한다 (#D2 2단 방어, 자연키 카탈로그는 `docs/reference/db/models.md` "시계열 자연키 UNIQUE" 표). 자식 6테이블의 `index_elements` 는 `_natural_key()` 가 모델 선언의 `UniqueConstraint` 에서 import 시점에 뽑는다. UNIQUE 가 둘 이상인 모델은 충돌 대상을 특정할 수 없어 기동 자체를 거부한다 — 자연키를 빠뜨린 모델이 첫 메트릭 메시지가 아니라 기동에서 드러난다.
- `create_task`: `IntegrityError` 가능 (부분 UNIQUE `uq_tasks_pending_per_server_type`) — service가 catch

## Query 계층 — `QueryRepository` (Web)

`QueryRepository` 는 server / metric / report / attention / task 5개 도메인 protocol 결합이고 자기 메서드는 0개다 — 새 메서드는 해당 도메인 protocol 에 추가한다. 패키지가 내보내는 것도 이 인터페이스 하나이고, 구현은 composition root 가 `query.repository_sql` 에서 직접 가져온다.

| 메서드 | 설명 |
|--------|------|
| `resolve_server_id(public_id)` | 단건 UUID → 정수 PK |
| `resolve_server_ids(public_ids)` | N건 batch — 단일 SQL (C5 N+1 회피) |
| `list_server_ids(limit=1000)` | 정수 PK만 fetch (페이로드 절감, T8 패턴) |
| `list_server_public_ids()` | 전 서버 public_id (id ASC) — 환경 단위 보고서 URL 합성 |
| `list_servers(page, limit, search)` | 목록 — 목록에 쓰는 컬럼만 명시 SELECT (큰 JSONB 제외) |
| `get_server(server_id)` / `get_servers(server_ids)` | 단건 / batch full row |
| `get_storage(server_id)` | inventory + mount_usage |
| `get_network(server_id)` | inventory IP + net_io |
| `get_collection_status(server_id)` | last_metric_at + last_inventory_at |
| `get_latest_metric_at()` | fleet 전체 최신 metric 수집 시각 — 상단 바 데이터 최신성 |
| `get_latest_dashboard(server_id)` | 대시보드 스냅샷 raw — `DashboardRaw` (필드 구성은 `docs/reference/db/dtos.md`) |
| `get_latest_saturation(server_ids, since)` | server별 실시간 포화 원자료 batch — `SaturationRaw` |
| `get_latest_errors(server_id, since)` | 단일 서버 창내 에러 카운트 — `ErrorFleetRaw` |
| `get_fleet_error_summary(server_ids, since)` / `get_fleet_error_hosts(server_ids, since)` | 함대 에러축 호스트 수 / 발화 server_id 집합 |
| `get_latest_link_speed(server_ids, since)` | iface별 최신 link speed — 인벤토리 speed 미보고 폴백 |
| `get_metric_snapshots(server_id, cursor, limit)` | 시계열 cursor pagination |
| `get_metric_chart(server_id, type, dim, range, bucket, agg, end)` | 차트 dispatcher (metric_type 카탈로그는 `types.py`) |
| `get_reboot_events(server_id, start, end)` | server_inventory_history boot_time/agent_started_at 변경 시점 |
| `get_report_aggregate(server_ids, period_days, end)` | USE Method 통계 (CPU p95/peak + MEM p95/peak + run_queue·blocked p95 + iowait/steal + await·conntrack) + 용량 임박 구동 마운트(`mount_runway` CTE — MIN runway 마운트 이름·runway·used%, 분류 단일 소스) — `server_metrics_5m`·`server_filesystem_5m`·`server_disk_io_5m`·`server_net_io_5m`·`server_cpu_core_5m` cagg |
| `get_report_uptime_stats(server_ids, period_days, end)` | 가동률 통계 — 창 안 boot_time 변경(재부팅) 횟수 |
| `get_report_agent_restart_stats(server_ids, period_days, end)` | 창 안 agent_started_at 변경(에이전트 재시작) 횟수 — 보고서 "시스템 안정성" 입력 |
| `get_agent_restart_counts_recent(server_ids, since)` | since 이후 server별 agent 재시작 횟수 — attention `agent_unstable` fixed 윈도우 |
| `get_report_disk_io_baseline` / `get_report_net_io_baseline` | I/O baseline (보고서 I/O baseline 표시 입력, `DiskIoBaselineRaw`/`NetIoBaselineRaw` 반환) — `server_disk_io_5m`/`server_net_io_5m` cagg counter_agg(reset 일률 처리, 물리 device 만 집계) |
| `get_report_mount_capacity_batch(server_ids, end)` | N대 마운트별 용량 사이징 입력 — assessment API 디스크 축(worst-mount 로 접지 않음) |
| `get_report_memory_breakdown(server_id, period_days, end)` | 개별 보고서 메모리 구성 (used/available/cached/buffers 전체 대비 %, 시점값 avg) |
| `get_report_cpu_breakdown(server_id, period_days, end)` | 개별 보고서 CPU 분류 (user/system/iowait, `server_metrics_5m` cagg counter_agg delta) |
| `get_report_memory_breakdown_batch` / `get_report_cpu_breakdown_batch` | 위 둘의 N대 배치판 (GROUP BY server_id) |
| `get_metric_gap_warnings(gap_minutes, recent_hours, limit)` | metric 발행 갭이 gap_minutes 초과 + 최근 recent_hours 안 발행이 있던 서버. limit=None 이면 전수 |
| `get_environment_utilization(period_days, end, server_ids?)` | 환경 평균 활용률 도넛 (capacity-weighted, sum(used)/sum(total)). server_ids 한정 시 선택 N대·단일(selection 보고서), None 이면 전체 환경 |
| `get_metric_trend(metric_type, start, end, bucket, server_ids?, agg, dimension, collapse)` | 통일 차트 시계열 — 환경·선택·서버상세 단일 진실. `bucket: BucketSize`(SQL interval·경계 timedelta 는 repo 내부 `_BUCKET_INFO` 파생, 캡슐화). metric_type 풀세트를 `_TREND_PAIRS` dispatch table 이 흡수 — 키 하나에 builder 함수 하나이고, 누락·중복은 import 시점 AssertionError (그룹별 시점값 산식은 아래). server_ids=None 전체·[1대]=서버상세 동치·[N]=선택. collapse=False 면 device/iface/mount dimension 보존(상세 멀티라인), True 면 합산 단일선(환경). agg=avg/max/p95 |

### 차트 집계 (`get_metric_trend`) — 그룹별 시점값 산식

집계 산식(시점값 -> 버킷 agg)·`server_ids`·`collapse` 의 의미는 `docs/reference/db/timescaledb.md` "통일 산식" 절. 아래 표는 그룹별 분자·분모와 필터만 담고, metric_type 카탈로그 자체는 `query/types.py` 의 `MetricType`/`EnvironmentMetricType` 이 단일 진실이다. 대시보드 부하 추이·환경 성능 추이·서버상세 차트·실시간 카드(최신 1점)·보고서 추이가 모두 본 함수(또는 동일 산식). CPU 분류·메모리 구성 등은 JS 가 별도 metric_type fetch 후 클라이언트 dimension 부여.

| 그룹 | metric_type | 시점값 산식 |
|------|-------------|-----------|
| capacity-weighted util | `cpu.usage/user/system/iowait/nice_percent`, `mem.usage/available/cached/buffers_percent`, `fs.usage_percent` | 시점별 sum(num)/sum(den) x 100. 자원 총량 가중(큰 서버 큰 비중). CPU=시간(s) counter LAG delta(`d_total > 0 AND d_num >= 0` 로 reset 흡수, `_CPU_NUMERATOR`), mem=시점값 바이트(`_ENV_SCALAR_WEIGHTED`), fs=mount bytes(collapse=True 가상 제외 합산 / False mount 보존) |
| 합산 rate | `disk.read/write_iops`, `disk.read/write_kbps`, `net.rx/tx_bytes_per_sec`, `net.rx/tx_packets_per_sec` | device/iface 별 LAG delta / dt. disk=물리 whole-disk 만(`_PHYS_DISK_SQL_FILTER` — fail-closed EXISTS, `server_inventory.block_devices` 조인해 `type='disk'` 인 항목과 `device_id` 매치되어야 통과. 매치는 `id_type:id` 우선, 실패 시 `name:name` 폴백(Windows agent 가 inventory 는 `id_type:id`, metrics 는 disk name 만 발행하는 스킴 불일치 흡수) — 파티션·LVM 이중계산 회피), net=집계 iface 만(`_PHYS_IFACE_SQL_FILTER` — 동일 EXISTS 패턴, `net_interfaces.kind in ('physical','bond_master')` — loopback·veth·터널·bond_member·bridge·vlan 제외, bond_master 는 본딩 집계 단위라 포함). collapse=False 면 device/iface 보존. dt<=0 은 제외하고 음수 delta 는 `GREATEST(delta, 0)` 로 0 클램프 (boot gate 없음) |
| 코어 정규화 | `cpu.run_queue` | 시점별 sum(실행큐) / sum(cpu_cores) (per_ts, server_inventory JOIN) -> 버킷 {agg}. 1.0=코어당 포화. os-aware 단일 `cpu_run_queue`(Linux procs_running / Windows Processor Queue), 환경·상세 공용, dimension=os_family(Linux/Windows 2선) |
| 응답 지연 (양 OS 단일선) | `disk.io_saturation` | 물리 device 별 delta(op_time)/delta(ops) = await(ms) 를 내고 시점마다 worst device MAX. io_time 사용률이 `DISKIO_UTIL_MIN` 미만인 유휴 device 는 제외(writeback 잔류 await 오탐 억제). 양 OS 통일, os 분기·dimension 없음. Windows 구세대 viostor 큐 폴백은 스냅샷 판정 전용(차트는 await) |
| 정체율 (PSI, Linux 전용) | `cpu.psi` · `mem.psi` · `disk.psi` | sum(delta(stall_time_s))/sum(delta(wall_time))*100 (server_pressure scope=some, resource cpu/memory/io 매핑, GREATEST reset 흡수). 단일선, Linux 4.20+ 만 행 존재 -> 미지원 OS 빈 결과 |
| 교차 테이블 rate | `net.retrans_percent` · `net.drop_percent` | retrans%=sum(delta(tcp_retrans))/sum(delta(tx_packets))*100 (server_metrics + server_net_io collected_at 조인), drop%=sum(delta(rx_dropped)+delta(tx_dropped))/sum(delta(rx)+tx_packets)*100 (분모가 rx+tx 라 retrans% 와 다름). GREATEST 로 reset 흡수, 분류 net_retrans·net_drop 과 동일 산식 |
| 포화 이진 (서버 상세) | `cpu.saturation` · `disk.saturation` · `net.congested` · `mem.paging_pressure` | 버킷 안에서 임계를 한 번이라도 넘었는지(`bool_or`)를 1.0/0.0 스텝으로. 임계는 `right_sizing` os-aware 상수를 bind — SQL 이 문턱을 새로 정의하지 않는다. 원 rate 를 그리면 OS 간 척도가 달라 비교가 안 되므로 판정 결과를 선으로 낸다 |
| 판정 crossing 호스트 수 (환경) | `cpu.saturation_hosts` · `mem.paging_pressure_hosts` · `disk.saturation_hosts` · `net.congested_hosts` | 위 이진 판정의 환경판 — 버킷 안 server 별 `bool_or(crossed)` 후 넘은 서버 수 count |
| gauge (Linux 전용) | `cpu.blocked` | D-state 블록 gauge 평균 — 실행 큐와 달리 코어 정규화 없이 원자값. dimension=os_family |

집계 필터 단일 진실(`db/repositories/query/types.py`): `_DATA_VOLUME_SQL_FILTER`(가상 fstype 제외 + `/boot%` 마운트 제외, raw 테이블용) · cagg 조회는 `fstype_any` 를 보는 `_DATA_VOLUME_CAGG_FILTER` · `_PHYS_DISK_SQL_FILTER`/`_PHYS_IFACE_SQL_FILTER`(fail-closed EXISTS 서브쿼리, 위 표) — 모두 agent kind/type 태그의 SQL 투영, `device_filters` 와 동기화. 모든 그룹 partition pruning(#C5) 하한 술어 의무 — delta 를 내는 그룹은 한 버킷 앞선 `collected_at >= :window_start`, gauge·판정 그룹은 `collected_at >= :start`.

#### 미측정 null 은 0 이 아니다 — COALESCE·guard 비대칭

Windows 는 Linux 가 세는 성분 일부(CPU 의 nice/iowait/irq/softirq/steal, 메모리의 cached/buffered)를 OS 개념 부재로 null 로 싣는다. null 을 어디서 0 으로 접고 어디서 행째 제외할지가 축마다 다르고, 이 비대칭이 capacity-weighted util 그룹의 규약이다.

- CPU 분모 `_CPU_TOTAL_EXPR` 는 8개 성분을 전부 COALESCE 한다. Postgres 는 `X + NULL` 을 NULL 로 전파하므로 한 성분만 null 이어도 raw 합이 null 이 되고, delta 가 null 이 돼 Windows CPU 추이 차트가 통째로 빈다. cagg 정의와 `compute_cpu` 도 같은 규칙을 따른다.
- CPU 분자 중 per-component(`cpu.user/system/iowait/nice_percent`)는 bare 로 둔다. COALESCE 하면 Windows 의 iowait 미측정이 "측정된 0%(여유)"로 읽힌다 — null 로 두어 그 시점이 자연 제외(N/A)되게 한다. `cpu.usage_percent` 분자는 idle 을 뺀 나머지 성분의 합이라 분모와 같은 규칙으로 COALESCE 한다.
- 메모리 `_ENV_SCALAR_WEIGHTED` 는 `(numerator, denominator, guard)` 3튜플이고 guard 가 분자 성분이 실측된 행만 집계에 넣는다(예: `mem_limit_bytes > 0 AND mem_cached_bytes IS NOT NULL`). guard 없이 `SUM` 하면 Windows 의 null 이 0 으로 삼켜져 "측정된 0%" 로 표시된다 — 미측정은 gap 으로 남아야 한다.

분모와 분자를 "일관성 있게" 맞추는 후속 수정은 Windows 차트를 비우거나 iowait 오탐을 만든다. 세 규칙은 각각 다른 이유로 서 있다.

`get_metric_trend(collapse=False)`(서버 상세 멀티라인) 의 범례 `dimension` 은 raw `id_type:id`(예: `mac:fa:16:3e:df:18:87`) 대신 `LEFT JOIN LATERAL` 로 `server_inventory.block_devices`/`net_interfaces` 조회한 사람이 읽는 `name`(예: `enp3s0`·`PhysicalDrive0`) 으로 치환(`COALESCE(dn.name, dim)`, 미매칭 시 raw 폴백) — Linux 는 id_type=mac 인터페이스가 흔해 MAC 원문 노출 시 가독성이 떨어짐. collapse=True(환경 합산)는 dimension 자체가 없어(단일선) 미적용.

### Task 조회 — `TaskQueryRepository`

운영자 가시성 전용 도메인 protocol (modal · timeline · 서버별 최신). 반환은 모두 `TaskRow`.

| 메서드 | 설명 |
|--------|------|
| `get_task_by_public_id(public_id)` | task_id(UUID) 단건 — API + modal 디버깅 |
| `list_recent_tasks(target_server_id, limit, cursor?)` | 한 서버의 task timeline — created_at 역순, cursor 기반 (E2) |
| `get_latest_tasks_by_servers(server_ids)` | 서버별 최근 task 1건 — `DISTINCT ON (target_server_id)`, 목록 행 표시 source |

## Diagnostic 계층 — `DiagnosticRepository` (보고서 발행 스냅샷)

`diagnostic_jobs` 테이블에 발행 시점 정적 스냅샷을 INSERT·조회 (#C1).

| 메서드 | 설명 |
|--------|------|
| `enqueue(job: DiagnosticJobCreate) -> str \| None` | active partial UNIQUE(scope·input_hash·job_type) 충돌 시 None (기존 job 그대로 반환) |
| `get_active_by_hash(scope, input_hash, job_type)` | 더블클릭 방어 lookup — 활성 job 1건 반환 |
| `get_by_id(job_id)` | `?job={id}` 스냅샷 단건 조회 |
| `claim_next_pending()` | pending job 1건 원자적 claim (`FOR UPDATE SKIP LOCKED` + running 마킹) — 워커 분산 |
| `mark_succeeded(job_id, result)` | running -> succeeded + result 저장 (워커 생성 완료 시) |
| `mark_failed(job_id, error_message)` | running -> failed + error_message (생성 불가·내부 오류, F8 sanitize 후) |
| `recover_stale_running(stale_seconds)` | started_at 초과 running -> pending 회수 (크래시 in-flight, 워커 기동 시) |
| `list_recent(days, scope?, server_public_ids?, job_type?, limit)` | 보고서 이력 페이지 — created_at DESC |
| `delete_retention(older_than_days)` | retention DELETE |

interval 표현은 `func.now() - timedelta(days=N)` 또는 `func.now() - timedelta(hours=N)` (SQLAlchemy idiomatic — Python timedelta가 PostgreSQL interval로 자동 변환·bind 파라미터 안전, C5 의무). f-string `text("interval '{N} days'")` 금지.

### 타입·윈도우 상수 (`db/repositories/query/types.py`)

`MetricType`·range·bucket·집계 함수는 본 모듈의 Literal 이 값을 갖는다. range 기본값은 right-sizing 평가 윈도우와 같아야 한다 — 한쪽만 바꾸면 분류 창과 갈린다 (동시 갱신 위치는 #F10).

(`DIAGNOSTIC_RANGE_LABEL_KR` time_range 한국어 표시 라벨은 표시 소속이라 `mappers/constants.py`.)

### `list_servers` 부분 SELECT 정책
`select(ServerInventory)` 풀 row 대신 목록에 쓰는 컬럼만 명시. `services`/`listen_ports`/`net_interfaces` JSONB 는 페이지당 N행 직렬화 비용이 크고 목록에서 안 쓴다. 트레이드오프: `docs/explanation/tradeoffs.md` T8. 정렬은 `hostname` ASC.

## `.returning()` 결과 수신

INSERT 결과 PK 는 `.returning()` 으로 받는다. `on_conflict_do_update`(upsert)는 항상 1행이라 `scalar_one()`, `on_conflict_do_nothing`(placeholder INSERT)은 충돌 시 0행이라 `scalar_one_or_none()` 이다 — 여기서 `scalar_one()` 을 쓰면 동시 등록 race 에서 예외가 난다.

## asyncpg 파라미터 주의사항

- named param `:dim` 뒤 `::text` 는 asyncpg 파싱 버그 — `CAST(:dim AS text)` 로 우회
- `ANY(:sids)` — 배열 파라미터. asyncpg 가 list/tuple 자동 변환
- TIMESTAMPTZ 비교는 tz-aware datetime 만 — naive datetime 전달 시 timezone 불일치 오류
