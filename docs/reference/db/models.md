# DB ORM 모델 카탈로그

정책: AGENTS.md #C1. 본 문서는 ORM 모델·식별자 규약·시계열 자연키 UNIQUE·tasks 부분 UNIQUE 단일 진실. 모델 소재는 `src/assessment_engine/db/models/`.

| 모델 | 테이블 | PK | 종류 | 설명 |
|------|--------|----|----|------|
| `ServerInventory` | `server_inventory` | Integer | 단일 행 | 등록 호스트 인벤토리. `agent_id` 기준 upsert |
| `ServerInventoryHistory` | `server_inventory_history` | BigInteger + collected_at | hypertable (append-only) | 인벤토리 변경 이력 |
| `ServerMetrics` | `server_metrics` | BigInteger + collected_at | hypertable | 호스트 집계 메트릭. envelope 메타(boot_time·agent_started_at) 보유 |
| `ServerDiskIo` | `server_disk_io` | BigInteger + collected_at | hypertable | per device I/O 누적 카운터 |
| `ServerNetIo` | `server_net_io` | BigInteger + collected_at | hypertable | per interface I/O 누적 카운터 |
| `ServerFilesystem` | `server_filesystem` | BigInteger + collected_at | hypertable | per mount 시점 용량·inode (gauge) |
| `ServerCpuCore` | `server_cpu_core` | BigInteger + collected_at | hypertable | per core CPU 시간 카운터 (단일스레드 병목 감지) |
| `ServerPressure` | `server_pressure` | BigInteger + collected_at | hypertable | PSI 압박 (resource·scope별, Linux 4.20+) |
| `ServerDiskError` | `server_disk_error` | BigInteger + collected_at | hypertable | 디스크·스토리지 오류 카운터 |
| `Task` | `tasks` | BigInteger | 단일 행 (audit log) | 원격 작업 명령 + 실행 이력 |
| `DiagnosticJob` | `diagnostic_jobs` | UUID | 일반 테이블 (hypertable 아님) | 보고서 발행 스냅샷·이력. UUID PK 자체가 URL 식별자 (#E4) |

## 식별자 규약

- 대리키 패턴: 내부 참조는 정수 PK, 비즈니스 식별자는 unique 제약
- `server_inventory` 호스트 식별 = `agent_id` 단독 UNIQUE (`uq_server_inventory_agent_id`) — upsert 키
- `composite_id`·`machine_id` (둘 다 nullable) — clone collision 진단용 감사 컬럼, 식별·라우팅 미사용. wire 값 정의는 `docs/reference/contracts/agent-data.md`
- `server_inventory.public_id` `UUID DEFAULT gen_random_uuid()` — URL 식별자
- 시계열 테이블 복합 PK `(id, collected_at)` — TimescaleDB 파티션 키 포함

## 시계열 자연키 UNIQUE

시계열 metric 7테이블 + `server_inventory_history` 가 자연키 UNIQUE 를 갖는다 — #D2 멱등성 2단 방어의 2단.

| 테이블 | UNIQUE |
|--------|--------|
| `server_metrics` | `(server_id, collected_at)` |
| `server_disk_io` | `(server_id, device_id, collected_at)` |
| `server_net_io` | `(server_id, iface_id, collected_at)` |
| `server_filesystem` | `(server_id, mountpoint, collected_at)` |
| `server_cpu_core` | `(server_id, core_id, collected_at)` |
| `server_pressure` | `(server_id, resource, scope, collected_at)` |
| `server_disk_error` | `(server_id, device_id, error_kind, error_class, member, collected_at)` — `error_class`·`member` NOT NULL('') 로 NULL 미포함 멱등키. wire 에 해당 attr 이 없는 point(Windows `{kind: eventlog}` 등)도 `''` 로 저장 |
| `server_inventory_history` | `(server_id, collected_at)` |

## envelope 메타 (boot_time — counter reset 정밀 식별)

`server_metrics` 만 `boot_time TIMESTAMPTZ NULL` + `agent_started_at TIMESTAMPTZ NULL` 컬럼을 보유한다 (수집 1회당 1행 = envelope). 자식 시계열(disk_io·net_io·filesystem·cpu_core·pressure·disk_error)은 동일 `(server_id, collected_at)` 로 server_metrics 행을 참조한다 — 메타 N중복 회피.

동일 부팅 판정 허용치는 `domain/boot_time.BOOT_TIME_JITTER_TOLERANCE`(5초) 단일 진실이다. 에이전트가 boot_time 을 `/proc/stat` btime 이 아니라 now - uptime 으로 산출해 매 수집마다 +/-1초 흔들리는 반면 실제 재부팅은 분 단위로 점프하므로, 그 사이인 5초에서 가른다. SQL 경로는 이 값에서 파생한 `db/repositories/query/types.BOOT_JITTER_SEC` 를 bound parameter 로 쓴다 — 허용치를 바꾸면 Python 판정과 SQL 술어가 함께 움직여야 한다.

- 실시간 스냅샷 delta 는 `boot_time.is_counter_reset` 이 두 시점 boot_time 을 비교해 reset 구간을 건너뛴다.
- 차트(`get_metric_trend`)는 boot_time 을 읽지 않는다 -> CPU 는 `d_total > 0 AND d_num >= 0`, 자식 시계열 rate 는 `GREATEST(delta, 0)` 로 흡수.
- 보고서 집계(`get_report_aggregate`·cagg)는 `counter_agg` 가 값-감소 기준 reset 을 일률 처리 -> boot_time gate 불요.
- 허용치를 쓰는 SQL 은 `get_reboot_events`(server_inventory_history) 의 재부팅 marker 판정뿐이다.

## tasks 테이블 — 부분 UNIQUE

```sql
CREATE UNIQUE INDEX uq_tasks_pending_per_server_type
  ON tasks (target_server_id, task_type)
  WHERE status = 'pending';
```

같은 server 에 같은 task_type pending 1개만 허용 — 두 번째 INSERT 는 `IntegrityError`. `TaskService` 가 catch -> `TaskDuplicatePendingError` -> router HTTPException(409).

## server_inventory_history — 변경 trigger

`upsert_server` 에서 직전 `server_inventory` 행과 비교해 비교 대상 컬럼 중 하나라도 다르면 한 행 INSERT (앱 레벨 trigger). 비교 제외: `collected_at`·`last_seen_at`·`agent_id`·`composite_id`·`machine_id`·`hostname`·`service_categories`(services 파생). `boot_time` 만 단순 부등호가 아니라 위 지터 허용치를 흡수하는 `boot_time_changed` 로 비교한다. 가장 빈번한 trigger 필드: `boot_time`(시스템 재부팅) / `agent_started_at`(에이전트 재시작) / `services` / `listen_ports`.

`ON CONFLICT DO NOTHING(server_id, collected_at)` — broker 재전송·동시 워커 race 시 중복 INSERT 흡수.

## 스키마 변경 운영

모델을 고친 뒤 밟을 절차와 `create_hypertable` 수동 보강 의무는 `docs/guides/migrate.md`.
