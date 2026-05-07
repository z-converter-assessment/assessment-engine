# 설계 트레이드오프

본 문서는 본 프로젝트가 의도적으로 채택한 설계 선택과 그로 인해 받아들인 한계를 정리한다.
다른 합리적 대안이 있다는 것을 인지한 채 단순성·운영 비용·구현 범위(scope)를 기준으로 의식적으로 결정한 것들이며, **버그가 아니다**.

각 항목은 다음 형식이다:

> **선택**: 채택한 방식
> 
> **대안**: 대신 가능했던 방식
> 
> **트레이드오프**: 무엇을 얻고 무엇을 포기했는가
> 
> **언제 다시 봐야 하는가**: 이 선택이 더 이상 유효하지 않은 시점

---

## T1. 멱등성: at-most-once + 2단 방어 (fail-open 1단)

> **관련 코드**: `src/assessment_engine/consumer/handler.py` `_check_idempotent`, `src/assessment_engine/db/redis.py` `safe_set_nx`, `src/assessment_engine/db/repositories/collect_repository.py` `insert_metric`
>
> **관련 문서**: CLAUDE.md §D2, `docs/decisions/redis-decoupling.md`

**선택**
1. Redis `SET idempotent:{message_id} 1 EX 86400 NX` (DB 커밋 이전). **fail-open** — Redis 장애 시 처리 진행.
2. 시계열 4개 테이블에 `(server_id, [dim,] collected_at)` UNIQUE + `pg_insert.on_conflict_do_nothing`. Redis 장애·evict로 1단이 깨져도 2단이 silent no-op으로 흡수.

**대안**
- **at-least-once + outbox 패턴**: DB 커밋과 message ack를 동일 트랜잭션에 묶고, ack 실패 시 outbox 테이블에서 재처리. 메시지 유실 0건 보장.
- **at-least-once + 자연키 멱등 INSERT만**: SET NX를 빼고 DB UNIQUE만으로 중복 차단. 메시지 유실은 막지만 중복 처리 비용 발생.

**트레이드오프**
- **얻은 것**: 가장 빠른 중복 차단 (Redis 1회 RTT), 단순한 구현 (outbox 테이블·트랜잭션 동기화 불필요). Redis 장애 시 시스템 회복력 확보 (fail-open).
- **포기한 것**:
  - **at-most-once 한계**: SET NX 후 DB 커밋 전 프로세스가 크래시하면 RabbitMQ 재전송 메시지가 idempotent 키 충돌로 silent 드롭 → 데이터 유실 가능. 1단(Redis)이 먼저 차단하므로 2단(DB UNIQUE)도 이 시나리오는 해결 못 함.
  - **fail-open의 비용**: Redis 장애 동안 1단의 빠른 차단(RTT)이 사라지고 매 메시지가 DB UNIQUE까지 도달. 트래픽 규모에서 영향 미미하지만 트래픽 증가 시 재평가 필요.

**왜 받아들였나**
- 1분 주기 metrics는 1건 유실의 시각화 영향이 작음 (다음 사이클에서 회복).
- inventory는 에이전트 재시작 시 재발행 (one-shot 보장이 약하지만 운영상 충분).
- B2B 내부 포털이라 통계 정확성보다 간결한 운영을 우선.
- fail-open은 2단(DB UNIQUE)의 흡수력에 명시적으로 의존 — 시계열 4개 테이블 UNIQUE 제약이 정상 동작해야 함.

**언제 다시 봐야 하는가**
- exactly-once 보장이 계약상 필요해질 때 (감사 로그·과금 연동 등).
- consumer 프로세스 크래시가 자주 관찰될 때.
- → outbox 패턴으로 전환.

---

## T2. 캐시 일관성: cache-aside (write-around)

> **관련 코드**: `src/assessment_engine/web/services/query_service.py` `get_latest_metric`, `src/assessment_engine/consumer/handler.py` metrics handler
> **관련 문서**: CLAUDE.md §D4

**선택**
- Web: cache MISS → DB query → `SET cache:metrics 60s`
- Consumer: DB COMMIT → `DEL cache:metrics` → `PUBLISH metrics.events`

**대안**
- **write-through**: consumer가 DB COMMIT 후 직접 ViewModel을 빌드해 Redis에 SET. cache MISS 자체가 발생 안 함.
- **read-through with version**: cache 키에 version stamp를 두고 SET 전에 다시 한 번 비교.

**트레이드오프**
- **얻은 것**: consumer가 web의 ViewModel/직렬화 로직을 모름 — 계층 결합도 낮음. 캐시 SET 책임이 단일 위치(web).
- **포기한 것**: **cache-aside race** — web이 cache MISS 후 DB query를 마쳤지만 SET을 수행하기 전에 consumer가 새 metrics 커밋 + cache DELETE를 끝낼 수 있다. 이 경우 web의 SET은 stale 데이터를 60s TTL로 캐싱.

**왜 받아들였나**
- SSE가 즉시 다음 fetch를 트리거하므로 stale 캐시는 최대 1회 표시 지연.
- 메트릭 자체가 60s 주기라 60s TTL stale은 실용적 영향이 작음.
- write-through는 consumer가 web 로직을 알게 되어 컴포넌트 경계 위반.

**언제 다시 봐야 하는가**
- 메트릭 주기가 분 단위 미만으로 짧아지거나, stale 표시가 비즈니스 영향(잘못된 알람) 일으킬 때.
- → version stamp 또는 single-flight 캐시.

---

## T3. 시계열 무한 누적

> **관련 코드**: `src/assessment_engine/db/models/server_metrics.py`, `src/assessment_engine/db/models/server_disk_io.py` 등
> **관련 문서**: CLAUDE.md §C1

**선택**
- TimescaleDB hypertable에 raw 메트릭을 그대로 누적. retention/aggregation 정책 없음.

**대안**
- **retention policy**: `add_retention_policy('server_metrics', INTERVAL '90 days')`로 오래된 청크 자동 drop.
- **continuous aggregate**: 분 단위 raw + 시간/일 단위 aggregate를 미리 계산해두고 차트가 aggregate 조회.

**트레이드오프**
- **얻은 것**: 가장 단순한 운영. 모든 raw 데이터를 영구 보존해 사후 분석 자유도 높음.
- **포기한 것**: 디스크 사용량 무한 증가. 30일 차트가 raw 30일 데이터를 매번 time_bucket로 집계 — 데이터 양 증가에 따라 응답 느려짐.

**왜 받아들였나**
- 현재 운영 중인 서버 수(VM 3대)와 1분 주기에서는 1개월 데이터가 ~130k행/서버. 운영 부담 미미.
- B2B 내부 포털이라 retention 요구사항이 명확하지 않음.

**언제 다시 봐야 하는가**
- 등록 서버가 100대 이상으로 증가할 때.
- 30일 차트 응답 시간이 200ms를 초과할 때.
- → continuous aggregate (분→시간→일 계층) + 90일 retention.

---

## T4. DEV 스키마 관리: web lifespan + create_all (prod skip)

> **관련 코드**: `src/assessment_engine/web/main.py` lifespan, `src/assessment_engine/db/models/`, `src/assessment_engine/config.py` `app_env`
> **관련 문서**: CLAUDE.md §C1, A2, `docs/dev-prod.md` §4 APP_ENV 마커

**선택**
- web 기동 시 `CREATE EXTENSION timescaledb` → `Base.metadata.create_all` → `create_hypertable(if_not_exists)`.
- consumer는 `depends_on web: condition: service_healthy`로 web 헬스체크 후 시작.
- **`APP_ENV=prod`일 때 lifespan이 자동 skip** — Alembic이 schema 관리 책임. `consumer depends_on web` 의존성도 단계적 제거 가능.

**대안**
- **Alembic**: 마이그레이션 스크립트로 스키마 관리. consumer가 web에 의존하지 않음.
- **수동 SQL**: 운영자가 docker compose 외부에서 SQL 실행.

**트레이드오프**
- **얻은 것**: 추가 도구 없이 빠른 개발 사이클. `docker compose up`만으로 스키마 생성.
- **포기한 것**: `create_all`은 **기존 테이블에 컬럼·제약을 추가하지 않음**. 모델 변경 시 `docker compose down -v`(데이터 손실)가 필요. 스키마 책임이 web에 섞여 SRP 위반. 프로덕션에 부적합.

**왜 받아들였나**
- 현재 단계는 개발/PoC. 데이터 영속성보다 빠른 반복.
- 프로덕션 배포 전에 Alembic 도입을 전제 (CLAUDE.md §C1에 명시).

**언제 다시 봐야 하는가**
- 첫 프로덕션 배포 직전.
- → Alembic 초기화 → 현재 스키마 dump → 초기 마이그레이션 작성 → `consumer depends_on web` 제거.

---

## T5. SSE 단일 채널 + 서버 측 필터링

> **관련 코드**: `src/assessment_engine/web/services/query_service.py` `stream_metrics_events`, `src/assessment_engine/consumer/handler.py` `redis.publish`

**선택**
- 모든 SSE 클라이언트가 `metrics.events` 단일 채널 구독. 서버 측에서 `payload.server_id == subscribed_server_id`로 필터링.

**대안**
- **채널 분리**: `metrics.events.{server_id}`로 publish/subscribe.

**트레이드오프**
- **얻은 것**: 채널 관리 단순. publish 측이 server_id를 알 필요 없음 (단일 channel name 상수).
- **포기한 것**: web이 자기 server_id 외 모든 메시지를 수신 후 버림. 트래픽 N배 증가 (N=서버 수).

**왜 받아들였나**
- B2B 내부 포털 — 동시 SSE 연결 수와 서버 수가 작음 (수십 대 미만).
- 채널 수가 늘면 Redis pubsub keyspace notification 비용 증가.

**언제 다시 봐야 하는가**
- 서버 수가 수백 대 이상.
- web 인스턴스가 SSE 트래픽으로 CPU 포화될 때.
- → 채널을 `metrics.events.{server_id}` 형태로 분리.

---

## T6. 클라이언트 차트 JS는 P3 명시 예외 (P4)

> **관련 코드**: `src/assessment_engine/web/templates/servers/*.html` `<script>` 블록, `src/assessment_engine/web/static/js/chart-utils.js`
> **관련 문서**: CLAUDE.md §E1 P4

**선택**
- 차트 JS에 그리드 계산·라벨 포매팅·Chart.js 옵션 조립 등의 연산을 허용 (P3 위반).
- 대신 5개 의무 규약 적용: sequence counter, capture-before-await, `Array.isArray`, 404 분기, suggestedMax 명명 상수.

**대안**
- **서버 사이드 SVG/PNG 차트**: matplotlib·plotly로 서버에서 이미지 생성. 클라이언트는 표시만.
- **WebComponent + 프레임워크**: Vue/React로 컴포넌트화하고 stale 응답을 컴포넌트 lifecycle로 관리.

**트레이드오프**
- **얻은 것**: range 토글·anchor 변경에 즉시 반응 (서버 라운드트립 0). Chart.js 4.4.3 한 번의 CDN script load만으로 동작 — 빌드 도구 불필요.
- **포기한 것**: 차트 JS가 P3 우회로가 될 가능성 — 임계값 분류·통계 재계산 같은 비즈니스 로직이 슬며시 들어올 수 있다. 5개 규약 누락 시 race condition·404 오인 등 미묘한 버그.

**왜 받아들였나**
- 동적 인터랙션이 필요한 차트가 ~10개. 서버 사이드 이미지 차트는 인터랙션 비용 큼.
- 프레임워크는 빌드/배포 파이프라인 도입 비용이 본 포털 규모와 맞지 않음.

**현재 상태 (2026-05-08 갱신)**
- 5개 규약 모두 cpu/memory/storage/network/performance 템플릿에 적용 완료.
- 공통 유틸은 `src/assessment_engine/web/static/js/chart-utils.js`로 추출 (T9).
- **inline `<script>` 본문 외부화 완료** — 5개 페이지 모두 `static/js/pages/{name}.js` 별도 파일. 페이지 간 회귀 격리·node syntax check 가능. 회귀 사례(sed 일괄 변환의 부작용으로 인한 `ReferenceError`)는 외부화로 한 파일 안에 가둠. 추후 ESLint·TS 도입 시 진입점 명확.

---

## T7. 에이전트 broker 자동 재연결 (이미 구현됨 — 진단 정정)

> **관련 코드**: `assessment-agent/src/publish.c`, `src/main.c` (외부 레포)
> **상태**: 이전 항목의 진단(자동 재연결 부재)은 **사실 오류**. 직접 코드 확인 결과 이미 구현되어 있음.

**실제 구현 (2026-05-07 확인)**
- `publish.c`: 매 publish가 fresh connection lifecycle (`amqp_new_connection` → `socket_open` → `login` → `channel_open` → publish → close → destroy). connection 재사용 안 함.
- `publish.c`: publisher confirm 모드 활성화 (`amqp_confirm_select(conn, 1)`) + wall-clock deadline ack 대기 (`wait_confirm`, 기본 5초 `RABBITMQ_CONFIRM_TIMEOUT_SEC`).
- `main.c` `publish_with_retry`: 지수 백오프 (1s → 2s → 4s → ... → max=AGENT_INTERVAL_SEC, 기본 60s) 무한 retry. `g_stop` 시그널 전까지.
- 복구 시 `PUBLISH_RECOVERED` error 메시지 자동 발행 (`retry_count`, `first_failed_at`, `recovered_at` 포함).

**즉 broker 재기동 시 자동 회복**
- broker 죽음 → 에이전트 publish 실패 → 백오프 retry 시작 → broker 살아남 → 다음 retry 사이클에서 publish 성공 → `PUBLISH_RECOVERED` 알림 메시지 → 정상 운영 복구.
- 사람이 `systemctl restart assessment-agent` 할 필요 없음.

**예전 운영 사례의 진짜 원인**
- "broker 재기동 후 systemctl restart 필요"로 잘못 해석된 시나리오의 실제 원인은 **`docker compose down -v`로 DB까지 초기화된 후 inventory가 one-shot이라 재발행 안 됨**.
- 에이전트는 broker 재연결 정상 → metrics는 정상 publish → DB의 server_inventory가 비어있어 `metrics dropped — server not registered` 누적.
- 의제 A(주기 inventory 재발행, `docs/meetings/2026-05-08-agent-protocol.md`) + 엔진 측 auto-register(`src/assessment_engine/consumer/handler.py`)로 본질적 해결됨.

**남은 한계**
- broker가 영구 down이면 에이전트는 백오프 상한(60s) 간격으로 영원히 재시도 — 정상 동작이지만 로그·CPU 미세 부담.
- inventory 메시지 자체가 broker down 동안 발행 시도되면 retry로 해결되나, **에이전트 기동 직후 inventory 1회 발행 후 다시 안 보내는 정책**은 별도 약점 (의제 A로 해결).

---

## T8. ListServers ORM 부분 SELECT vs full row

> **관련 코드**: `src/assessment_engine/db/repositories/query_repository.py` `list_servers`

**선택**
- `select(ServerInventory.id, .public_id, .machine_id, ...)`로 11개 컬럼만 명시 SELECT. `mounts`/`listen_ports`/`kernel_version`/`boot_time`/`swap_total_kb`/`agent_version`/`last_seen_at`/`ip_internal`/`os_codename`/`cpu_model` 제외.

**대안**
- `select(ServerInventory)` 풀로우 SELECT (이전 구현).
- ORM lazy loading + 필요한 속성만 접근.

**트레이드오프**
- **얻은 것**: 페이로드 축소 — 큰 JSONB 컬럼(`mounts`, `listen_ports`)을 페이지당 N개 행에서 직렬화하지 않음.
- **포기한 것**: 컬럼 추가 시 list 화면에 노출하려면 SELECT 목록을 함께 갱신 (DRY 위반 작은 케이스).

**왜 받아들였나**
- list 화면은 페이지당 20개 서버 — JSONB 컬럼 직렬화/네트워크 비용이 작지 않음.
- 컬럼 추가 빈도가 낮고, 추가 시 mapper도 함께 변경되는 게 자연스러워 SELECT 갱신을 잊을 가능성 낮음.

**언제 다시 봐야 하는가**
- ServerInventory에 컬럼이 자주 추가될 때.
- → ORM 컬럼 그룹(`deferred()` 또는 별도 entity) 도입 검토.

---

## T9. 차트 공통 JS — 인라인 중복 제거 (chart-utils.js 추출)

> **관련 코드**: `src/assessment_engine/web/static/js/chart-utils.js`, `src/assessment_engine/web/main.py` `StaticFiles` 마운트, `src/assessment_engine/web/templates/servers/*.html`

**선택**
- 5개 차트 템플릿에 흩어진 공통 정의(`fmtKst` / `bindToggle` / `COLORS` / `AUTO_BUCKET` / `BUCKET_MS` / `makeBucketGrid` / `joinToGrid` / `fmtLabel` / `getAnchorEnd` / `initAnchor` / SSE 초기화)를 `/static/js/chart-utils.js`로 추출. `base.html`에서 단일 로드 → 전역 `ChartUtils` IIFE 객체 노출.
- 각 템플릿은 상단에서 `const { ... } = ChartUtils;`로 destructure.

**대안**
- **인라인 유지** (이전 상태): 각 템플릿이 자기 사본을 가짐. 빌드 도구 불필요, 한 파일만 보면 모든 로직 파악.
- **번들러 도입 (Vite/esbuild + ESM)**: import/export로 모듈화. 트리쉐이킹·타입체크 가능.
- **WebComponent**: 차트를 컴포넌트로 분리하고 props/이벤트로 인터랙션.

**트레이드오프**
- **얻은 것**: 중복 정의가 한 곳으로 — 시그니처 변경 시 5곳 수정 → 1곳 수정. 템플릿 평균 200~500 라인이던 인라인 `<script>`가 200~300 라인으로 감소 (차트 데이터셋 빌드 로직만 남음).
- **포기한 것**: 모듈 시스템(import/export) 없음. `ChartUtils.X` 또는 destructure 형태로만 노출. 의존 그래프가 명시적이지 않음. 타입체크 없음 (TS 도입 X).

**왜 받아들였나**
- 본 포털은 번들러 운영 비용(node_modules·빌드 스텝·소스맵·CI 변경) 대비 이득 작음.
- IIFE + 단일 로드는 브라우저 캐싱 친화적이고 디버깅이 단순.

**현재 상태 (2026-05-08 갱신) — Phase 6**
- 5개 페이지 inline `<script>` 본문 외부화 완료: `src/assessment_engine/web/static/js/pages/{cpu,memory,storage,network,performance}.js`.
- 페이지 .html은 짧은 inline `<script>`로 Jinja2 변수만 정의(`SERVER_ID`, `CPU_CORES`) + 외부 .js를 `defer` 로드.
- 정적 자원 6개: `chart-utils.js` + 5개 페이지 .js. **node 의존성 0** — 빌드 단계 추가 없이 외부화만으로 회귀 격리 + node syntax check 가능.

**언제 다시 봐야 하는가**
- TS 타입체크 또는 ESLint 정적 검증 필요성이 명확해질 때.
- → 의존 최소: `tsc --checkJs --noEmit` (빌드 산출 없음, JSDoc 주석으로 타입) 또는 `eslint --rule no-undef` (npm 1개).
- 빌드 도입은 Vite/esbuild — `app.mount("/static", ...)`을 `dist/`로 변경.

---

## T10. ViewModel 비대화 vs 클라이언트 재계산 (P5 적용)

> **관련 코드**: `src/assessment_engine/web/view_models.py`, `src/assessment_engine/web/services/mappers.py`, `src/assessment_engine/web/services/metrics_calculator.py`
> **관련 문서**: CLAUDE.md §E1 P5 / §E4

**선택**
- `ListenPortItem.is_well_known` (port ≤ 1024 boolean)
- `ServerDetailResponse.sorted_services` / `sorted_listen_ports` (mapper 정렬 결과)
- `MountUsageItem.badge_class` / `bar_color` (임계값 → CSS 클래스/hex)
- `MemSnapshot.cached_pct` / `buffers_pct` (stacked-bar 누적 비율)

→ 이 파생 필드를 **모두 mapper에서 미리 계산**해 ViewModel에 둠. 템플릿/JS는 read-only.

**대안**
- **클라이언트 재계산**: 템플릿이 `{% if p.port <= 1024 %}` / `| sort` / 임계값 `{% if pct >= 90 %}`, JS가 `mem.cached_kb / total * 100`. ViewModel은 raw에 가깝게 유지.

**트레이드오프**
- **얻은 것**: P3·P5 정합 — 템플릿/JS는 표시만. 임계값/정렬 규칙 변경 시 mapper 한 곳만 수정. 캐시된 ViewModel과 SSR 직후 ViewModel이 항상 동일한 표현 결과를 만듦.
- **포기한 것**: ViewModel 필드 수 증가(`ServerDetailResponse`만 +5필드). dataclass 필드 순서 제약(`non-default follows default`)으로 default factory 필드를 끝으로 모아야 함. 캐시 직렬화 페이로드 미세 증가.

**왜 받아들였나**
- 임계값 변경(예: 90 → 85)이 발생할 때 클라이언트·서버 분산이면 한쪽 누락 가능성 큼. 본 포털은 임계값 정책이 향후 조정 가능성 있음.
- 캐시 페이로드 증가는 정렬·boolean 정도라 실측상 무시 가능.

**언제 다시 봐야 하는가**
- ViewModel 필드가 50개 이상으로 비대해질 때 — 화면별 sub-ViewModel로 분리.

---

## T11. 단일 Redis 인스턴스 — 캐시·멱등성·PubSub 한 통합 (fail-open)

> **관련 코드**: `src/assessment_engine/db/redis.py` `safe_*` helper, `src/assessment_engine/consumer/handler.py`, `src/assessment_engine/web/services/query_service.py`
> **관련 문서**: `docs/components/redis.md`, `docs/decisions/redis-decoupling.md`

**선택**
- 한 Redis 인스턴스에서 5가지 역할 동시 처리: 캐시 / 온라인 TTL / 멱등성 / PUB/SUB / public_id 해석.
- eviction 정책 `volatile-lru` (TTL 있는 키만 evict 대상).
- 모든 Redis 호출은 `src/assessment_engine/db/redis.py`의 `safe_*` helper 경유 — **fail-open 정책**.
- list 화면 online 표시는 `last_seen_at` 컬럼 fallback 보유.

**대안**
- **분리**: 캐시용 / 멱등성용 / PUB/SUB용 인스턴스 분리.
- **외부 시스템**: idempotency를 PostgreSQL `INSERT ... ON CONFLICT DO NOTHING` 으로 (멱등성 키 테이블).
- **인터페이스 추상화 (옵션 E)**: `BaseCache`/`BaseEventBus`/`BasePresenceTracker` 추상으로 Redis 자체를 옵션화. Redis를 다른 캐시로 교체할 계획이 없으므로 채택 안 함.

**트레이드오프**
- **얻은 것**:
  - 운영 단순. docker compose 1개 컨테이너. 코드의 `get_redis()` 1개 함수.
  - **fail-open 정책으로 운영 결합도가 통상 수준 도달** — Redis 장애 시 web은 느려질 뿐 응답 가능, consumer는 DLQ 누적 없이 처리 진행.
- **포기한 것**:
  - 멱등성 키도 `volatile-lru` 대상 — maxmemory 압박 시 evict 가능 → 1단 방어 깨짐. fail-open과 동일하게 DB UNIQUE(2단)이 흡수 (T1과 연결).
  - PUB/SUB 부하가 캐시 hit/miss 응답 latency에 영향 가능.
  - Redis 단일 장애 시 5가지 역할 모두 영향 — 단 fail-open으로 시스템 다운은 회피. SSE는 끊김(브라우저 자동 재연결), 멱등성 1단은 우회(DB가 흡수), 캐시는 DB 직접 조회.

**왜 받아들였나**
- B2B 내부 포털 — 동시 요청 수·멱등성 키 수가 작아 evict 시나리오 드묾.
- 단일 인스턴스로 운영 비용 최소화.
- Redis를 다른 캐시 시스템으로 교체할 계획 없음 — 인터페이스 추상화는 무의미한 복잡도 증가.

**언제 다시 봐야 하는가**
- 멱등성 키 evict가 실제 관찰될 때 (Redis `INFO stats` `evicted_keys` 모니터링).
- → 멱등성을 PostgreSQL 테이블로 옮기거나, Redis를 namespace별로 분리.