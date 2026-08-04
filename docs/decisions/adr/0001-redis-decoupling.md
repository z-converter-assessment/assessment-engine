# 리팩토링 — Redis 결합도 재검토

> 상태: 옵션 B + list mget fallback 적용 완료 (2026-05-07).
> 작성: 2026-05-07
> 관련 문서: `docs/reference/redis.md` (현황), `docs/explanation/tradeoffs.md` T1·T2·T5·T11
> 관련 코드: `src/assessment_engine/db/redis.py`, `src/assessment_engine/consumer/handler.py`, `src/assessment_engine/web/services/query_service.py`, `src/assessment_engine/web/deps.py`, `src/assessment_engine/web/services/cache_serializer.py`, `src/assessment_engine/config.py`

---

## 0. 이 문서의 목적

현재 본 엔진은 Redis 단일 인스턴스에 캐시 / 온라인 TTL / 멱등성 / PUB/SUB / public_id 해석 캐시의 5가지 역할을 모두 위임하고, 모든 외부 의존을 fail-close 정책으로 처리한다 (Redis 장애 시 핸들러 raise → DLQ 누적, web 캐시 GET 실패 → 5xx).

질문은 두 가지다.

1. 현재 코드가 Redis에 종속적인 정도가 일반적인 Redis 사용 패턴 대비 과도한가?
2. 그렇다면 어디를 어떻게 분리할 수 있는가? 분리해야 하는가?

본 문서는 (a) 현재 결합 매트릭스를 기록하고, (b) 일반 패턴과 대조하고, (c) 분리 옵션을 그라데이션으로 제시한다. 실행 결정은 별도 — 본 문서는 의사결정 자료.

---

## 1. 현재 Redis 사용 매트릭스

`grep -rn "redis" web/ consumer/ db/ --include="*.py"` 기준 53개 호출 지점. 역할별로 묶으면 다음과 같다.

| # | 역할 | 키 / 채널 | TTL | 호출 지점 | 장애 영향 (현재) |
|---|------|----------|-----|---------|------------|
| 1 | 멱등성 | `idempotent:{message_id}` | 24h | consumer `_check_idempotent` | 핸들러 raise → nack → DLQ 누적 |
| 2 | 온라인 TTL (write) | `online:{server_id}` | 90s | consumer (inventory·metrics 양쪽) | 핸들러 raise → DLQ 누적 |
| 3 | 온라인 TTL (read) | `online:{server_id}` | — | web `_is_online`, `list_servers` (mget) | `/servers` 목록·detail 응답 5xx |
| 4 | 인벤토리 캐시 | `cache:inventory:{server_id}` | 300s | web GET/SET, consumer DEL | `/servers/{id}` 응답 5xx (fail-close) |
| 5 | 메트릭 캐시 | `cache:metrics:{server_id}` | 60s | web GET/SET, consumer DEL | `/api/v1/.../metrics/latest` 5xx |
| 6 | public_id → server_id | `cache:resolve:{public_id}` | 없음 | web `resolve_server_id` | 모든 detail 라우트 5xx |
| 7 | PUB/SUB | `metrics.events` 채널 | — | consumer publish, web subscribe | SSE 끊김 (사용자는 자동 재연결 안내 받음) |

→ 8 중 7개 역할이 활성. 1~7 모두 fail-close.

### 의존도 평가 (코드 의존 / 설계 의존)

| 역할 | 코드 의존도 | 설계 의존도 | 비고 |
|------|----------|----------|------|
| 멱등성 | 중 | 낮음 | 1단(Redis) 빠지면 2단(DB UNIQUE)로 흡수. 정확성 영향 없음, 비용만 증가 |
| 온라인 TTL | 높음 | 높음 | "온라인" 정의 자체가 Redis TTL — DB last_seen으로 옮기려면 의미·쿼리 모두 재설계 |
| 캐시 (3종) | 중 | 낮음 | 표준 cache-aside. fail-open으로 빼도 응답 가능 (성능만 영향) |
| PUB/SUB | 중 | 중 | SSE 자체는 web 책임이지만 트리거 메커니즘은 Redis pubsub에 묶여 있음 |

핵심: 진짜 깊이 묶인 건 온라인 TTL 한 개. 나머지는 모두 표준 패턴이고 디커플링 비용이 작다.

---

## 2. 일반 Redis 사용 패턴 대조

각 역할에 대해 (a) 본 엔진의 결합 형태, (b) 업계 통상 패턴, (c) 격차를 정리.

### 2.1 캐시 (cache-aside)

본 엔진: cache GET 실패 시 raise → 5xx (fail-close).
통상 패턴: cache GET 실패 시 DB 직접 조회 → 응답 (fail-open). Redis가 다운돼도 사용자는 느린 응답을 받지 5xx를 받지 않음.

```python
# 통상 패턴 (예시)
try:
    cached = await redis.get(key)
    if cached:
        return deserialize(cached)
except RedisError:
    pass  # silent fallback — 캐시 미스로 간주
result = await db.query(...)
try:
    await redis.set(key, serialize(result), ex=ttl)
except RedisError:
    pass
return result
```

격차: 본 엔진은 try/except 없음. RedisError가 그대로 전파.

### 2.2 멱등성

본 엔진: SET NX → 실패 시 raise → DLQ.
통상 패턴:
- (a) outbox 패턴: DB 트랜잭션에 멱등 처리 + ack를 묶음. Redis 안 씀.
- (b) Redis 1단 + DB UNIQUE 2단: 본 엔진과 같음. 단 1단은 best-effort (실패해도 2단이 흡수).
- (c) DB UNIQUE 단독: 가장 단순. 비용은 INSERT 1회 시도 + ON CONFLICT 처리.

격차: 본 엔진의 1단이 fail-close라 Redis 장애 시 2단의 흡수 능력을 활용 못함. (b) 패턴으로 fail-open 처리하면 1단의 성능 이득 + 2단의 안전망을 함께 누릴 수 있음.

### 2.3 PUB/SUB → SSE 트리거

본 엔진: Redis pubsub.
통상 패턴:
- (a) Redis pubsub — 단순, 본 엔진과 동일.
- (b) Postgres LISTEN/NOTIFY — DB가 이미 있으니 추가 인프라 불필요.
- (c) 메시지 큐 fanout — 본 엔진에 이미 RabbitMQ가 있으므로 fanout exchange로 처리 가능. 다만 web이 broker에 새 connection 필요.
- (d) DB polling — web이 주기적으로 `last_metric_at` 조회. 가장 단순하지만 부하·지연 trade-off.

격차: pubsub 자체는 표준. 단 본 엔진은 Redis 전체가 fail-close라 pubsub 끊김도 별도 fallback 없음.

### 2.4 온라인 상태 (TTL 기반 heartbeat)

본 엔진: consumer가 metrics/inventory 처리할 때마다 `SET online:{id} 1 EX 90`. web은 `EXISTS` / `MGET`으로 체크.
통상 패턴:
- (a) Redis TTL heartbeat — 본 엔진과 동일. 표준 패턴 중 하나.
- (b) DB last_seen 컬럼 — 매번 DB UPDATE + 조회 시 `WHERE last_seen > NOW() - INTERVAL '90s'`. 정확하지만 DB 쓰기·읽기 부하.
- (c) 분리된 heartbeat 서비스 — 별도 in-memory store / etcd / Consul.

격차: TTL heartbeat 패턴 자체는 표준. 본 엔진이 특이한 건 목록 화면 N개 서버 mget이 핵심 경로에 들어가 있어 Redis가 빠지면 목록 자체가 안 뜬다는 것. 일반 패턴은 list 화면이 DB만으로도 동작하고 online은 부가 정보.

### 2.5 종합

| 역할 | 결합 형태 | 통상 대비 평가 |
|------|---------|------------|
| 캐시 | fail-close | 이례적 — 표준은 fail-open |
| 멱등성 | fail-close 1단 + 2단 | 이례적 — 표준은 fail-open 1단 (2단이 안전망) |
| PUB/SUB | Redis 전용 | 표준 (단 fallback 없는 건 이례적) |
| 온라인 TTL | Redis 전용 + 핵심 경로 | 표준이지만 list 화면이 의존하는 건 강한 결합 |
| public_id 해석 캐시 | fail-close | 이례적 — 표준은 fail-open |

결론: 패턴 자체는 모두 표준 범주. 다만 "옵션 느낌"이 표준인 곳까지 fail-close로 묶어둔 것이 본 엔진의 특이점이다. 즉 사용자가 느낀 "Redis가 코드 로직에 깊이 관여한다"는 직관은 장애 처리 정책의 결과이지 캐시·pubsub·heartbeat 패턴 자체의 문제가 아니다.

---

## 3. 결합도 분류

리팩토링 선택지를 가르는 기준선:

```
┌────────────────────────────────────────────────────────┐
│ 표면적 결합 — fail-close 정책만 바꾸면 됨               │
│   - 캐시 (inventory / metrics)                          │
│   - public_id 해석 캐시                                 │
│   - 멱등성 1단                                          │
├────────────────────────────────────────────────────────┤
│ 중간 결합 — 대체 인프라 필요 또는 SSE UX 변경 동반      │
│   - PUB/SUB (Postgres LISTEN/NOTIFY 등으로 대체 가능)   │
├────────────────────────────────────────────────────────┤
│ 깊은 결합 — "온라인" 의미·쿼리·스키마 함께 재설계        │
│   - 온라인 TTL (특히 list 화면 mget)                    │
└────────────────────────────────────────────────────────┘
```

---

## 4. 리팩토링 옵션 (그라데이션)

### 옵션 A — 현상 유지 (변경 없음)

범위: 변경 없음.
비용: 0.
얻는 것: 안정성. 운영 단순.
포기하는 것: Redis 단일 장애 시 시스템 5xx (현 상태).
언제 적합: Redis HA(Sentinel/Cluster) 도입이 가까운 시점이라면 fail-close로 두고 인프라로 해결.

### 옵션 B — Cache + 멱등성을 fail-open으로 (작은 변경, 큰 회복력)

범위:
- `query_service.py`의 모든 `redis.get`/`redis.set`을 try/except RedisError로 감싸 silent fallback.
- `_check_idempotent`를 fail-open으로 변경 (Redis 실패 시 True 반환 → DB UNIQUE가 2단으로 흡수).
- consumer의 `redis.delete(cache_key)` / `redis.set(online_key)` / `redis.publish` 도 try/except (실패 시 로그 + 진행).

예상 변경 규모: 대략 LOC 50~100. `src/assessment_engine/db/redis.py`에 helper 추가하는 방식 권장 (`safe_get`, `safe_set` 등).

얻는 것:
- Redis 다운 시 web은 느려질 뿐 응답 가능. consumer는 멱등성 1단을 잠시 잃지만 DB UNIQUE가 흡수 → DLQ 누적 없음.
- T11(단일 인스턴스) 트레이드오프의 가장 아픈 부분(4가지 역할 동시 down) 완화.

포기하는 것:
- fail-open 시 멱등성 1단의 성능 이득 일부 상실 (Redis 다운 동안만). DB UNIQUE 충돌 처리 비용 약간 증가.
- 캐시 fail-open은 SET 실패 시 다음 요청도 캐시 MISS — 일시적 DB 부하 증가.

위험:
- consumer fail-open 도입 시 `online:{id}` SET 실패해도 진행하면 web 표시가 잠시 오프라인으로 보임. UX 측면에서 허용 가능한지 정책 결정 필요.
- 멱등성 fail-open은 at-most-once 보장이 명시적으로 약화됨을 운영자가 알아야 함. T1을 갱신해야 함.

적합도: ★★★ — 가장 적은 비용으로 가장 큰 회복력. 추천 후보.

### 옵션 C — 멱등성을 DB로 일원화 (Redis 1단 제거)

범위:
- `_check_idempotent` 제거.
- DB UNIQUE 2단만으로 운용. 또는 별도 `processed_messages(message_id PK, processed_at)` 테이블 도입.

얻는 것:
- 멱등성 책임이 단일 위치(DB).
- Redis maxmemory 압박 시 멱등성 키 evict 위험(T11 핵심) 해소.

포기하는 것:
- 매 메시지마다 DB INSERT 시도. 1분 주기 N서버 환경이라면 부하 증가 미미하지만, 트래픽 증가 시 영향 큼.
- 1단 빠른 차단의 latency 이득 상실.

적합도: ★★ — 옵션 B로 우선 회복력만 확보 후, evict 가시화되면 도입. 지금 단계에선 오버엔지니어링 가능.

### 옵션 D — PUB/SUB을 Postgres LISTEN/NOTIFY로

범위:
- consumer가 `redis.publish` 대신 `NOTIFY metrics_events, '...'` 발행.
- web이 별도 asyncpg connection으로 `LISTEN metrics_events`.

얻는 것:
- 인프라 1개 의존 감소 (Redis pubsub 역할 제거). 단 Redis는 캐시/멱등성/온라인 TTL로 여전히 필요.
- Postgres 트랜잭션과 NOTIFY가 같은 connection에서 묶여서 DB COMMIT 후 NOTIFY가 atomic — cache-aside race(T2)도 약간 완화.

포기하는 것:
- LISTEN connection이 web 측에 영구 점유 — connection pool에서 1~N개 long-lived 연결 분리 필요.
- NOTIFY payload는 8000 bytes 제한 — 현재 payload는 작아서 영향 없음.

적합도: ★ — 현 단계에서는 이득 작음. Redis를 진짜 빼고 싶을 때(옵션 E의 일부)에만 의미 있음.

### 옵션 E — Redis를 옵션화 (full decoupling)

범위:
- 모든 Redis 의존을 인터페이스 뒤로. `BaseCache`, `BaseEventBus`, `BasePresenceTracker` 등.
- 구현체: `RedisCache` / `InMemoryCache`, `RedisPubSubBus` / `PostgresListenBus`, `RedisPresence` / `DbLastSeenPresence`.
- composition root에서 환경에 따라 선택 주입.
- 온라인 상태를 `server_inventory.last_seen_at` (이미 존재)으로 일원화하고 `_is_online`을 `last_seen_at > now() - 90s`로 재정의.

얻는 것:
- Redis 없이도 전체 시스템 동작. dev/test에서 Redis 컨테이너 불필요. PoC·소규모 배포 비용 절감.
- 인터페이스 우선 원칙(F4)의 자연스러운 확장.

포기하는 것:
- 코드 복잡도 증가 (인터페이스 + 구현체 N쌍).
- `last_seen_at` 기반 online은 매 list 요청에 N개 서버 last_seen 비교 — DB 부하 증가. mget 1회 vs DB WHERE 1회는 비슷하지만 Redis는 메모리, DB는 디스크/캐시.
- 한 번에 너무 많은 변경 — 리팩토링 위험성 큼.

적합도: ★★ — 명확한 동기(예: 운영 인프라에서 Redis 제거 결정)가 있을 때만. 지금은 과대.

---

## 5. 추천안

### 1차 권고: 옵션 B (fail-open 전환)

이유:
- "Redis가 코드 로직에 종속적"이라는 사용자 직관의 핵심 원인이 fail-close 정책. 정책만 바꾸면 코드 결합도는 그대로지만 운영상 회복력은 크게 개선.
- 변경 범위가 좁아 리팩토링 위험이 낮음.
- 통상 Redis 사용 패턴(cache-aside fail-open + 1단 best-effort 멱등성)에 맞춤.

조건:
- T1·T11에 fail-open 전환 사실을 추가 기록.
- consumer fail-open 시 `online:{id}` 임시 누락이 UX에 미치는 영향 검토 (작을 것으로 예상).

### 2차 (조건부): 옵션 C (멱등성 DB 일원화)

조건: 옵션 B 도입 후 운영하면서 다음 중 하나라도 관측되면.
- Redis `INFO stats` `evicted_keys`가 멱등성 키에서 발생 (T11 본문 시나리오).
- 메시지 트래픽 증가로 멱등성 키 수가 maxmemory 압박을 일으키기 시작.

### 3차 이상: 옵션 D, E

조건: 인프라 정책 변경(예: Redis 제거 결정, multi-region 배포)이 별도로 결정되었을 때. 지금 시점에선 불필요.

---

## 6. 옵션 B 단계별 실행 계획 (예시)

추천안 채택 시 적용 순서. 각 단계 독립 PR.

### 단계 1 — Redis helper 도입
`src/assessment_engine/db/redis.py`에 fail-open 래퍼 추가.
```python
async def safe_get(redis: Redis, key: str) -> str | None:
    try:
        return await redis.get(key)
    except RedisError as e:
        logger.warning("redis get failed key={} err={}", key, e)
        return None

async def safe_set(redis: Redis, key: str, value: str, ex: int | None = None) -> bool:
    try:
        await redis.set(key, value, ex=ex)
        return True
    except RedisError as e:
        logger.warning("redis set failed key={} err={}", key, e)
        return False
# delete / mget / publish 도 동일 패턴
```

### 단계 2 — web 측 캐시 fail-open
`query_service.py`의 `get_server`, `get_latest_metric`, `resolve_server_id`, `list_servers` 모두 helper 경유로 변경. 코드 변경 좁음.

### 단계 3 — consumer 측 부수 작업 fail-open
`handler.py`의 `redis.set(online_key, ...)`, `redis.delete(cache_key)`, `redis.publish` 를 helper 경유. 메시지 처리 자체는 진행.

### 단계 4 — 멱등성 fail-open (가장 신중)
`_check_idempotent`이 RedisError 시 True 반환. DB UNIQUE가 2단 안전망 역할을 한다는 가정에 명시적으로 의존. 단계 4 적용 전에 시계열 4개 테이블의 UNIQUE 제약이 모두 정상 동작하는지 검증 필요 (테스트 케이스 수동 작성).

### 단계 5 — 문서 갱신
- `docs/reference/redis.md` "Redis 장애 시 동작" 섹션 갱신: fail-close → fail-open, 단계별 영향 명시.
- `docs/explanation/tradeoffs.md` T1, T11 갱신.
- CLAUDE.md #C3, #D4 갱신: fail-open 정책 반영.

---

## 7. 정리 — 사용자 질문에 대한 답

> Q1. 원래 Redis를 이용한 코드가 이토록 종속적인지, 아니면 보통은 옵션 느낌인지?

보통은 옵션 느낌이 표준이다. 캐시는 fail-open, 멱등성은 1단 best-effort + 2단 안전망, pubsub은 SSE 트리거 정도가 통상. 본 엔진은 패턴 선택은 표준 범주지만 장애 정책이 fail-close로 묶여 있어 결과적으로 종속적으로 보인다.

> Q2. 현재 설계에서 이게 최선인가?

회복력 관점에선 최선이 아니다. 옵션 B로 fail-open 전환하면 코드 변경은 작고 회복력은 통상 수준에 도달한다. 단 fail-close가 의식적 결정이었다면(예: 정확성 우선, HA 도입 예정) 현 상태도 합리적.

깊이 결합된 부분은 온라인 TTL의 list 화면 mget 한 군데. 이건 옵션 E 수준의 큰 변경이 필요하므로 지금 단계에선 손대지 않는 게 합리적이다.

---

## 8. 결정 기록 (decision log)

| 일자 | 결정 | 근거 |
|------|------|------|
| 2026-05-07 | 옵션 B + list mget fallback 채택, 옵션 E 기각 | 옵션 B로 운영 결합도를 통상 수준으로 낮출 수 있음. 옵션 E(인터페이스 추상화)는 Redis를 다른 캐시로 교체할 계획이 없으므로 무의미한 복잡도 증가. |
| 2026-05-07 | 구현 완료 | 변경 파일: `src/assessment_engine/db/redis.py`(safe_* helper 6종 추가), `src/assessment_engine/web/services/query_service.py`(모든 redis 호출 helper 경유 + last_seen_at fallback), `src/assessment_engine/consumer/handler.py`(`_check_idempotent` fail-open + 부수 작업 helper 경유), `src/assessment_engine/db/repositories/outbound.py`·`src/assessment_engine/db/repositories/query_repository.py`(ServerSummary에 last_seen_at 추가). 문서: `docs/reference/redis.md`·`docs/explanation/tradeoffs.md` T1·T11·CLAUDE.md #C3·#D2·#D3·#D4 갱신. |

## 9. 구현 결과 요약

### 추가된 fail-open helper (`src/assessment_engine/db/redis.py`)

| 함수 | 정상 반환 | 장애 시 반환 |
|------|---------|-----------|
| `safe_get(redis, key)` | str / None | None (캐시 MISS와 동일) |
| `safe_set(redis, key, value, ex)` | True | False (다음 요청도 MISS) |
| `safe_set_nx(redis, key, value, ex)` | True/False | None (호출자가 fail-open 결정 — 멱등성 핸들러는 True로 간주) |
| `safe_delete(redis, key)` | True | False (TTL 만료로 자연 회복) |
| `safe_mget(redis, keys)` | list[str\|None] | None (호출자가 fallback 선택 — `list_servers`는 last_seen_at 사용) |
| `safe_publish(redis, channel, msg)` | True | False (SSE 트리거 누락, 브라우저 재연결 시 회복) |

모두 RedisError 캐치 + warning 로그.

### 평시·장애 시 동작 매트릭스

| 호출 지점 | 평시 | Redis 장애 시 |
|---------|------|------------|
| `resolve_server_id` (cache:resolve) | hit/miss | DB 직접 조회 |
| `list_servers` (mget online) | mget 1회 | `last_seen_at > now() - 90s` fallback |
| `get_server` (cache:inventory) | hit/miss | DB 직접 조회 + ViewModel 빌드 |
| `get_latest_metric` (cache:metrics) | hit/miss | DB 직접 조회 + dashboard 빌드 |
| `_is_online` (online EXISTS) | hit/miss | False (단 detail 페이지 collection_status에서만 사용 — 영향 작음) |
| `_check_idempotent` (SET NX) | True/False | True (DB UNIQUE 2단 흡수) |
| consumer online SET | OK | warning + 진행 (list 화면이 last_seen_at fallback로 대체 표시) |
| consumer cache DELETE | OK | warning + 진행 (60/300s TTL 만료로 자연 회복) |
| consumer publish | OK | warning + 진행 (SSE 끊김, 브라우저 재연결) |
| SSE pubsub | listen | RedisError 캐치 후 스트림 종료 |

### 약화된 보장 (운영자 인지 필수)

1. 멱등성 1단의 빠른 차단: Redis 장애 동안 매 메시지가 DB UNIQUE까지 도달. 트래픽 규모에서 영향 미미.
2. list 화면 online 정밀도: `last_seen_at` 기반 fallback은 임계값(90s)이 동일하므로 정밀도 거의 동일. 단 매 list 요청에 N개 행의 `last_seen_at` 직접 비교 부하.
3. `_is_online` (detail collection_status): Redis 장애 시 항상 False. 큰 영향은 없으나 fallback 추가하려면 `last_seen_at` 사용 가능 (현재는 비워둠 — detail 페이지는 list와 달리 N+1 없음).
4. SSE 실시간성: Redis publish 실패 시 SSE 트리거 누락. 브라우저는 SSE 재연결 후 `/metrics/latest` 1회 fetch로 회복하지만 60s 캐시 TTL 만료까지 stale 표시 가능.

### 의존하는 안전망

- DB UNIQUE 제약: `server_metrics(server_id, collected_at)`, `server_disk_io(server_id, device, collected_at)`, `server_net_io(server_id, interface, collected_at)`, `server_mount_usage(server_id, mount, collected_at)` (CLAUDE.md #C1).
- `server_inventory.last_seen_at` 컬럼 (consumer가 inventory·metrics 처리 시마다 갱신).