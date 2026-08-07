# Consumer

정책: CLAUDE.md #D. aio-pika 기반 순수 비동기 컨슈머, FastAPI와 독립 프로세스.

책임 축 — `schemas.py`(에이전트 메시지 파싱·검증 계약) / `mappers.py`(Pydantic 스키마 → Inbound DTO) / `handlers/`(routing key 별 처리 흐름 + `_common.py` 공용 helper — 멱등성·DB 재시도·시계 invariant·agent 재시작 추적) / `task_policy.py`(task.result 성공/실패 판정 — 소비자가 handlers 하나라 consumer 소속) / `main.py`(진입점·MQ 토폴로지 선언·Redis 생명주기).

---

## schemas.py — 메시지 파싱·검증

`model_validate_json(raw)`로 파싱·타입 검증 동시. Pydantic Input 모델은 `extra=ignore` 유지(CLAUDE.md #B 계약 진화 정책).

메시지 타입·공통 메타·필드 카탈로그·미사용/활용 필드·routing key별 스키마: `docs/reference/contracts/agent-data.md` 단일 진실.

판별자·닫힌 어휘 필드(`message_type`·`os_family`·`type`·`family`·`proto`)만 `Literal` 로 좁히고, 에이전트가 값을 늘릴 수 있는 축(`status`·`failure_reason`·`failed_component`)은 자유 문자열로 받는다 — 어느 축이 어느 쪽인지와 그 근거는 위 계약 문서. `default_factory=list` 사용 의무 (`default=[]`는 인스턴스 간 객체 공유 버그).

---

## handlers/ — 메시지 처리 흐름

공통 사이클:

```
파싱 (model_validate_json)
  → 실패: raise → nack(requeue=False) → DLX → DLQ

멱등성 체크 (Redis SET NX)
  → 중복: ack 후 조기 리턴

DB 저장 (지수 백오프 재시도, _db_retry)
  → 최종 실패: raise → nack → DLX → DLQ
```

저장 성공 시 routing key별 Redis 후처리 (키·TTL 은 `docs/reference/redis.md` "캐시 무효화 (cache-aside)" 절 단일 진실):

### inventory 후처리
online 마킹 + 인벤토리 캐시 무효화.

### metrics 후처리
online 마킹 + 메트릭 캐시 무효화 + `_track_agent_restart` 호출 (동작은 아래 "부가 시그널" 절).

### error 후처리
없음. 파싱 + 멱등성 + 로깅만 (재시도 컨텍스트 `retry_count`/`first_failed_at`/`recovered_at` 포함).

### task.result 후처리
1. 성공 보정 — `consumer/task_policy.py` 의 `effective_task_result` 가 status/failure_reason 을 보정한다. 정책 키·기본값은 `docs/reference/contracts/env.md` `TASK_INSTALL_SUCCESS_EXIT_CODES` 단일 진실. `exit_code` 는 raw 로 보존한다(감사용).
2. DB UPDATE — `complete_task(TaskResultUpdate)`. 저장 필드는 `db/dtos/inbound.py` `TaskResultUpdate` 단일 진실. 매칭 불가 task_id 는 silent ack — 미존재(운영자가 task 삭제)와 비 UUID(wire 계약상 free string 이나 `tasks.public_id` 는 uuid 컬럼) 둘 다 DLQ 부적합이다.
3. 보정이 일어난 경우 remapped INFO 로그를 남긴다.

`boot_time` / `agent_started_at` 은 본 메시지에서 항상 null 이라 `_log_time_invariants` 호출 생략.

### 미등록 서버 metrics — auto-register
metrics 핸들러는 `repo.ensure_server_id(agent_id, placeholder)`로 한 번에 처리 — find 실패 시 fallback placeholder 사용. `(server_id, auto_registered)` 튜플 반환으로 handler가 auto-register 시점만 운영 로그를 남김. 식별자는 `agent_id` 단일 키 (#C1) — agent 가 첫 실행 시 생성·영구저장한 불변 UUID 라 재부팅·MAC 재발급·machine_id 중복과 무관.

placeholder는 `mappers.build_placeholder_inventory`가 생성. metrics envelope 이 싣고 오는 값만 실값이고 나머지 정적 정보는 None/빈 배열이며, metrics 메시지에 hostname 필드가 없어 hostname 은 agent_id 로 채운다. 다음 진짜 inventory 도착 시 `agent_id` UNIQUE 를 타고 ON CONFLICT DO UPDATE 로 풀 정보를 덮는다.

metrics 저장 자체는 `repo.record_metrics(server_id, dto)`가 metric 7개 시계열 테이블 INSERT를 facade로 묶어 처리. 반환 `MetricInsertResult`의 각 행 수는 handler 로그에 노출되어 운영 관측 가능.

### 팩토리 함수 + 클로저

`make_*_handler`는 인자로 받은 의존성을 내부 처리 함수가 클로저로 캡처하는 팩토리다 — 클래스 없이 함수 수준에서 의존성을 바인딩한다.

파싱과 ack/nack 경계는 팩토리가 직접 열지 않고 `_common._in_message_context(model, label, handle)` 가 만든다 — 4 핸들러가 같은 블록을 복제하면 정제 문구나 컨텍스트 진입 하나가 어긋나도 드러나지 않는다. 팩토리는 파싱된 모델을 받는 처리 함수만 쓰고, 그 함수 안에서 모든 await 를 마친다(#F11). 핸들러 시그니처 별칭 `MessageHandler` 는 `handlers/_types.py` 단일 진실 — `handlers/__init__.py` 에 두면 4 핸들러 모듈과 순환이다.

### `message.process(requeue=False)`

aio-pika async context manager. 블록 정상 종료 → ack, 예외 발생 → nack(`requeue=False`) → DLX 라우팅.

### DB 재시도 정책

3회 시도, `2 ** attempt` full jitter 백오프. 1 실패 → [0,2s] sleep → 2, 2 실패 → [0,4s] sleep → 3, 3 실패 → 즉시 raise(sleep 없음). full jitter 는 동시 재연결 쏠림(thundering herd)을 막고, 최악 6초인 재시도 창은 큐 TTL 안에 들어가 단기 DB 장애 회복을 커버한다(값은 `docs/reference/rabbitmq.md` 큐 정책 표).

재시도 대상은 일시 장애다. `_is_retryable_db_failure` 가 예외 타입(`OperationalError`·`InterfaceError`)과 SQLSTATE 두 축 중 하나만 걸려도 재시도로 판정한다 — asyncpg dialect 가 커넥션 유실·서버 재기동·deadlock 을 base `DBAPIError` 로만 래핑해 타입 축 하나로는 갈리지 않기 때문이다. SQLSTATE 축은 class `08`(connection exception) 전 코드 + `40001` serialization_failure + `40P01` deadlock_detected + `57P01`·`57P02`·`57P03`(서버 종료·크래시 복구·기동 중). asyncpg 의 connect·command timeout 은 `DBAPIError` 가 아니라 `asyncio.TimeoutError` 로 올라오므로 별도 분기가 같은 백오프를 탄다. deadlock 은 동시 신규서버 insert 가 hypertable chunk 생성에서 경합해 발생하고, victim rollback 후 재시도가 흡수한다. `IntegrityError`·영구 `DBAPIError`(`ProgrammingError`/`DataError` 등)는 즉시 raise → nack → DLQ (F6).

---

## main.py — 진입점 및 MQ 토폴로지

### MQ 토폴로지

토폴로지 정의(vhost·exchange·DLX·큐·TTL·x-max-length·정책 근거)와 dev/prod 분기는 `docs/reference/rabbitmq.md` 단일 진실. 본 절은 consumer가 그 토폴로지를 코드로 어떻게 declare·subscribe하는지만 다룬다.

핵심 동작 요약:
- 기동 시 collect·task 두 계열의 exchange 와 각 DLX(`{exchange}.dlx`) 4개를 한 루프에서 declare → 큐별로 `{queue}.dead` 선언·DLX 바인딩 → 본 큐 선언(`x-dead-letter-exchange`/`x-dead-letter-routing-key` + 옵셔널 `x-message-ttl`/`x-max-length`) → exchange 바인딩 → consume 시작.
- prefetch_count 는 `channel.set_qos` 로 설정 (값·근거는 `docs/reference/rabbitmq.md` 정의 표).
- TTL/max-length 값은 `src/assessment_engine/consumer/main.py` 상단 명명 상수.

### aio-pika

RabbitMQ 전용 비동기 클라이언트(AMQP 0-9-1). 연결은 `connect_robust` 로 열어 끊김 시 자동 재연결하고, 재연결 중 채널·큐·컨슈머는 내부적으로 복구된다. declare·bind 는 각각 브로커 승인 프레임을 기다리는 왕복이고, `queue.consume` 이후에는 브로커가 메시지를 push 한다. `main()` 은 `stop_event` 를 기다리며 이벤트 루프를 유지한다.

---

## 설계 결정

### 멱등성: 2단 방어

정책: CLAUDE.md #D2. 자연키 UNIQUE 카탈로그: `docs/reference/db/models.md` "시계열 자연키 UNIQUE" 표. at-most-once 한계·outbox 대안: `docs/explanation/tradeoffs.md` T1.

### inventory 수신 시 online 즉시 마킹

upsert 성공 후 online 플래그를 즉시 SET 한다 (키·TTL 은 `docs/reference/redis.md` 단일 진실). 첫 메트릭 수신 전까지는 온라인 표시가 "등록됐다"는 의미에 가깝지만, inventory 를 발행한 에이전트가 직후 60초 안에 metrics 를 발행해 그 구간이 짧고 등록 즉시 피드백이 UX 상 낫다.

### 부가 시그널 — 운영 가시성

handler 본 처리 흐름과 별개로 두 가지 부가 시그널을 발행 (모두 fail-open · 처리 ack 영향 없음. 쓰는 Redis 키·TTL 은 `docs/reference/redis.md` "키 설계" 표):

1. `_log_time_invariants(redis, data)` — 모든 핸들러(inventory/metrics/error)에서 멱등성 체크 직후 호출.
   - `boot_time > agent_started_at` → systemd 시작 순서 또는 시계 동기화 비정상 (드뭄)
   - `agent_started_at > collected_at` → VM 시계 동기화 문제 (가장 흔함, VM resume 직후)
   위반 시 warning 로그만 — agent 별 쿨다운 키로 반복 억제 (#F7). DLQ 안 보냄 — 시계 문제는 데이터 reject 의미 없음.

2. `_track_agent_restart(redis, server_id, agent_id, agent_started_at)` — metrics 핸들러 후처리 끝에서 호출.
   - 직전 `agent_started_at` 과 비교 → 변경 시 슬라이딩 윈도우 카운터 INCR
   - `agent_restart_alert_threshold` 도달 시 warning (운영자가 crash loop 인지)
   - 시스템 재부팅도 같은 카운터 — 1h 내 3회 재부팅도 unusual이라 alert 적정
   - Redis 장애 시 silent skip (fail-open — 재시작 감지 1회 누락, 다음 sample 회복)

### Disposability — SIGTERM 흐름 (#F11)

종료 신호(SIGTERM/SIGINT)는 `loop.add_signal_handler` 로 받아 `stop_event` 를 set 한다. 이후 순서는 `_drain` 이 consumer 별 `queue.cancel(tag)`(basic.cancel)로 신규 배달을 끊고 → in-flight 핸들러가 ack/nack 를 마칠 때까지 기다린 뒤 → `async with conn, conn.channel()` unwind 로 채널·커넥션 close → `finally` 로 DB 엔진 dispose·Redis pool close. cancel 호출과 대기가 같은 예산 `_SHUTDOWN_DRAIN_SEC`(`main.py` 상단 명명 상수)을 나눠 쓰며, 예산은 compose 가 선언한 `stop_grace_period` 안에서 끝나야 SIGKILL 이 drain 을 자르지 않는다. 채널 close 가 진행 중 consumer task 에 CancelledError 를 던지므로 cancel·대기가 close 보다 앞선다.

진행 중 메시지는 `async with message.process(requeue=False)` 컨텍스트가 보장 — 다음 둘 중 하나:
- 정상 종료 → broker ACK → 메시지 사라짐
- 예외 raise → broker NACK + DLX 라우팅 → DLQ로 이동

메시지 손실 0 은 drain 이 예산 안에서 끝났을 때의 보장이다. 예산을 넘기면 `drain timeout inflight=N` warning 을 남기고 미완 메시지는 unack 로 남아 broker 가 재전송하는데, 커밋 직전에 잘린 메시지면 그 재전송이 멱등성 1단(#D2)에 중복으로 걸려 조용히 드롭될 수 있다 (`docs/explanation/tradeoffs.md` T1 범위). 신규 핸들러 추가 시 본 컨텍스트 안에서 모든 await 완료를 보장하면 됨 — `signal.signal()` 또는 `os._exit()` 같은 우회 호출 금지 (#F11).

---

## 운영 / 디버깅

```bash
docker compose logs -f consumer
docker compose exec rabbitmq rabbitmqctl -p assessment list_queues name messages_ready messages_unacknowledged
# DLQ peek — 비번은 dev 환경변수, prod file-secret 어느 채널이든 집는다.
docker compose exec rabbitmq sh -c 'rabbitmqadmin -u "$RABBITMQ_USER" \
  -p "${RABBITMQ_PASSWORD:-$(cat /run/secrets/rabbitmq_password)}" \
  -V assessment get queue=server.metrics.dead count=1 ackmode=ack_requeue_true'
docker compose restart consumer       # 의존성 변경 등 dev watchfiles 가 못 잡는 변경 후
```

| 증상 | 원인 |
|------|------|
| 메시지 처리 안 됨 | broker queue declare 실패 — 로그에 `consuming queue=...` 라인 확인 |
| 같은 메시지 반복 처리 | timeout nack 후 재전송 — `_db_retry` 가 DB call 3회를 백오프 [0,6s] 사이에 끼워 돌린다. 최악 벽시계는 backoff 가 아니라 `command_timeout`(30s) x 3 이 지배해 약 96초 |
| `<메시지 타입> parse error count=N <필드경로>=<오류종류>` 로그 | 검증 실패 — 4 핸들러가 원본 `ValidationError` 대신 필드 경로·오류 종류만 담은 `ValueError` 로 정제 후 nack → DLQ. 새 필드는 `extra=ignore` 로 통과하므로 실린 필드의 타입·제약 위반 또는 스키마가 계약보다 좁은 경우 |
| DLQ 누적 | 영구 오류. 위 DLQ peek 명령으로 확인 — 큐 이름은 `{queue}.dead` 실명 지정 (와일드카드 불가) |