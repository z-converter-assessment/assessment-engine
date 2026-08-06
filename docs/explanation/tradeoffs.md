# 설계 트레이드오프

의식적 설계 선택과 그로 인한 한계 카탈로그. 단순성·운영 비용·scope 기준 결정 — 버그 아님.

각 항목 형식: 선택 / 대안 / 트레이드오프 / 언제 다시 봐야 하는가.

## 위치·역할

- 본 파일 — 결정의 의도된 한계 카탈로그. 영구·갱신, 항목 추가 자유. cross-cutting reference 라 어느 카테고리 (architecture · development · operations · products) 에도 안 속하는 직속 위치.
- 각 항목은 현재 상태만 선언한다 — 의도된 한계와 그 근거를 기술하고, 결정에 이르게 된 이력 서사는 담지 않는다.

새 항목 추가 시: 본 파일 다음 T 번호 + 항목 작성. 삭제된 번호는 재사용하지 않는다.

---

## T1. 멱등성: at-most-once + 2단 방어 (fail-open 1단)

> 관련 코드: `src/assessment_engine/consumer/handlers/` `_check_idempotent`, `src/assessment_engine/cache/redis.py` `safe_set_nx`, `src/assessment_engine/db/repositories/collect_sql.py` `record_metrics`
>
> 관련 문서: CLAUDE.md #D2 · #C1

선택
1. Redis `SET idempotent:{message_id} 1 EX 86400 NX` (DB 커밋 이전). fail-open — Redis 장애 시 처리 진행.
2. 시계열 metric 7테이블에 `(server_id, [dim,] collected_at)` UNIQUE + `pg_insert.on_conflict_do_nothing`. Redis 장애·evict로 1단이 깨져도 2단이 silent no-op으로 흡수.

대안
- at-least-once + outbox 패턴: DB 커밋과 message ack를 동일 트랜잭션에 묶고, ack 실패 시 outbox 테이블에서 재처리. 메시지 유실 0건 보장.
- at-least-once + 자연키 멱등 INSERT만: SET NX를 빼고 DB UNIQUE만으로 중복 차단. 메시지 유실은 막지만 중복 처리 비용 발생.

트레이드오프
- 얻은 것: 가장 빠른 중복 차단 (Redis 1회 RTT), 단순한 구현 (outbox 테이블·트랜잭션 동기화 불필요). Redis 장애 시 시스템 회복력 확보 (fail-open).
- 포기한 것:
  - at-most-once 한계: SET NX 후 DB 커밋 전 프로세스가 크래시하면 RabbitMQ 재전송 메시지가 idempotent 키 충돌로 silent 드롭 -> 데이터 유실 가능. 1단(Redis)이 먼저 차단하므로 2단(DB UNIQUE)도 이 시나리오는 해결 못 함.
  - fail-open의 비용: Redis 장애 동안 1단의 빠른 차단(RTT)이 사라지고 매 메시지가 DB UNIQUE까지 도달. 트래픽 규모에서 영향 미미하지만 트래픽 증가 시 재평가 필요.

왜 받아들였나
- 1분 주기 metrics는 1건 유실의 시각화 영향이 작음 (다음 사이클에서 회복).
- inventory는 에이전트 재시작 시 재발행 (one-shot 보장이 약하지만 운영상 충분).
- B2B 내부 포털이라 통계 정확성보다 간결한 운영을 우선.
- fail-open은 2단(DB UNIQUE)의 흡수력에 명시적으로 의존 — 시계열 metric 7테이블 UNIQUE 제약이 정상 동작해야 함.

언제 다시 봐야 하는가
- exactly-once 보장이 계약상 필요해질 때 (감사 로그·과금 연동 등).
- consumer 프로세스 크래시가 자주 관찰될 때.
- -> outbox 패턴으로 전환.

---

## T2. 캐시 일관성: cache-aside (write-around)

> 관련 코드: `src/assessment_engine/web/services/query/metric.py` `get_latest_metric`, `src/assessment_engine/consumer/handlers/` metrics handler
> 관련 문서: CLAUDE.md #D1

선택
- Web: cache MISS -> DB query -> `SET cache:metrics 60s`
- Consumer: DB COMMIT -> `DEL cache:metrics`

대안
- write-through: consumer가 DB COMMIT 후 직접 ViewModel을 빌드해 Redis에 SET. cache MISS 자체가 발생 안 함.
- read-through with version: cache 키에 version stamp를 두고 SET 전에 다시 한 번 비교.

트레이드오프
- 얻은 것: consumer가 web의 ViewModel/직렬화 로직을 모름 — 계층 결합도 낮음. 캐시 SET 책임이 단일 위치(web).
- 포기한 것: cache-aside race — web이 cache MISS 후 DB query를 마쳤지만 SET을 수행하기 전에 consumer가 새 metrics 커밋 + cache DELETE를 끝낼 수 있다. 이 경우 web의 SET은 stale 데이터를 60s TTL로 캐싱.

왜 받아들였나
- 브라우저 30초 polling 이 다음 주기에 다시 fetch 하므로 stale 캐시는 최대 1회 표시 지연.
- 메트릭 자체가 60s 주기라 60s TTL stale은 실용적 영향이 작음.
- write-through는 consumer가 web 로직을 알게 되어 컴포넌트 경계 위반.

언제 다시 봐야 하는가
- 메트릭 주기가 분 단위 미만으로 짧아지거나, stale 표시가 비즈니스 영향(잘못된 알람) 일으킬 때.
- -> version stamp 또는 single-flight 캐시.

---

## T3. 시계열 raw 무한 누적 (retention 정책 없음)

> 관련 코드: `src/assessment_engine/db/models/server_metrics.py`, `src/assessment_engine/db/models/server_disk_io.py` 등
> 관련 문서: CLAUDE.md #C1 · #C5

선택
- TimescaleDB hypertable 의 raw 메트릭을 무기한 보존. 무거운 집계는 5분 버킷 continuous aggregate 가 흡수하고(#C5), 만료 정책만 두지 않는다.

대안
- retention policy: `add_retention_policy('server_metrics', INTERVAL '90 days')`로 오래된 청크 자동 drop.

트레이드오프
- 얻은 것: 가장 단순한 운영. 모든 raw 데이터를 영구 보존해 사후 분석 자유도 높음.
- 포기한 것: 디스크 사용량 무한 증가. 차트(`metric_trend`, 동적 버킷)는 목적상 raw 조회라 range 가 길수록 스캔량 증가.

왜 받아들였나
- 소규모 dev 환경 서버 수와 1분 주기에서는 1개월 데이터가 ~130k행/서버. 운영 부담 미미.
- B2B 내부 포털이라 retention 요구사항이 명확하지 않음.

언제 다시 봐야 하는가
- 등록 서버가 100대 이상으로 증가할 때.
- 30일 차트 응답 시간이 200ms를 초과할 때.
- -> 90일 retention policy 도입.

---

## T5. 실시간 메트릭 전달: 30초 polling

> 관련 코드: `src/assessment_engine/web/services/query/metric.py` `get_latest_metric`, `src/assessment_engine/web/static/js/pages/detail.js`, `chart-utils.js` `initAutoRefresh` (4탭 공용)

현황
- 서버 상세 실시간 메트릭과 4탭(cpu/memory/storage/network) 현재 상태는 브라우저 30초 polling(`setInterval`)으로 `/metrics/latest` 를 재요청한다. Redis PUB/SUB(`metrics.events`) 푸시·SSE EventSource 메커니즘은 사용하지 않는다.

선택
- 환경 실시간 갱신과 동일한 30초 polling 으로 통일. consumer 메트릭 후처리는 DB 저장 + `online:{id}` SET + `cache:metrics` DEL 까지만 (publish 없음).

트레이드오프
- 얻은 것: pubsub 채널·SSE 스트림 핸들러·구독 클라이언트 관리 제거 — 메커니즘 단일화로 단순. web 이 자기 server_id 외 메시지를 수신·필터링하던 부하 소거.
- 포기한 것: 푸시 즉시성 — 최대 30초 표시 지연. 갱신 없는 구간에도 주기 요청 발생.

왜 받아들였나
- 메트릭 자체가 60s 주기라 30초 polling 으로 충분. B2B 내부 포털 규모(수십 대)에서 polling 트래픽 무시 가능.
- SSE 단일 채널 필터링·Redis pubsub keyspace 비용·브라우저 자동 재연결 처리 등 push 경로 복잡도 전부 제거.

언제 다시 봐야 하는가
- 초 단위 즉시성이 필요해지거나 polling 트래픽이 web 부하로 드러날 때.
- -> SSE 또는 WebSocket push 재도입 (별도 ADR).

---

## T6. 클라이언트 차트 JS는 P3 명시 예외 (P4)

> 관련 코드: `src/assessment_engine/web/static/js/pages/`, `src/assessment_engine/web/static/js/chart-utils.js`
> 관련 문서: CLAUDE.md #E1 P4, `docs/reference/web/static-assets.md`

선택
- 차트 JS에 그리드 계산·라벨 포매팅·Chart.js 옵션 조립 등의 연산을 허용 (P3 위반).
- 대신 5개 의무 규약 적용: sequence counter, capture-before-await, `Array.isArray`, 404 분기, suggestedMax 명명 상수.

대안
- 서버 사이드 SVG/PNG 차트: matplotlib·plotly로 서버에서 이미지 생성. 클라이언트는 표시만.
- WebComponent + 프레임워크: Vue/React로 컴포넌트화하고 stale 응답을 컴포넌트 lifecycle로 관리.

트레이드오프
- 얻은 것: range 토글·anchor 변경에 즉시 반응 (서버 라운드트립 0). Chart.js UMD 빌드를 `static/js/vendor/` 에 vendoring 해 정적 서빙하므로 번들러·외부 CDN 없이 동작 (차트 쓰는 페이지가 개별 로드).
- 포기한 것: 차트 JS가 P3 우회로가 될 가능성 — 임계값 분류·통계 재계산 같은 비즈니스 로직이 슬며시 들어올 수 있다. 5개 규약 누락 시 race condition·404 오인 등 미묘한 버그.

왜 받아들였나
- 동적 인터랙션이 필요한 차트가 ~10개. 서버 사이드 이미지 차트는 인터랙션 비용 큼.
- 프레임워크는 빌드/배포 파이프라인 도입 비용이 본 포털 규모와 맞지 않음.

P4 5 의무 규약(a~e) 적용 위치: 차트 페이지 JS 본문은 `static/js/pages/` 아래 외부 .js. 페이지 .html은 Jinja2 변수 정의(`SERVER_ID`, `CPU_CORES`) + `defer` 로드만. 파일 카탈로그는 `docs/reference/web/static-assets.md` 단일 진실.

---

## T7. 에이전트 broker 자동 재연결

> 관련 코드: `assessment-agent/src/publish.c`, `src/main.c` (외부 레포)

구현
- `publish.c`: 매 publish가 fresh connection lifecycle (`amqp_new_connection` -> `socket_open` -> `login` -> `channel_open` -> publish -> close -> destroy). connection 재사용 안 함.
- `publish.c`: publisher confirm 모드 활성화 (`amqp_confirm_select(conn, 1)`) + wall-clock deadline ack 대기 (`wait_confirm`, 기본 5초 `RABBITMQ_CONFIRM_TIMEOUT_SEC`).
- `main.c` `publish_with_retry`: 지수 백오프 (1s -> 2s -> 4s -> ... -> max=AGENT_INTERVAL_SEC, 기본 60s) 무한 retry. `g_stop` 시그널 전까지.
- 복구 시 `PUBLISH_RECOVERED` error 메시지 자동 발행 (`retry_count`, `first_failed_at`, `recovered_at` 포함).

broker 재기동 시 자동 회복
- broker 죽음 -> 에이전트 publish 실패 -> 백오프 retry 시작 -> broker 살아남 -> 다음 retry 사이클에서 publish 성공 -> `PUBLISH_RECOVERED` 알림 메시지 -> 정상 운영 복구.
- `systemctl restart assessment-agent` 불요.

inventory 비어 있는 데이터베이스로 metrics가 도착하면 1시간 주기 inventory 재발행 + 엔진 auto-register(`src/assessment_engine/consumer/handlers/`)로 해결.

남은 한계
- broker 영구 down 시 에이전트는 백오프 상한(60s) 간격으로 영원히 재시도 — 정상 동작이나 로그·CPU 미세 부담.

---

## T8. ListServers ORM 부분 SELECT vs full row

> 관련 코드: `src/assessment_engine/db/repositories/query/server_sql.py` `list_servers`

선택
- `select(ServerInventory.<컬럼>)`로 목록 화면이 쓰는 컬럼만 명시 SELECT — 큰 JSONB(`services`·`listen_ports`·`net_interfaces`) 제외. 서비스 뱃지는 ingest 사전계산 `service_categories`(text[]) 소비라 `services` JSONB 역직렬화가 필요 없다. 정확한 컬럼 목록은 코드가 단일 진실.

대안
- `select(ServerInventory)` 풀로우 SELECT.
- ORM lazy loading + 필요한 속성만 접근.

트레이드오프
- 얻은 것: 페이로드 축소 — 큰 JSONB 컬럼(`services`, `listen_ports`, `net_interfaces`)을 페이지당 N개 행에서 직렬화하지 않음.
- 포기한 것: 컬럼 추가 시 list 화면에 노출하려면 SELECT 목록을 함께 갱신 (DRY 위반 작은 케이스).

왜 받아들였나
- list 화면은 페이지당 20개 서버 — JSONB 컬럼 직렬화/네트워크 비용이 작지 않음.
- 컬럼 추가 빈도가 낮고, 추가 시 mapper도 함께 변경되는 게 자연스러워 SELECT 갱신을 잊을 가능성 낮음.

언제 다시 봐야 하는가
- ServerInventory에 컬럼이 자주 추가될 때.
- -> ORM 컬럼 그룹(`deferred()` 또는 별도 entity) 도입 검토.

---

## T9. 차트 공통 JS — 전역 `ChartUtils` 단일 로드 (모듈 시스템 없음)

> 관련 코드: `src/assessment_engine/web/static/js/chart-utils.js`, `src/assessment_engine/web/main.py` `StaticFiles` 마운트, `src/assessment_engine/web/static/js/pages/`

선택
- 차트 페이지 공통 정의(시각 포매터·버킷 그리드·색·range 토글 등)는 `/static/js/chart-utils.js` 한 곳. `base.html`에서 단일 로드 -> 전역 `ChartUtils` IIFE 객체 노출. API 카탈로그는 `docs/reference/web/static-assets.md` 단일 진실.
- 각 페이지 .js 는 상단에서 `const { ... } = ChartUtils;`로 destructure.

대안
- 페이지별 사본: 각 페이지 .js 가 공통 정의를 자기 안에 둠. 빌드 도구 불필요, 한 파일만 보면 모든 로직 파악.
- 번들러 도입 (Vite/esbuild + ESM): import/export로 모듈화. 트리쉐이킹·타입체크 가능.
- WebComponent: 차트를 컴포넌트로 분리하고 props/이벤트로 인터랙션.

트레이드오프
- 얻은 것: 공통 정의가 한 곳 — 시그니처 변경이 1곳 수정으로 끝난다. 페이지 .js 에는 차트 데이터셋 빌드 로직만 남는다.
- 포기한 것: 모듈 시스템(import/export) 없음. `ChartUtils.X` 또는 destructure 형태로만 노출. 의존 그래프가 명시적이지 않음.

왜 받아들였나
- 본 포털은 번들러 운영 비용(node_modules·빌드 스텝·소스맵·CI 변경) 대비 이득 작음.
- IIFE + 단일 로드는 브라우저 캐싱 친화적이고 디버깅이 단순.

정적 자원 형태 (외부화 배치 자체는 T6):
- 번들 없이 브라우저가 파일을 그대로 받는다 — 빌드 산출물 0. node 는 dev/CI 전용(타입 계약 검사·codegen)이라 런타임 의존이 아니다. 타입 계약 메커니즘은 `docs/reference/web/type-contract.md` 단일 진실.

언제 다시 봐야 하는가
- 모듈 그래프가 커져 전역 `ChartUtils` 노출로 의존을 못 따라갈 때.
- -> Vite/esbuild 번들 도입 — `app.mount("/static", ...)`을 `dist/`로 변경.

---

## T10. ViewModel 비대화 vs 클라이언트 재계산 (P2 따름)

> 관련 코드: `src/assessment_engine/web/view_models/`, `src/assessment_engine/web/services/mappers/`, `src/assessment_engine/web/services/mappers/metrics_calculator.py`
> 관련 문서: CLAUDE.md #E1 P2 · #E3, `docs/reference/web/view-models.md`

선택
- `ListenPortItem.is_significant`
- `ServerDetailResponse.sorted_services` / `sorted_listen_ports` (mapper 정렬 결과)
- `MountUsageItem.badge_class` / `bar_color` (임계값 -> CSS 클래스/hex)
- `MemSnapshot.cached_pct` / `buffers_pct` (stacked-bar 누적 비율)

-> 이 파생 필드를 모두 mapper에서 미리 계산해 ViewModel에 둠. 템플릿/JS는 read-only.

대안
- 클라이언트 재계산: 템플릿이 `{% if p.port < 49152 %}` / `| sort` / 임계값 `{% if pct >= 90 %}`, JS가 `mem.cached_kb / total * 100`. ViewModel은 raw에 가깝게 유지.

트레이드오프
- 얻은 것: P2·P3 정합 — 템플릿/JS는 표시만. 임계값/정렬 규칙 변경 시 mapper 한 곳만 수정. 캐시된 ViewModel과 SSR 직후 ViewModel이 항상 동일한 표현 결과를 만듦.
- 포기한 것: ViewModel 필드 수 증가 — `ServerDetailResponse` 는 raw 필드 위에 mapper 파생 필드가 덧붙는다. dataclass 필드 순서 제약(`non-default follows default`)으로 default factory 필드를 끝으로 모아야 함. 캐시 직렬화 페이로드 미세 증가.

왜 받아들였나
- 임계값 변경(예: 90 -> 85)이 발생할 때 클라이언트·서버 분산이면 한쪽 누락 가능성 큼. 본 포털은 임계값 정책이 향후 조정 가능성 있음.
- 캐시 페이로드 증가는 정렬·boolean 정도라 실측상 무시 가능.

언제 다시 봐야 하는가
- ViewModel 필드가 50개 이상으로 비대해질 때 — 화면별 sub-ViewModel로 분리.

---

## T11. 단일 Redis 인스턴스 — 모든 용도 한 통합 (fail-open)

> 관련 코드: `src/assessment_engine/cache/redis.py` `safe_*` helper, `src/assessment_engine/consumer/handlers/`, `src/assessment_engine/web/services/query/`
> 관련 문서: `docs/reference/redis.md`

선택
- 한 Redis 인스턴스가 캐시·온라인 TTL·멱등성·시그널 쿨다운을 모두 담는다 (용도별 키·TTL 카탈로그는 `docs/reference/redis.md`).
- eviction 정책 `volatile-lru` (TTL 있는 키만 evict 대상).
- 모든 Redis 호출은 `src/assessment_engine/cache/redis.py`의 `safe_*` helper 경유 — fail-open 정책.
- list 화면 online 표시는 `last_seen_at` 컬럼 fallback 보유.

대안
- 분리: 캐시용 / 멱등성용 인스턴스 분리.
- 외부 시스템: idempotency를 PostgreSQL `INSERT ... ON CONFLICT DO NOTHING` 으로 (멱등성 키 테이블).
- 인터페이스 추상화 (옵션 E): `BaseCache`/`BaseEventBus`/`BasePresenceTracker` 추상으로 Redis 자체를 옵션화. Redis를 다른 캐시로 교체할 계획이 없으므로 채택 안 함.

트레이드오프
- 얻은 것:
  - 운영 단순. docker compose 1개 컨테이너. 코드의 `get_redis()` 1개 함수.
  - fail-open 정책으로 운영 결합도가 통상 수준 도달 — Redis 장애 시 web은 느려질 뿐 응답 가능, consumer는 DLQ 누적 없이 처리 진행.
- 포기한 것:
  - 멱등성 키도 `volatile-lru` 대상 — maxmemory 압박 시 evict 가능 -> 1단 방어 깨짐. fail-open과 동일하게 DB UNIQUE(2단)이 흡수 (T1과 연결).
  - Redis 단일 장애 시 모든 역할(online 판정·캐시·멱등성) 영향 — 단 fail-open으로 시스템 다운은 회피. 멱등성 1단은 우회(DB가 흡수), 캐시는 DB 직접 조회, online 은 폴링 데이터 신선도로 자연 회복.

왜 받아들였나
- B2B 내부 포털 — 동시 요청 수·멱등성 키 수가 작아 evict 시나리오 드묾.
- 단일 인스턴스로 운영 비용 최소화.
- Redis를 다른 캐시 시스템으로 교체할 계획 없음 — 인터페이스 추상화는 무의미한 복잡도 증가.

언제 다시 봐야 하는가
- 멱등성 키 evict가 실제 관찰될 때 (Redis `INFO stats` `evicted_keys` 모니터링).
- -> 멱등성을 PostgreSQL 테이블로 옮기거나, Redis를 namespace별로 분리.

---

## T12. server_inventory 호스트 식별 — 불변 agent_id 단독 UNIQUE

> 관련 코드: `src/assessment_engine/db/models/server_inventory.py`, `src/assessment_engine/db/repositories/collect_sql.py`
> 관련 문서: CLAUDE.md #C1, `docs/reference/db/models.md`, `docs/reference/contracts/agent-data.md`

선택 (현행)
- `server_inventory` UNIQUE = `agent_id` 단독. agent_id 는 agent 가 첫 실행 시 1회 생성·영구저장하는 불변 UUID — 부팅마다 NIC MAC 이 재발급되는 환경(OpenStack Windows VM)에서도 동일 agent_id 가 자연히 같은 행을 upsert 한다. 별도 재연결 로직 없음.
- `composite_id`(SHA-256(machine_id + 정렬·dedup MAC 들), nullable)·`machine_id`(raw, nullable) 는 clone collision 진단용 감사·표시 컬럼 — 식별·라우팅 미사용. hostname 은 display (UNIQUE X).
- MQ queue `agent.tasks.{agent_id}` / routing key `task.install.{agent_id}`.

대안 (채택 안 함)
- composite_id 단독 UNIQUE: machine_id + MAC 해시로 식별. 부팅마다 MAC 이 재발급되는 환경에서 값이 변동해 같은 호스트가 새 행으로 갈라진다 — 이를 흡수하려면 machine_id+hostname 재연결 로직이 필요하고, 미sysprep clone 오병합 위험이 잔존한다. 불변 agent_id 는 변동원 자체를 제거하므로 재연결 불요.
- hardware UUID (`/sys/class/dmi/id/product_uuid`) 우선 + machine-id fallback: VM clone 시 hardware UUID 도 동일 가능. agent 변경 필요.
- 운영자 부여 server_id (install 시 운영자가 UUID 주입): install workflow 에 등록 step 추가.

트레이드오프
- 얻은 것:
  - VM 템플릿 복제·이미지 clone·container `/etc/machine-id` 마운트로 machine_id·MAC 가 겹치거나 재부팅마다 변동해도 agent_id 단독 UNIQUE 라 식별 안정. hostname 변경도 같은 행 유지 (agent_id 불변).
  - agent 가 자기 agent_id 로 queue subscribe — 라우팅 키가 불변이라 재부팅·MAC 변동에도 큐 안정. 두 호스트가 같은 큐를 공유하는 message race 없음.
- 포기한 것:
  - agent_id 저장소(첫 실행 시 생성한 UUID)를 잃으면 (디스크 초기화·agent_id 미보존 re-image) 새 agent_id 가 발급돼 새 row INSERT 로 history 끊김. 같은 호스트가 둘로 분리.
  - agent_id 파일까지 그대로 복제한 clone (미regenerate) 은 두 호스트가 동일 agent_id 를 공유해 같은 행으로 오병합. composite_id/machine_id 감사 컬럼이 collision 을 진단용으로 노출하나 자동 분리는 안 함.

왜 받아들였나
- B2B 내부 포털 — agent_id 저장소 소실·미regenerate clone 은 둘 다 운영에서 드문 예외. 흔한 케이스(재부팅 MAC 변동·hostname 변경)는 agent_id 불변으로 자연 흡수.
- agent_id 생성·영구저장은 agent 와 이미 합의된 계약 — 엔진은 이 단독 키로 식별·라우팅을 완결하고 payload 추가 합의가 필요 없다.

언제 다시 봐야 하는가
- agent_id 미regenerate clone 오병합이 운영에서 관측될 때 -> composite_id 기반 collision 자동 분리 또는 agent 측 clone 감지(sysprep 유도).
- agent_id 저장소 소실로 history 끊김이 잦으면 -> agent 측 저장 위치 견고화 또는 보조 식별자 병합 절차.

---

## T13. 보고서 = diagnostic_jobs 통합 (job_type)

> 관련 코드: `src/assessment_engine/db/models/diagnostic_job.py`, `src/assessment_engine/web/services/diagnostic_service.py`
> 관련 문서: CLAUDE.md #C1, `docs/reference/db/models.md`

선택
- `diagnostic_jobs.job_type` 컬럼 (`customer_report`/`engineer_report`) — 보고서 발행이 본 테이블에 row 저장 (이력 보존).
- 양식 분리:
  - server scope (`/reports/servers?ids=...`): row 단위 상세, 양식 A/B (`servers/report.html`).
  - environment scope (`/reports/environment`): high-level (KPI·USE Method 분류 도넛·Top N risk·OS 분포·view별 정성 요약, `reports/environment.html`). 전체 등록 서버 자동, `EnvironmentReportSummary` view_model + `mappers.environment_report`.
- 두 라우터 모두 `enqueue_report` 로 job 을 등록하고 즉시 `?job={id}` 로 돌려보낸다 — 스냅샷 생성은 전용 워커가 맡는다.
- 보고서 이력 (`/reports/history`) 페이지 — customer + engineer union + view 필터 select. 서버 목록에서 진입점 지원 (선택 N대 버튼 + 환경 카드 link).
- view(고객/엔지니어)는 발행 시점에 고정된다 — 스냅샷 1건이 view 1개를 담고, 다른 view 는 별도 발행이다.

대안
- 보고서를 별도 테이블 `report_jobs` 로 분리 — 모델 명확하나 두 테이블 간 통합 표시 SQL union 복잡. job_type 단일 분기로 충분.

트레이드오프
- 얻은 것:
  - 보고서 발행 이력 단일 페이지에서 통합 추적.
  - 모델 통합 — 보고서별 별도 service·테이블 신설 없이 기존 diagnostic_jobs 재사용.
- 포기한 것:
  - 발행 1회가 row 1건(N대 선택은 parent 1 + child N) — 같은 입력 더블클릭은 active partial UNIQUE 로 기존 job 에 합류하나, 시각·view 를 바꿔 반복 발행하면 이력 row 가 선형 증가. `delete_retention` 은 리포지토리에 있으나 호출하는 실행 경로가 없어 오래된 row 가 그대로 남는다.
  - 양식(템플릿) 변경 시 옛 스냅샷은 발행 당시 구조 그대로라 현행 양식과 어긋날 수 있다. 스냅샷에 양식 버전 태깅은 미적용.

왜 받아들였나
- 보고서 발행은 운영자가 명시 액션 (선택 N대 -> 보고서 버튼) — 매 발행이 의미 있는 이벤트라 이력 row 로 남겨도 OK.
- 스냅샷 보존이 목적이라 재조회는 raw data 변경과 무관하게 발행 시점을 그대로 재현.

언제 다시 봐야 하는가
- 이력 row 가 폭증하면 -> worker 에 retention purge tick 추가(`delete_retention` 주기 호출).
- 양식 변경으로 옛 스냅샷과 현행 템플릿 불일치가 문제가 되면 -> 스냅샷에 양식 버전 태깅.

## T14. Windows saturation 임계 근거 비대칭 + perflib 의존

right-sizing 분류는 USE Method 의 Utilization + Saturation 두 축을 본다. saturation 3축 모두 OS별 실측 신호로 정규화된다(os-aware helper 단일 진실) — CPU 포화는 Linux loadavg / Windows Processor Queue Length, 메모리 포화는 Linux swap page-out / Windows Memory Pages Input/sec rate, 디스크 IO 포화는 Linux iowait / Windows Avg Disk Queue Length. Windows 도 세 포화 축을 실측하되, 신호원과 임계 근거의 성숙도가 다르다.

- 받아들인 한계:
  - Windows 메모리 포화는 Memory\Pages Input/sec(하드 read 폴트, mmap 미혼입) rate p95 >= 20 로 판정한다. 총 Pages/sec 은 mmap 파일 I/O 혼입으로 부풀려져(관측 82775) 미사용 — 하드폴트만 세는 Pages Input/sec 가 순수 압박 신호. 임계 20 은 Microsoft/업계 관례(5=증설·20=체감 저하·100=thrashing)의 '체감 저하'(`WIN_PAGES_INPUT_SATURATION`). 잔여 한계: 고정 임계라 워크로드별 미세 편차 가능.
  - saturation 축은 perflib/diskperf 의존이다 — Windows 에서 해당 카운터를 못 읽거나 미부착(예: OpenStack virtio 에 diskperf 미부착 -> disk queue 빈 배열)이면 그 축만 미관측이 된다. 분류는 utilization·capacity·측정된 나머지 포화 축으로 완결하고, 못 본 축만 "포화 수치 미관측" confidence 단서로 노출.
  - Windows pagefile 사용량(swap_used)은 수집·표시하되 saturation 판정엔 미반영 — pagefile 은 여유 RAM 에도 상시 baseline 이라 사용량이 아닌 페이징 rate 로 판정(P2 의도).

왜 받아들였나
- Windows 가 노출하지 않는 신호를 0/baseline 으로 날조해 분류에 넣으면(예: iowait=0 을 "IO 여유"로) 더 큰 왜곡 — 미측정은 미측정으로 두는 게 정직(P1).
- disk queue·CPU run queue·메모리 하드폴트(Pages Input/sec) 세 축 모두 Microsoft/업계 관례 임계로 근거 추적 가능.
- "부분 평가" 마커가 운영자에게 confidence 한계를 명시 — 침묵하는 오분류보다 가시화된 한계가 낫다(P4).

언제 다시 봐야 하는가
- Windows 메모리 페이징 오탐/누락이 관측되면 -> 실측 분포로 `WIN_PAGES_INPUT_SATURATION` 재보정 (현재 20 pages-input/sec, Microsoft '체감 저하' 관례).
- perflib 미발행이 특정 Windows 환경에서 상시화되면 -> agent 측 수집 경로 점검 (엔진은 미관측으로 정직 처리, 신호원 자체는 agent repo 이슈).

## T15. 서비스 분류 — pid 부재 유닛의 per-unit 귀속 한계 (호스트 union 으로 보완)

agent 는 `services[]` 에 pid/exe 를, `listen_ports[]` 에 pid/comm 을 싣는다. 양쪽에 pid 가 있으면 `_attributed_ports` 가 동일 pid 소켓만 귀속해 per-unit 분류(`classify`)가 확정된다. pid 가 null 인 구간(소켓 액티베이션 리스너·비-systemd 열거·권한 부족 Windows 포트)에서만 `comm~name` substring -> name well-known 포트 순 fallback 이라, 이름이 comm 과 무관한 opaque 서비스를 그 구간에서 per-unit 으론 못 잡는다.

pid 부재 구간 보완 — 호스트 워크로드 union:
- 뱃지/role/환경분포는 per-unit 분류에 의존하지 않고, `detect_listen_categories(listen_ports)` 로 listen 소켓을 직접 분류(comm/port)해 services 이름 분류와 합집합(`workload_category_counter`)한다. listen 소켓의 comm·port 는 깨끗·안정 식별자라 opaque 이름을 우회 — `MSSQL$무엇` 이든 1433/`sqlservr` 로 db 탐지.

- 포기한 것(union 후 잔존):
  - listen 안 하거나 localhost-only 바인드 워크로드 + opaque 이름 = 두 소스 모두 못 잡아 미상. (listen 하는 워크로드는 union 으로 거의 구제됨.)
  - pid 미발행 유닛에 한해 services 탭의 행별 카테고리가 이름 기반 best-effort — opaque unit 은 그 행에서 unknown (호스트 뱃지는 union 으로 db 표시되어도).

  호스트 union 은 ingest 시 1회 계산(`compute_service_categories`)해 `server_inventory.service_categories`(text[])에 저장 — 목록·상세·리포트·필터가 동일 저장값 소비라 화면 간 카테고리 집합 비대칭 0(목록은 listen_ports 재로드·행별 재분류 없이 경량 유지, #E7). 남는 한계는 위 pid 미발행 유닛의 행 단위 귀속뿐.

왜 받아들였나
- per-unit 귀속 게이트를 풀어 임의 listen 포트를 아무 unknown service 에 붙이면 multi-service 호스트(nginx:80 + opaque:1433)에서 오분류 — 그래서 per-unit 은 보수적으로 두고, 호스트 레벨에서만 union 으로 보충(set 합집합이라 오분류 아닌 "탐지 누락 보완").
- 분류 산출물(뱃지·role)은 본질적으로 "이 호스트가 무슨 워크로드를 도느냐" 의 근사 — listen 이 그 질문의 직접 증거다.

언제 다시 봐야 하는가
- pid null 구간이 좁혀지면(소켓 액티베이션 리스너의 소유자 해석 등) -> `comm~name` fallback 휴리스틱 제거.

## T16. 비동기 보고서 발행 — 전용 워커 job-claim (DB 상태머신)

무엇을
- 보고서 발행은 비동기다: emit 은 parent job 을 pending enqueue 후 즉시 `?job={id}` 반환, 전용 워커 프로세스(`assessment_engine.worker`)가 job 을 claim 해 생성한다. consumer 큐 워커가 아니라 전용 워커 + DB 상태머신 방식.

왜 consumer 큐 워커가 아니라 전용 워커 프로세스인가
- 보고서 생성 코드(query_service report 메서드 + mappers·view_models·serializer)가 web/services 강결합. consumer(F4 CollectRepository 만)로 위임하면 web 표시계층 절반을 web 비의존 패키지로 승격하는 대공사 + 양방향 의존. 워크로드가 DB 집계 I/O(수초)라 큐 분리 효용도 낮다. 전용 워커는 web/services 를 그대로 재사용(단일 이미지)하면서 별도 프로세스로만 뗀다 — 추출 0.
- 메모리 task 방식은 in-flight 손실 위험으로 기각 — job 상태를 DB 에 두고 stale 복구로 그 손실을 무효화한다(FOR UPDATE SKIP LOCKED 로 멀티노드 분산까지).

포기한 것 / 한계
- 워커가 web/services(application 계층)를 import 하는 패키지 의존은 단일 이미지 전제에 묶인다 — web/services 를 중립 패키지로 추출하려면 별도 ADR(현재 불필요, 런타임 무해).
- 크래시/타임아웃으로 parent 가 running 잔류 -> stale 복구 후 재처리 시, 이전 run 에서 이미 succeeded 로 만든 child(단일 보고서)가 orphan 으로 이력에 중복될 수 있다. 데이터 정합성 허점은 아님 — parent succeeded 시 `child_jobs` 는 최신 유효분을 가리킨다. 다만 orphan child 는 자동 정리 경로가 없어 이력에 남고, child 멱등(같은 input_hash succeeded 재사용)·재처리 전 cleanup 은 드문 크래시 경로라 미구현.
- child fan-out 은 raws·breakdown·details 를 배치 1회 조회하나 trend·online 조회는 서버별로 남는다 — cpu/mem/disk 가 다른 테이블이라 단일 SQL 불가, 서버별 시계열이라 배치 불가.

왜 받아들였나
- 발행 응답을 즉시(job_id)로 만들어 N 증가에도 사용자 응답 시간 일정 — 가장 큰 요구(발행 느림) 해소. 생성은 백그라운드.
- 도메인 추출 0 으로 큐 워커 대공사·회귀 위험 회피하면서 in-flight 손실 0·멀티노드·graceful 달성. 전용 프로세스라 생성 부하가 web HTTP 처리와 프로세스 격리(web 자원 경합 없음).

언제 다시 봐야 하는가
- 생성 처리량이 부족하면 -> worker replica 를 늘려 수평 확장(SKIP LOCKED 로 중복 claim 안전, web 과 독립 스케일).
- orphan child 중복이 운영 이슈로 부상하면 -> child 멱등(같은 input_hash 의 succeeded child 재사용 조회를 신설) 또는 parent 재처리 전 이전 child cleanup.

## T17. install 배달 창 단일 정합 — deadline == 큐 TTL

무엇을
- install task 의 engine `tasks.deadline_at` 과 broker 큐 `x-message-ttl` 을 하나의 창(`install_task_deadline_sec`, 기본 3600)으로 통일. 오프라인 대상은 발행 차단이 아니라 비차단 advisory(store-and-forward + `target_online` 알림), 무회신 pending 은 reaper 가 전역 timeout 전이.

왜 단일 창인가
- 두 타임아웃이 어긋나면(예: deadline 11분인데 큐 TTL 1h) 엔진이 timeout 선언한 뒤에도 메시지가 큐에 생존해, 뒤늦게 재접속한 agent 가 이미 실패 처리된 task 를 실행하는 zombie 지연 실행이 생긴다. 동일 창이면 "엔진이 포기하는 시점 == broker 가 배달 포기하는 시점"이라 zombie 0.
- 오프라인을 게이트로 막지 않는 이유: online 은 Redis TTL 스냅샷이라 stale·racy. durable 큐 + TTL 이 간헐 연결을 흡수하는 메커니즘인데 liveness 추정으로 배달을 막으면 그 이득을 버린다.

포기한 것 / 한계
- 배달 창(1h)이 곧 online-but-crashed task 의 timeout 감지 상한. agent 가 완전 소실돼 `task.result` 를 못 보내면 최대 1h pending 후 timeout(대부분 실패는 agent 가 failure 를 명시 발행하므로 즉시 반영 — 1h 대기는 agent 소실 케이스 한정).
- 오프라인 유예도 1h bounded — 그 이상 오프라인이던 호스트가 돌아오면 메시지는 이미 만료라 재발행 필요.

왜 받아들였나
- zombie 지연 실행 제거 + 유령 pending 제거(reaper) + 오프라인 store-and-forward 유지를 한 상수로 달성. 온라인 실패 감지 지연은 실질적으로 agent 소실 케이스만이라 파급 작음.

언제 다시 봐야 하는가
- 오프라인 유예와 실행 timeout 을 독립 조절해야 하면 -> task 에 pickup(running) 신호를 추가해 "배달 TTL(길게) + 실행 deadline(픽업부터 짧게)" 2-타임아웃 모델로 분리. 현재는 pickup 신호가 없어 단일 창.

## T18. 용량 runway 전체 이력 집계 — cagg 하한 술어 예외

무엇을
- `report_aggregate` 의 mount_span CTE 는 `server_filesystem_5m` cagg 를 `WHERE server_id = ANY(:sids) AND bucket <= :end` 로 조회한다 — 다른 CTE(분류·포화·품질)가 전부 `bucket >= :start`(평가 윈도우) 인 것과 달리 하한 술어가 없다. 용량 runway(바이트·inode 소진 일수)는 실제 관측 span 전체의 fill_rate 로 산출하기 때문이다(#C5 partition pruning 하한 술어 원칙의 의식적 예외).

왜 전체 이력인가
- CPU·메모리 이용률은 변동 신호라 최근 평가 윈도우의 대표 부하로 p95 를 뽑는다(오래된 데이터는 지금을 대변 못 함). 반면 디스크 용량은 누적 신호라 채워지는 속도(추세)가 곧 모델이고, 데이터가 길수록 기울기가 정확하다. 평가 윈도우로 자르면 완만히 차는 볼륨의 runway 를 과소·과대 추정한다. 그래서 runway 만 분류 창과 분리해 전체 이력을 쓴다(윈도우 2분리 기준, #F10).

포기한 것 / 한계
- 하한 없는 조회라 해당 서버의 데이터 볼륨 마운트 전 chunk 를 스캔한다 — cagg 보존 기간이 길어질수록 스캔량이 unbounded 로 증가. 현재는 5분 버킷·데이터 볼륨 한정이라 규모가 작아 수용하나, cagg retention 이 수개월+로 늘면 runway 조회 비용이 선형 증가한다.
- cagg 재생성(마이그레이션) 직후엔 materialized 청크가 비어 real-time aggregation(raw hypertable)에 의존하는데, raw `server_filesystem` 보존 기간이 필요 이력보다 짧으면 오래된 endpoint 를 잃어 runway 가 짧은 span 으로 근사된다(continuous aggregate 패턴 내, 운영 관찰 대상).
- 2점(first/last) 선형 fill_rate 라 비단조(정리 후 급락·계절 변동)에 약하다. 강건 추정(Theil-Sen)은 SQL O(n^2) 비용으로 미채택 — 완만·단조 증가 가정에서만 신뢰(#E3 report mapper 소비, 신뢰도 축이 짧은 이력을 흡수).

왜 받아들였나
- 용량 추세는 누적 신호라 전체 이력이 정답이고, 현재 fleet 규모(5분 버킷·데이터 볼륨 한정)에서 스캔 비용이 작다. 분류(14일)와 runway(전체)를 한 쿼리에서 서로 다른 창으로 뽑아 왕복을 줄인다.

언제 다시 봐야 하는가
- cagg retention 확대로 runway 조회가 느려지면 -> mount_span 에 실용 상한(예: 90일) 하한 술어를 넣어 pruning 복원. 비단조 추세가 오판을 일으키면 -> Theil-Sen(샘플링 점쌍) 또는 최근 구간 가중 회귀로 격상.

## T19. Assessment API — 포털 표준에서의 의식적 이탈 (pagination·캐시·인증)

무엇을
- `/api/assessment`(+ POST export)는 인터랙티브 포털의 세 표준에서 벗어난다: (1) pagination 없음 — 매칭 전량을 한 응답으로(E2 page/cursor 규약 이탈). (2) Redis 캐시 없음 — 매 요청이 매칭 fleet 전체를 `report_aggregate` 로 재계산(대시보드 캐시 경로와 달리). (3) 인증 없음 — 전체 인프라 청사진(재현 레이아웃·IP·사이징)을 관리망 격리만 신뢰하고 무인증 노출. 소비자 계약 관점의 선언은 계약 문서(contracts/assessment-api.md) 10절 — 본 절은 엔진측 설계 근거·확장 트리거만.

왜 표준을 벗어났나
- pagination: 소비자가 인터랙티브 사용자가 아니라 재해복구/마이그레이션 자동화다. fleet 프로비저닝은 원자적 전량 소비가 목적 — 페이지 슬라이스로는 부분 인프라만 재현돼 under-provision 위험. cursor/페이지는 "계속 새 데이터 유입"(시계열)이나 "사람이 스크롤"(목록) 전제인데 assessment 는 스냅샷 1회 소비라 둘 다 안 맞는다. 스코프 축소는 필터(hostname/ip/public_id/pair)로 한다.
- 캐시 없음: assessment 는 저빈도 운영/자동화 액션(핫 대시보드 경로 아님)이라 신선도·정확성 > 지연. per-mount 디스크 + 수십 필드 스냅샷은 변동이 커 캐시 churn 이 높고, stale 사이징은 안전 최우선 원칙에 반한다. `report_aggregate` 는 cagg 사전집계라 현 fleet 규모에서 비용이 작다.
- 무인증: 관리망 전용 내부 B2B 포털로 나머지 화면과 같은 신뢰 경계. 이 엔드포인트만 별도 토큰 게이트를 세우면 포털 전체 인증 모델과 이원화된다.

포기한 것 / 한계
- 대규모 fleet 무필터 호출은 매칭 전량 `report_aggregate` 를 캐시 없이 매번 — 현재 70 VM 규모는 수백 ms 수준이나 수천 대·고빈도 폴링이면 반복 재계산이 선형 비용.
- 이 엔드포인트 하나가 전체 인프라를 가장 진하게 노출하는 단일 지점(재현 청사진 + IP + 토폴로지). 관리망 격리가 뚫리면 노출이 이 한 곳에 집중.
- 전량 응답이라 응답 크기가 매칭 수에 선형 — 필터를 안 걸면 fleet 전체 JSON.

왜 받아들였나
- 소비자가 자동화의 1회 스냅샷 소비라 표준 3개(페이지·캐시·토큰)가 오히려 목적에 역행하거나 무가치. 현재 규모에서 전량·무캐시·무인증의 비용/위험이 작고, 안전 최우선(정확·신선) 원칙과 정합.

언제 다시 봐야 하는가
- 단일 필터 스코프 응답이 실용 한계(응답 크기/지연)를 넘으면 -> cursor 또는 스트리밍(NDJSON) 분할.
- assessment 가 고빈도 자동 폴링이 되면 -> (filter, window, end-bucket) 키의 짧은 TTL 캐시 도입(대시보드 패턴 준용).
- 외부 노출이 필요해지면 -> 앞단 인증 게이트웨이(계약 10절 명시) + 이 엔드포인트의 인프라 노출 집중도를 감안한 인가 스코프.

## T20. 실시간 스냅샷 포화 축 — 윈도우 분류 경로와의 미세 원자료·경계 불일치

무엇을
- 포화 판정에는 두 경로가 있다: (A) 평가 윈도우 분류·환경·보고서 = `recommendation` 도메인의 os-aware verdict helper(`cpu_saturated`·`mem_saturated`·`disk_io_saturated`, dual-gate) 경유(#E3). (B) 실시간 현황·서버 상세 순간 스냅샷 = sibling index/active helper(`cpu_saturation_index`·`mem_pressure_active`·`disk_io_saturation_index`·`net_signal_active`, 목적상 single-gate) 경유. 두 경로가 같은 `RS_*` 상수를 공유하나, 두 축에서 미세하게 어긋나 같은 서버가 화면 간 다른 포화 판정을 낼 수 있다.
- 네트워크(실무 심각도 중): 서버 상세 실시간 네트워크 축(`metrics_calculator.build_saturation_signals`)이 단일 진실 `net_signal_active` 를 경유하지 않고 ratio 비교를 직접 조립한다. 두 이탈 — (1) 저트래픽 게이트 부재: `net_signal_active`/`assess_network` 는 트래픽 < `RS_NET_MIN_TRAFFIC_KBPS`(10 kB/s)면 retrans/drop 을 억제하나, 실시간 net 축은 무조건 임계 비교(`SaturationRaw` 에 net traffic 필드가 없어 구조적으로 게이트 불가). (2) 경계 연산자: 실시간은 `>=`, `net_signal_active` 는 strict `>`. -> 유휴 저트래픽 서버의 retrans 1.5% 나 정확히 1.0%/0.5% 경계값이 서버 상세엔 "혼잡", 환경/보고서엔 "정상".
- 디스크 await(실무 심각도 하): `disk_io_saturated` 는 `await_p95 > RS_DISKIO_AWAIT_MS`(strict `>`), `disk_io_saturation_index` 소비 게이트는 `>= 1.0`(실질 `await >= 20`). await == 20.000ms 정확값에서만 갈린다(measure-zero) — index docstring "동일 로직" 선언과의 latent slip.

왜 이대로 두나
- 두 경로는 목적이 다르다: 윈도우 분류는 dual-gate(신호 AND 이용률)로 오탐을 억제하는 결론이고, 실시간은 순간 단일 신호 crossing 을 그대로 보여주는 스냅샷(30초 갱신)이다. single-gate 는 위반이 아니라 실시간의 의도된 정의(#E3 취지 "임계 재계산·직접 해석 금지"는 두 경로 모두 충족 — 같은 도메인 상수 재사용, 소비처 임계 재선언 0).
- 네트워크 게이트/경계 정합은 `SaturationRaw` 에 net traffic(kB/s) 필드를 추가해야 근본 수정된다(스키마·배선 변경). 실무 발현이 저트래픽+경계값이라 희소해, HIGH(Windows 실시간 메모리 배선 — 이미 수정)와 달리 즉시성이 낮아 의식적 유예.

포기한 것 / 한계
- 저트래픽이면서 retrans/drop 이 임계 근처인 서버는 서버 상세 실시간 네트워크가 "혼잡", 같은 서버의 환경 요약·보고서는 "정상"으로 갈릴 수 있다(화면 간 네트워크 판정 불일치).
- disk await 정확히 20.000ms 서버는 윈도우 분류=비포화, 실시간=포화(measure-zero라 실측 조우 거의 없음).

왜 받아들였나
- 실시간(순간 스냅샷)과 윈도우(통계 분류)는 애초에 다른 축이고, 불일치가 저트래픽·경계 measure-zero 라는 희소 지점에만 발현. 근본 정합은 원자료 스키마 확장을 요구해 비용 대비 즉시 이득이 작다.

언제 다시 봐야 하는가
- 화면 간 네트워크 판정 불일치가 실제 운영 혼선을 부르면 -> `SaturationRaw` 에 net traffic 필드 추가 후 실시간 net 축을 `net_signal_active` 경유로 교체(저트래픽 억제 + strict `>` 통일).
- disk await 경계를 정합하려면 -> index 소비 게이트를 `> 1.0` 으로 좁히거나 `disk_io_saturated` 를 `>=` 로 통일(docstring "동일 로직" 선언과 일치).

## T21. 계약 예시가 에이전트 실제 발행과 일치하는지 확인할 채널 없음

> 관련 문서: `docs/reference/contracts/agent-data.md`, `docs/reference/contracts/wire-examples.json`

무엇을
- wire 계약의 정본은 넷이다 — 계약 문서의 필드 표, JSON Schema, 인바운드 Pydantic 모델, 에이전트 C 소스. 앞의 셋은 저장소 안에 있어 CI 가 서로 대조한다(예시가 스키마를 만족하는지, 스키마가 허용한 값을 Pydantic 이 수용하는지).
- 네 번째인 에이전트 C 소스는 다른 저장소에 있고, 그것이 실제로 발행하는 메시지와 저장소의 예시 파일이 여전히 같은지 확인하는 자동 채널이 없다. 예시는 사람이 쓴 것이고, 에이전트가 필드를 바꿔도 여기서는 아무 신호가 나지 않는다.

왜 이대로 두나
- 실 환경 메시지를 캡처하려면 고객사 내부망에 배포된 에이전트가 필요하다. 개발 환경에는 그 에이전트가 없고, 합성 발행기를 만들면 그것 자체가 계약의 네 번째 정본이 되어 같은 문제를 한 겹 늘린다.
- 인바운드 모델이 모르는 필드를 통과시키므로(`extra=ignore`) 에이전트가 필드를 추가하는 방향의 드리프트는 메시지를 죽이지 않는다. 위험한 것은 기존 필드의 값 종류가 바뀌는 경우인데, 그건 계약 버전(major)을 올려야 하는 변경이라 통보 경로가 따로 있다.

포기한 것 / 한계
- 예시 파일이 낡아도 CI 는 통과한다. 세 정본끼리 정합하기만 하면 되고, 그 셋이 함께 현실과 어긋날 수 있다.
- 에이전트가 계약 버전을 올리지 않고 값 종류를 바꾸면(minor 로 잘못 판단하거나 실수로) 그 필드를 쓰는 축이 조용히 유실된다. 매퍼가 값 타입 차이를 흡수하도록 고쳐 이 실패 모드의 폭은 줄였으나, 흡수 범위 밖의 변경은 여전히 드러나지 않는다.

왜 받아들였나
- 배포 비대칭이 이 시스템의 전제다. 에이전트가 고객사마다 다른 버전으로 떠 있어 "현재 에이전트 버전" 이라는 단일 기준 자체가 없다.

언제 다시 봐야 하는가
- 에이전트 저장소와 CI 를 연동할 수 있게 되면 -> C 소스에서 발행 필드 목록을 추출해 예시·스키마와 대조하는 게이트 추가.
- 실 환경에서 원인 불명의 축 유실이 관측되면 -> 그 시점에 메시지를 캡처해 예시 파일을 갱신하고, 같은 유형을 잡는 경계 fixture 를 추가.

---

## T22. broker 는 단일 credential + plain AMQP 로 운영한다

> 관련 문서: `docs/reference/rabbitmq.md`

무엇을
- RabbitMQ 접속은 단일 user 하나가 collector·worker·engine 역할을 모두 갖는다. 역할별 권한 분리를 두지 않았다.
- 전송은 plain AMQP(5672)다. TLS(5671)를 켜지 않았다.

왜 이대로 두나
- 큐를 동적으로 declare 한다. `agent.tasks.{agent_id}` 가 task 발행마다 생기므로 least-privilege 를 걸면 configure 권한을 어디까지 열지가 매번 판단 대상이 된다.
- TLS 는 내부 CA 발급·인증서 분배·갱신이 따라온다. 인증서를 받아야 하는 쪽이 엔진이 아니라 고객사 호스트마다 떠 있는 에이전트라 분배·갱신 비용이 호스트 수에 비례한다.
- 관리 UI·`rabbitmqadmin` 직접 디버깅이 TLS 핸드셰이크 없이 그대로 된다.

포기한 것 / 한계
- credential 하나가 새면 발행·소비·토폴로지 변경이 모두 가능하다. 역할별 회수가 안 된다.
- 에이전트가 broker 로 보내는 구간이 평문이다. broker 는 5672 를 외부 호스트에 열어 두므로(에이전트 발행 통로) 그 경로에서 트래픽을 볼 수 있는 위치라면 메시지 본문이 보인다.

언제 다시 봐야 하는가
- 고객사 망이 신뢰 경계로 충분하지 않다고 판단되면 -> TLS 를 먼저 켠다. 에이전트 배포에 CA 분배가 함께 실려야 한다.
- 에이전트 credential 을 고객사별로 나눠야 하는 요구가 생기면 -> 역할별 user 로 분리한다. 그 시점에 초기 셋업용 admin 을 one-shot 으로 쓰고 회수하는 절차를 `docs/guides/deploy.md` 에 신설한다.

## T23. 요청 흐름을 식별자 grep 으로 따라간다

Request/Correlation ID 를 심지 않는다. HTTP 진입점이 `X-Request-ID` 를 읽거나 만들지 않고, MQ `message_id` 는
멱등성 키로만 쓴다. 한 흐름을 따라가려면 `agent_id`·`server_id`·`message_id` 로 로그를 grep 한다.

왜 이대로 두나
- 컴포넌트가 셋(web·consumer·worker)이고 각자 자기 진입점에서 식별자를 로그에 싣는다. 흐름 하나가 여러
  프로세스를 건너는 경우가 보고서 발행과 task 발행 둘뿐이라 식별자만으로 이어붙는다.
- trace ID 를 심으려면 contextvars 전파를 전 계층에 넣어야 하고, 그 배선은 안 쓰는 동안에도 유지 대상이 된다.

포기한 것 / 한계
- 같은 사용자의 연쇄 요청을 하나로 묶을 수 없다. 요청 단위 지연 분해도 안 된다.
- 로그 aggregator 를 붙여도 필드가 없으니 trace 뷰가 서지 않는다.

언제 다시 봐야 하는가
- prod 에서 식별자 grep 으로 흐름을 못 잇는 사례가 나오면 -> `contextvars` + loguru `contextualize` 로 심는다.
- OpenTelemetry 를 도입하면 -> trace_id 가 그 자리를 대신하므로 별도 request_id 를 만들지 않는다.

둘 다 계약 표면(로그 format)이 바뀌므로 ADR 을 먼저 쓴다.

## T24. 유휴 판정 디스크 활동 축 — 보고서 경로에만 주입한다

무엇을
- `build_resource_stats(raw, *, disk_baseline)` 의 `disk_baseline` 은 유휴 판정 활동 축(`recommendation` 의 `IDLE_DISK_IOPS` 비교)이다. 이 값을 실제로 채우는 경로는 보고서 prefetch(`query/report.py::_assemble_report_raws`) 하나뿐이고, 나머지 7 호출 경로(서버 목록·서버 세부·환경 개요·환경 자원 평가·운영 신호·계약 API 둘)는 `None` 을 명시적으로 넘긴다.
- 결과적으로 같은 호스트가 보고서에서는 디스크 활동을 근거로 `idle` 로 갈릴 수 있고, 서버 목록·환경 개요에서는 그 축이 미관측이라 `over_provisioned` 에 머무를 수 있다.

왜 이대로 두나
- 통일 방향이 둘인데 어느 쪽도 공짜가 아니다. (A) 전 경로에 주입하면 `report_disk_io_baseline` 쿼리가 서버 목록·환경 개요·계약 API 요청마다 붙는다 — 목록은 페이지당 수십 대, 환경 개요는 전체 인벤토리다. (B) 보고서에서 빼면 보고서의 유휴 판정이 지금보다 보수적으로 바뀌어 발행된 스냅샷과 새 보고서가 갈린다.
- 어느 쪽이든 화면 분류가 실제로 바뀌므로 계약 개정에 해당한다. 이 저장소의 이번 현대화는 결과물 보존이 조건이라 범위 밖이다.

포기한 것 / 한계
- 화면 간 유휴 판정 정합(#E3)이 이 축 하나에서만 깨져 있다. 활동이 거의 없는 호스트가 보고서에서만 유휴로 뜬다.

왜 받아들였나
- 비대칭 자체는 이제 코드에 드러나 있다. `disk_baseline` 이 필수 키워드라 새 호출 경로는 이 결정을 내리지 않고는 컴파일되지 않고, `tests/unit/test_resource_stats_inputs.py` 가 경로별 인자값을 고정한다. 조용히 새는 상태에서 명시적으로 유예한 상태로 옮긴 것이 이번 변경의 성과다.

언제 다시 봐야 하는가
- 유휴 판정을 근거로 실제 다운사이즈를 집행하기 시작하면 -> 화면 간 판정이 갈리는 것이 곧 운영 사고이므로 (A) 로 통일하고 baseline 쿼리를 목록·개요 경로에 배치(벌크 1회, N+1 금지).
- `report_disk_io_baseline` 이 cagg 사전집계로 충분히 싸지면 -> (A) 의 비용 근거가 사라지므로 재검토.
