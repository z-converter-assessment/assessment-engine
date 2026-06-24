# ADR 0042 — 서비스 카테고리 ingest 사전계산 + service_classifier 도메인 이전

상태: Accepted (2026-06-24)

## Context

서비스 카테고리(web/db/cache/mq/container/monitor) 판정이 화면마다 다른 코드 경로로 계산돼 결과가 불일치했다. 분류 신호는 셋 — 서비스 unit 이름 / listen 소켓 comm / listen 포트(ADR 0032). 상세·리포트는 `classify(unit, listen_ports)` + `detect_listen_categories` 로 세 신호 전부 반영하나, 서버 목록은 `ServerSummary` 경량 partial SELECT 라 `listen_ports` 를 안 실어 이름 신호만 썼다. 결과: 포트/comm 으로만 식별되는 워크로드(예: 6379 listen redis 인데 unit 이름이 중립)가 상세엔 `cache` 로 뜨는데 목록엔 안 떴다 (T15 비대칭).

분류 단일 진실 `service_classifier` 는 `web/services/` 에 있고 `web.view_models.server.MatchedPort` 에 의존했다. ingest(consumer)가 분류를 호출하려면 consumer -> web 역방향 의존이 생긴다.

## Decision

분류 결과를 ingest 시 1회 계산해 저장하고 모든 read 가 저장값을 읽는다.

- 저장: `server_inventory.service_categories text[]` (카테고리 키 집합). 마이그레이션 `a7c3e5f1b9d4` (+ 필터용 GIN 인덱스).
- 계산: `compute_service_categories(services, listen_ports)` = unit 이름 분류 ∪ listen 소켓 분류(unknown 제외, 정렬·dedup). `workload_category_counter` 키셋과 동일 분류 로직. inventory upsert(`to_inventory_create`)에서 호출.
- read: 목록·상세·리포트·필터가 저장값 소비 — 화면별 재계산 0, 이름·comm·포트 어느 신호로 식별되든 동일 집합. 목록은 services JSONB 재로드·행별 `classify` 제거(경량화).
- 레이어링(정석): `service_classifier` 를 `web/services/` -> 도메인 `assessment_engine/service_classifier.py` 로 이전 (`recommendation.py` 동급, web 역의존 0). `MatchedPort` 도 도메인으로 옮기고 web view_model 이 re-export. consumer·web 양쪽이 도메인 단일 진실을 import.

## Options

### A. 화면마다 같은 union 을 read 시 재계산
목록 SELECT 에 listen_ports JSONB 추가 후 행별 union. 일관은 되나 매 요청·행마다 무거운 JSONB 역직렬화 + 재분류 — 목록 경량 정책(#C2) 위반.

### B. ingest 사전계산 + 저장 (채택)
계산 1회(쓰기 경로) -> 모든 읽기 경로 소비. 목록 경량 유지 + 일관 보장. agent `AGENT_INVENTORY_REFRESH_SEC` 주기로 최신 추종.

### 핸드오프 문서와의 차이 (정석 우선)
핸드오프(`docs/temp/service-badge-unify-and-query-optimization.md`)는 "service_classifier 변경 없음, ingest 가 web 모듈 호출"을 제시했으나, consumer -> web 역의존을 피하려 분류 코드를 도메인 모듈로 이전(정석)했다. 결과(ingest 사전계산·단일 진실)는 동일.

## Consequences

- 화면 간 카테고리 집합 비대칭 0 (T15 목록-상세 비대칭 해소). per-unit 행 단위 귀속 한계(pid join 부재)는 유지(T15).
- 목록 쿼리에서 services JSONB 로드 + 행별 분류 제거 — 부수 최적화.
- 목록 뱃지는 카테고리 키 집합이라 인스턴스 카운트("db 2") 미표시 — 상세 뱃지와 일관(카운트 필요 경로는 `workload_category_counter` 유지).
- 백필: 기존 행은 일회성 recompute UPDATE 로 채움.
- 분류 단일 진실이 도메인 모듈 — consumer·web 공용, 추가 read 경로도 저장값만 소비.
