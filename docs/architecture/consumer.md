# Consumer

aio-pika 기반 순수 비동기 컨슈머. FastAPI와 독립 프로세스로 실행된다.

```
src/assessment_engine/consumer/
├── schemas.py   — 에이전트 메시지 파싱·검증 계약 (Pydantic)
├── mappers.py   — Pydantic 스키마 → Inbound DTO 변환
├── handler.py   — 메시지 처리 흐름 (멱등성·DB 저장·Redis 후처리)
└── main.py      — 진입점, MQ 토폴로지 선언, Redis 생명주기
```

---

## schemas.py — 에이전트 메시지 계약

에이전트(C99)가 RabbitMQ로 발행하는 3가지 메시지 타입의 파싱·검증 계약.
`model_validate_json(raw)` 한 번으로 파싱·타입 검증이 동시에 일어난다.

### 공통 메타데이터 (MessageBase)

| 필드 | 타입 | 설명 |
|------|------|------|
| `machine_id` | `str` (max 64) | `/etc/machine-id`. 표준 Linux 32 hex, 가상화 환경 UUID(36자) 가능 |
| `agent_version` | `str` (max 32) | 에이전트 빌드 버전. 스키마 계약 버전 역할 |
| `collected_at` | `datetime` | 수집 시각 (ISO 8601 UTC) |
| `hostname` | `str` (max 255) | 보조 식별자. 가변이므로 식별 기준으로 사용 안 함 |
| `message_id` | `UUID` | 멱등성 키 (UUID v4) |

### InventoryInput — `server.inventory` (기동 시 1회)

정적 인프라 정보. OS·kernel·CPU·메모리/스왑 총량, `disks[]`, `mounts[]`, IP, `boot_time`, `services[]`, `listen_ports[]`.

서브모델:
- `DiskInfo`: 물리 디스크 1개 (`name`, `size_bytes`, `type`)
- `InventoryMountInfo`: 마운트 포인트 1개 (`mount`, `total_bytes`, `fstype` 등)
  - `free_bytes` / `avail_bytes`는 handler에서 무시. 인벤토리는 `total_bytes`(정적)만 저장, 동적 사용량은 metrics에서 별도 저장

### MetricsInput — `server.metrics` (1분 주기)

모두 raw 누적값. Web이 연속 2회 readings의 차(delta)로 CPU%·IOPS·kBps를 계산한다.

서브모델:
- `CpuStat`: `/proc/stat` jiffies 단위 누적값
- `DiskIoInfo`: 장치별 I/O 누적 카운터
- `MetricsMountInfo`: 마운트별 현재 사용량 (매 주기 전송)
- `NetIoInfo`: 인터페이스별 누적 바이트·패킷·에러

단위: 메모리 `kb`, 디스크/네트워크 `bytes` (`/proc` 출력 관례)

### ErrorInput — `server.error`

| 필드 | 타입 | 설명 |
|------|------|------|
| `error_code` | `str` | 에러 코드 |
| `error_message` | `str` | 에러 상세 |
| `failed_component` | `Literal["collect","publish"]` | 실패 단계 |
| `retry_count` | `int \| None` | 재시도 요약 보고 시점에만 |
| `first_failed_at` | `datetime \| None` | 재시도 요약 보고 시점에만 |
| `recovered_at` | `datetime \| None` | 복구 보고 시점에만 |

파싱 후 로깅만 수행. DB 저장 없음. 재시도 요약 옵셔널 필드는 스키마 v3 (`payload-schema.md` "발행 정책") 참조.

### Pydantic Field 제약

| 인자 | 의미 |
|------|------|
| `min_length` / `max_length` | 문자열 길이 제한 |
| `ge` | greater or equal (≥) — 숫자 하한 |
| `gt` | greater than (>) — 0 초과 |
| `default` | 필드 누락 시 기본값 |
| `default_factory=list` | 리스트 기본값. `default=[]` 쓰면 인스턴스 간 객체 공유 버그 |

`Literal` 타입: routing key로 타입이 이미 확정되는 구조에서 payload 무결성 이중 검증 역할.

---

## handler.py — 메시지 처리 흐름

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
2. DELETE cache:metrics:{server_id}      — 캐시 즉시 무효화
3. PUBLISH metrics.events {...}          — 브라우저 SSE 트리거
4. _track_agent_restart                  — 직전 agent_started_at과 비교, 변경 시 1h 슬라이딩 카운터 INCR. threshold 도달 시 warning
```

### error 후처리
없음. 파싱 + 멱등성 + 로깅만 (재시도 컨텍스트 `retry_count`/`first_failed_at`/`recovered_at` 포함).

### 미등록 서버 metrics — auto-register
metrics 핸들러는 `repo.ensure_server_id(machine_id, placeholder)`로 한 번에 처리 — find 실패 시 fallback placeholder 사용. `(server_id, auto_registered)` 튜플 반환으로 handler가 auto-register 시점만 운영 로그를 남김.

placeholder는 `mappers.placeholder_inventory_from_metrics`가 생성. machine_id/hostname/agent_version만 실값, 나머지 정적 정보(OS·CPU·메모리·디스크 등)는 None/빈 배열. 다음 진짜 inventory 도착 시 ON CONFLICT DO UPDATE로 풀 정보 자동 덮어씀 (machine_id UNIQUE 제약).

metrics 저장 자체는 `repo.record_metrics(server_id, dto)`가 4개 시계열 테이블 INSERT를 facade로 묶어 처리. `boot_time`·`agent_started_at`은 시계열 4개 테이블 모두에 동일 시점값으로 함께 저장 → metrics·disk_io·net_io는 `web/services/metrics_calculator._is_counter_reset`이 두 시점 비교로 시스템 재부팅 시 delta 건너뛰기 (CLAUDE.md B1). mount_usage는 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 + 운영 디버깅 단일 테이블 SELECT 위해 보존. 반환 `MetricInsertResult`의 각 행 수는 handler 로그에 노출되어 운영 관측 가능.

→ metrics drop 0. inventory one-shot 정책으로 인한 영구 미등록 시나리오 해소. 에이전트 변경 없이 엔진 단독 안전망.

### 팩토리 함수 + 클로저

`make_*_handler`는 핸들러 함수를 반환하는 팩토리. 인자로 받은 의존성을 내부 `_handle`이 클로저로 캡처한다. 클래스 없이 의존성을 함수 수준에서 바인딩하는 패턴.

### `message.process(requeue=False)`

aio-pika async context manager. 블록 정상 종료 → ack, 예외 발생 → nack(`requeue=False`) → DLX 라우팅.

### `_db_retry` 타입 시그니처

`TypeVar T`로 `fn`의 반환 타입을 그대로 호출자에게 전달. `Any`로 고정하면 타입 체커가 반환값을 검사하지 못한다.

```python
T = TypeVar("T")
async def _db_retry(
    ...,
    fn: Callable[[BaseCollectRepository], Coroutine[Any, Any, T]],
) -> T: ...
```

### DB 재시도 정책

`5  (attempt + 1)` — 5, 25, 125초 대기 후 최종 실패. 합 155초로 inventory/metrics 모두 큐 TTL(없음·72h) 내에서 DB 재시작을 커버. error 큐는 TTL 300s지만 error 핸들러는 DB 접근 안 해 영향 없음.

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

### 멱등성: 2단 방어 (at-most-once)

1단 — Redis 키: `SET idempotent:{message_id} 1 EX 86400 NX`. 24h 동안 동일 message_id 재전송을 가장 빠르게 차단.

2단 — DB UNIQUE 제약: 시계열 4개 테이블에 자연키 UNIQUE + `pg_insert(...).on_conflict_do_nothing(index_elements=...)` 적용. Redis 키 만료·evict·재시작·수동 flush로 1단이 깨져도 DB 레벨에서 중복 INSERT가 silent no-op으로 흡수된다.

| 테이블 | 자연키 |
|--------|-------|
| `server_metrics` | `(server_id, collected_at)` |
| `server_disk_io` | `(server_id, device, collected_at)` |
| `server_net_io` | `(server_id, interface, collected_at)` |
| `server_mount_usage` | `(server_id, mount, collected_at)` |

at-most-once 트레이드오프: SET NX는 DB 커밋 이전에 실행. 커밋 전 프로세스 크래시 시 RabbitMQ 재전송 메시지가 중복으로 판정되어 조용히 드롭됨. DB UNIQUE로도 해결 못 함 — 1단이 먼저 차단하기 때문. exactly-once가 필요하면 outbox 패턴으로 전환해야 한다 (`docs/adr/tradeoffs.md`).

### Redis: 멱등성 체크 critical path 포함

Redis 장애 시 멱등성 체크 예외 → nack → DLQ. fail-open(장애 시 체크 생략)으로 바꾸면 중복 처리 가능성이 생긴다. Redis는 캐시·PUB/SUB·온라인 TTL로 이미 hard dependency이므로 새로운 단일 장애점이 추가되는 구조가 아니다.

### 미등록 서버 메트릭 — auto-register (drop 0)

inventory 처리 전 metrics가 도달하면(레이스 컨디션, DB 초기화 후 inventory 미수신, inventory DLQ 등) 이전엔 metrics drop이었으나, 현재는 placeholder inventory 자동 생성으로 처리 (`handler.py` + `mappers.placeholder_inventory_from_metrics`). 정적 정보는 None/빈 배열로 채우고 다음 진짜 inventory 도착 시 upsert가 풀 정보로 덮어씀.

장점: metrics 시계열 손실 0. inventory의 one-shot 약점 해소. 에이전트 변경 없이 엔진 단독 처리.
한계: placeholder 상태 동안 web UI에 OS/CPU/메모리 등 정적 정보 미표시 — 다음 inventory 도착 시 자동 채워짐. 에이전트 정상 운영 시 분 단위 짧음.

### inventory 수신 시 online 즉시 마킹

upsert 성공 후 `SET online:{server_id} EX 90`. 첫 메트릭 수신 전(최대 60초)까지 온라인 표시가 "등록됐다"는 의미로 오해될 수 있다. inventory를 발행한 에이전트는 직후 60초 안에 metrics를 발행하므로 오표시 구간이 짧고, 등록 즉시 피드백을 주는 것이 UX상 낫다.

### InventoryMountInfo 미사용 필드

`free_bytes`, `avail_bytes`가 스키마에 있으나 `src/assessment_engine/consumer/mappers.py:to_inventory_create`에서 명시적으로 drop된다 (`{"mount": ..., "fstype": ..., "total_bytes": ...}`만 매핑). 인벤토리에는 정적 정보만 저장하고, 동적 사용량은 metrics 메시지의 `mounts[]` → `server_mount_usage` 시계열 테이블로 분리한다.

스키마 v3에 추가된 `disks[].major/minor`, `mounts[].major/minor`, `disk_io[].major/minor`도 Pydantic `extra=ignore`로 통과 후 사용 안 한다. 활용·제거 결정은 다음 agent_version 협의 시점.

### metrics 공통 메타 — boot_time / agent_started_at

이전엔 metrics 메시지의 `boot_time`/`agent_started_at`을 mapper에서 무시했으나, 현재는 `to_metric_create`가 `ServerMetricCreate`에 매핑 + `record_metrics`가 시계열 4개 테이블 모두에 함께 저장. metrics·disk_io·net_io는 `web/services/metrics_calculator._is_counter_reset`이 두 시점 비교로 시스템 재부팅 시 delta 건너뛰기 (CLAUDE.md B1). 차트 SQL(`_chart_cpu_delta` / `_chart_rate_per_dimension`)도 동일 정책 적용 — `LAG(boot_time)` 추출 후 CASE에서 reset 식별. mount_usage는 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 + 운영 디버깅 단일 테이블 SELECT 위해 보존. 옛 데이터(컬럼 NULL)는 d<0 휴리스틱 fallback. 활용 카탈로그는 `agent.md` "활용 중인 필드" 표 참조.

### 부가 시그널 — 운영 가시성

handler 본 처리 흐름과 별개로 두 가지 부가 시그널을 발행 (모두 fail-open · 처리 ack 영향 없음):

1. `_log_time_invariants(data)` — 모든 핸들러(inventory/metrics/error)에서 멱등성 체크 직후 호출.
   - `boot_time > agent_started_at` → systemd 시작 순서 또는 시계 동기화 비정상 (드뭄)
   - `agent_started_at > collected_at` → VM 시계 동기화 문제 (가장 흔함, VM resume 직후)
   위반 시 warning 로그만. DLQ 안 보냄 — 시계 문제는 데이터 reject 의미 없음.

2. `_track_agent_restart(redis, server_id, machine_id, agent_started_at)` — metrics 핸들러 후처리 끝에서 호출.
   - `last_agent_start:{sid}` (24h)에서 직전 값 비교 → 변경 시 `agent_restarts:{sid}` (1h 슬라이딩) INCR
   - `agent_restart_alert_threshold` (기본 3) 도달 시 warning (운영자가 crash loop 인지)
   - 시스템 재부팅도 같은 카운터 — 1h 내 3회 재부팅도 unusual이라 alert 적정
   - Redis 장애 시 silent skip (옛 휴리스틱과 동일 효과)

---

## 운영 / 디버깅

### 로그 확인

```bash
docker compose logs -f consumer                                 # 실시간
docker compose logs consumer --since=10m                        # 최근 10분
docker compose logs consumer 2>&1 | grep -E "ERROR|WARNING"     # 에러만
```

기대 정상 로그 예:
```
consumer.main - consumer starting exchange=assessment
consumer.main - consuming queue=server.inventory ttl_ms=None max_len=None
consumer.main - consuming queue=server.metrics ttl_ms=259200000 max_len=1000000
consumer.main - consuming queue=server.error ttl_ms=300000 max_len=None
consumer.handler - inventory stored machine_id=f1e90cdc...
consumer.handler - metrics stored machine_id=f1e90cdc...
```

문제 신호:
- `auto-registered server from metrics machine_id=...` → DB에 미등록 서버 metrics 수신 시 placeholder 자동 등록. 정상 동작 (drop 아님)
- `db error attempt=N error=...` → DB 일시 장애. attempt 1~2는 정상 자동 복구
- `db error after 3 attempts: ...` → 최종 실패 → DLQ 라우팅

### MQ 큐 적재량 확인

```bash
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged
```

기대 (정상):
```
server.inventory          0  0
server.metrics            0  0
server.error              0  0
server.inventory.dead     0  0
server.metrics.dead       0  0
server.error.dead         0  0
```

`messages_ready`가 누적되면 consumer가 처리 못 하고 있는 상태. DLQ가 비어있지 않으면 처리 실패 누적.

### DLQ 메시지 검사

```bash
# RabbitMQ 관리 UI: http://localhost:15672 (assessment/assessment)
# Queues 탭 → server.metrics.dead → Get messages

# 또는 CLI로 1건 peek
docker compose exec rabbitmq rabbitmqadmin get queue=server.metrics.dead count=1 ackmode=ack_requeue_true
```

DLQ 메시지를 다시 처리 큐로 옮기려면 `shovel` 플러그인 또는 manual republish 필요.

### consumer 재기동

```bash
docker compose restart consumer
```

코드 변경 후 또는 broker 큐 재선언이 필요할 때. consumer는 reload 모드가 아니므로 항상 수동 restart.

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| consumer는 떴지만 메시지 처리 안 됨 | broker queue가 아직 declare 안 됐거나 connection 실패 | 로그에 `consuming queue=...` 라인이 있는지 확인 |
| 같은 메시지가 반복 처리되어 보임 | 사실은 처리 중인 메시지가 timeout으로 nack → 재전송 | DB 응답 시간 확인. `_db_retry`가 1회 처리에 최대 155s 소요 |
| Pydantic ValidationError 누적 | 에이전트가 새 필드 추가 + 컨슈머 스키마 미반영 | `src/assessment_engine/consumer/schemas.py`에 필드 추가 또는 `extra=ignore`로 통과 |
| `agent error` 로그 다량 | 에이전트가 `error` 메시지를 발행 중 | 에이전트 로그(`vagrant ssh ... journalctl`) 확인 |