# DB ORM 모델 카탈로그

정책: CLAUDE.md #C1. 본 문서는 ORM 모델·식별자 규약·시계열 자연키 UNIQUE·tasks 부분 UNIQUE 단일 진실. `src/assessment_engine/db/models/` — 8개 모델.

| 모델 | 테이블 | PK | 종류 | 설명 |
|------|--------|----|----|------|
| `ServerInventory` | `server_inventory` | Integer | 단일 행 | `(machine_id, hostname)` 복합 키 기준 upsert. 현재 상태 |
| `ServerInventoryHistory` | `server_inventory_history` | BigInteger + collected_at | hypertable (append-only) | 인벤토리 변경 이력 (boot_time/agent_started_at 변경이 trigger) |
| `ServerMetrics` | `server_metrics` | BigInteger + collected_at | hypertable | 스칼라 메트릭 시계열 (CPU/Mem/Load) |
| `ServerDiskIo` | `server_disk_io` | BigInteger + collected_at | hypertable | per device I/O 누적 카운터 |
| `ServerNetIo` | `server_net_io` | BigInteger + collected_at | hypertable | per interface I/O 누적 카운터 |
| `ServerMountUsage` | `server_mount_usage` | BigInteger + collected_at | hypertable | per mount 시점 사용량 |
| `Task` | `tasks` | BigInteger | 단일 행 (audit log) | 원격 작업 명령 + 실행 이력 |
| `DiagnosticJob` | `diagnostic_jobs` | UUID | 일반 테이블 (hypertable 아님) | 진단 job + 보고서 발행 이력 통합 (ADR 0004 + 0010). `job_type` 으로 분류 (`ai_diagnostic`/`customer_report`/`engineer_report`). active UNIQUE = `(scope, input_hash, job_type)`. UUID PK는 URL 노출용 (E5) |

## 식별자 규약 (CLAUDE.md C1)

- 대리키 패턴: 내부 참조는 정수 PK, 비즈니스 식별자는 unique 제약
- `server_inventory` 호스트 식별 = `(machine_id, hostname)` 복합 UNIQUE (`uq_server_inventory_machine_hostname`) — upsert 키. `machine_id` 단독은 VM 템플릿 복제·이미지 clone·container host `/etc/machine-id` 마운트 등 실제 운영 환경에서 중복 가능 → hostname 과 함께 격리.
- `server_inventory.public_id` `UUID DEFAULT gen_random_uuid()` — URL 식별자 (정수 PK 노출 금지)
- 시계열 5개 테이블 복합 PK `(id BIGINT, collected_at TIMESTAMPTZ)` — TimescaleDB 파티션 키 포함
- 시계열 5개 테이블 자연키 UNIQUE (D2 멱등성 2단 방어):

| 테이블 | UNIQUE |
|--------|--------|
| `server_metrics` | `(server_id, collected_at)` |
| `server_disk_io` | `(server_id, device, collected_at)` |
| `server_net_io` | `(server_id, interface, collected_at)` |
| `server_mount_usage` | `(server_id, mount, collected_at)` |
| `server_inventory_history` | `(server_id, collected_at)` |

## 시계열 4테이블 공통 메타 (B1 counter reset 정밀 식별)

`server_metrics`·`server_disk_io`·`server_net_io`·`server_mount_usage` 모두 `boot_time TIMESTAMPTZ NULL` + `agent_started_at TIMESTAMPTZ NULL` 컬럼 보유.

- metrics·disk_io·net_io: `metrics_calculator._is_counter_reset` 두 시점 boot_time 비교 → reset 시 None
- mount_usage: 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 + 운영 디버깅(단일 테이블 SELECT로 재부팅 인지)
- 옛 데이터(NULL)는 d<0 휴리스틱 fallback

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

dev·staging·prod 모든 환경 Alembic 단일 진실 (ADR 0005).

- 모델 변경 시 (1) ORM 모델 (2) `alembic revision --autogenerate` (3) `alembic check` 통과 의무 — drift 0건 (#C4)
- TimescaleDB hypertable 신규 테이블은 마이그레이션에 `op.execute("SELECT create_hypertable(...)")` 수동 보강 (autogenerate 미지원)
- `migrate` 컨테이너가 모든 환경에서 `alembic upgrade head` 자동 적용 후 종료. 앱 서비스는 `depends_on: migrate (service_completed_successfully)`로 그 다음 기동
- 상세: `docs/operations/alembic.md` + ADR 0005
