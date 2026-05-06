# Redis 전략

캐시·온라인 TTL·멱등성·PUB/SUB의 4가지 역할을 한 인스턴스로 처리한다.
Consumer / Web 양쪽이 같은 키 네임스페이스를 공유하며, 키 패턴은 `WebSettings`에 정의한다 (`config.py`).

```
db/
└── redis.py        — ConnectionPool 싱글턴 + get_redis()

config.py           — redis_key_*, redis_ttl_*, redis_channel_*

consumer/handler.py — _check_idempotent, online SET, cache DEL, PUBLISH
web/services/query_service.py — cache GET/SET, mget(online), pubsub.subscribe
```

---

## 키 설계

| 용도 | 키 | TTL | 무효화 트리거 |
|------|----|-----|-------------|
| public_id 조회 | `cache:resolve:{public_id}` | 없음 (불변) | — |
| 인벤토리 캐시 | `cache:inventory:{server_id}` | 300s | consumer가 새 inventory 저장 시 즉시 DELETE |
| 메트릭 캐시 | `cache:metrics:{server_id}` | 60s | consumer가 새 metrics 저장 시 즉시 DELETE |
| 멱등성 | `idempotent:{message_id}` | 24h | TTL 만료만 |
| 온라인 TTL | `online:{server_id}` | 90s | consumer가 inventory·metrics 양쪽에서 매번 갱신 |
| 인증 토큰 | `token:{token}` | 1h | TTL 만료만 |

### TTL 값 근거

- `online:{server_id}` 90s — metrics 발행 주기(60s)의 1.5배. 1회 발행 누락은 허용, 2회 연속 누락 시 오프라인 표시.
- `cache:metrics:{server_id}` 60s — metrics 주기와 동일. consumer DELETE가 없어도 1주기 후 자연 갱신.
- `cache:inventory:{server_id}` 300s — inventory 변경 빈도가 낮음. consumer DELETE가 즉시 반영.
- `idempotent:{message_id}` 24h — message_id는 UUID v4이므로 24h 동안 unique 보장. broker 재전송 윈도우 충분히 커버.

---

## PUB/SUB 채널

| 채널 | 발행자 | 구독자 | payload |
|------|--------|--------|---------|
| `metrics.events` | consumer (metrics 저장 후) | web SSE 핸들러 | `{"server_id": int, "machine_id": str}` |

웹 측 `stream_metrics_events`(query_service.py)는 단일 채널을 모두 구독하고 server_id 일치 여부로 필터링. 구독 클라이언트별 채널 분리 안 함 (트레이드오프 T5).

---

## 멱등성 처리 (2단 방어)

### 1단 — Redis 키 (consumer/handler.py `_check_idempotent`)

```python
SET idempotent:{message_id} 1 EX 86400 NX
```

- 메시지 수신 직후, 파싱 성공 후 첫 처리 단계.
- `NX` 옵션으로 원자적 set-if-not-exists. 이미 처리된 message_id면 ack 후 조기 리턴.
- 24h 동안 동일 message_id 재전송을 가장 빠르게 차단.

### 2단 — DB UNIQUE 제약 (db/repositories/collect_repository.py `insert_metric`)

시계열 4개 테이블 자연키 UNIQUE + `pg_insert(...).on_conflict_do_nothing(index_elements=...)`.

| 테이블 | conflict 키 |
|--------|-----|
| server_metrics | (server_id, collected_at) |
| server_disk_io | (server_id, device, collected_at) |
| server_net_io | (server_id, interface, collected_at) |
| server_mount_usage | (server_id, mount, collected_at) |

Redis 키 만료·evict·재시작·수동 flush 등으로 1단이 깨져도 DB 레벨에서 silent no-op 흡수.

### at-most-once 트레이드오프

`SET NX` 후 DB 커밋 이전에 프로세스 크래시 시 broker 재전송 메시지가 idempotent 충돌로 silent 드롭 → 데이터 유실 가능. 1단이 먼저 차단하므로 2단 UNIQUE도 못 막음. `docs/tradeoffs.md` T1 참조.

---

## 캐시 무효화 (cache-aside)

### inventory 처리 후 (consumer/handler.py)

```
1. SET online:{server_id} 1 EX 90        — 등록 즉시 온라인 판정
2. DELETE cache:inventory:{server_id}    — 인벤토리 변경 즉시 반영
```

서비스/포트/디스크가 추가/제거된 경우 다음 detail 페이지 요청에서 새 값이 노출됨. 300s TTL 만료 대기 제거.

### metrics 처리 후 (consumer/handler.py)

```
1. SET online:{server_id} 1 EX 90
2. DELETE cache:metrics:{server_id}      — 캐시 즉시 무효화
3. PUBLISH metrics.events {...}          — 브라우저 SSE 트리거
```

브라우저는 SSE 메시지 수신 후 `/metrics/latest` AJAX 재요청 → 캐시 MISS → DB 조회 → 새 캐시 SET.

### cache-aside race (알려진 한계)

web의 `get_latest_metric`이 cache MISS 후 DB query를 마쳤지만 SET을 수행하기 전에 consumer가 새 metrics 커밋 + DELETE를 끝낼 수 있다. 이 경우 web의 SET이 stale 데이터를 60s TTL로 캐싱.

실용적 영향은 최대 1회 표시 지연 (SSE가 즉시 다음 fetch 트리거). exactly-once 캐시 일관성 대신 단순성 선택. `docs/tradeoffs.md` T2.

---

## 효율 패턴

### N+1 회피 — `mget`

서버 목록 페이지가 N개 서버의 온라인 상태를 조회할 때, N번 직렬 `EXISTS online:{id}` 대신 `redis.mget([online:{id} for ...])` 한 번으로.

```python
# web/services/query_service.py:list_servers
keys = [web_settings.redis_key_online.format(dto.id) for dto in dtos]
online_flags = await self.redis.mget(keys)
for dto, flag in zip(dtos, online_flags):
    item = to_server_list_item(dto)
    item.is_online = flag is not None
```

페이지당 라운드트립 N → 1.

### 직접 캐시 read/write (read-through)

`get_server`, `get_latest_metric`이 read-through 패턴.

```python
cached = await self.redis.get(cache_key)
if cached:
    return server_detail_from_json(cached)
result = ...  # DB 조회 + ViewModel 변환
await self.redis.set(cache_key, server_detail_to_json(result), ex=300)
return result
```

cache_serializer가 dataclass↔JSON serde 담당. 역직렬화 직후 `enrich_server_detail()` 재호출로 파생 필드 일관성 유지.

---

## 의존성 주입 / 생명주기

### 커넥션 풀 (db/redis.py)

```python
_pool: ConnectionPool | None = None

def get_pool() -> ConnectionPool:
    if _pool is None:
        _pool = ConnectionPool.from_url(web_settings.redis_url, decode_responses=True)
    return _pool

def get_redis() -> Redis:
    return Redis(connection_pool=get_pool())

async def close_pool() -> None: ...
```

- 단일 모듈 레벨 `_pool`. 모든 호출이 같은 풀 공유.
- `decode_responses=True` — bytes가 아닌 str로 자동 디코딩. JSON 캐시 직렬화/역직렬화 단순화.

### web 측 — DI

`web/deps.py:get_service`가 `Depends(get_redis)`로 주입받아 `QueryService`에 전달.
`web/main.py` lifespan 종료 시 `close_pool()` 호출.

### consumer 측 — 직접 호출

`consumer/main.py`가 `get_redis()`를 직접 호출해 핸들러 팩토리에 전달. lifespan 종료 시 `close_pool()`.

---

## 설정·운영

### eviction
`maxmemory 256mb`, `volatile-lru` 정책. TTL 있는 키만 evict 대상. 멱등성 키도 TTL 있어 evict 가능 (T1 트레이드오프 일부).

### 키 패턴 정의 위치
모든 키 패턴(`redis_key_online`, `redis_key_cache_*`, `redis_key_idempotent`, `redis_channel_metrics`)은 `WebSettings`에 정의. `ConsumerSettings`는 `WebSettings`를 상속하므로 동일 키 사용. `query_service.py`는 `web_settings`를 직접 참조 — consumer/web 모두 같은 키 네임스페이스.

### Redis 장애 시 동작 — fail-close
멱등성 체크가 critical path에 포함되어 있어 Redis 장애 시 핸들러가 예외 → nack → DLQ.
fail-open(장애 시 멱등성 체크 생략)으로 바꾸면 중복 처리 가능성이 생긴다. Redis는 캐시·PUB/SUB·온라인 TTL로 이미 hard dependency이므로 새로운 단일 장애점이 추가되는 구조가 아니다.

---

## 트레이드오프 인덱스

| 항목 | 위치 |
|------|------|
| at-most-once 멱등성 한계 | `docs/tradeoffs.md` T1 |
| cache-aside race | `docs/tradeoffs.md` T2 |
| SSE 단일 채널 + 서버 측 필터링 | `docs/tradeoffs.md` T5 |
| 단일 Redis 인스턴스 | `docs/tradeoffs.md` T11 |

---

## 운영 / 디버깅

### redis-cli 접속

```bash
docker compose exec redis redis-cli
```

### 자주 쓰는 명령

```bash
# 키 패턴 조회 (운영에서는 SCAN 사용 권장 — KEYS는 블로킹)
SCAN 0 MATCH 'online:*' COUNT 100
SCAN 0 MATCH 'cache:metrics:*' COUNT 100
SCAN 0 MATCH 'idempotent:*' COUNT 100

# 특정 키 TTL 확인
TTL online:1            # 양수=남은 초, -1=TTL 없음, -2=키 없음
TTL idempotent:550e8400-e29b-41d4-a716-446655440000

# 캐시 강제 무효화 (디버그용)
DEL cache:metrics:1
DEL cache:inventory:1

# 온라인 상태 확인
EXISTS online:1
MGET online:1 online:2 online:3      # 일괄

# PUB/SUB 채널 모니터링
SUBSCRIBE metrics.events             # 새 publish 실시간 수신

# 서버 통계
INFO stats                            # evicted_keys, expired_keys, ...
INFO memory                           # used_memory, maxmemory, ...
INFO clients                          # 연결 수
DBSIZE                                # 전체 키 수
```

### 멱등성 시나리오 검증

```bash
# 1. 새 message_id로 SET NX
docker compose exec redis redis-cli SET idempotent:test-uuid 1 EX 86400 NX
# (integer) 1   ← 성공

# 2. 같은 키로 SET NX 재시도
docker compose exec redis redis-cli SET idempotent:test-uuid 1 EX 86400 NX
# (nil)         ← 이미 존재 — 핸들러는 중복으로 판단해 ack 후 리턴

# 3. 정리
docker compose exec redis redis-cli DEL idempotent:test-uuid
```

### maxmemory 압박 시뮬레이션

```bash
# 현재 사용량 확인
docker compose exec redis redis-cli INFO memory | grep -E "used_memory_human|maxmemory_human"

# evicted_keys 확인 — 0이면 evict 발생 안 함
docker compose exec redis redis-cli INFO stats | grep evicted_keys
```

`evicted_keys`가 누적되면 캐시·온라인 키뿐 아니라 멱등성 키도 evict 가능 → at-most-once 보장 약화. T11 참조.

### Redis 장애 시뮬레이션

```bash
# Redis 컨테이너 stop
docker compose stop redis

# 영향:
# - consumer: SET NX 실패 → 핸들러 raise → nack(requeue=False) → DLQ 누적
# - web: cache GET 실패 → 500 응답 (또는 fail-open 코드 없음)
# - SSE 스트림: 끊김

# 복구
docker compose start redis
```

fail-close 정책이라 Redis가 곧 복구되지 않으면 DLQ에 메시지가 쌓이고 사용자에게 503에 가까운 경험. 운영에서 Redis HA(Sentinel/Cluster) 도입 시 검토.

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| 새 inventory가 web에 반영 안 됨 | consumer의 cache:inventory DELETE 실패 또는 web 캐시 stale | `DEL cache:inventory:{id}` 수동 / consumer 로그 확인 |
| 같은 message_id가 두 번 처리되어 중복 행 | Redis 키 만료 또는 evict | DB UNIQUE 제약(2단)이 흡수 — 중복 행 없으면 정상 |
| 온라인 뱃지가 깜빡임 (online↔offline) | metrics 발행 주기(60s) > online TTL(90s) 거의 한계 | 주기 단축 또는 TTL 연장 |
| SSE 클라이언트가 못 받음 | metrics.events 채널에 이미 publish됐지만 구독 시작 전 — Redis pubsub은 fire-and-forget | 클라이언트가 SUBSCRIBE 후 즉시 `/metrics/latest` 1회 fetch로 보완 (현재 구현) |