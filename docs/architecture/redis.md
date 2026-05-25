# Redis 전략

정책: CLAUDE.md #C3. 캐시·온라인 TTL·멱등성·PUB/SUB의 4가지 역할을 한 인스턴스로 처리.
키 패턴은 `WebSettings` 단일 정의(`src/assessment_engine/config.py`). `ConsumerSettings`는 `WebSettings` 상속 — consumer/web 동일 네임스페이스.

```
src/assessment_engine/db/
└── redis.py        — ConnectionPool 싱글턴 + get_redis()

src/assessment_engine/config.py
                    — redis_key_*, redis_ttl_*, redis_channel_*

src/assessment_engine/consumer/handlers/
                    — _check_idempotent, online SET, cache DEL, PUBLISH
src/assessment_engine/web/services/query_service.py
                    — cache GET/SET, mget(online), pubsub.subscribe
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
| 직전 agent_started_at | `last_agent_start:{server_id}` | 24h | metrics 처리 시 매번 SET (직전 값과 비교 → 재시작 감지) |
| 재시작 카운터 (1h 슬라이딩) | `agent_restarts:{server_id}` | 1h | `_track_agent_restart`가 변경 감지 시 INCR + EXPIRE reset (마지막 INCR 후 1h 유지) |

### TTL 값 근거

- `online:{server_id}` 90s — metrics 발행 주기(60s)의 1.5배. 1회 발행 누락은 허용, 2회 연속 누락 시 오프라인 표시.
- `cache:metrics:{server_id}` 60s — metrics 주기와 동일. consumer DELETE가 없어도 1주기 후 자연 갱신.
- `cache:inventory:{server_id}` 300s — inventory 변경 빈도가 낮음. consumer DELETE가 즉시 반영.
- `idempotent:{message_id}` 24h — message_id는 UUID v4이므로 24h 동안 unique 보장. broker 재전송 윈도우 충분히 커버.
- `last_agent_start:{server_id}` 24h — 직전 비교용 캐시. evict 시 다음 메시지에서 재시작 감지 1회 누락만 — 다음 정상 sample에서 회복.
- `agent_restarts:{server_id}` 1h — 슬라이딩 윈도우 (마지막 INCR 후 1h). `agent_restart_alert_threshold` (기본 3) 도달 시 warning 로그.

원격 작업 명령 전달은 ADR 0007 (별도 큐 모델) 채택으로 broker 가 메시지 보유 — Redis pending hot path 캐시 폐기. DB `tasks` + broker `agent.tasks.<host_id>` 큐가 단일 진실.

---

## PUB/SUB 채널

| 채널 | 발행자 | 구독자 | payload |
|------|--------|--------|---------|
| `metrics.events` | consumer (metrics 저장 후) | web SSE 핸들러 | `{"server_id": int, "host_id": str}` |

웹 측 `stream_metrics_events`(query_service.py)는 단일 채널을 모두 구독하고 server_id 일치 여부로 필터링. 구독 클라이언트별 채널 분리 안 함 (트레이드오프 T5).

---

## 캐시 무효화 (cache-aside)

### inventory 처리 후 (consumer/handlers/)

```
1. SET online:{server_id} 1 EX 90        — 등록 즉시 온라인 판정
2. DELETE cache:inventory:{server_id}    — 인벤토리 변경 즉시 반영
```

서비스/포트/디스크가 추가/제거된 경우 다음 detail 페이지 요청에서 새 값이 노출됨. 300s TTL 만료 대기 제거.

### metrics 처리 후 (consumer/handlers/)

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
cached = await safe_get(self.redis, cache_key)
if cached:
    return server_detail_from_json(cached)
result = ...  # DB 조회 + ViewModel 변환
await safe_set(self.redis, cache_key, server_detail_to_json(result), ex=300)
return result
```

cache_serializer가 dataclass-JSON serde 담당. 역직렬화 직후 `enrich_server_detail()` 재호출로 파생 필드 일관성 유지.

---

## 의존성 주입 / 생명주기

### 커넥션 풀 (cache/redis.py)

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

`src/assessment_engine/web/deps.py:get_service`가 `Depends(get_redis)`로 주입받아 `QueryService`에 전달.
`src/assessment_engine/web/main.py` lifespan 종료 시 `close_pool()` 호출.

### consumer 측 — 직접 호출

`src/assessment_engine/consumer/main.py`가 `get_redis()`를 직접 호출해 핸들러 팩토리에 전달. lifespan 종료 시 `close_pool()`.

---

## 설정·운영

### eviction
`maxmemory 256mb`, `volatile-lru` 정책. TTL 있는 키만 evict 대상. 멱등성 키도 TTL 있어 evict 가능 (T1 트레이드오프 일부).

### 키 패턴 정의 위치
모든 키 패턴(`redis_key_online`, `redis_key_cache_*`, `redis_key_idempotent`, `redis_channel_metrics`)은 `WebSettings`에 정의. `ConsumerSettings`는 `WebSettings`를 상속하므로 동일 키 사용. `query_service.py`는 `web_settings`를 직접 참조 — consumer/web 모두 같은 키 네임스페이스.

### Redis 장애 시 동작 — fail-open

정책: CLAUDE.md #C3 · #F6. 본 절은 운영 동작 매트릭스만.

`safe_*` helper 카탈로그: `safe_get`/`safe_set`/`safe_set_nx`/`safe_delete`/`safe_mget`/`safe_publish`/`safe_incr_with_ttl` (`src/assessment_engine/cache/redis.py`). 정확성 보장은 2단 안전망(DB UNIQUE / DB query / `last_seen_at` 컬럼)에 위임.

| 역할 | 평시 | Redis 장애 시 |
|------|------|-------------|
| 캐시 GET (`get_server`/`get_latest_metric`/`resolve_server_id`) | hit/miss | DB 직접 조회 (응답 정상, 느려질 뿐) |
| 캐시 SET | TTL 적용 | silent skip (다음 요청도 MISS) |
| 멱등성 1단 (`_check_idempotent`) | SET NX | True 반환 → 처리 진행 → DB UNIQUE(2단)이 중복 흡수 |
| consumer cache DELETE / online SET / publish | 정상 호출 | 로그만, 메시지 처리 정상 진행 |
| list mget (`list_servers`) | 1회 mget | `last_seen_at > now() - 90s` fallback (TTL 임계와 동일) |
| SSE pubsub (`stream_metrics_events`) | subscribe + listen | RedisError 캐치 → 스트림 종료 (브라우저 자동 재연결) |

약화되는 보장:
- 멱등성 1단: 평시 1회 RTT 차단 → 장애 시 매번 DB INSERT 시도 + UNIQUE 충돌 흡수. 트래픽 규모에서 영향 미미.
- list 화면 online: Redis 90s TTL 기반 → DB `last_seen_at` 기반. 정밀도 거의 동일, DB N개 행 비교 부하 추가.
- SSE: 끊김. 브라우저가 자동 재연결.

약화되지 않는 보장: 데이터 정확성. 멱등성 fail-open은 1단 차단을 잃지만 시계열 4개 테이블의 `(server_id, [dim,] collected_at)` UNIQUE 제약이 중복 INSERT를 silent no-op으로 흡수.

상세 의사결정과 옵션 비교는 `docs/adr/0001-redis-decoupling.md`.

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

```bash
docker compose exec redis redis-cli
SCAN 0 MATCH 'online:*' COUNT 100              # 운영은 SCAN (KEYS는 블로킹)
TTL idempotent:<uuid>                          # 양수=남은초, -1=TTL 없음, -2=키 없음
DEL cache:metrics:1                            # 강제 무효화 (디버그)
SUBSCRIBE metrics.events                       # PUB/SUB 모니터링
INFO memory | grep -E "used_memory|maxmemory"  # 사용량
INFO stats | grep evicted_keys                 # evict 누적 (T11: 멱등성 보장 약화)
```

| 증상 | 원인 |
|------|------|
| 새 inventory 반영 안 됨 | consumer cache DELETE 실패 — `DEL cache:inventory:{id}` 수동 |
| 같은 message_id 중복 행 | Redis 키 만료/evict — DB UNIQUE 2단이 흡수, 중복 행 없으면 정상 |
| 온라인 뱃지 깜빡임 | metrics 주기(60s) ≈ TTL(90s) 한계 — 주기 단축 또는 TTL 연장 |
| SSE 못 받음 | pubsub은 fire-and-forget — SUBSCRIBE 후 즉시 `/metrics/latest` 1회 fetch로 보완(현재 구현) |