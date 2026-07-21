# DB ORM 모델 카탈로그

정책: CLAUDE.md #C1. 본 문서는 ORM 모델·식별자 규약·시계열 자연키 UNIQUE·tasks 부분 UNIQUE 단일 진실. `src/assessment_engine/db/models/` — 11개 모델.

| 모델 | 테이블 | PK | 종류 | 설명 |
|------|--------|----|----|------|
| `ServerInventory` | `server_inventory` | Integer | 단일 행 | `agent_id` 단독 UNIQUE 기준 upsert. `composite_id`/`machine_id` 감사·표시 전용 |
| `ServerInventoryHistory` | `server_inventory_history` | BigInteger + collected_at | hypertable (append-only) | 인벤토리 변경 이력 (boot_time/agent_started_at 변경이 trigger) |
| `ServerMetrics` | `server_metrics` | BigInteger + collected_at | hypertable | 호스트 스칼라 메트릭 (CPU jiffies·Mem·run_queue·blocked·paging·conntrack). envelope 메타(boot_time·agent_started_at) 보유 |
| `ServerDiskIo` | `server_disk_io` | BigInteger + collected_at | hypertable | per device I/O 누적 카운터 (io bytes·ops·op_time·io_time) |
| `ServerNetIo` | `server_net_io` | BigInteger + collected_at | hypertable | per interface I/O 누적 카운터 (rx/tx bytes·packets·drops·errors) |
| `ServerFilesystem` | `server_filesystem` | BigInteger + collected_at | hypertable | per mount 시점 용량·inode (used/free/total·inode) |
| `ServerCpuCore` | `server_cpu_core` | BigInteger + collected_at | hypertable | per core CPU jiffies 카운터 (단일스레드 병목 감지) |
| `ServerPressure` | `server_pressure` | BigInteger + collected_at | hypertable | PSI 압박 (resource·scope별, Linux 4.20+) |
| `ServerDiskError` | `server_disk_error` | BigInteger + collected_at | hypertable | 디스크·스토리지 오류 카운터 (error_kind·error_class·member별) |
| `Task` | `tasks` | BigInteger | 단일 행 (audit log) | 원격 작업 명령 + 실행 이력 |
| `DiagnosticJob` | `diagnostic_jobs` | UUID | 일반 테이블 (hypertable 아님) | 보고서 발행 스냅샷·이력. `job_type` 으로 분류 (`customer_report`/`engineer_report`). active UNIQUE = `(scope, input_hash, job_type)`. UUID PK는 URL 노출용 (E5) |

## 식별자 규약 (CLAUDE.md C1)

- 대리키 패턴: 내부 참조는 정수 PK, 비즈니스 식별자는 unique 제약
- `server_inventory` 호스트 식별 = `agent_id` 단독 UNIQUE (`uq_server_inventory_agent_id`) — upsert 키. `agent_id` 는 agent 가 첫 실행 시 생성·영구저장한 불변 UUID 라 MAC/machine_id 재발급과 무관. `composite_id`(SHA-256(machine_id + 정렬·dedup MAC 들), nullable)·`machine_id`(raw machine-id, nullable) 는 clone collision 진단용 감사·표시 컬럼 — 식별·라우팅 미사용.
- `server_inventory.public_id` `UUID DEFAULT gen_random_uuid()` — URL 식별자 (정수 PK 노출 금지)
- 시계열 테이블 복합 PK `(id BIGINT, collected_at TIMESTAMPTZ)` — TimescaleDB 파티션 키 포함
- 시계열 metric 7테이블 + `server_inventory_history` 자연키 UNIQUE (D2 멱등성 2단 방어):

| 테이블 | UNIQUE |
|--------|--------|
| `server_metrics` | `(server_id, collected_at)` |
| `server_disk_io` | `(server_id, device_id, collected_at)` |
| `server_net_io` | `(server_id, iface_id, collected_at)` |
| `server_filesystem` | `(server_id, mountpoint, collected_at)` |
| `server_cpu_core` | `(server_id, core_id, collected_at)` |
| `server_pressure` | `(server_id, resource, scope, collected_at)` |
| `server_disk_error` | `(server_id, device_id, error_kind, error_class, member, collected_at)` — `member` NOT NULL('') 로 NULL 미포함 멱등키 |
| `server_inventory_history` | `(server_id, collected_at)` |

## envelope 메타 (boot_time — counter reset 정밀 식별)

`server_metrics` 만 `boot_time TIMESTAMPTZ NULL` + `agent_started_at TIMESTAMPTZ NULL` 컬럼 보유 (수집 1회당 1행 = envelope). 자식 시계열(disk_io·net_io·filesystem·cpu_core·pressure·disk_error)은 동일 `(server_id, collected_at)` 로 server_metrics 행을 참조 — 메타 N중복 회피.

- server_metrics 차트(`metric_trend`)는 `LAG(boot_time)` 두 시점 비교로 재부팅 식별 → reset 시 delta 건너뛰기.
- 자식 시계열은 boot_time 미보유 → rate 차트 reset 은 `GREATEST(delta, 0)` 로 흡수.
- 보고서 집계(`report_aggregate`·cagg)는 `counter_agg` 가 값-감소 기준 reset 을 일률 처리 → boot_time gate 불요.

## tasks 테이블 — 부분 UNIQUE (C1 + 운영자 더블클릭 방어)

```sql
CREATE UNIQUE INDEX uq_tasks_pending_per_server_type
  ON tasks (target_server_id, task_type)
  WHERE status = 'pending';
```

같은 server에 같은 task_type pending 1개만 허용 — 두 번째 INSERT는 `IntegrityError`. `TaskService`가 catch → `TaskDuplicatePending` → router HTTPException(409).

## server_inventory_history — 변경 trigger

`upsert_server`에서 직전 `server_inventory` 행과 비교 후 비교 대상 컬럼 중 하나라도 다르면 한 행 INSERT (앱 레벨 trigger). 비교 제외: `collected_at`·`last_seen_at`. 가장 빈번한 trigger 필드: `boot_time`(시스템 재부팅) / `agent_started_at`(에이전트 재시작) / `services` / `listen_ports`.

`ON CONFLICT DO NOTHING(server_id, collected_at)` — broker 재전송·동시 워커 race 시 중복 INSERT 흡수.

## 스키마 변경 운영

dev·staging·prod 모든 환경 Alembic 단일 진실.

- 모델 변경 시 (1) ORM 모델 (2) `alembic revision --autogenerate` (3) `alembic check` 통과 의무 — drift 0건 (#C4)
- TimescaleDB hypertable 신규 테이블은 마이그레이션에 `op.execute("SELECT create_hypertable(...)")` 수동 보강 (autogenerate 미지원)
- `migrate` 컨테이너가 모든 환경에서 `alembic upgrade head` 자동 적용 후 종료. 앱 서비스는 `depends_on: migrate (service_completed_successfully)`로 그 다음 기동
- 상세: `docs/guides/migrate.md`
