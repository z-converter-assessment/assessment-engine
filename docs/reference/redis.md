# Redis 전략

정책: AGENTS.md #C3. 캐시·온라인 TTL·멱등성·부가 시그널 상태의 4가지 역할을 한 인스턴스로 처리.
키 패턴은 `WebSettings` 단일 정의(`src/assessment_engine/config.py`). `ConsumerSettings`는 `WebSettings` 상속 — consumer/web 동일 네임스페이스.

---

## 키 설계

| 용도 | 키 | TTL | 무효화 트리거 |
|------|----|-----|-------------|
| public_id 조회 | `cache:resolve:{public_id}` | 없음 (불변) | — |
| 인벤토리 캐시 | `cache:inventory:{server_id}` | 300s | consumer가 새 inventory 저장 시 즉시 DELETE |
| 메트릭 캐시 | `cache:metrics:{server_id}` | 60s | consumer가 새 metrics 저장 시 즉시 DELETE |
| ZDM 패키지 sha256 | `cache:zdm_package:sha256:{host}:{etag}` | 6h | ETag(없으면 Last-Modified) 변경 = 키 변경 |
| 멱등성 | `idempotent:{message_id}` | 24h | TTL 만료만 |
| 온라인 TTL | `online:{server_id}` | 300s | consumer가 inventory·metrics 양쪽에서 매번 갱신 |
| 시계 invariant 경고 쿨다운 | `time_invariant_warned:{agent_id}` | 1h | TTL 만료만 |
| 직전 agent_started_at | `last_agent_start:{server_id}` | 24h | metrics 처리 시 매번 SET (직전 값과 비교 → 재시작 감지) |
| 재시작 카운터 | `agent_restarts:{server_id}` | 1h | `_track_agent_restart` 가 변경 감지 시 증가하고 수명을 1h 로 되감는다 |

### TTL 값 근거

- `online:{server_id}` 300s — 오프라인 판단 임계. 운영 신호 "통신 끊김"(gap_minutes 5분, `redis_ttl_online` = `config.py`)과 단일 진실 — 5분간 inventory·metrics 어느 쪽도 없으면 오프라인. Redis 장애 시 web 은 같은 `redis_ttl_online` 값을 임계로 `last_seen_at` 을 비교한다(`web/services/query/server.py:list_servers` 의 `online_flags is None` 분기).
- `cache:metrics:{server_id}` 60s — metrics 주기와 같다. consumer DELETE 가 없어도 1주기 후 자연 갱신된다.
- `cache:inventory:{server_id}` 300s — inventory 는 변경 빈도가 낮고, consumer DELETE 가 변경을 즉시 반영한다.
- `cache:zdm_package:sha256:{host}:{etag}` 6h — 패키지 교체 빈도가 낮고 무효화는 ETag 가 키로 흡수한다. miss 면 GET stream 으로 sha256 을 재계산한다.
- `idempotent:{message_id}` 24h — message_id 가 UUID v4 라 24h 동안 unique 가 보장되고, broker 재전송 윈도우를 충분히 덮는다.
- `time_invariant_warned:{agent_id}` 1h — 시계 invariant warning 쿨다운이다 (같은 호스트가 매 메시지 warning 을 내지 않게). evict 되면 다음 위반에서 1회 더 출력된다.
- `last_agent_start:{server_id}` 24h — 직전 비교용 캐시다. evict 되면 다음 메시지에서 재시작 감지를 1회 놓치고 그다음 정상 sample 에서 회복한다.
- `agent_restarts:{server_id}` 1h — 증가할 때마다 수명이 되감기므로 재시작 간격이 1h 보다 짧게 이어지면 카운터가 누적되고, 1h 넘게 조용하면 0 부터 다시 시작한다. `agent_restart_alert_threshold` 도달 시 warning 로그를 남긴다.

원격 작업 명령 전달 자체는 Redis 를 거치지 않는다 — 별도 큐 모델 채택으로 broker 가 메시지를 보유하고, DB `tasks` + broker `agent.tasks.<agent_id>` 큐가 단일 진실. 발행 직전 ZDM 패키지 sha256 조회만 위 캐시를 쓴다.

---

## PUB/SUB 채널

사용하는 채널이 없다. 서버 상세 실시간 메트릭과 4탭 현재 상태는 브라우저 polling 으로 `/metrics/latest` 를 재요청한다 — 푸시 메커니즘을 쓰지 않는 근거는 `docs/explanation/tradeoffs.md` T5.

---

## 캐시 무효화 (cache-aside)

consumer 는 저장 성공 후 online 키를 SET 하고 해당 캐시 키를 DELETE 한다 (키·TTL 은 위 "키 설계" 표). TTL 만료를 기다리지 않으므로 서비스·포트·디스크 증감이 다음 detail 요청에 바로 드러나고, 브라우저 polling 의 `/metrics/latest` 재요청은 MISS -> DB 조회 -> 새 캐시 SET 으로 이어진다.

### cache-aside race (알려진 한계)

web 의 `get_latest_metric` 이 cache MISS 후 DB query 를 마쳤지만 SET 을 수행하기 전에 consumer 가 새 metrics 커밋 + DELETE 를 끝낼 수 있다. 이 경우 web 의 SET 이 stale 데이터를 60s TTL 로 캐싱한다.

실용적 영향은 최대 1회 표시 지연이고 다음 polling 주기에 회복한다. exactly-once 캐시 일관성 대신 단순성을 택한 근거는 `docs/explanation/tradeoffs.md` T2.

---

## 효율 패턴

### N+1 회피 — `mget`

서버 목록은 N번 직렬 조회 대신 `safe_mget` 1회로 온라인 플래그를 받는다 — 페이지당 라운드트립이 N 에서 1 로 준다. 장애면 `last_seen_at` 으로 폴백한다.

### 직접 캐시 read/write (read-through)

`get_server`, `get_latest_metric` 이 read-through 패턴이다. 캐시 hit 이면 역직렬화해 반환하고, miss 면 DB 조회 + ViewModel 변환 후 SET 한다.

`cache_serializer` 가 dataclass-JSON serde 를 담당한다. 역직렬화 직후 `enrich_server_detail()` 재호출로 파생 필드 일관성을 유지한다.

---

## 의존성 주입 / 생명주기

### 커넥션 풀 (cache/redis.py)

풀은 `cache/redis.py` 모듈이 단일 인스턴스로 갖고 첫 호출 때 만든다. 각 entry 는 종료 시 `close_pool` 로 닫는다. 풀 옵션은 이 모듈이 갖고 환경변수로 열지 않는다.

- `@cache` 로 감싼 `get_pool()` 하나 — 첫 호출에서 만들고 이후 모든 호출이 같은 풀을 공유한다. import 만으로 설정을 요구하지 않으려는 배치이고, `close_pool()` 이 `cache_clear()` 로 비운다.
- `decode_responses=True` — bytes 가 아닌 str 로 자동 디코딩. JSON 캐시 직렬화/역직렬화 단순화.
- `socket_timeout` / `socket_connect_timeout` — fail-open 경계. 응답 없는 Redis 가 요청을 매달아 두지 않고 이 시간 안에 `RedisError` 로 떨어져 `safe_*` 폴백으로 넘어간다 (#F6 timeout 의무).
- `health_check_interval` + `socket_keepalive` — 방화벽·서버가 idle 소켓을 끊는 환경 대비. 사용 직전 PING 으로 죽은 소켓을 걸러, 실제 장애가 아닌 `ConnectionResetError` 가 fail-open 캐시미스로 새는 것을 막는다. keepalive 는 TCP dead-peer 감지.
- `max_connections` — 소켓 고갈 상한. 라이브러리 기본이 무제한이라 명시하지 않으면 폭주 시 풀이 파일 디스크립터를 다 쓴다.

### 클라이언트 전달 경로

web 은 `RedisDep` 으로 주입받아 `QueryService` 에 넘긴다 (FastAPI DI). consumer 와 worker 는 DI 컨테이너가 없으므로 `get_redis()` 를 직접 불러 각각 핸들러 팩토리와 job 별 `QueryService` 에 전달한다.

세 프로세스 모두 종료 경로에서 `close_pool()` 을 부른다 — 순서 규약은 AGENTS.md #F11.

---

## 설정·운영

### eviction
기본 정책은 TTL 있는 키만 evict 대상으로 삼는다 — TTL 없는 `cache:resolve:{public_id}`(불변 매핑)는 남는다. 멱등성 키도 TTL 이 있어 evict 가능 (T1 트레이드오프 일부). 상한·정책 값은 `REDIS_MAXMEMORY`·`REDIS_MAXMEMORY_POLICY` 로 열려 있고 값 카탈로그는 `docs/reference/contracts/env.md`.

### Redis 장애 시 동작 — fail-open

정책: AGENTS.md #C3 · #F6.

`safe_*` helper 카탈로그: `safe_get`/`safe_set`/`safe_set_nx`/`safe_delete`/`safe_mget`/`safe_incr_with_ttl` (`src/assessment_engine/cache/redis.py`). 정확성 보장은 2단 안전망(DB UNIQUE / DB query / `last_seen_at` 컬럼)에 위임한다.

| 역할 | 평시 | Redis 장애 시 |
|------|------|-------------|
| 캐시 GET (`get_server`/`get_latest_metric`/`resolve_server_id`) | hit/miss | DB 직접 조회 (응답 정상, 느려질 뿐) |
| 캐시 SET | TTL 적용 | silent skip (다음 요청도 MISS) |
| 멱등성 1단 (`_check_idempotent`) | SET NX | True 반환 → 처리 진행 → DB UNIQUE(2단)이 중복 흡수 |
| consumer cache DELETE / online SET | 정상 호출 | 로그만, 메시지 처리 정상 진행 |
| list mget (`list_servers`) | 1회 mget | `last_seen_at` fallback (`redis_ttl_online` 임계와 동일) |
| ZDM 패키지 sha256 (`HttpZdmPackageResolver`) | ETag 키 hit 시 재계산 생략 | 매 발행마다 GET stream 재계산 (install 발행은 정상) |

약화되는 것은 부하와 온라인 판정 정밀도뿐이다. 멱등성 1단이 막던 중복 INSERT 는 매번 DB 까지 내려가 UNIQUE 충돌로 흡수되고, 실시간 폴링은 매 요청이 캐시 MISS 로 DB 를 친다. 목록 화면 온라인은 Redis 300s TTL 대신 DB `last_seen_at` 비교라 정밀도는 거의 같고 N행 비교 부하만 붙는다.

약화되지 않는 것은 데이터 정확성이다. 멱등성 fail-open 은 1단 차단을 잃지만 시계열 metric 7개 테이블의 `(server_id, [dim,] collected_at)` UNIQUE 제약이 중복 INSERT 를 silent no-op 으로 흡수한다.

Redis 는 강결합 방지용 — 장애 시 fail-open, 진실은 DB. 옵션 비교와 한계는 `docs/explanation/tradeoffs.md` T11.

---

## 운영 / 디버깅

```bash
docker compose exec redis redis-cli
SCAN 0 MATCH 'online:*' COUNT 100              # 운영은 SCAN (KEYS는 블로킹)
TTL idempotent:<uuid>                          # 양수=남은초, -1=TTL 없음, -2=키 없음
DEL cache:metrics:1                            # 강제 무효화 (디버그)
INFO memory | grep -E "used_memory|maxmemory"  # 사용량
INFO stats | grep evicted_keys                 # evict 누적 (T11: 멱등성 보장 약화)
```

| 증상 | 원인 |
|------|------|
| 새 inventory 반영 안 됨 | consumer cache DELETE 실패 — `DEL cache:inventory:{id}` 수동 |
| 같은 message_id 중복 행 | Redis 키 만료/evict — DB UNIQUE 2단이 흡수, 중복 행 없으면 정상 |
| 온라인 뱃지 깜빡임 | TTL(300s) 안 inventory·metrics 5분 연속 미수신 — 에이전트 다운·네트워크 단절 확인 |
| 실시간 메트릭 갱신 안 됨 | 브라우저 polling 이 `/metrics/latest` 재요청 — 네트워크 탭에서 주기 요청 확인 |