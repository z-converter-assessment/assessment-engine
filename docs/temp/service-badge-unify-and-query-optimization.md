# [작업 핸드오프] 서비스 뱃지 인식 단일화(ingest 사전계산) + 무거운 페이지 쿼리 최적화

> 작성: 2026-06-24. self-contained. 구현 담당자용. 2단계 작업: (1) 서비스 카테고리 인식 경로 단일화, (2) 느린 페이지 쿼리 최적화. (1)을 먼저 한다. (1)은 (2)의 일부(목록 페이지)를 자동으로 개선한다.

---

## 배경 — 서비스 뱃지 인식이 경로마다 다르다 (현 문제)

서비스 카테고리(web/db/cache/mq/container/monitor) 판정이 화면마다 다른 코드 경로로 계산돼 결과가 불일치한다.

신호는 3가지: 서비스 unit 이름 / listen 소켓 comm(프로세스명) / listen 포트번호. 그런데:

- 서버 상세·리포트: `detect_listen_categories(listen_ports)` (comm -> 포트인덱스) + `classify(unit, listen_ports)` 둘 다 사용 -> 이름·comm·포트 전부 반영.
  - `src/assessment_engine/web/services/mappers/server.py:424-425` (detail enrich), `mappers/report.py:424`.
- 서버 목록: `classify(unit, listen_ports=None)` 로 **이름 신호만** 사용. `detect_listen_categories` 미호출.
  - `mappers/server.py:256` `_services_or_none(dto.services, listen_ports=None)`.
  - 근본 원인: 목록 DTO `ServerSummary`(`src/assessment_engine/db/dtos/outbound.py`)에 `listen_ports` 필드가 없음(목록 쿼리가 경량 SELECT라 listen_ports 미로드). 그래서 포트/comm 기반 인식이 목록에 구조적으로 부재.

결과(실측 repro, 2026-06-24):
- suse12bios, centos6bios: 6379 listen 워크로드. 상세/리포트 = `cache` 인식. 목록 = 뱃지 없음(이름 미매칭 + 목록은 포트 안 봄).
- 즉 "상세엔 뜨는데 목록엔 안 뜨는" 불일치.

분류 단일 소스: `src/assessment_engine/web/services/service_classifier.py`
- `classify(unit, listen_ports)`: name -> (attributed) comm -> port. listen_ports 없으면 name만.
- `detect_listen_categories(listen_ports)`: services unit 무관, 소켓 comm -> `_PORT_INDEX[port]` (ADR 0032).
- `_PORT_INDEX`, `_NAME_INDEX`, `SERVICE_CATALOG` 가 카탈로그 단일 진실.

---

## 1단계 — 인식 경로 단일화 (채택: ingest 시점 사전계산)

### 목표
"어떤 신호(이름/comm/포트)로 식별되든" 모든 화면이 동일한 카테고리 집합을 보게 한다. 카테고리는 한 곳(ingest)에서 한 번 계산해 저장하고, 모든 read 경로는 저장값을 읽는다. 화면별 재계산/불일치 제거 + 목록 경량 유지.

### 설계
1. 저장: `server_inventory` 에 `service_categories` 컬럼 추가.
   - 타입 권장: `text[]`(카테고리 키 집합, 예: `{web,cache}`) — 목록 뱃지/필터에 충분하고 가벼움. 근거(매칭 포트/유닛)까지 필요하면 `jsonb`로 `{cache:[6379], web:[80]}` 형태도 가능하나, 상세는 이미 services+listen_ports를 로드하므로 근거는 상세에서 재계산해도 됨 -> 목록용은 `text[]` 카테고리 키만으로 충분.
   - 모델: `src/assessment_engine/db/models/`(server_inventory 모델)에 컬럼 추가.
   - 마이그레이션: alembic (`migrations/versions/<hash>_<desc>.py`, `alembic.ini`/`migrations/env.py`). dev는 migrate 컨테이너가 적용.

2. 계산(ingest): inventory upsert 시 `service_classifier` 단일 소스로 카테고리 집합 산출.
   - 경로: `consumer/handlers/inventory.py`(`make_inventory_handler`) -> `consumer/mappers.py:to_inventory_create(InventoryInput)` -> `ServerInventoryCreate` -> `CollectRepository.upsert_server`(ON CONFLICT DO UPDATE).
   - `to_inventory_create`(또는 핸들러)에서 services + listen_ports 로 카테고리 계산:
     - services 각 unit -> `classify(unit, listen_ports)` 결과 수집,
     - `detect_listen_categories(listen_ports)` 의 키 수집,
     - 합집합(dedup, "unknown" 제외) -> `service_categories`.
   - `ServerInventoryCreate` DTO + upsert SQL 에 `service_categories` 포함(매 inventory 갱신마다 재계산되어 최신 유지; 갱신 주기는 agent `AGENT_INVENTORY_REFRESH_SEC`).
   - 분류 로직은 service_classifier 에만 존재(단일 진실 유지). ingest가 호출, read는 소비만.

3. read 경로 통일(저장 컬럼 소비):
   - 목록: `ServerSummary` 에 `service_categories` 필드 추가, `list_servers`(`query_service.py:232`) SELECT 에 컬럼 추가, `to_server_list_item`(`mappers/server.py:246`)이 저장값으로 뱃지 생성(이름 기반 재계산 제거, listen_ports 로드 불요 유지).
   - 상세/리포트: 뱃지 집합은 저장 컬럼으로 일치시키되, 포트별/유닛별 근거 표시는 기존대로 services+listen_ports(이미 로드)로 재계산 가능. 단 "표시되는 카테고리 집합"은 저장값과 반드시 일치해야 함(불일치 방지 테스트).
   - 필터(`service_categories` 필터, `list_page.py:246`, `list_table.html:87`): 저장 컬럼 기준으로 서버 필터링 가능(저렴) — GIN 인덱스 고려(`text[]`/jsonb).
   - 템플릿: `templates/servers/list_table.html`, `servers/services.html` 의 뱃지 렌더가 저장 카테고리를 쓰도록.

4. 백필(기존 행):
   - alembic 데이터 마이그레이션은 python classifier 호출이 번거로움 -> 일회성 recompute 스크립트 권장: 각 server_inventory 행의 services+listen_ports 로 service_categories 재계산해 UPDATE. (또는 다음 inventory 발행 때 자연 갱신되나, 즉시 일관성 위해 backfill 권장.)

### 영향 파일 (체크리스트)
- 모델/마이그레이션: `db/models/`(server_inventory), `migrations/versions/<new>`.
- ingest: `consumer/handlers/inventory.py`, `consumer/mappers.py`, `db/dtos/inbound.py`(ServerInventoryCreate), `db/repositories/collect_repository.py`(upsert_server).
- DTO/read: `db/dtos/outbound.py`(ServerSummary), `web/services/query_service.py`(list_servers SELECT), `web/services/mappers/server.py`(to_server_list_item, detail), `mappers/report.py`.
- 단일 소스: `web/services/service_classifier.py`(변경 없음, ingest에서 호출만).
- 템플릿: `templates/servers/list_table.html`, `servers/services.html`.

### 엔진 규약 (동시 갱신)
- assessment-engine/.claude/CLAUDE.md 의 단일 진실(#E 계열), DB/스키마 변경 절차(#F 체크리스트: 모델+마이그레이션+docs+테스트), P1-P4 렌더 계층 규약 준수.
- 테스트: service_classifier 단위테스트 유지 + ingest 카테고리 산출 테스트 + 목록/상세 카테고리 집합 일치 테스트(불일치 회귀 방지).
- docs/architecture(agent/consumer/inventory) 갱신.

### 수용 기준
- 포트/comm 으로만 식별되는 워크로드(예: 6379 listen)가 목록·상세·리포트·필터 모두에서 동일하게 `cache` 뱃지.
- 목록 페이지가 listen_ports 를 로드하지 않고도(경량 유지) 정확한 뱃지 표시.
- 분류 로직은 service_classifier 한 곳에만.

---

## 2단계 — 무거운 페이지 쿼리 최적화 (서버목록·환경자원평가·실시간현황)

> 원칙: SQL 튜닝 전에 "레포/서비스 로직"부터 점검한다. 불필요한 재계산·N+1·과다 로드를 먼저 제거.

### 대상 페이지 / 쿼리 진입점
- 서버목록: `web/routers/pages/list_page.py` -> `query_service.list_servers`(query_service.py:232).
- 환경자원평가(리포트): `mappers/report.py` / `mappers/environment_report.py` + 해당 query_service 메서드.
- 실시간현황: `query_service.get_environment_realtime`(query_service.py:454), `_online_map`(:470), `get_selection_attention`(:796).

### 점검 순서(로직 우선)
1. 레포/서비스 로직 감사:
   - per-server 루프 내 개별 쿼리(N+1) 여부 — 배치/조인으로 전환 가능한가.
   - 행마다 반복되는 파생 연산(예: 행별 classify/카테고리 재계산 -> 1단계로 이미 제거됨; 다른 페이지에도 유사 패턴 있는지).
   - 모든 행에 대해 무거운 jsonb(services/listen_ports/disks) 역직렬화를 불필요하게 로드하는지(목록은 1단계로 listen_ports 불요).
   - 매 요청 재계산되는데 ingest/주기 사전계산이나 redis 캐시로 옮길 수 있는 값(온라인 여부, 카테고리, 집계).
2. 측정: 페이지별 발생 쿼리 수 + 각 쿼리 EXPLAIN ANALYZE + 페이지 응답 시간. 병목 쿼리 식별.
3. 최적화(확실히):
   - 사전계산/저장(1단계 패턴 확장), 배치 쿼리/조인, 적절한 인덱스(필터·정렬·조인 키), redis 캐시(실시간/집계), payload 축소(필요 컬럼만 SELECT).
   - 변경마다 측정 재확인(회귀 방지). 인덱스는 EXPLAIN 으로 사용 확인.

### 비고
- 1단계(카테고리 ingest 사전계산)는 그 자체로 목록 페이지 최적화: 행별 classify 제거 + 뱃지 위해 listen_ports 로드 불요.
- 동일 "ingest/주기 사전계산 -> 화면은 읽기만" 원칙을 실시간현황·환경자원평가의 반복 집계에도 적용 검토.

---

## 참고 — 1단계 근거 데이터(재현)
포트 기반 인식이 동작함을 보인 실측: 중립 이름 바이너리를 6379 listen -> 엔진 `detect_listen_categories` 가 `{'cache':[6379]}` 반환(comm 미매칭, 순수 포트). 상세/리포트엔 반영, 목록엔 미반영 -> 본 작업의 동기. (fleet 측 상세: z-converter-assessment 루트 `service-badge-port-method-findings.md`.)
