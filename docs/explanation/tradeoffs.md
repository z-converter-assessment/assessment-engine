# 설계 트레이드오프

의식적 설계 선택과 그로 인한 한계 카탈로그. 단순성·운영 비용·scope 기준 결정이라 버그가 아니다.

각 항목은 선택 / 대안 / 트레이드오프 / 언제 다시 봐야 하는가를 담는다. T 번호는 추가 순서일 뿐이고, 삭제된 번호는 재사용하지 않는다.

---

## T1. 멱등성: at-most-once + 2단 방어 (fail-open 1단)

> 관련 코드: `src/assessment_engine/consumer/handlers/` `_check_idempotent`, `src/assessment_engine/cache/redis.py` `safe_set_nx`, `src/assessment_engine/db/repositories/collect_sql.py` `record_metrics`
>
> 관련 문서: AGENTS.md #D2 · #C1

선택
1. 1단은 DB 커밋 이전의 Redis `SET idempotent:{message_id} 1 EX 86400 NX` 다. fail-open 이라 Redis 장애 시에도 처리를 진행한다.
2. 2단은 시계열 metric 7테이블의 자연키 UNIQUE + `pg_insert.on_conflict_do_nothing` 이다. Redis 장애·evict 로 1단이 깨져도 2단이 silent no-op 으로 흡수한다.

대안
- at-least-once + outbox 패턴: DB 커밋과 message ack 를 동일 트랜잭션에 묶고, ack 실패 시 outbox 테이블에서 재처리한다. 메시지 유실을 0 으로 보장한다.
- at-least-once + 자연키 멱등 INSERT 만: SET NX 를 빼고 DB UNIQUE 만으로 중복을 차단한다. 메시지 유실은 막지만 중복 처리 비용이 든다.

트레이드오프
- 얻은 것: Redis 1회 RTT 로 가장 빠르게 중복을 차단하고, outbox 테이블·트랜잭션 동기화 없이 구현이 단순하다. fail-open 이라 Redis 장애에도 회복력이 있다.
- 포기한 것:
  - at-most-once 한계 — SET NX 후 DB 커밋 전에 프로세스가 크래시하면 RabbitMQ 재전송 메시지가 idempotent 키 충돌로 silent 드롭돼 데이터가 유실될 수 있다. 1단이 먼저 차단하므로 2단도 이 시나리오는 해결하지 못한다.
  - fail-open 의 비용 — Redis 장애 동안 1단의 빠른 차단이 사라지고 매 메시지가 DB UNIQUE 까지 도달한다. 현재 트래픽 규모에서는 영향이 미미하나 트래픽이 늘면 재평가가 필요하다.

왜 받아들였나
- 1분 주기 metrics 는 1건 유실의 시각화 영향이 작다 (다음 사이클에서 회복).
- inventory 는 에이전트 재시작 시 재발행된다 (one-shot 보장이 약하지만 운영상 충분).
- B2B 내부 포털이라 통계 정확성보다 간결한 운영을 우선한다.

언제 다시 봐야 하는가
- exactly-once 보장이 계약상 필요해질 때 (감사 로그·과금 연동 등).
- consumer 프로세스 크래시가 자주 관찰될 때.
- -> outbox 패턴으로 전환.

## T2. 캐시 일관성: cache-aside (write-around)

> 관련 코드: `src/assessment_engine/web/services/query/metric.py` `get_latest_metric`, `src/assessment_engine/consumer/handlers/` metrics handler
> 관련 문서: AGENTS.md #D1

선택
- web 은 cache MISS 시 DB 를 조회하고 그 결과를 `cache:metrics` 에 SET 한다.
- consumer 는 DB COMMIT 후 `cache:metrics` 를 DEL 한다.

대안
- write-through: consumer 가 DB COMMIT 후 직접 ViewModel 을 빌드해 Redis 에 SET 한다. cache MISS 자체가 발생하지 않는다.
- read-through with version: cache 키에 version stamp 를 두고 SET 전에 다시 한 번 비교한다.

트레이드오프
- 얻은 것: consumer 가 web 의 ViewModel·직렬화 로직을 몰라 계층 결합도가 낮고, 캐시 SET 책임이 web 한 곳에 있다.
- 포기한 것: cache-aside race — web 이 cache MISS 후 DB 조회를 마쳤지만 SET 하기 전에 consumer 가 새 metrics 커밋과 cache DELETE 를 끝낼 수 있다. 이 경우 web 의 SET 이 stale 데이터를 TTL 동안 캐싱한다.

왜 받아들였나
- 브라우저 30초 polling 이 다음 주기에 다시 fetch 하므로 stale 캐시는 최대 1회 표시 지연이다.
- 메트릭 자체가 60s 주기라 60s TTL stale 은 실용적 영향이 작다.
- write-through 는 consumer 가 web 로직을 알게 되어 컴포넌트 경계를 넘는다.

언제 다시 봐야 하는가
- 메트릭 주기가 분 단위 미만으로 짧아지거나, stale 표시가 비즈니스 영향(잘못된 알람) 일으킬 때.
- -> version stamp 또는 single-flight 캐시.

## T3. 시계열 raw 무한 누적 (retention 정책 없음)

> 관련 코드: `src/assessment_engine/db/models/server_metrics.py`, `src/assessment_engine/db/models/server_disk_io.py` 등
> 관련 문서: AGENTS.md #C1 · #C5

선택
- TimescaleDB hypertable 의 raw 메트릭을 무기한 보존한다. 무거운 집계는 5분 버킷 continuous aggregate 가 흡수하고(#C5), 만료 정책만 두지 않는다.

대안
- retention policy: `add_retention_policy('server_metrics', INTERVAL '90 days')` 로 오래된 청크를 자동 drop 한다.

트레이드오프
- 얻은 것: 운영이 가장 단순하고, 모든 raw 데이터가 남아 사후 분석 자유도가 높다.
- 포기한 것: 디스크 사용량이 무한히 증가한다. 차트(`get_metric_trend`, 동적 버킷)는 목적상 raw 를 조회하므로 range 가 길수록 스캔량이 늘어난다.

왜 받아들였나
- 소규모 dev 환경 서버 수와 1분 주기에서는 1개월 데이터가 서버당 약 130k 행이라 운영 부담이 미미하다.
- B2B 내부 포털이라 retention 요구사항이 명확하지 않다.

언제 다시 봐야 하는가
- 등록 서버가 100대 이상으로 증가할 때.
- 30일 차트 응답 시간이 200ms를 초과할 때.
- -> 90일 retention policy 도입.

## T5. 실시간 메트릭 전달: 30초 polling

> 관련 코드: `src/assessment_engine/web/services/query/metric.py` `get_latest_metric`, `src/assessment_engine/web/static/js/pages/detail.js`, `chart-utils.js` `initAutoRefresh` (4탭 공용)

선택
- 서버 상세 실시간 메트릭과 4탭(cpu/memory/storage/network) 현재 상태는 브라우저가 30초 주기로 `/api/servers/{id}/metrics/latest` 를 재요청해 갱신한다. 환경 실시간 갱신도 같은 주기다.
- consumer 메트릭 후처리는 DB 저장 + `online:{id}` SET + `cache:metrics` DEL 까지고 push 채널을 두지 않는다.

트레이드오프
- 얻은 것: pubsub 채널·SSE 스트림 핸들러·구독 클라이언트 관리가 없다. web 이 자기 server_id 외 메시지를 수신·필터링할 일도 없다.
- 포기한 것: 푸시 즉시성 — 최대 30초 표시 지연. 갱신 없는 구간에도 주기 요청이 발생한다.

왜 받아들였나
- 메트릭 자체가 60s 주기라 30초 polling 으로 충분하고, B2B 내부 포털 규모(수십 대)에서 polling 트래픽은 무시 가능하다.
- SSE 단일 채널 필터링·Redis pubsub keyspace 비용·브라우저 자동 재연결 처리 등 push 경로 복잡도를 전부 지운다.

언제 다시 봐야 하는가
- 초 단위 즉시성이 필요해지거나 polling 트래픽이 web 부하로 드러날 때.
- -> SSE 또는 WebSocket push 재도입 (별도 ADR).

## T6. 클라이언트 차트 JS는 P3 명시 예외 (P4)

> 관련 코드: `src/assessment_engine/web/static/js/pages/`, `src/assessment_engine/web/static/js/chart-utils.js`
> 관련 문서: AGENTS.md #E1 P4, `docs/reference/web/static-assets.md`

선택
- 차트 JS 에 그리드 계산·라벨 포매팅·Chart.js 옵션 조립 등의 연산을 허용한다 (P3 위반).
- 대신 5 의무 규약을 적용한다 (규약 본문은 `docs/reference/web/static-assets.md` 단일 진실).

대안
- 서버 사이드 SVG/PNG 차트: matplotlib·plotly 로 서버에서 이미지 생성. 클라이언트는 표시만.
- WebComponent + 프레임워크: Vue/React 로 컴포넌트화하고 stale 응답을 컴포넌트 lifecycle 로 관리.

트레이드오프
- 얻은 것: range 토글·anchor 변경에 서버 라운드트립 없이 즉시 반응한다.
- 포기한 것: 차트 JS 가 P3 우회로가 될 가능성 — 임계값 분류·통계 재계산 같은 비즈니스 로직이 슬며시 들어올 수 있다. 5 규약 누락 시 race condition·404 오인 등 미묘한 버그가 난다.

왜 받아들였나
- 동적 인터랙션이 필요한 차트가 열 개 안팎이라 서버 사이드 이미지 차트는 인터랙션 비용이 크다.
- 프레임워크는 빌드/배포 파이프라인 도입 비용이 본 포털 규모와 맞지 않는다.

## T7. 에이전트 broker 자동 재연결

> 관련 코드: `assessment-agent/src/publish.c`, `src/main.c` (외부 레포)

구현
- 매 publish 가 fresh connection lifecycle 이다 (connection 재사용 없음). publisher confirm 모드 + wall-clock deadline ack 대기(기본 5초 `RABBITMQ_CONFIRM_TIMEOUT_SEC`).
- publish 실패는 지수 백오프(1s 부터 상한 `AGENT_INTERVAL_SEC`, 기본 60s)로 정지 시그널 전까지 무한 retry 한다. 복구 시 `PUBLISH_RECOVERED` error 메시지를 자동 발행한다.

broker 재기동 시 에이전트는 다음 retry 사이클에서 publish 에 성공해 스스로 회복하므로 `systemctl restart assessment-agent` 가 필요 없다. inventory 가 비어 있는 데이터베이스로 metrics 가 먼저 도착하는 경우는 1시간 주기 inventory 재발행과 엔진 auto-register 가 흡수한다.

남은 한계
- broker 영구 down 시 에이전트는 백오프 상한(60s) 간격으로 영원히 재시도한다 — 정상 동작이나 로그·CPU 에 미세한 부담이 남는다.

## T8. ListServers ORM 부분 SELECT vs full row

> 관련 코드: `src/assessment_engine/db/repositories/query/server_sql.py` `list_servers`

선택
- 목록 화면이 쓰는 컬럼만 명시 SELECT 하고 큰 JSONB 컬럼은 뺀다. 서비스 뱃지는 ingest 사전계산 `service_categories`(text[])를 쓰므로 `services` JSONB 역직렬화가 필요 없다. 정확한 컬럼 목록은 코드가 단일 진실이다.

대안
- `select(ServerInventory)` 풀로우 SELECT.
- ORM lazy loading + 필요한 속성만 접근.

트레이드오프
- 얻은 것: 페이지당 N 개 행에서 큰 JSONB 컬럼을 직렬화하지 않아 페이로드가 줄어든다.
- 포기한 것: 새 컬럼을 목록 화면에 노출하려면 SELECT 목록을 함께 갱신해야 한다 (DRY 위반의 작은 사례).

왜 받아들였나
- list 화면은 페이지당 20개 서버라 JSONB 컬럼 직렬화·네트워크 비용이 작지 않다.
- 컬럼 추가 빈도가 낮고, 추가 시 mapper 도 함께 바뀌는 게 자연스러워 SELECT 갱신을 잊을 가능성이 낮다.

언제 다시 봐야 하는가
- ServerInventory 에 컬럼이 자주 추가될 때.
- -> ORM 컬럼 그룹(`deferred()` 또는 별도 entity) 도입 검토.

## T9. 클라이언트 JS — 브라우저 네이티브 ESM, 번들러 미도입

> 관련 코드: `src/assessment_engine/web/static/js/`, `src/assessment_engine/web/templates/base.html` importmap, `tsconfig.json` paths

선택
- 공용 유틸은 ESM 모듈로 두고 페이지 .js 가 `import` 로 부른다. bare specifier(`@/chart-utils`)를 base.html `importmap` 이 `?v=` 붙은 실 URL 로 해석한다.
- 번들러는 두지 않는다 — 브라우저가 파일을 그대로 받는다. 빌드 산출물 0, node 는 dev/CI 전용(타입 계약 검사·codegen)이라 런타임 의존이 아니다.

대안
- 번들러 도입 (Vite/esbuild): 트리쉐이킹·코드 분할. 대신 node_modules·빌드 스텝·소스맵·CI 변경이 런타임 경로에 들어온다.
- 페이지별 사본: 빌드 도구 불필요하고 한 파일만 보면 되지만, 공통 정의가 페이지 수만큼 갈라진다.
- WebComponent: 차트를 컴포넌트로 분리. 지금 필요한 것은 함수 재사용이지 캡슐화가 아니다.

트레이드오프
- 얻은 것: 의존 그래프가 `import` 문으로 명시된다. tsc 가 구현에서 타입을 추론하므로 손으로 미러링한 ambient 선언이 필요 없고, 따라서 그 선언이 구현과 어긋날 자리도 없다.
- 포기한 것: 번들이 없어 모듈 수만큼 요청이 난다. HTTP/2 다중화와 모듈 캐시로 흡수되는 범위라 판단.

왜 받아들였나
- ESM 은 브라우저 기본 기능이라 번들러를 기각한 근거(node_modules·빌드 스텝·소스맵)가 여기엔 적용되지 않는다.
- 캐시 무효화는 importmap 이 맡는다. 상대 경로 import 를 쓰면 Jinja 가 그 URL 에 손댈 수 없어 재배포 후 브라우저가 옛 모듈을 캐시에서 꺼낸다.

언제 다시 봐야 하는가
- 모듈 수가 늘어 초기 요청 수가 체감될 때 -> Vite/esbuild 번들 도입, `app.mount("/static", ...)` 을 `dist/` 로 변경.
- vendor UMD(Chart·cytoscape)가 ESM 배포판을 내면 -> `globals.d.ts` 의 마지막 ambient 선언 둘도 제거.

## T10. ViewModel 비대화 vs 클라이언트 재계산 (P2 따름)

> 관련 코드: `src/assessment_engine/web/view_models/`, `src/assessment_engine/web/services/mappers/`, `src/assessment_engine/web/services/mappers/metric_dashboard.py`
> 관련 문서: AGENTS.md #E1 P2 · #E3, `docs/reference/web/view-models.md`

선택
- 정렬 결과·임계값 -> CSS 클래스/hex·누적 비율 같은 표시 파생을 모두 mapper 에서 미리 계산해 ViewModel 에 둔다. 템플릿과 JS 는 read-only 다. 파생 필드 카탈로그는 `docs/reference/web/view-models.md` 가 갖는다.

대안
- 클라이언트 재계산: 템플릿이 포트 범위 비교·`| sort`·임계값 비교를 하고 JS 가 비율을 나눈다. ViewModel 은 raw 에 가깝게 유지.

트레이드오프
- 얻은 것: P2·P3 정합 — 템플릿과 JS 는 표시만 한다. 임계값·정렬 규칙 변경 시 mapper 한 곳만 고치면 되고, 캐시된 ViewModel 과 SSR 직후 ViewModel 이 항상 같은 표현 결과를 만든다.
- 포기한 것: ViewModel 필드 수가 늘어난다. dataclass 필드 순서 제약(`non-default follows default`) 때문에 default factory 필드를 끝으로 모아야 하고, 캐시 직렬화 페이로드도 미세하게 커진다.

왜 받아들였나
- 임계값을 조정할 때 계산이 클라이언트·서버로 흩어져 있으면 한쪽을 빠뜨리기 쉽다. 본 포털은 임계값 정책이 앞으로 조정될 여지가 있다.
- 캐시 페이로드 증가는 정렬 결과·boolean 정도라 실측상 무시 가능하다.

언제 다시 봐야 하는가
- ViewModel 필드가 50개 이상으로 비대해질 때 -> 화면별 sub-ViewModel 로 분리.

## T11. 단일 Redis 인스턴스 — 모든 용도 한 통합 (fail-open)

> 관련 코드: `src/assessment_engine/cache/redis.py` `safe_*` helper, `src/assessment_engine/consumer/handlers/`, `src/assessment_engine/web/services/query/`
> 관련 문서: `docs/reference/redis.md`

선택
- 한 Redis 인스턴스가 캐시·온라인 TTL·멱등성·시그널 쿨다운을 모두 담는다. 용도별 키·TTL·eviction 정책 카탈로그는 `docs/reference/redis.md` 가 갖는다.
- 모든 Redis 호출은 `safe_*` helper 를 거치는 fail-open 이고, list 화면 online 표시는 `last_seen_at` 컬럼 fallback 을 갖는다.

대안
- 분리: 캐시용 / 멱등성용 인스턴스 분리.
- 외부 시스템: 멱등성 키 테이블을 두고 PostgreSQL `INSERT ... ON CONFLICT DO NOTHING` 으로 처리.
- 인터페이스 추상화: `BaseCache`/`BaseEventBus`/`BasePresenceTracker` 추상으로 Redis 자체를 옵션화.

트레이드오프
- 얻은 것: 컨테이너 하나·접속 함수 하나로 운영이 단순하다. fail-open 이라 Redis 장애 시 web 은 느려질 뿐 응답하고, consumer 는 DLQ 누적 없이 처리를 이어간다.
- 포기한 것:
  - 멱등성 키도 TTL 이 있어 evict 대상이다. maxmemory 압박 시 1단 방어가 깨지고 DB UNIQUE(2단)가 흡수한다 (T1 과 연결).
  - Redis 단일 장애가 online 판정·캐시·멱등성 세 역할에 동시에 미친다. fail-open 으로 시스템 다운은 피하지만, 멱등성 1단은 우회되고 캐시는 DB 직접 조회로 떨어지며 online 은 폴링 신선도에 기댄다.

왜 받아들였나
- B2B 내부 포털이라 동시 요청 수·멱등성 키 수가 작아 evict 시나리오가 드물다.
- 단일 인스턴스가 운영 비용이 가장 낮다.
- Redis 를 다른 캐시 시스템으로 교체할 계획이 없어 인터페이스 추상화는 복잡도만 늘린다.

언제 다시 봐야 하는가
- 멱등성 키 evict 가 실제 관찰될 때 (Redis `INFO stats` `evicted_keys` 모니터링).
- -> 멱등성을 PostgreSQL 테이블로 옮기거나, Redis 를 namespace 별로 분리.

## T12. server_inventory 호스트 식별 — 불변 agent_id 단독 UNIQUE

> 관련 코드: `src/assessment_engine/db/models/server_inventory.py`, `src/assessment_engine/db/repositories/collect_sql.py`
> 관련 문서: AGENTS.md #C1, `docs/reference/db/models.md`, `docs/reference/contracts/agent-data.md`

선택
- `server_inventory` UNIQUE 는 `agent_id` 단독이다. agent 가 첫 실행 시 1회 생성해 영구저장하는 불변 UUID 라, 부팅마다 NIC MAC 이 재발급되는 환경(OpenStack Windows VM)에서도 같은 행을 upsert 한다. 별도 재연결 로직이 없다.
- `composite_id`·`machine_id` 는 clone collision 진단용 감사 컬럼이라 식별·라우팅에 쓰지 않고, hostname 은 표시용이라 UNIQUE 가 아니다.

대안 (채택 안 함)
- composite_id 단독 UNIQUE: machine_id + MAC 해시로 식별. 부팅마다 MAC 이 재발급되는 환경에서 값이 변동해 같은 호스트가 새 행으로 갈라진다 — 이를 흡수하려면 machine_id+hostname 재연결 로직이 필요하고, 미sysprep clone 오병합 위험이 잔존한다. 불변 agent_id 는 변동원 자체를 제거하므로 재연결 불요.
- hardware UUID (`/sys/class/dmi/id/product_uuid`) 우선 + machine-id fallback: VM clone 시 hardware UUID 도 동일 가능. agent 변경 필요.
- 운영자 부여 server_id (install 시 운영자가 UUID 주입): install workflow 에 등록 step 추가.

트레이드오프
- 얻은 것:
  - VM 템플릿 복제·이미지 clone·container `/etc/machine-id` 마운트로 machine_id·MAC 이 겹치거나 재부팅마다 변동해도 식별이 안정적이다. hostname 을 바꿔도 같은 행을 유지한다.
  - agent 가 자기 agent_id 로 queue 를 subscribe 하므로 라우팅 키가 불변이고, 두 호스트가 같은 큐를 공유하는 message race 가 없다.
- 포기한 것:
  - agent_id 저장소를 잃으면(디스크 초기화·agent_id 미보존 re-image) 새 agent_id 가 발급돼 새 row 로 INSERT 되고 history 가 끊긴다. 같은 호스트가 둘로 갈린다.
  - agent_id 파일까지 그대로 복제한 clone 은 두 호스트가 같은 agent_id 를 공유해 한 행으로 오병합된다. 감사 컬럼이 collision 을 진단용으로 노출할 뿐 자동 분리는 하지 않는다.

왜 받아들였나
- agent_id 저장소 소실과 미regenerate clone 은 둘 다 운영에서 드문 예외고, 흔한 케이스(재부팅 MAC 변동·hostname 변경)는 agent_id 불변으로 자연히 흡수된다.
- agent_id 생성·영구저장은 agent 와 이미 합의된 계약이라, 엔진은 이 단독 키로 식별·라우팅을 완결하고 payload 추가 합의가 필요 없다.

언제 다시 봐야 하는가
- agent_id 미regenerate clone 오병합이 운영에서 관측될 때 -> composite_id 기반 collision 자동 분리 또는 agent 측 clone 감지(sysprep 유도).
- agent_id 저장소 소실로 history 끊김이 잦으면 -> agent 측 저장 위치 견고화 또는 보조 식별자 병합 절차.

## T13. 보고서 = diagnostic_jobs 통합 (job_type)

> 관련 코드: `src/assessment_engine/db/models/diagnostic_job.py`, `src/assessment_engine/web/services/diagnostic_service.py`
> 관련 문서: AGENTS.md #C1, `docs/reference/db/models.md`

선택
- 보고서 발행은 별도 테이블을 만들지 않고 `diagnostic_jobs` 에 row 를 남긴다. `job_type` 이 customer/engineer 를 가르고, server scope 와 environment scope 는 같은 테이블 위에서 양식만 다르다.
- view(고객/엔지니어)는 발행 시점에 고정된다. 스냅샷 1건이 view 1개를 담고, 다른 view 는 별도 발행이다.
- 발행 이력은 두 job_type 을 union 한 단일 페이지에서 추적한다.

대안
- 보고서를 별도 테이블 `report_jobs` 로 분리. 모델은 명확해지지만 두 테이블을 union 하는 표시 SQL 이 복잡해진다.

트레이드오프
- 얻은 것: 보고서별 별도 service·테이블 신설 없이 기존 diagnostic_jobs 를 재사용하고, 발행 이력을 한 페이지에서 통합 추적한다.
- 포기한 것:
  - 발행 1회가 row 1건이다 (N대 선택은 parent 1 + child N). 같은 입력을 더블클릭하면 active partial UNIQUE 로 기존 job 에 합류하지만, 시각·view 를 바꿔 반복 발행하면 이력 row 가 선형으로 늘어난다. `delete_retention` 은 리포지토리에 있으나 호출하는 실행 경로가 없어 오래된 row 가 그대로 남는다.
  - 양식(템플릿)을 바꾸면 옛 스냅샷은 발행 당시 구조 그대로라 현행 양식과 어긋날 수 있다. 스냅샷에 양식 버전을 태깅하지 않는다.

왜 받아들였나
- 보고서 발행은 운영자의 명시 액션이라 매 발행이 의미 있는 이벤트고, 이력 row 로 남길 값어치가 있다.
- 스냅샷 보존이 목적이라 재조회가 raw data 변경과 무관하게 발행 시점을 그대로 재현한다.

언제 다시 봐야 하는가
- 이력 row 가 폭증하면 -> worker 에 retention purge tick 추가(`delete_retention` 주기 호출).
- 양식 변경으로 옛 스냅샷과 현행 템플릿 불일치가 문제가 되면 -> 스냅샷에 양식 버전 태깅.

## T14. Windows saturation 임계 근거 비대칭 + perflib 의존

right-sizing 분류는 USE Method 의 Utilization + Saturation 두 축을 본다. saturation 3축 모두 OS별 실측 신호로 정규화된다(os-aware helper 단일 진실) — CPU 포화는 Linux procs_running(실행 큐) / Windows Processor Queue Length, 메모리 포화는 Linux paging_major(refault) / Windows Memory Pages Input/sec rate, 디스크 IO 포화는 양 OS 모두 await p95(await 를 못 읽는 구세대 viostor Windows 만 Avg Disk Queue Length 폴백). Windows 도 세 포화 축을 실측하되, 신호원과 임계 근거의 성숙도가 다르다.

loadavg 와 iowait 은 수집하되 판정 입력으로 쓰지 않는다 — loadavg 는 D-state IO 블록이 섞여 오염되고, iowait 은 게스트 CPU 스케줄링 왜곡을 탄다(virtio).

받아들인 한계
- Windows 메모리 포화는 Memory\Pages Input/sec 의 rate p95 로 판정한다. 총 Pages/sec 은 mmap 파일 I/O 가 섞여 부풀려지므로(관측 82775) 쓰지 않고, 하드 read 폴트만 세는 Pages Input/sec 이 순수 압박 신호다. 임계는 Microsoft·업계 관례(5=증설, 20=체감 저하, 100=thrashing) 중 체감 저하 지점을 쓴다. 고정 임계라 워크로드별 미세 편차가 남는다.
- saturation 축은 perflib·diskperf 에 의존한다. Windows 에서 해당 카운터를 못 읽거나 미부착이면(예: OpenStack virtio 에 diskperf 미부착) 그 축만 미관측이 된다. 분류는 utilization·capacity 와 측정된 나머지 포화 축으로 완결하고, 못 본 축만 포화 수치 미관측이라는 confidence 단서로 노출한다.
- Windows pagefile 사용량은 수집·표시하되 saturation 판정에 넣지 않는다. pagefile 은 여유 RAM 에서도 상시 baseline 을 차지하므로 사용량이 아니라 페이징 rate 로 판정한다.

왜 받아들였나
- Windows 가 노출하지 않는 신호를 0 이나 baseline 으로 날조해 분류에 넣으면(예: iowait=0 을 "IO 여유" 로 읽으면) 더 큰 왜곡이 난다. 미측정은 미측정으로 두는 편이 정직하다.
- disk queue·CPU run queue·메모리 하드폴트 세 축 모두 Microsoft·업계 관례 임계라 근거를 추적할 수 있다.
- "부분 평가" 마커가 운영자에게 confidence 한계를 알린다. 침묵하는 오분류보다 가시화된 한계가 낫다.

언제 다시 봐야 하는가
- Windows 메모리 페이징 오탐·누락이 관측되면 -> 실측 분포로 `WIN_PAGES_INPUT_SATURATION` 재보정.
- perflib 미발행이 특정 Windows 환경에서 상시화되면 -> agent 측 수집 경로 점검. 엔진은 미관측으로 정직하게 처리하고, 신호원 자체는 agent 저장소 이슈다.

## T15. 서비스 분류 — pid 부재 유닛의 per-unit 귀속 한계 (호스트 union 으로 보완)

agent 는 `services[]` 에 pid/exe 를, `listen_ports[]` 에 pid/comm 을 싣는다. 양쪽에 pid 가 있으면 `_attributed_ports` 가 동일 pid 소켓만 귀속해 per-unit 분류(`classify_service`)가 확정된다. pid 가 null 인 구간(소켓 액티베이션 리스너·비-systemd 열거·권한 부족 Windows 포트)에서만 `comm~name` substring -> name well-known 포트 순 fallback 이라, 이름이 comm 과 무관한 opaque 서비스를 그 구간에서 per-unit 으론 못 잡는다.

fallback 은 `comm == "systemd"` 인 소켓을 귀속에서 제외한다. 소켓 액티베이션 리스너(pid null)의 보유자는 systemd 매니저라 comm 이 "systemd" 인데, 이 generic 이름이 양방향 substring 매칭으로 모든 `systemd-*.socket` 유닛명에 걸린다. 그대로 두면 매니저가 든 22(ssh) 등 타 소켓까지 한 유닛으로 흡입해 최저 well-known 포트로 오분류한다. 매니저 placeholder 는 특정 유닛 소유의 증거가 아니다. systemd-resolved 처럼 자기 comm 을 가진 데몬은 이 제외에 걸리지 않고 정상 매칭된다. 제외 조건 한 항이 fallback 조건에서 군더더기로 보여 되돌리기 쉬우므로 근거를 여기 둔다.

pid 부재 구간 보완 — 호스트 워크로드 union:
- 뱃지·role·환경분포는 per-unit 분류에 의존하지 않고, `detect_listen_categories(listen_ports)` 로 listen 소켓을 직접 분류(comm/port)해 services 이름 분류와 합집합(`workload_category_counter`)한다. listen 소켓의 comm·port 는 깨끗하고 안정적인 식별자라 opaque 이름을 우회한다 — `MSSQL$무엇` 이든 1433/`sqlservr` 로 db 를 탐지한다.
- 이 union 은 ingest 시 1회 계산해 저장하고 모든 read 경로가 그 저장값만 쓴다 (메커니즘은 `docs/reference/web/services.md`).

포기한 것 (union 후 잔존):
- listen 하지 않거나 localhost-only 로 바인드하는 워크로드가 opaque 이름을 가지면 두 소스 모두 못 잡아 미상으로 남는다. listen 하는 워크로드는 union 으로 거의 구제된다.
- pid 미발행 유닛에 한해 services 탭의 행별 카테고리가 이름 기반 best-effort 다. 호스트 뱃지는 union 으로 db 가 표시되더라도 opaque unit 은 그 행에서 unknown 이다.

왜 받아들였나
- per-unit 귀속 게이트를 풀어 임의 listen 포트를 아무 unknown service 에 붙이면 multi-service 호스트(nginx:80 + opaque:1433)에서 오분류가 난다. 그래서 per-unit 은 보수적으로 두고 호스트 레벨에서만 union 으로 보충한다 — set 합집합이라 오분류가 아니라 탐지 누락 보완이다.
- 분류 산출물(뱃지·role)은 본질적으로 "이 호스트가 무슨 워크로드를 도느냐" 의 근사고, listen 이 그 질문의 직접 증거다.

언제 다시 봐야 하는가
- pid null 구간이 좁혀지면(소켓 액티베이션 리스너의 소유자 해석 등) -> `comm~name` fallback 휴리스틱과 systemd 제외 조건을 함께 제거.

## T16. 비동기 보고서 발행 — 전용 워커 job-claim (DB 상태머신)

무엇을
- 보고서 발행은 비동기다. emit 은 parent job 을 pending 으로 enqueue 하고 즉시 job id 를 돌려주며, 전용 워커 프로세스(`assessment_engine.worker`)가 job 을 claim 해 생성한다. consumer 큐 워커가 아니라 전용 워커 + DB 상태머신 방식이다.

왜 consumer 큐 워커가 아니라 전용 워커 프로세스인가
- 보고서 생성 코드가 web/services 에 강하게 묶여 있다. consumer 로 위임하면 web 표시계층 절반을 web 비의존 패키지로 승격하는 대공사에 양방향 의존까지 생긴다. 워크로드가 수초짜리 DB 집계 I/O 라 큐로 분리해 얻을 것도 적다. 전용 워커는 web/services 를 단일 이미지 안에서 그대로 재사용하면서 프로세스만 뗀다.
- 메모리 task 방식은 in-flight 손실 위험 때문에 기각했다. job 상태를 DB 에 두면 stale 복구로 그 손실을 무효화하고, FOR UPDATE SKIP LOCKED 로 멀티노드 분산까지 얻는다.

포기한 것 / 한계
- 워커가 web/services 를 import 하는 패키지 의존이 단일 이미지 전제에 묶인다. web/services 를 중립 패키지로 추출하려면 별도 ADR 이 필요하다 (현재 불필요하고 런타임에는 무해하다).
- 크래시·타임아웃으로 parent 가 running 에 잔류했다가 stale 복구 후 재처리되면, 이전 run 에서 이미 succeeded 로 만든 child 가 orphan 으로 이력에 중복될 수 있다. parent 가 succeeded 일 때 `child_jobs` 는 최신 유효분을 가리키므로 데이터 정합성 허점은 아니다. 다만 orphan child 를 자동 정리하는 경로가 없어 이력에 남는다.
- child fan-out 은 raws·breakdown·details 를 배치 1회로 조회하지만 trend·online 조회는 서버별로 남는다. cpu/mem/disk 가 다른 테이블이라 단일 SQL 로 못 묶고, 서버별 시계열이라 배치도 안 된다.

왜 받아들였나
- 발행이 즉시 job id 를 돌려주므로 대상 N 이 늘어도 사용자 응답 시간이 일정하다. 가장 큰 불만이던 발행 지연이 여기서 해소된다.
- 도메인 추출을 하지 않아 큐 워커 대공사와 회귀 위험을 피하면서 in-flight 손실 0·멀티노드·graceful 을 얻는다. 전용 프로세스라 생성 부하가 web HTTP 처리와 격리된다.

언제 다시 봐야 하는가
- 생성 처리량이 부족하면 -> worker replica 를 늘려 수평 확장(SKIP LOCKED 로 중복 claim 안전, web 과 독립 스케일).
- orphan child 중복이 운영 이슈로 부상하면 -> child 멱등(같은 input_hash 의 succeeded child 재사용 조회를 신설) 또는 parent 재처리 전 이전 child cleanup.

## T17. install 배달 창 단일 정합 — deadline == 큐 TTL

무엇을
- install task 의 engine `tasks.deadline_at` 과 broker 큐 `x-message-ttl` 을 하나의 창(`install_task_deadline_sec`)으로 통일한다. 오프라인 대상은 발행을 막지 않고 비차단 advisory(store-and-forward + `target_online` 알림)로 처리하며, 무회신 pending 은 reaper 가 전역 timeout 으로 전이시킨다.

왜 단일 창인가
- 두 타임아웃이 어긋나면 엔진이 timeout 을 선언한 뒤에도 메시지가 큐에 살아 있어, 뒤늦게 재접속한 agent 가 이미 실패 처리된 task 를 실행하는 zombie 지연 실행이 생긴다. 동일 창이면 엔진이 포기하는 시점과 broker 가 배달을 포기하는 시점이 같아 이 상태가 만들어지지 않는다.
- 오프라인을 게이트로 막지 않는다. online 은 Redis TTL 스냅샷이라 stale 하고 racy 하다. durable 큐와 TTL 이 간헐 연결을 흡수하는 메커니즘인데, liveness 추정으로 배달을 막으면 그 이득을 버린다.

포기한 것 / 한계
- 배달 창이 곧 online-but-crashed task 의 timeout 감지 상한이다. agent 가 완전히 소실돼 `task.result` 를 못 보내면 그 창만큼 pending 으로 있다가 timeout 된다. 대부분의 실패는 agent 가 failure 를 명시 발행하므로 즉시 반영되고, 이 대기는 agent 소실 케이스에 한정된다.
- 오프라인 유예도 같은 창으로 bounded 다. 그보다 오래 오프라인이던 호스트가 돌아오면 메시지가 이미 만료라 재발행해야 한다.

왜 받아들였나
- zombie 지연 실행 제거, 유령 pending 제거, 오프라인 store-and-forward 유지를 상수 하나로 달성한다. 온라인 실패 감지 지연은 실질적으로 agent 소실 케이스뿐이라 파급이 작다.

언제 다시 봐야 하는가
- 오프라인 유예와 실행 timeout 을 독립 조절해야 하면 -> task 에 pickup(running) 신호를 추가해 배달 TTL 과 실행 deadline 을 가르는 2-타임아웃 모델로 분리. 현재는 pickup 신호가 없어 단일 창이다.

## T18. 용량 runway 전체 이력 집계 — cagg 하한 술어 예외

무엇을
- 하한 술어 없는 조회는 둘이다. 하나는 아래 mount_span 이고, 다른 하나는 환경 개요의 fleet 에러 집계(`get_fleet_error_summary(since=epoch)`)다 — 에러는 드문 이벤트라 창을 제한하면 최근에 안 난 장애가 화면에서 사라지고, 집계 비용도 낮아 창을 두지 않는다.
- `get_report_aggregate` 의 mount_span CTE 는 `server_filesystem_5m` cagg 를 `WHERE server_id = ANY(:sids) AND bucket <= :end` 로 조회한다 — 다른 CTE(분류·포화·품질)가 전부 `bucket >= :start`(평가 윈도우) 인 것과 달리 하한 술어가 없다. 용량 runway(바이트·inode 소진 일수)는 실제 관측 span 전체의 fill_rate 로 산출하기 때문이다(#C5 partition pruning 하한 술어 원칙의 의식적 예외).

왜 전체 이력인가
- CPU·메모리 이용률은 변동 신호라 최근 평가 윈도우의 대표 부하로 p95 를 뽑는다(오래된 데이터는 지금을 대변 못 함). 반면 디스크 용량은 누적 신호라 채워지는 속도(추세)가 곧 모델이고, 데이터가 길수록 기울기가 정확하다. 평가 윈도우로 자르면 완만히 차는 볼륨의 runway 를 과소·과대 추정한다. 그래서 runway 만 분류 창과 분리해 전체 이력을 쓴다(윈도우 2분리 기준, #F10).

포기한 것 / 한계
- 하한 없는 조회라 해당 서버의 데이터 볼륨 마운트 전 chunk 를 스캔한다 — cagg 보존 기간이 길어질수록 스캔량이 unbounded 로 증가. 현재는 5분 버킷·데이터 볼륨 한정이라 규모가 작아 수용하나, cagg retention 이 수개월+로 늘면 runway 조회 비용이 선형 증가한다.
- cagg 재생성(마이그레이션) 직후엔 materialized 청크가 비어 real-time aggregation(raw hypertable)에 의존하는데, raw `server_filesystem` 보존 기간이 필요 이력보다 짧으면 오래된 endpoint 를 잃어 runway 가 짧은 span 으로 근사된다(continuous aggregate 패턴 내, 운영 관찰 대상).
- 2점(first/last) 선형 fill_rate 라 비단조(정리 후 급락·계절 변동)에 약하다. 강건 추정(Theil-Sen)은 SQL O(n^2) 비용으로 미채택 — 완만·단조 증가 가정에서만 신뢰(#E3 report mapper 소비, 신뢰도 축이 짧은 이력을 흡수).
- runway 가 None 인 이유는 둘이다 — 관측 span 이 rate 산출 최소치에 못 미쳐 추세를 아직 못 낸 경우와, span 은 충분한데 free 가 줄지 않아 추세상 안 채워지는 경우. 표시는 이 둘을 "N/A (관측 부족)" 와 "안정 (추세 없음)" 으로 갈라 보여주는데, 그 구분에 쓰는 값은 마운트별 span 이 아니라 host-level `history_hours` 근사다. 같은 agent 가 모든 마운트를 함께 수집하므로 수집 시작점이 거의 같다는 전제다. 마운트마다 시작점이 실제로 갈리는 환경에서는 두 원인이 뒤바뀔 수 있다.

왜 받아들였나
- 용량 추세는 누적 신호라 전체 이력이 정답이고, 현재 fleet 규모(5분 버킷·데이터 볼륨 한정)에서 스캔 비용이 작다. 분류(14일)와 runway(전체)를 한 쿼리에서 서로 다른 창으로 뽑아 왕복을 줄인다.

언제 다시 봐야 하는가
- cagg retention 확대로 runway 조회가 느려지면 -> mount_span 에 실용 상한(예: 90일) 하한 술어를 넣어 pruning 복원. 비단조 추세가 오판을 일으키면 -> Theil-Sen(샘플링 점쌍) 또는 최근 구간 가중 회귀로 격상.
- 마운트별 정확한 span 이 필요해지면 -> `get_report_aggregate` 에 마운트 span 컬럼을 추가해 host-level `history_hours` 근사를 대체.

## T19. Assessment API — 포털 표준에서의 의식적 이탈 (pagination·캐시·인증)

무엇을
- `/api/assessment` 와 그 POST export 는 인터랙티브 포털의 세 표준에서 벗어난다. pagination 이 없어 매칭 전량을 한 응답으로 낸다(#E2 page/cursor 규약 이탈). Redis 캐시가 없어 매 요청이 매칭 fleet 전체를 `get_report_aggregate` 로 재계산한다. 인증이 없어 전체 인프라 청사진(재현 레이아웃·IP·사이징)을 관리망 격리만 믿고 노출한다.
- 소비자 계약 관점의 선언은 `docs/reference/contracts/assessment-api.md` 10절이 갖고, 여기는 엔진측 설계 근거와 확장 트리거만 담는다.

왜 표준을 벗어났나
- pagination: 소비자가 인터랙티브 사용자가 아니라 재해복구·마이그레이션 자동화다. fleet 프로비저닝은 원자적 전량 소비가 목적이라, 페이지 슬라이스로는 부분 인프라만 재현돼 under-provision 위험이 있다. cursor 와 페이지는 각각 계속 새 데이터가 들어오는 시계열과 사람이 스크롤하는 목록을 전제하는데 assessment 는 스냅샷 1회 소비라 둘 다 맞지 않는다. 스코프 축소는 필터로 한다.
- 캐시 없음: assessment 는 저빈도 운영·자동화 액션이라 지연보다 신선도와 정확성이 앞선다. per-mount 디스크와 수십 필드 스냅샷은 변동이 커 캐시 churn 이 높고, stale 사이징은 안전 최우선 원칙에 반한다. `get_report_aggregate` 는 cagg 사전집계라 현 fleet 규모에서 비용이 작다.
- 무인증: 관리망 전용 내부 B2B 포털이라 나머지 화면과 같은 신뢰 경계에 있다. 이 엔드포인트만 별도 토큰 게이트를 세우면 포털 전체 인증 모델이 이원화된다.

포기한 것 / 한계
- 대규모 fleet 을 필터 없이 호출하면 매칭 전량을 캐시 없이 매번 재계산한다. 현재 규모에서는 수백 ms 수준이나, 수천 대에 고빈도 폴링이면 반복 재계산이 선형 비용이 된다.
- 이 엔드포인트 하나가 전체 인프라를 가장 진하게 노출하는 단일 지점이다. 관리망 격리가 뚫리면 노출이 여기 집중된다.
- 전량 응답이라 응답 크기가 매칭 수에 선형이다. 필터를 걸지 않으면 fleet 전체가 한 JSON 으로 나간다.

왜 받아들였나
- 소비자가 자동화의 1회 스냅샷 소비라 페이지·캐시·토큰 세 표준이 오히려 목적에 역행하거나 무가치하다. 현재 규모에서 전량·무캐시·무인증의 비용과 위험이 작고, 정확하고 신선한 값을 우선하는 안전 원칙과도 맞는다.

언제 다시 봐야 하는가
- 단일 필터 스코프 응답이 실용 한계(응답 크기/지연)를 넘으면 -> cursor 또는 스트리밍(NDJSON) 분할.
- assessment 가 고빈도 자동 폴링이 되면 -> (filter, window, end-bucket) 키의 짧은 TTL 캐시 도입(대시보드 패턴 준용).
- 외부 노출이 필요해지면 -> 앞단 인증 게이트웨이(계약 10절 명시) + 이 엔드포인트의 인프라 노출 집중도를 감안한 인가 스코프.

## T20. 실시간 스냅샷 포화 축 — 윈도우 분류 경로와의 미세 원자료·경계 불일치

무엇을
- 포화 판정에는 두 경로가 있다: (A) 평가 윈도우 분류·환경·보고서 = `right_sizing` 도메인의 os-aware verdict helper(`cpu_saturated`·`mem_saturated`·`disk_io_saturated`, dual-gate) 경유(#E3). (B) 실시간 현황·서버 상세 순간 스냅샷 = sibling index/active helper(`cpu_saturation_index`·`mem_pressure_active`·`disk_io_saturation_index`·`net_signal_active`, 목적상 single-gate) 경유. 두 경로가 같은 임계 상수를 공유하나, 두 축에서 미세하게 어긋나 같은 서버가 화면 간 다른 포화 판정을 낼 수 있다.
- 네트워크(실무 심각도 중): 서버 상세 실시간 네트워크 축(`metric_dashboard.build_saturation_signals`)이 단일 진실 `net_signal_active` 를 경유하지 않고 ratio 비교를 직접 조립한다. 두 이탈 — (1) 저트래픽 게이트 부재: `net_signal_active`/`assess_network` 는 트래픽 < `NET_MIN_TRAFFIC_KBPS`(10 kB/s)면 retrans/drop 을 억제하나, 실시간 net 축은 무조건 임계 비교(`SaturationRaw` 에 net traffic 필드가 없어 구조적으로 게이트 불가). (2) 경계 연산자: 실시간은 `>=`, `net_signal_active` 는 strict `>`. -> 유휴 저트래픽 서버의 retrans 1.5% 나 정확히 1.0%/0.5% 경계값이 서버 상세엔 "혼잡", 환경/보고서엔 "정상".
- 디스크 await(실무 심각도 하): `disk_io_saturated` 는 `await_p95 > DISKIO_AWAIT_MS`(strict `>`)로 보고, `disk_io_saturation_index` 소비 게이트는 `>= 1.0`(실질 `await >= 20`)으로 본다. 두 helper 는 신호 선택(await 우선, Windows 미측정 시 큐 깊이 폴백)만 같고 경계 연산자가 다르다 — await 이 정확히 20.000ms 인 지점에서만 갈린다(measure-zero).

왜 이대로 두나
- 두 경로는 목적이 다르다. 윈도우 분류는 dual-gate(신호 AND 이용률)로 오탐을 억제한 결론이고, 실시간은 순간의 단일 신호 crossing 을 그대로 보여주는 스냅샷이다. single-gate 는 위반이 아니라 실시간의 의도된 정의고, #E3 의 취지인 "임계 재계산·직접 해석 금지" 는 두 경로 모두 충족한다 — 같은 도메인 상수를 재사용하고 소비처에서 임계를 다시 선언하지 않는다.
- 네트워크의 게이트·경계 정합은 `SaturationRaw` 에 net traffic(kB/s) 필드를 추가해야 근본적으로 고쳐진다. 스키마와 배선을 함께 바꾸는 일인데 실무 발현이 저트래픽과 경계값이라는 희소 지점뿐이라 유예한다.

포기한 것 / 한계
- 저트래픽이면서 retrans/drop 이 임계 근처인 서버는 서버 상세 실시간 네트워크에서 "혼잡" 으로, 같은 서버의 환경 요약·보고서에서 "정상" 으로 갈릴 수 있다.
- disk await 이 정확히 20.000ms 인 서버는 윈도우 분류에서 비포화, 실시간에서 포화로 갈린다. measure-zero 라 실측에서 마주칠 일은 거의 없다.

왜 받아들였나
- 실시간 순간 스냅샷과 윈도우 통계 분류는 애초에 다른 축이고, 불일치가 저트래픽과 경계 measure-zero 라는 희소 지점에만 나타난다. 근본 정합은 원자료 스키마 확장을 요구해 비용 대비 즉시 이득이 작다.

언제 다시 봐야 하는가
- 화면 간 네트워크 판정 불일치가 실제 운영 혼선을 부르면 -> `SaturationRaw` 에 net traffic 필드 추가 후 실시간 net 축을 `net_signal_active` 경유로 교체(저트래픽 억제 + strict `>` 통일).
- disk await 경계를 정합하려면 -> index 소비 게이트를 `> 1.0` 으로 좁히거나 `disk_io_saturated` 를 `>=` 로 통일.

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
- 에이전트가 계약 버전을 올리지 않고 값 종류를 바꾸면(minor 로 잘못 판단하거나 실수로) 그 필드를 쓰는 축이 조용히 유실된다. 매퍼가 값 타입 차이를 흡수해 폭을 좁혀 두었으나, 흡수 범위 밖의 변경은 드러나지 않는다.

왜 받아들였나
- 배포 비대칭이 이 시스템의 전제다. 에이전트가 고객사마다 다른 버전으로 떠 있어 "현재 에이전트 버전" 이라는 단일 기준 자체가 없다.

언제 다시 봐야 하는가
- 에이전트 저장소와 CI 를 연동할 수 있게 되면 -> C 소스에서 발행 필드 목록을 추출해 예시·스키마와 대조하는 게이트 추가.
- 실 환경에서 원인 불명의 축 유실이 관측되면 -> 그 시점에 메시지를 캡처해 예시 파일을 갱신하고, 같은 유형을 잡는 경계 fixture 를 추가.

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
- `build_resource_stats(raw, *, disk_baseline)` 의 `disk_baseline` 은 유휴 판정 활동 축(`right_sizing` 의 `IDLE_DISK_IOPS` 비교)이다. 이 값을 raw 에 실제로 채우는 코드는 보고서 prefetch(`query/report.py::_assemble_report_raws`) 하나뿐이다. 나머지 호출 경로는 `None` 을 명시적으로 넘기거나, raw 필드를 그대로 읽되 그 경로에서는 그 필드가 채워지지 않아 결과가 `None` 이다.
- 결과적으로 같은 호스트가 보고서에서는 디스크 활동을 근거로 `idle` 로 갈릴 수 있고, 서버 목록·환경 개요에서는 그 축이 미관측이라 `over_provisioned` 에 머무를 수 있다.

왜 이대로 두나
- 통일 방향이 둘인데 어느 쪽도 공짜가 아니다. (A) 전 경로에 주입하면 `get_report_disk_io_baseline` 쿼리가 서버 목록·환경 개요·계약 API 요청마다 붙는다 — 목록은 페이지당 수십 대, 환경 개요는 전체 인벤토리다. (B) 보고서에서 빼면 보고서의 유휴 판정이 지금보다 보수적으로 바뀌어 이미 발행된 스냅샷과 새 보고서가 갈린다.
- 어느 쪽이든 화면 분류가 실제로 바뀌므로 계약 개정에 해당한다.

포기한 것 / 한계
- 화면 간 유휴 판정 정합(#E3)이 이 축 하나에서만 깨져 있다. 활동이 거의 없는 호스트가 보고서에서만 유휴로 뜬다.

왜 받아들였나
- 비대칭이 코드에 드러나 있다. `disk_baseline` 이 필수 키워드라 새 호출 경로는 이 결정을 내리지 않고는 컴파일되지 않고, `tests/unit/test_resource_stats_inputs.py` 가 경로별 인자값과 "raw 필드를 채우는 코드는 보고서 경로 하나" 라는 단정을 함께 고정한다. 경로가 늘면 그 테스트가 먼저 걸린다.

언제 다시 봐야 하는가
- 유휴 판정을 근거로 실제 다운사이즈를 집행하기 시작하면 -> 화면 간 판정이 갈리는 것이 곧 운영 사고이므로 (A) 로 통일하고 baseline 쿼리를 목록·개요 경로에 배치(벌크 1회, N+1 금지).
- `get_report_disk_io_baseline` 이 cagg 사전집계로 충분히 싸지면 -> (A) 의 비용 근거가 사라지므로 재검토.
