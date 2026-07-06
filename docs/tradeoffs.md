# 설계 트레이드오프

의식적 설계 선택과 그로 인한 한계 카탈로그 (T1~T16). 단순성·운영 비용·scope 기준 결정 — 버그 아님.

각 항목 형식: 선택 / 대안 / 트레이드오프 / 언제 다시 봐야 하는가.

## ADR 과의 책임 분담

- `docs/adr/` — 결정 자체 historical record. 시간 순서 (0001, 0002, ...) + Status / Context / Decision / Consequences. 영구·불변 (정정만, 덮어쓰기 금지).
- 본 파일 — 결정의 의도된 한계 카탈로그. 영구·갱신, 항목 추가 자유. cross-cutting reference 라 어느 카테고리 (architecture · development · operations · products) 에도 안 속하는 직속 위치.
- 중첩 — 일부 T 항목은 ADR 과 같은 결정을 다른 각도로 가리킴 (예: T1 = ADR 0001 Redis fail-open, T4 = ADR 0005 Resolved). 각 항목 헤더의 "관련 문서" 줄이 cross-link.

새 항목 추가 시: 본 파일 다음 T 번호 + 항목 작성. 같은 결정이 ADR 도 필요하면 별도 ADR 신설 후 cross-link.

---

## T1. 멱등성: at-most-once + 2단 방어 (fail-open 1단)

> 관련 코드: `src/assessment_engine/consumer/handlers/` `_check_idempotent`, `src/assessment_engine/cache/redis.py` `safe_set_nx`, `src/assessment_engine/db/repositories/collect_repository.py` `insert_metric`
>
> 관련 문서: CLAUDE.md #D2, `docs/adr/0001-redis-decoupling.md`

선택
1. Redis `SET idempotent:{message_id} 1 EX 86400 NX` (DB 커밋 이전). fail-open — Redis 장애 시 처리 진행.
2. 시계열 4개 테이블에 `(server_id, [dim,] collected_at)` UNIQUE + `pg_insert.on_conflict_do_nothing`. Redis 장애·evict로 1단이 깨져도 2단이 silent no-op으로 흡수.

대안
- at-least-once + outbox 패턴: DB 커밋과 message ack를 동일 트랜잭션에 묶고, ack 실패 시 outbox 테이블에서 재처리. 메시지 유실 0건 보장.
- at-least-once + 자연키 멱등 INSERT만: SET NX를 빼고 DB UNIQUE만으로 중복 차단. 메시지 유실은 막지만 중복 처리 비용 발생.

트레이드오프
- 얻은 것: 가장 빠른 중복 차단 (Redis 1회 RTT), 단순한 구현 (outbox 테이블·트랜잭션 동기화 불필요). Redis 장애 시 시스템 회복력 확보 (fail-open).
- 포기한 것:
  - at-most-once 한계: SET NX 후 DB 커밋 전 프로세스가 크래시하면 RabbitMQ 재전송 메시지가 idempotent 키 충돌로 silent 드롭 → 데이터 유실 가능. 1단(Redis)이 먼저 차단하므로 2단(DB UNIQUE)도 이 시나리오는 해결 못 함.
  - fail-open의 비용: Redis 장애 동안 1단의 빠른 차단(RTT)이 사라지고 매 메시지가 DB UNIQUE까지 도달. 트래픽 규모에서 영향 미미하지만 트래픽 증가 시 재평가 필요.

왜 받아들였나
- 1분 주기 metrics는 1건 유실의 시각화 영향이 작음 (다음 사이클에서 회복).
- inventory는 에이전트 재시작 시 재발행 (one-shot 보장이 약하지만 운영상 충분).
- B2B 내부 포털이라 통계 정확성보다 간결한 운영을 우선.
- fail-open은 2단(DB UNIQUE)의 흡수력에 명시적으로 의존 — 시계열 4개 테이블 UNIQUE 제약이 정상 동작해야 함.

언제 다시 봐야 하는가
- exactly-once 보장이 계약상 필요해질 때 (감사 로그·과금 연동 등).
- consumer 프로세스 크래시가 자주 관찰될 때.
- → outbox 패턴으로 전환.

---

## T2. 캐시 일관성: cache-aside (write-around)

> 관련 코드: `src/assessment_engine/web/services/query_service.py` `get_latest_metric`, `src/assessment_engine/consumer/handlers/` metrics handler
> 관련 문서: CLAUDE.md #D1

선택
- Web: cache MISS → DB query → `SET cache:metrics 60s`
- Consumer: DB COMMIT → `DEL cache:metrics`

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
- → version stamp 또는 single-flight 캐시.

---

## T3. 시계열 무한 누적

> 관련 코드: `src/assessment_engine/db/models/server_metrics.py`, `src/assessment_engine/db/models/server_disk_io.py` 등
> 관련 문서: CLAUDE.md #C1

선택
- TimescaleDB hypertable에 raw 메트릭을 그대로 누적. retention/aggregation 정책 없음.

대안
- retention policy: `add_retention_policy('server_metrics', INTERVAL '90 days')`로 오래된 청크 자동 drop.
- continuous aggregate: 분 단위 raw + 시간/일 단위 aggregate를 미리 계산해두고 차트가 aggregate 조회.

트레이드오프
- 얻은 것: 가장 단순한 운영. 모든 raw 데이터를 영구 보존해 사후 분석 자유도 높음.
- 포기한 것: 디스크 사용량 무한 증가. 30일 차트가 raw 30일 데이터를 매번 time_bucket로 집계 — 데이터 양 증가에 따라 응답 느려짐.

왜 받아들였나
- 소규모 dev 환경 서버 수와 1분 주기에서는 1개월 데이터가 ~130k행/서버. 운영 부담 미미.
- B2B 내부 포털이라 retention 요구사항이 명확하지 않음.

언제 다시 봐야 하는가
- 등록 서버가 100대 이상으로 증가할 때.
- 30일 차트 응답 시간이 200ms를 초과할 때.
- → continuous aggregate (분→시간→일 계층) + 90일 retention.

---

## T4. DEV 스키마 관리: web lifespan + create_all (prod skip) — Resolved by ADR 0005

> 본 트레이드오프는 ADR 0005 "DB Schema 관리 표준화"로 해소. 본 절은 historical record로 보존 — 의사결정 history는 ADR 0005 본문 참조.
>
> 관련 코드 (당시): `src/assessment_engine/web/main.py` lifespan, `src/assessment_engine/db/models/`
> 현재 결정: 모든 환경(dev·staging·prod·테스트) Alembic 단일 진실. `migrate` init-container가 `alembic upgrade head` 1회 실행 후 종료. lifespan create_all 제거됨. consumer는 `depends_on: migrate (service_completed_successfully)`. CLAUDE.md #C4 + `docs/operations/alembic.md` 참조.

선택 (당시 — 폐기)
- web 기동 시 `CREATE EXTENSION timescaledb` → `Base.metadata.create_all` → `create_hypertable(if_not_exists)`.
- consumer는 `depends_on web: condition: service_healthy`로 web 헬스체크 후 시작.
- `APP_ENV=prod`일 때 lifespan이 자동 skip — Alembic이 schema 관리 책임.

대안 (당시 — 채택됨)
- Alembic: 마이그레이션 스크립트로 스키마 관리. consumer가 web에 의존하지 않음.

해소 (ADR 0005 채택 후)
- migrate init-container 패턴 — `migrate` 서비스가 1회 실행 후 종료(`restart: "no"`). 앱 2 서비스(`web`/`consumer`) 모두 `depends_on: migrate: service_completed_successfully`.
- `consumer depends_on web` 제거됨 — web과 consumer가 동등 lifecycle.
- CI `alembic check`가 ORM·migration drift 자동 차단.

---

## T5. 실시간 메트릭 전달: 30초 polling

> 관련 코드: `src/assessment_engine/web/services/query_service.py` `get_latest_metric`, `src/assessment_engine/web/static/js/pages/detail.js`, `chart-utils.js` `initAutoRefresh` (4탭 공용)

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
- → SSE 또는 WebSocket push 재도입 (별도 ADR).

---

## T6. 클라이언트 차트 JS는 P3 명시 예외 (P4)

> 관련 코드: `src/assessment_engine/web/templates/servers/*.html` `<script>` 블록, `src/assessment_engine/web/static/js/chart-utils.js`
> 관련 문서: CLAUDE.md #E1 P4

선택
- 차트 JS에 그리드 계산·라벨 포매팅·Chart.js 옵션 조립 등의 연산을 허용 (P3 위반).
- 대신 5개 의무 규약 적용: sequence counter, capture-before-await, `Array.isArray`, 404 분기, suggestedMax 명명 상수.

대안
- 서버 사이드 SVG/PNG 차트: matplotlib·plotly로 서버에서 이미지 생성. 클라이언트는 표시만.
- WebComponent + 프레임워크: Vue/React로 컴포넌트화하고 stale 응답을 컴포넌트 lifecycle로 관리.

트레이드오프
- 얻은 것: range 토글·anchor 변경에 즉시 반응 (서버 라운드트립 0). Chart.js 4.4.3 한 번의 CDN script load만으로 동작 — 빌드 도구 불필요.
- 포기한 것: 차트 JS가 P3 우회로가 될 가능성 — 임계값 분류·통계 재계산 같은 비즈니스 로직이 슬며시 들어올 수 있다. 5개 규약 누락 시 race condition·404 오인 등 미묘한 버그.

왜 받아들였나
- 동적 인터랙션이 필요한 차트가 ~10개. 서버 사이드 이미지 차트는 인터랙션 비용 큼.
- 프레임워크는 빌드/배포 파이프라인 도입 비용이 본 포털 규모와 맞지 않음.

P4 5 의무 규약(a~e) 적용 위치: 5개 차트 페이지(cpu/memory/storage/network/performance) inline `<script>` 본문은 `static/js/pages/{name}.js` 외부 .js로 격리. 페이지 .html은 Jinja2 변수 정의(`SERVER_ID`, `CPU_CORES`) + `defer` 로드만. 공통 유틸은 `static/js/chart-utils.js` (T9 추출).

---

## T7. 에이전트 broker 자동 재연결

> 관련 코드: `assessment-agent/src/publish.c`, `src/main.c` (외부 레포)

구현
- `publish.c`: 매 publish가 fresh connection lifecycle (`amqp_new_connection` → `socket_open` → `login` → `channel_open` → publish → close → destroy). connection 재사용 안 함.
- `publish.c`: publisher confirm 모드 활성화 (`amqp_confirm_select(conn, 1)`) + wall-clock deadline ack 대기 (`wait_confirm`, 기본 5초 `RABBITMQ_CONFIRM_TIMEOUT_SEC`).
- `main.c` `publish_with_retry`: 지수 백오프 (1s → 2s → 4s → ... → max=AGENT_INTERVAL_SEC, 기본 60s) 무한 retry. `g_stop` 시그널 전까지.
- 복구 시 `PUBLISH_RECOVERED` error 메시지 자동 발행 (`retry_count`, `first_failed_at`, `recovered_at` 포함).

broker 재기동 시 자동 회복
- broker 죽음 → 에이전트 publish 실패 → 백오프 retry 시작 → broker 살아남 → 다음 retry 사이클에서 publish 성공 → `PUBLISH_RECOVERED` 알림 메시지 → 정상 운영 복구.
- `systemctl restart assessment-agent` 불요.

inventory 비어 있는 데이터베이스로 metrics가 도착하면 1시간 주기 inventory 재발행 + 엔진 auto-register(`src/assessment_engine/consumer/handlers/`)로 해결.

남은 한계
- broker 영구 down 시 에이전트는 백오프 상한(60s) 간격으로 영원히 재시도 — 정상 동작이나 로그·CPU 미세 부담.

---

## T8. ListServers ORM 부분 SELECT vs full row

> 관련 코드: `src/assessment_engine/db/repositories/query/server.py` `list_servers`

선택
- `select(ServerInventory.id, .public_id, .composite_id, ...)`로 11개 컬럼만 명시 SELECT. `mounts`/`listen_ports`/`kernel_version`/`boot_time`/`swap_total_kb`/`agent_version`/`last_seen_at`/`ip_internal`/`os_codename`/`cpu_model` 제외.

대안
- `select(ServerInventory)` 풀로우 SELECT (이전 구현).
- ORM lazy loading + 필요한 속성만 접근.

트레이드오프
- 얻은 것: 페이로드 축소 — 큰 JSONB 컬럼(`mounts`, `listen_ports`)을 페이지당 N개 행에서 직렬화하지 않음.
- 포기한 것: 컬럼 추가 시 list 화면에 노출하려면 SELECT 목록을 함께 갱신 (DRY 위반 작은 케이스).

왜 받아들였나
- list 화면은 페이지당 20개 서버 — JSONB 컬럼 직렬화/네트워크 비용이 작지 않음.
- 컬럼 추가 빈도가 낮고, 추가 시 mapper도 함께 변경되는 게 자연스러워 SELECT 갱신을 잊을 가능성 낮음.

언제 다시 봐야 하는가
- ServerInventory에 컬럼이 자주 추가될 때.
- → ORM 컬럼 그룹(`deferred()` 또는 별도 entity) 도입 검토.

---

## T9. 차트 공통 JS — 인라인 중복 제거 (chart-utils.js 추출)

> 관련 코드: `src/assessment_engine/web/static/js/chart-utils.js`, `src/assessment_engine/web/main.py` `StaticFiles` 마운트, `src/assessment_engine/web/templates/servers/*.html`

선택
- 5개 차트 템플릿에 흩어진 공통 정의(`fmtKst` / `bindToggle` / `COLORS` / `AUTO_BUCKET` / `BUCKET_MS` / `makeBucketGrid` / `joinToGrid` / `fmtLabel` / `getAnchorEnd` / `initAnchor`)를 `/static/js/chart-utils.js`로 추출. `base.html`에서 단일 로드 → 전역 `ChartUtils` IIFE 객체 노출.
- 각 템플릿은 상단에서 `const { ... } = ChartUtils;`로 destructure.

대안
- 인라인 유지 (이전 상태): 각 템플릿이 자기 사본을 가짐. 빌드 도구 불필요, 한 파일만 보면 모든 로직 파악.
- 번들러 도입 (Vite/esbuild + ESM): import/export로 모듈화. 트리쉐이킹·타입체크 가능.
- WebComponent: 차트를 컴포넌트로 분리하고 props/이벤트로 인터랙션.

트레이드오프
- 얻은 것: 중복 정의가 한 곳으로 — 시그니처 변경 시 5곳 수정 → 1곳 수정. 템플릿 평균 200~500 라인이던 인라인 `<script>`가 200~300 라인으로 감소 (차트 데이터셋 빌드 로직만 남음).
- 포기한 것: 모듈 시스템(import/export) 없음. `ChartUtils.X` 또는 destructure 형태로만 노출. 의존 그래프가 명시적이지 않음. 타입체크 없음 (TS 도입 X).

왜 받아들였나
- 본 포털은 번들러 운영 비용(node_modules·빌드 스텝·소스맵·CI 변경) 대비 이득 작음.
- IIFE + 단일 로드는 브라우저 캐싱 친화적이고 디버깅이 단순.

외부화 형태:
- 5개 페이지 inline `<script>` 본문은 `src/assessment_engine/web/static/js/pages/{cpu,memory,storage,network,performance}.js`.
- 페이지 .html은 Jinja2 변수만 정의(`SERVER_ID`, `CPU_CORES`) + 외부 .js `defer` 로드.
- 정적 자원: `chart-utils.js` + 5개 페이지 .js. node 의존성 0 — node syntax check만으로 회귀 격리.

언제 다시 봐야 하는가
- TS 타입체크 또는 ESLint 정적 검증 필요성이 명확해질 때.
- → 의존 최소: `tsc --checkJs --noEmit` (빌드 산출 없음, JSDoc 주석으로 타입) 또는 `eslint --rule no-undef` (npm 1개).
- 빌드 도입은 Vite/esbuild — `app.mount("/static", ...)`을 `dist/`로 변경.

---

## T10. ViewModel 비대화 vs 클라이언트 재계산 (P2 따름)

> 관련 코드: `src/assessment_engine/web/view_models/`, `src/assessment_engine/web/services/mappers/`, `src/assessment_engine/web/services/metrics_calculator.py`
> 관련 문서: CLAUDE.md #E1 P2 · #E3, `docs/architecture/web/view-models.md`

선택
- `ListenPortItem.is_well_known` (port <= 1024 boolean)
- `ServerDetailResponse.sorted_services` / `sorted_listen_ports` (mapper 정렬 결과)
- `MountUsageItem.badge_class` / `bar_color` (임계값 → CSS 클래스/hex)
- `MemSnapshot.cached_pct` / `buffers_pct` (stacked-bar 누적 비율)

→ 이 파생 필드를 모두 mapper에서 미리 계산해 ViewModel에 둠. 템플릿/JS는 read-only.

대안
- 클라이언트 재계산: 템플릿이 `{% if p.port <= 1024 %}` / `| sort` / 임계값 `{% if pct >= 90 %}`, JS가 `mem.cached_kb / total * 100`. ViewModel은 raw에 가깝게 유지.

트레이드오프
- 얻은 것: P2·P3 정합 — 템플릿/JS는 표시만. 임계값/정렬 규칙 변경 시 mapper 한 곳만 수정. 캐시된 ViewModel과 SSR 직후 ViewModel이 항상 동일한 표현 결과를 만듦.
- 포기한 것: ViewModel 필드 수 증가(`ServerDetailResponse`만 +5필드). dataclass 필드 순서 제약(`non-default follows default`)으로 default factory 필드를 끝으로 모아야 함. 캐시 직렬화 페이로드 미세 증가.

왜 받아들였나
- 임계값 변경(예: 90 → 85)이 발생할 때 클라이언트·서버 분산이면 한쪽 누락 가능성 큼. 본 포털은 임계값 정책이 향후 조정 가능성 있음.
- 캐시 페이로드 증가는 정렬·boolean 정도라 실측상 무시 가능.

언제 다시 봐야 하는가
- ViewModel 필드가 50개 이상으로 비대해질 때 — 화면별 sub-ViewModel로 분리.

---

## T11. 단일 Redis 인스턴스 — 캐시·멱등성·PubSub 한 통합 (fail-open)

> 관련 코드: `src/assessment_engine/cache/redis.py` `safe_*` helper, `src/assessment_engine/consumer/handlers/`, `src/assessment_engine/web/services/query_service.py`
> 관련 문서: `docs/architecture/redis.md`, `docs/adr/0001-redis-decoupling.md`

선택
- 한 Redis 인스턴스에서 4가지 역할 동시 처리: 캐시 / 온라인 TTL / 멱등성 / public_id 해석.
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
  - 멱등성 키도 `volatile-lru` 대상 — maxmemory 압박 시 evict 가능 → 1단 방어 깨짐. fail-open과 동일하게 DB UNIQUE(2단)이 흡수 (T1과 연결).
  - Redis 단일 장애 시 모든 역할(online 판정·캐시·멱등성) 영향 — 단 fail-open으로 시스템 다운은 회피. 멱등성 1단은 우회(DB가 흡수), 캐시는 DB 직접 조회, online 은 폴링 데이터 신선도로 자연 회복.

왜 받아들였나
- B2B 내부 포털 — 동시 요청 수·멱등성 키 수가 작아 evict 시나리오 드묾.
- 단일 인스턴스로 운영 비용 최소화.
- Redis를 다른 캐시 시스템으로 교체할 계획 없음 — 인터페이스 추상화는 무의미한 복잡도 증가.

언제 다시 봐야 하는가
- 멱등성 키 evict가 실제 관찰될 때 (Redis `INFO stats` `evicted_keys` 모니터링).
- → 멱등성을 PostgreSQL 테이블로 옮기거나, Redis를 namespace별로 분리.

---

## T12. server_inventory 호스트 식별 — `agent_id` 단독 UNIQUE (ADR 0049 정정)

> 관련 코드: `src/assessment_engine/db/models/server_inventory.py`, `src/assessment_engine/db/repositories/collect_repository.py`
> 관련 문서: CLAUDE.md #C1, `docs/architecture/db/models.md`, `docs/architecture/agent.md`
> 관련 migration: `migrations/versions/e8b4d2f6a1c9_agent_id_identity.py`

선택 (현행, ADR 0049)
- `server_inventory` UNIQUE = `agent_id` 단독 (agent 가 첫 실행 시 생성·영구저장한 불변 UUID). `composite_id`(SHA-256(machine_id + 정렬·dedup MAC 들), nullable)·`machine_id`(raw, nullable) 는 clone collision 진단용 감사·표시 컬럼, hostname 은 display.
- 식별 진화: ADR 0022 `host_id` 단독 -> (한때 `(host_id, hostname)` 복합 시도, migration `f5c1e2d3a4b8`) -> 0027 `composite_id` 단독 -> 0049 `agent_id` 단독. composite_id 는 부팅마다 NIC MAC 재발급(OpenStack Windows VM)으로 변동해 중복 행·재연결 로직(ADR 0044)이 필요했으나, 불변 agent_id 는 그 문제 자체를 제거 (재연결 불요). 아래 대안·본문 중 composite_id/복합 UNIQUE 논의는 그 이전 단계 기록.

대안 (채택 안 함, 단 agent_id 는 결국 ADR 0049 로 채택)
- hardware UUID (`/sys/class/dmi/id/product_uuid`) 우선 + machine-id fallback. VM clone 시 hardware UUID 도 동일 가능. agent 변경 필요.
- 운영자 부여 server_id (install 시 운영자가 UUID 주입). install workflow 에 등록 step 추가.
- composite_id 유지 + 재연결(ADR 0044): machine_id+hostname 으로 재부팅 변동을 흡수. clone(미sysprep) 오병합 위험 잔존. agent_id 도입으로 폐기.

트레이드오프
- 얻은 것:
  - 실제 운영에서 흔한 host_id 중복 시나리오 (VM 템플릿 복제·이미지 clone·container host `/etc/machine-id` 마운트) 즉시 격리.
  - agent 변경 0 — 엔진 단독으로 완결. payload 합의 영향 없음.
  - 영향 코드 단순 (Repository signature + consumer 핸들러 hostname 1개 추가 전달 + cooldown 키 인자 1개 추가).
- 포기한 것:
  - hostname 변경 시 새 row INSERT (다른 호스트로 인식) — 운영자가 명시적으로 hostname 변경하면 history 끊김. 같은 호스트 분리. 운영자 의도와 다를 수 있음.
  - 두 다른 호스트가 동일 host_id + 동일 hostname 보유 시 여전히 충돌 (rare — 클론 후 hostname 안 바꾼 케이스).
  - MQ queue `agent.tasks.{host_id}` / routing key `task.install.{host_id}` 는 여전히 host_id 단독 — agent 가 자기 host_id 로 queue subscribe 하니 agent 변경 없이 hostname 포함 불가. 같은 host_id 다른 hostname 두 호스트가 동일 큐 공유 시 message race 가능 (rare).

왜 받아들였나
- B2B 내부 포털 — 인벤토리 등록 호스트 수가 작아 hostname 충돌 자체가 드묾.
- agent 측 코드는 외부 repo + 사용 중인 binary. 본 repo 단독 결정이 빠르고 안전.
- MQ race 는 같은 image clone + 같은 hostname 시나리오에서만 발생 — 흔치 않음.

언제 다시 봐야 하는가
- MQ queue 충돌이 운영에서 관측될 때 → ADR 신설 + agent_id 도입 또는 queue 식별자 변경.
- hostname 변경으로 history 끊김 운영자 불만 누적 시 → agent_id 같은 mutable-free 식별자 도입 재검토.

---

## T13. 보고서 = diagnostic_jobs 통합 (job_type) + 환경 보고서 view toggle

> 관련 코드: `src/assessment_engine/db/models/diagnostic_job.py`, `src/assessment_engine/web/services/diagnostic_service.py::record_report_emission`, `src/assessment_engine/web/templates/diagnostics/results.html`
> 관련 문서: CLAUDE.md #C1, `docs/architecture/db/models.md`
> 관련 migration: `migrations/versions/a1b2c3d4e5f6_diagnostic_jobs_job_type.py`

선택
- `diagnostic_jobs.job_type` 컬럼 (`customer_report`/`engineer_report`) — 보고서 발행이 본 테이블에 row 저장 (이력 보존).
- 양식 분리:
  - server scope (`/reports/servers?ids=...`): row 단위 상세, 양식 A/B (`servers/report.html`).
  - environment scope (`/reports/environment`): high-level (KPI·USE Method 분류 도넛·Top N risk·OS 분포·view별 정성 요약, `reports/environment.html`). 전체 등록 서버 자동, `EnvironmentReportSummary` view_model + `mappers.environment_report`.
- 두 라우터 모두 합성 직후 `record_report_emission` 호출 (best-effort, 응답 흐름 영향 없음).
- 보고서 이력 (`/reports/history`) 페이지 — customer + engineer union + view 필터 select. 서버 목록에서 진입점 지원 (선택 N대 버튼 + 환경 카드 link).
- 환경 scope 보고서 페이지는 같은 페이지 안 view tab (고객 보고서/엔지니어 보고서). 각 view 는 `<iframe src="/reports/environment?view=...">` SSR 미리 렌더 + JS `display` toggle.

대안
- 보고서를 별도 테이블 `report_jobs` 로 분리 — 모델 명확하나 두 테이블 간 통합 표시 SQL union 복잡. job_type 단일 분기로 충분.
- view toggle 을 AJAX lazy fetch — 첫 로드 가벼우나 새 API + client JS 필요. iframe SSR 미리 렌더가 단순.
- 보고서 본문 (HTML) 을 `result` JSONB 에 snapshot 저장 — DB 비대화 + 양식 변경 시 옛 snapshot 불일치. 현재는 메타만 저장하고 보기 시 재합성 (data 변경 시 결과 달라지는 한계 수용).

트레이드오프
- 얻은 것:
  - 보고서 발행 이력 단일 페이지에서 통합 추적.
  - 같은 페이지에서 고객/엔지니어 보고서 즉시 비교 (toggle).
  - 모델 통합 — 보고서별 별도 service·테이블 신설 없이 기존 diagnostic_jobs 재사용.
- 포기한 것:
  - 매 보고서 GET 마다 row INSERT (active UNIQUE 통과 후 즉시 succeeded) — 같은 입력 N회 조회 시 N row 생성. retention 90일로 sizing 자체는 OK 이나 dedup view 또는 view_count 증분 모델은 미적용.
  - 환경 진단 결과 페이지 매 로드 시 iframe 2개 동시 fetch — 보고서 페이지 자체가 무거우면 (server N대 SQL 5x2) 첫 표시 늦음. 캐시는 미적용.
  - `result` JSONB 에 양식 HTML snapshot 미저장 — 옛 보고서 재조회 시 raw data 변경 영향. snapshot 의도면 별도 결정.

왜 받아들였나
- 보고서 발행은 운영자가 명시 액션 (선택 N대 → 보고서 버튼) — 매 발행은 의미 있는 이벤트라 row 1개 기록 OK.
- 환경 진단 결과 페이지 부담은 운영자가 명시 진입 시점만 — list page 같은 hot path 아님.
- snapshot 미적용은 보고서 자체가 시계열 raw → 양식 합성이라 data drift 자연스러움. 정확한 시점 snapshot 필요 시 별도 PDF export 기능으로 분리.

언제 다시 봐야 하는가
- 보고서 row 가 운영에서 폭증 (운영자가 N회 새로고침) 시 → dedup 또는 view_count 증분 모델.
- 환경 진단 결과 페이지 첫 표시 느림 운영자 불만 시 → 보고서 페이지 server-side 캐시 또는 lazy fetch 전환.
- 보고서 snapshot 필요 운영 요구 시 → `result` JSONB 에 합성 결과 저장 + size 모니터링.

## T14. Windows saturation 임계 근거 비대칭 + perflib 의존 (ADR 0029)

right-sizing 분류는 USE Method 의 Utilization + Saturation 두 축을 본다. saturation 3축 모두 OS별 실측 신호로 정규화된다(os-aware helper 단일 진실) — CPU 포화는 Linux loadavg / Windows Processor Queue Length, 메모리 포화는 Linux swap page-out / Windows Memory Pages/sec rate, 디스크 IO 포화는 Linux iowait / Windows Avg Disk Queue Length. Windows 도 세 포화 축을 실측하되, 신호원과 임계 근거의 성숙도가 다르다.

- 받아들인 한계:
  - Windows 메모리 포화 임계(Pages/sec p95 >= 1000)는 절대 임계 근거가 약한 rule-of-thumb 이다 — disk queue(>= 2)·CPU run queue(>= 2/core)의 Microsoft 표준 병목 기준과 달리 실측 튜닝 대상(`MEM_PAGING_RATE_SATURATION`, 잠정 상수). 너무 높으면 실제 페이징 압박을 놓치고, 낮으면 정상 페이징을 과잉 발화.
  - saturation 축은 perflib/diskperf 의존이다 — Windows 에서 해당 카운터를 못 읽거나 미부착(예: OpenStack virtio 에 diskperf 미부착 -> disk queue 빈 배열)이면 그 축만 미관측이 된다. 분류는 utilization·capacity·측정된 나머지 포화 축으로 완결하고, 못 본 축만 "포화 수치 미관측" confidence 단서로 노출.
  - Windows pagefile 사용량(swap_used)은 수집·표시하되 saturation 판정엔 미반영 — pagefile 은 여유 RAM 에도 상시 baseline 이라 사용량이 아닌 페이징 rate 로 판정(P2 의도).

왜 받아들였나
- Windows 가 노출하지 않는 신호를 0/baseline 으로 날조해 분류에 넣으면(예: iowait=0 을 "IO 여유"로) 더 큰 왜곡 — 미측정은 미측정으로 두는 게 정직(P1).
- disk queue·CPU run queue 는 Microsoft 표준 병목 기준이 있어 임계 근거가 탄탄하나, 메모리 페이징은 절대 임계 합의가 약해 보수적 상수 + "잠정·튜닝 대상" 명시가 정직한 선택 — 근거 없는 정밀 임계보다 명시된 잠정 임계가 낫다.
- "부분 평가" 마커가 운영자에게 confidence 한계를 명시 — 침묵하는 오분류보다 가시화된 한계가 낫다(P4).

언제 다시 봐야 하는가
- Windows 메모리 페이징 오탐/누락이 관측되면 → 실측 분포로 `MEM_PAGING_RATE_SATURATION` 재보정 (현재 1000 pages/sec 잠정).
- perflib 미발행이 특정 Windows 환경에서 상시화되면 → agent 측 수집 경로 점검 (엔진은 미관측으로 정직 처리, 신호원 자체는 agent repo 이슈).

## T15. 서비스 분류 — services <-> listen_ports join key 부재 (호스트 union 으로 보완, ADR 0032)

agent 메시지의 `services[]`(unit·sub)와 `listen_ports[]`(proto·port·comm·pid) 사이에 신뢰할 join key 가 없다 — services 가 pid/exe 를 발행하지 않아 "이 service unit 이 그 포트를 연다"를 확정할 수 없다. Windows agent 는 `EnumServicesStatusExW(SC_ENUM_PROCESS_INFO)` 가 `dwProcessId` 를 쥐고도 안 싣고(엔진이 못 바꾸는 제약), Linux 는 `systemctl list-units` 파싱이라 pid 없음. 그래서 per-unit 분류(`classify`)는 comm/port 를 `comm~name` 귀속될 때만 쓸 수 있고, 이름이 comm 과 무관한 opaque 서비스를 per-unit 으론 못 잡는다.

agent 불변 전제의 최선 — 호스트 워크로드 union:
- 뱃지/role/환경분포는 per-unit 분류에 의존하지 않고, `detect_listen_categories(listen_ports)` 로 listen 소켓을 직접 분류(comm/port)해 services 이름 분류와 합집합(`workload_category_counter`)한다. listen 소켓의 comm·port 는 깨끗·안정 식별자라 opaque 이름을 우회 — `MSSQL$무엇` 이든 1433/`sqlservr` 로 db 탐지.

- 포기한 것(union 후 잔존):
  - listen 안 하거나 localhost-only 바인드 워크로드 + opaque 이름 = 두 소스 모두 못 잡아 미상. (listen 하는 워크로드는 union 으로 거의 구제됨.)
  - per-unit services 탭의 행별 카테고리는 여전히 이름 기반 best-effort — opaque unit 은 그 행에서 unknown (호스트 뱃지는 union 으로 db 표시되어도). 행 단위 정확 귀속은 pid join 부재로 불가.

  호스트 union 은 ingest 시 1회 계산(`compute_service_categories`)해 `server_inventory.service_categories`(text[])에 저장 — 목록·상세·리포트·필터가 동일 저장값 소비라 화면 간 카테고리 집합 비대칭 0(목록은 listen_ports 재로드·행별 재분류 없이 경량 유지, #E7). 남는 한계는 위 per-unit 행 단위 귀속뿐.

왜 받아들였나
- per-unit 귀속 게이트를 풀어 임의 listen 포트를 아무 unknown service 에 붙이면 multi-service 호스트(nginx:80 + opaque:1433)에서 오분류 — 그래서 per-unit 은 보수적으로 두고, 호스트 레벨에서만 union 으로 보충(set 합집합이라 오분류 아닌 "탐지 누락 보완").
- 분류 산출물(뱃지·role)은 본질적으로 "이 호스트가 무슨 워크로드를 도느냐" 의 근사 — listen 이 그 질문의 직접 증거다.

언제 다시 봐야 하는가
- agent 가 `services[]` 에 main pid 또는 exe basename 을 실어주면 → listen_ports 와 pid join 으로 per-unit 정확 귀속, 행 단위까지 정확. union 보완 불필요.

## T16. 비동기 보고서 발행 — web job-claim 워커 (ADR 0040)

무엇을
- 보고서 발행을 동기 즉시 succeeded 에서 비동기로 전환: emit 은 parent job 을 pending enqueue 후 즉시 `?job={id}` 반환, web lifespan 의 job-claim 워커가 생성. consumer 큐 워커(ADR 0004 옵션 B)가 아니라 web 내부 워커 + DB 상태머신(옵션 C).

왜 큐 워커가 아니라 web 내부 워커인가
- 보고서 생성 코드(query_service report 메서드 + mappers·view_models·serializer, 약 4900+ LOC)가 web/services 강결합. consumer(F4 BaseCollectRepository 만)로 위임하면 web 표시계층 절반을 web 비의존 패키지로 승격하는 대공사 + 양방향 의존. 워크로드가 DB 집계 I/O(수초)라 큐 분리 효용도 낮다.
- 옵션 A(메모리 task) 기각 사유(in-flight 손실)는 job 상태를 DB 에 두고 stale 복구로 무효화 — FOR UPDATE SKIP LOCKED 로 멀티노드 분산까지.

포기한 것 / 한계
- web 프로세스가 생성 부하를 짊어진다(요청과 완전 격리 아님). DB I/O 바운드라 경미하나 생성 폭주 시 web 자원 경합.
- 크래시/타임아웃으로 parent 가 running 잔류 -> stale 복구 후 재처리 시, 이전 run 에서 이미 succeeded 로 만든 child(단일 보고서)가 orphan 으로 이력에 중복될 수 있다. 데이터 정합성 허점은 아님 — parent succeeded 시 `child_jobs` 는 최신 유효분을 가리키고, orphan child 는 retention 으로 정리. child 멱등(같은 input_hash succeeded 재사용)·재처리 전 cleanup 은 드문 크래시 경로라 미구현.

왜 받아들였나
- 발행 응답을 즉시(job_id)로 만들어 N 증가에도 사용자 응답 시간 일정 — 가장 큰 요구(발행 느림) 해소. 생성은 백그라운드.
- 추출 0 으로 큐 워커 대공사·회귀 위험 회피하면서 in-flight 손실 0·멀티노드·graceful 달성.

언제 다시 봐야 하는가
- 생성 부하가 web 요청 처리를 압박하면 → consumer 큐 워커(옵션 B)로 분리(보고서 생성 도메인 계층 추출 동반).
- orphan child 중복이 운영 이슈로 부상하면 → child 멱등(get_latest_succeeded_by_hash 재사용) 또는 parent 재처리 전 이전 child cleanup.
- A2(aggregate/net 중복 제거)·A3(breakdown 배치)·A5(fan-out prefetch 배치)는 적용 완료 — child fan-out 의 raws·breakdown·details 를 배치 1회 조회(`build_child_prefetched_reports` -> `get_single_server_report(prefetch=)`). A4(trend)만 보류: cpu/mem/disk 가 다른 테이블이라 단일 SQL 불가, 서버별 시계열이라 배치 불가, gather 는 QueryService composition root 대수술 + 커넥션 3배 + B 백그라운드라 응답 ROI 0. trend·online redis 는 서버별 잔존.

## T17. install 배달 창 단일 정합 — deadline == 큐 TTL (ADR 0051)

무엇을
- install task 의 engine `tasks.deadline_at` 과 broker 큐 `x-message-ttl` 을 하나의 창(`install_task_deadline_sec`, 기본 3600)으로 통일. 오프라인 대상은 발행 차단이 아니라 비차단 advisory(store-and-forward + `target_online` 알림), 무회신 pending 은 reaper 가 전역 timeout 전이.

왜 단일 창인가
- 두 타임아웃이 어긋나면(옛: deadline 11분 vs 큐 TTL 1h) 엔진이 timeout 선언한 뒤에도 메시지가 큐에 생존해, 뒤늦게 재접속한 agent 가 이미 실패 처리된 task 를 실행하는 zombie 지연 실행이 생긴다. 동일 창이면 "엔진이 포기하는 시점 == broker 가 배달 포기하는 시점"이라 zombie 0.
- 오프라인을 게이트로 막지 않는 이유: online 은 Redis TTL 스냅샷이라 stale·racy. durable 큐 + TTL 이 간헐 연결을 흡수하는 메커니즘인데 liveness 추정으로 배달을 막으면 그 이득을 버린다.

포기한 것 / 한계
- 배달 창(1h)이 곧 online-but-crashed task 의 timeout 감지 상한. agent 가 완전 소실돼 `task.result` 를 못 보내면 최대 1h pending 후 timeout(대부분 실패는 agent 가 failure 를 명시 발행하므로 즉시 반영 — 1h 대기는 agent 소실 케이스 한정).
- 오프라인 유예도 1h bounded — 그 이상 오프라인이던 호스트가 돌아오면 메시지는 이미 만료라 재발행 필요.

왜 받아들였나
- zombie 지연 실행 제거 + 유령 pending 제거(reaper) + 오프라인 store-and-forward 유지를 한 상수로 달성. 온라인 실패 감지 지연은 실질적으로 agent 소실 케이스만이라 파급 작음.

언제 다시 봐야 하는가
- 오프라인 유예와 실행 timeout 을 독립 조절해야 하면 -> task 에 pickup(running) 신호를 추가해 "배달 TTL(길게) + 실행 deadline(픽업부터 짧게)" 2-타임아웃 모델로 분리. 현재는 pickup 신호가 없어 단일 창.

## T18. 용량 runway 전체 이력 집계 — cagg 하한 술어 예외 (ADR 0052)

무엇을
- `report_aggregate` 의 mount_span CTE 는 `server_mount_usage_5m` cagg 를 `WHERE server_id = ANY(:sids) AND bucket <= :end` 로 조회한다 — 다른 CTE(분류·포화·품질)가 전부 `bucket >= :start`(14일 창) 인 것과 달리 하한 술어가 없다. 용량 runway(바이트·inode 소진 일수)는 실제 관측 span 전체의 fill_rate 로 산출하기 때문이다(#C5 partition pruning 하한 술어 원칙의 의식적 예외).

왜 전체 이력인가
- CPU·메모리 이용률은 변동 신호라 최근 14일 대표 부하로 p95 를 뽑는다(오래된 데이터는 지금을 대변 못 함). 반면 디스크 용량은 누적 신호라 채워지는 속도(추세)가 곧 모델이고, 데이터가 길수록 기울기가 정확하다. 14일 창으로 자르면 완만히 차는 볼륨의 runway 를 과소·과대 추정한다. 그래서 runway 만 분류 창과 분리해 전체 이력을 쓴다(윈도우 3분리 기준, #F10).

포기한 것 / 한계
- 하한 없는 조회라 해당 서버의 데이터 볼륨 마운트 전 chunk 를 스캔한다 — cagg 보존 기간이 길어질수록 스캔량이 unbounded 로 증가. 현재는 5분 버킷·데이터 볼륨 한정이라 규모가 작아 수용하나, cagg retention 이 수개월+로 늘면 runway 조회 비용이 선형 증가한다.
- cagg 재생성(마이그레이션) 직후엔 materialized 청크가 비어 real-time aggregation(raw hypertable)에 의존하는데, raw `server_mount_usage` 보존 기간이 필요 이력보다 짧으면 오래된 endpoint 를 잃어 runway 가 짧은 span 으로 근사된다(ADR 0043 패턴 내, 운영 관찰 대상).
- 2점(first/last) 선형 fill_rate 라 비단조(정리 후 급락·계절 변동)에 약하다. principle 초안은 Theil-Sen 강건 추정을 명세하나 SQL O(n^2) 비용으로 미채택 — 완만·단조 증가 가정에서만 신뢰(#E3 report mapper 소비, 신뢰도 축이 짧은 이력을 흡수).

왜 받아들였나
- 용량 추세는 누적 신호라 전체 이력이 정답이고, 현재 fleet 규모(5분 버킷·데이터 볼륨 한정)에서 스캔 비용이 작다. 분류(14일)와 runway(전체)를 한 쿼리에서 서로 다른 창으로 뽑아 왕복을 줄인다.

언제 다시 봐야 하는가
- cagg retention 확대로 runway 조회가 느려지면 -> mount_span 에 실용 상한(예: 90일) 하한 술어를 넣어 pruning 복원. 비단조 추세가 오판을 일으키면 -> Theil-Sen(샘플링 점쌍) 또는 최근 구간 가중 회귀로 격상.
