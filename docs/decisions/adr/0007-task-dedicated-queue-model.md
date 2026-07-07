# ADR 0007 — Task 명령 전달 모델: 별도 큐 모델로 전환

상태: 채택 (2026-05-14) — Supersedes ADR 0002

## Context

ADR 0002 에서 RPC piggyback (`amq.rabbitmq.reply-to`) 을 채택했다 (latency 60s 허용 + 인프라 재활용). 그 후 발행 측 (원격 호스트) 이 다음과 같이 진화했다:

- collector 와 worker 가 같은 바이너리 안에서 분리된 프로세스 모델로 발전. worker 가 별도 AMQP 연결과 별도 큐로 install 명령을 수신.
- task 결과 보고가 단순 `status` / `result_message` 에서 `failure_reason` (10종 enum) / `exit_code` / `duration_ms` / `stdout_tail` / `stderr_tail` / `completed_at` 까지 확장됨.
- worker 컨텍스트는 collector 의 `boot_time` / `agent_started_at` 캐시와 분리되어, task.result 메시지는 두 필드를 `null` 로 발행.

엔진 측은 piggyback reply 발행만 하고 있어 발행 측 worker 가 명령을 받지 못하고 (수신 큐 자체가 없음), `_log_time_invariants` 가 nullable 메타에 부합하지 못해 모든 task.result 가 검증 실패로 DLQ 직행 위험. 즉 ADR 0002 의 가정 (옛 agent reply_to 없으면 다음 주기 재시도 가능) 이 깨졌다.

## Decision

ADR 0002 "즉시성 요구 발생 시 전환 경로" 의 옵션 C 로 전환. 단 본 전환의 트리거는 즉시성 요구가 아니라 데이터 형식 정합화. 결과적으로 옵션 C 의 인프라를 그대로 채택.

토폴로지:

| 항목 | 값 |
|------|-----|
| Task exchange | `assessment.tasks` (direct, durable) — collector exchange `assessment` 와 분리 |
| Task DLX | `assessment.tasks.dlx` (direct, durable) |
| 엔진 → 호스트 routing key | `task.install.<machine_id>` |
| 호스트 → 엔진 routing key | `task.result` |
| 머신별 수신 큐 | `agent.tasks.<machine_id>` (durable, `x-message-ttl=3600000`, `x-max-length=100`, `x-overflow=reject-publish`, DLX 없음) |
| 결과 보고 큐 | `worker.result` (durable, TTL 24h, max-length 100,000, DLX bound) |

큐 declare 책임:
- collector exchange / DLX / 4 큐 (server.inventory/metrics/error + worker.result): consumer 측 `consumer/main.py` lifespan 에서 declare.
- task exchange: web 측 `web/main.py` lifespan + consumer 양쪽에서 declare (idempotent).
- 머신별 큐 (`agent.tasks.<machine_id>`): 엔진 web 측 `TaskService._ensure_machine_queue` 가 task.install 발행 시점에 동적 declare. 발행 측 worker 는 큐 declare 권한 없음.

메시지 페이로드:
- task.install: `message_type` / `task_id` / `machine_id` / `issued_at` / `download.{url, sha256, size_bytes}` / `install.{script, args, timeout_sec}`. 발행 측이 sha256·size_bytes 를 받아 다운로드 후 검증.
- task.result: 공통 메타 (`boot_time` / `agent_started_at` 은 null) + `task_id` / `status` (`"success"` / `"failure"`) / `failure_reason` (10종 enum 또는 null) / `exit_code` / `duration_ms` / `stdout_tail` / `stderr_tail` / `completed_at`.

엔진 데이터 매핑:
- `Task` 모델에 6 컬럼 추가: `failure_reason` (VARCHAR(32)) / `exit_code` (SMALLINT) / `duration_ms` (BIGINT) / `stdout_tail` (TEXT) / `stderr_tail` (TEXT). 기존 `result_message` 컬럼 폐기.
- `status` enum: `"success"` / `"failure"` ("failed" 폐기). 기존 row 는 Alembic 데이터 마이그레이션으로 일괄 UPDATE.
- `completed_at` 의미 정정: 엔진 `func.now()` 대신 발행 측 보고 시각 (`data.completed_at`) 그대로 저장. row metadata 시각과 도메인 시각 분리.

## Consequences

### 긍정

- 데이터 형식이 발행 측 진화와 정합. task.install · task.result 양방향 모두 검증 통과 보장.
- 즉시성 확보 — collector metrics 주기와 무관하게 push. 운영자 클릭 후 ~ms latency.
- failure 분류 데이터 (`failure_reason` enum) 와 디버깅 데이터 (stdout/stderr tail) 가 영구 보존되어 통계·운영 가시성 증가.
- 권한 분리 모델 (prod least-privilege) 이 collector / worker 채널을 별도 user 로 자연스럽게 표현 가능 (`agent-publisher` / `agent-worker` / `engine-publisher` / `engine-consumer`).

### 부정·한계

- 머신별 큐 동적 declare 의무 — 큐 declare 권한이 엔진에 집중. 머신 수 N 만큼 큐 메타가 broker 에 누적. 머신 unregister 정책은 별도 ADR (현재는 broker TTL 1h + max-length 100 으로 자연 폐기 대신, 머신 영구 제거 시 큐 수동 삭제 의무).
- `agent.tasks.<machine_id>` 큐 DLX 미정. 본 ADR 단순화 단계 — TTL 만료 / max-length 초과 메시지는 drop. prod 운영자 가시성 요구 도달 시 별도 ADR 로 DLX 정책 보강.
- Redis `task:pending:{machine_id}` hot path 캐시 폐기. broker 가 메시지 보유 + DB `tasks` 가 source of truth. task 발행 시 운영자 더블클릭 방어는 부분 UNIQUE (`uq_tasks_pending_per_server_type`) 가 유지.
- web 의 install bundle endpoint (`/zconverter.tar.gz`) 의 sha256·size 가 단일 진실. 외부 mirror 사용 요구 도달 시 sha256·size 입력 경로 추가 ADR.

### 영향도 (CLAUDE.md F9)

- 코드: `consumer/schemas.py::TaskResultInput` + `consumer/handlers/task_result.py::make_task_result_handler` + `db/dtos/inbound.py::TaskResultUpdate` + `db/repositories/collect_repository.py::complete_task` + `db/models/task.py` + `web/services/task_service.py` + `web/routers/tasks.py` + `web/main.py` lifespan + `web/deps.py::get_task_service` + `consumer/main.py` + `config.py` (WebSettings: `install_timeout_sec`, ConsumerSettings: `rabbitmq_task_exchange` / `rabbitmq_task_queue_prefix` / `rabbitmq_task_install_key_prefix` / `rabbitmq_queue_worker_result`).
- 마이그레이션: `migrations/versions/e3a5b7c9d1f2_task_result_schema_alignment.py` — 6 컬럼 추가 + `result_message` DROP + `status` 값 정정.
- 인프라: `dev/agent.env` + `dev/agent.env.example` + `scripts/pipeline-up.sh` REQUIRED_AGENT_KEYS 및 heredoc.
- 문서: `docs/reference/contracts/agent-data.md` (메시지 데이터 형식) + `docs/reference/rabbitmq.md` (토폴로지 표 + 권한 모델) + `docs/decisions/adr/0002-*.md` (Superseded 표기) + 본 ADR.

## 관련 문서

- `docs/reference/contracts/agent-data.md` — 메시지 데이터 형식 단일 진실
- `docs/reference/rabbitmq.md` — 토폴로지 / 큐 정책 / dev·prod 분기
- ADR 0002 — 본 ADR 이 superseded 함
- CLAUDE.md #B (메시지 계약) + #F9 (영향도)
