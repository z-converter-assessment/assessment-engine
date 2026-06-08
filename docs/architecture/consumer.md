# Consumer

정책: CLAUDE.md #D. aio-pika 기반 순수 비동기 컨슈머, FastAPI와 독립 프로세스.

```
src/assessment_engine/consumer/
├── schemas.py   — 에이전트 메시지 파싱·검증 계약 (Pydantic)
├── mappers.py   — Pydantic 스키마 → Inbound DTO 변환
├── handlers/    — routing key 별 메시지 처리 흐름 — inventory.py / metrics.py / task_result.py / error.py + _common.py (멱등성·DB 재시도·시계 invariant·agent 재시작 추적 helper)
└── main.py      — 진입점, MQ 토폴로지 선언, Redis 생명주기
```

---

## schemas.py — 메시지 파싱·검증

`model_validate_json(raw)`로 파싱·타입 검증 동시. Pydantic Input 모델은 `extra=ignore` 유지(CLAUDE.md #B 계약 진화 정책).

메시지 타입·공통 메타·필드 카탈로그·미사용/활용 필드·routing key별 스키마: `docs/architecture/agent.md` 단일 진실.

`Literal` 타입(routing key·status 등)으로 payload 무결성 이중 검증. `default_factory=list` 사용 의무 (`default=[]`는 인스턴스 간 객체 공유 버그).

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

저장 성공 시 routing key별 Redis 후처리:

### inventory 후처리
```
1. SET online:{server_id} 1 EX 90        — 등록 즉시 온라인 판정
2. DELETE cache:inventory:{server_id}    — 인벤토리 변경(서비스/포트/디스크) 즉시 반영. 300s TTL 만료 대기 제거
```

### metrics 후처리
```
1. SET online:{server_id} 1 EX 90        — 온라인 상태 갱신
2. DELETE cache:metrics:{server_id}      — 캐시 즉시 무효화 (브라우저 30초 polling 이 다음 주기에 새 값 fetch)
3. _track_agent_restart                  — 직전 agent_started_at 과 비교, 변경 시 1h 슬라이딩 카운터 INCR. threshold 도달 시 warning
```

### error 후처리
없음. 파싱 + 멱등성 + 로깅만 (재시도 컨텍스트 `retry_count`/`first_failed_at`/`recovered_at` 포함).

### task.result 후처리
```
1. DB UPDATE — complete_task(public_id, status, completed_at, failure_reason, exit_code, duration_ms, stdout_tail, stderr_tail).
   public_id 미존재 시 silent ack (운영자가 task 삭제했을 가능성 — DLQ 부적합)
```
`boot_time` / `agent_started_at` 은 본 메시지에서 항상 null 이라 `_log_time_invariants` 호출 생략.

### 미등록 서버 metrics — auto-register
metrics 핸들러는 `repo.ensure_server_id(composite_id, placeholder)`로 한 번에 처리 — find 실패 시 fallback placeholder 사용. `(server_id, auto_registered)` 튜플 반환으로 handler가 auto-register 시점만 운영 로그를 남김. 식별자는 `composite_id` 단일 키 (#C1, ADR 0027) — SHA-256 composite hash (machine_id + 정렬 MAC 들) 라 VM 템플릿 복제·container `/etc/machine-id` 마운트 등 machine_id 중복도 MAC 조합으로 구분.

placeholder는 `mappers.build_placeholder_inventory`가 생성. composite_id/machine_id/hostname/agent_version만 실값, 나머지 정적 정보(OS·CPU·메모리·디스크 등)는 None/빈 배열. 다음 진짜 inventory 도착 시 ON CONFLICT DO UPDATE로 풀 정보 자동 덮어씀 (`composite_id` UNIQUE 제약).

metrics 저장 자체는 `repo.record_metrics(server_id, dto)`가 4개 시계열 테이블 INSERT를 facade로 묶어 처리. `boot_time`·`agent_started_at`은 시계열 4개 테이블 모두에 동일 시점값으로 함께 저장 → metrics·disk_io·net_io는 `web/services/metrics_calculator._is_counter_reset`이 두 시점 비교로 시스템 재부팅 시 delta 건너뛰기 (CLAUDE.md B1). mount_usage는 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 + 운영 디버깅 단일 테이블 SELECT 위해 보존. 반환 `MetricInsertResult`의 각 행 수는 handler 로그에 노출되어 운영 관측 가능.

→ metrics drop 0. inventory one-shot 정책으로 인한 영구 미등록 시나리오 해소. 에이전트 변경 없이 엔진 단독 안전망.

### 팩토리 함수 + 클로저

`make_*_handler`는 핸들러 함수를 반환하는 팩토리. 인자로 받은 의존성을 내부 `_handle`이 클로저로 캡처한다. 클래스 없이 의존성을 함수 수준에서 바인딩하는 패턴.

### `message.process(requeue=False)`

aio-pika async context manager. 블록 정상 종료 → ack, 예외 발생 → nack(`requeue=False`) → DLX 라우팅.

### `_db_retry` 타입 시그니처

PEP 695 generic syntax(`def f[T](...)`)로 `fn`의 반환 타입을 호출자에게 그대로 전달. Python 3.12 표준이라 `TypeVar` 모듈 임포트 불요.

```python
async def _db_retry[T](
    ...,
    fn: Callable[[BaseCollectRepository], Coroutine[Any, Any, T]],
) -> T: ...
```

### DB 재시도 정책

3회 시도 (`attempt 0/1/2`), `5 ** (attempt + 1)` 백오프. attempt 0 실패 → 5s sleep → 1, 1 실패 → 25s sleep → 2, 2 실패 → 즉시 raise(sleep 없음). 총 sleep 합 30s + 3회 DB call. inventory/metrics 큐 TTL(없음·72h) 내에서 단기 DB 장애 회복 커버. error 큐 TTL 300s는 error 핸들러가 DB 접근 안 해 영향 없음.

---

## main.py — 진입점 및 MQ 토폴로지

### MQ 토폴로지

토폴로지 정의(vhost·exchange·DLX·큐·TTL·x-max-length·정책 근거)와 dev/prod 분기는 `docs/architecture/rabbitmq.md` 단일 진실. 본 절은 consumer가 그 토폴로지를 코드로 어떻게 declare·subscribe하는지만 다룬다.

핵심 동작 요약:
- 기동 시 `_DLX = "{exchange}.dlx"` 먼저 선언 → 정상 exchange 선언 → routing key별로 DLQ 선언·DLX 바인딩 → 정상 큐 선언(`x-dead-letter-exchange`/`x-dead-letter-routing-key`/옵셔널 `x-message-ttl`/옵셔널 `x-max-length`) → exchange 바인딩 → consume 시작.
- prefetch_count 10 (`channel.set_qos`).
- TTL/max-length 값은 `src/assessment_engine/consumer/main.py` 상단 `_METRICS_TTL_MS`/`_METRICS_MAX_LEN`/`_ERROR_TTL_MS` 명명 상수.

### aio-pika

RabbitMQ 전용 비동기 클라이언트. AMQP 0-9-1 프로토콜만 지원.

`connect_robust`: 연결 끊김 시 자동 재연결. 재연결 중 채널·큐·컨슈머가 내부적으로 복구된다.

내부 동작:
1. TCP 소켓 생성 + AMQP 커넥션 수립 (버전 확인·인증·vhost 접속)
2. 소켓 fd를 `loop.add_reader`로 이벤트 루프에 등록 → epoll 감시 시작
3. `channel()` — 기존 TCP 위에 AMQP 채널 오픈. 새 소켓 없이 channel_id로 트래픽 구분
4. `declare_exchange/queue/bind` — 각각 요청-응답 왕복 (브로커 승인 프레임 수신 후 진행)
5. `queue.consume(handler)` — AMQP `Basic.Consume`. 이후 브로커가 메시지를 push로 전송
6. `await asyncio.Future()` — 이벤트 루프 무한 유지. CPU는 루프로 반환, 이후 "epoll 신호 → aio-pika 콜백 → handler 코루틴 재개" 사이클

### Redis 생명주기

획득(`get_redis`)과 해제(`close_pool`)를 `main()` 같은 스코프에 두어 생명주기를 명확히 한다.

---

## 설계 결정

### 멱등성: 2단 방어

정책: CLAUDE.md #D2. 자연키 UNIQUE 카탈로그: `docs/architecture/db/models.md` "시계열 5개 테이블 자연키 UNIQUE" 표. at-most-once 한계·outbox 대안: `docs/tradeoffs.md` T1.

### inventory 수신 시 online 즉시 마킹

upsert 성공 후 `SET online:{server_id} EX 90`. 첫 메트릭 수신 전(최대 60초)까지 온라인 표시가 "등록됐다"는 의미로 오해될 수 있다. inventory를 발행한 에이전트는 직후 60초 안에 metrics를 발행하므로 오표시 구간이 짧고, 등록 즉시 피드백을 주는 것이 UX상 낫다.

### InventoryMountInfo 미사용 필드

`free_bytes`, `avail_bytes`가 스키마에 있으나 `src/assessment_engine/consumer/mappers.py:to_inventory_create`에서 명시적으로 drop된다 (`{"mount": ..., "fstype": ..., "total_bytes": ...}`만 매핑). 인벤토리에는 정적 정보만 저장하고, 동적 사용량은 metrics 메시지의 `mounts[]` → `server_mount_usage` 시계열 테이블로 분리한다.

`disks[].major/minor`와 inventory `mounts[].major/minor`는 mount-disk 조인 키로 활용 중 (`web/services/device_filters.find_parent_disk`, mapper의 `MountUsageItem.device_name` 채움). 반면 metrics `mounts[].major/minor`·`disk_io[].major/minor`는 시계열 테이블에 컬럼 없어 Pydantic `extra=ignore`로 통과 후 미저장 — 정확한 활용 카탈로그는 `agent.md` "활용 중인 필드" / "엔진이 받지만 사용하지 않는 필드" 표.

### 부가 시그널 — 운영 가시성

handler 본 처리 흐름과 별개로 두 가지 부가 시그널을 발행 (모두 fail-open · 처리 ack 영향 없음):

1. `_log_time_invariants(data)` — 모든 핸들러(inventory/metrics/error)에서 멱등성 체크 직후 호출.
   - `boot_time > agent_started_at` → systemd 시작 순서 또는 시계 동기화 비정상 (드뭄)
   - `agent_started_at > collected_at` → VM 시계 동기화 문제 (가장 흔함, VM resume 직후)
   위반 시 warning 로그만. DLQ 안 보냄 — 시계 문제는 데이터 reject 의미 없음.

2. `_track_agent_restart(redis, server_id, composite_id, agent_started_at)` — metrics 핸들러 후처리 끝에서 호출.
   - `last_agent_start:{sid}` (24h)에서 직전 값 비교 → 변경 시 `agent_restarts:{sid}` (1h 슬라이딩) INCR
   - `agent_restart_alert_threshold` (기본 3) 도달 시 warning (운영자가 crash loop 인지)
   - 시스템 재부팅도 같은 카운터 — 1h 내 3회 재부팅도 unusual이라 alert 적정
   - Redis 장애 시 silent skip (옛 휴리스틱과 동일 효과)

### Disposability — SIGTERM 흐름 (#F11)

`async with message.process(requeue=False)` 컨텍스트가 본질적 보장. SIGTERM이 와도 진행 중 메시지는 다음 둘 중 하나:
- 정상 종료 → broker ACK → 메시지 사라짐
- 예외 raise → broker NACK + DLX 라우팅 → DLQ로 이동

따라서 메시지 손실 0이 코드 패턴의 자연 결과. 신규 핸들러 추가 시 본 컨텍스트 안에서 모든 await 완료를 보장하면 됨 — `signal.signal()` 또는 `os._exit()` 같은 우회 호출 금지 (#F11).

aio-pika `connect_robust`는 connection 단계 SIGTERM에서도 안전 종료 — `async with conn`이 자체 cleanup.

---

## 운영 / 디버깅

```bash
docker compose logs -f consumer
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged
docker compose restart consumer       # 코드 변경 후 (reload 모드 아님)
```

| 증상 | 원인 |
|------|------|
| 메시지 처리 안 됨 | broker queue declare 실패 — 로그에 `consuming queue=...` 라인 확인 |
| 같은 메시지 반복 처리 | timeout nack 후 재전송 — `_db_retry` 총 sleep 30s + 3회 DB call |
| Pydantic ValidationError | 에이전트 새 필드 + 스키마 미반영. `extra=ignore`로 통과 또는 schema 추가 |
| DLQ 누적 | 영구 오류. `rabbitmqadmin get queue=*.dead count=1 ackmode=ack_requeue_true`로 peek |