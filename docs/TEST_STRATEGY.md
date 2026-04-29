# 테스트 전략

## 철학

**동작 중심 테스트.** 구현 세부사항이 아닌 "이 코드는 무엇을 해야 하는가"를 검증한다.
커버리지 수치보다 **핵심 계약**(입출력, 에러 처리, 외부 시스템과의 상호작용)을 검증하는 것을 목표로 한다.

- 내부 함수 구조·변수명 변경이 테스트를 깨지 않아야 한다
- 외부 계약(MQ 메시지 스키마, API 응답 형태, Redis 키)이 바뀌면 테스트가 깨져야 한다
- 테스트는 변경 시 깨지는 것이 아니라 잘못된 변경 시 깨져야 한다

---

## 레이어 구분

```
tests/
├── unit/           # 외부 의존성 없음. Mock/AsyncMock 사용
│   ├── consumer/   # 메시지 파싱 + 핸들러 행동
│   └── web/
│       ├── services/   # 서비스·계산기·매퍼 순수 로직
│       └── routers/    # HTTP 계층 (FastAPI TestClient)
└── integration/    # testcontainers TimescaleDB 사용. Redis 없음(모킹)
    └── db/         # Repository 구현체 실 DB 검증
```

---

## 단위 테스트

### `unit/consumer/test_schemas.py` — Pydantic 스키마 계약

에이전트가 보내는 3종 메시지(inventory / metrics / error)의 **수용/거부 규칙** 검증.
필드 값 자체보다 "어떤 페이로드가 통과하고 어떤 것이 거부되는가"에 집중한다.

- `AgentMessage` discriminated union이 `message_type`으로 올바른 구체 타입으로 분기하는가
- 필수 필드 누락·빈 문자열·범위 위반 시 `ValidationError` 발생 여부
- 옵셔널 필드가 `None` 또는 기본값으로 처리되는가

> 페이로드 상수(`_INVENTORY`, `_METRICS`, `_ERROR`)는 파일 상단에 집중 관리.
> 스키마 필드 추가·삭제 시 상수와 해당 클래스만 수정.

---

### `unit/consumer/test_handler.py` — 핸들러 행동

`make_inventory_handler` / `make_metrics_handler` / `make_error_handler` 팩토리가 반환하는 핸들러의 행동 검증.

- 유효 메시지 → repo 메서드 호출 + commit
- inventory 처리 성공 → Redis `online:{server_id}` 세팅 (에이전트 기동 즉시 온라인 처리)
- metrics 처리 성공 → Redis `online:` 세팅 + `cache:metrics:` DELETE + PubSub 발행
- 미등록 서버의 metrics → 조용히 드롭 (DB 쓰기 없음)
- 중복 `message_id` → 멱등성 키 체크 후 조기 리턴
- JSON 파싱 실패 → raise (nack → DLQ)
- DB 오류 → 지수 백오프 3회 재시도 후 raise

---

### `unit/web/services/test_metrics_calculator.py` — 순수 계산 로직

`metrics_calculator.py`의 delta 계산 함수들. 외부 의존성 없음 — 입력 데이터클래스만으로 테스트.

- CPU: delta total=0이거나 음수면 `None` 반환
- CPU: 두 readings의 jiffy 차로 usage_pct 계산
- 디스크/네트워크: 단일 sample이면 rate `None`, 2개 이상이면 delta/dt
- counter reset(d_val < 0): `None` 반환
- 마운트: total_bytes 기준 usage_pct 계산, mount 기준 정렬

---

### `unit/web/services/test_mappers.py` — DTO → ViewModel 변환

`mappers.py`의 to_xxx 함수들. DTO 필드가 ViewModel에 올바르게 매핑되는가.

- 단위 변환: KB→GB, bytes→GB 정확성
- 스토리지: 인벤토리 마운트와 실시간 사용량 병합 로직, mount 정렬
- 네트워크: `compute_net_io` 위임 확인

---

### `unit/web/services/test_query_service.py` — 서비스 오케스트레이션

`QueryService`가 repo + Redis를 올바른 인자로 호출하는지, 반환값을 변환해 위로 전달하는지.

- 캐시 hit: repo 호출 없이 캐시 값 반환
- 캐시 miss: repo 호출 → 캐시 저장 → 반환
- `is_online` 필터: Redis 키 존재 여부 기반, 서비스 계층에서 처리
- 잘못된 `metric_type` / `time_range`: 빈 리스트 반환 (DB 쿼리 없음)

---

### `unit/web/routers/test_pages.py` — SSR 라우터

서비스 결과 None → 404, 값 있음 → 200 + `text/html`. 서비스에 올바른 인자 전달 여부.

템플릿 렌더링은 `TemplateResponse`를 패치해 HTML 응답 여부만 확인.

---

### `unit/web/routers/test_api.py` — API 라우터

JSON 응답 상태코드, 쿼리 파라미터 바인딩, FastAPI 레벨 422 유효성 검증.

- `metric_type` 미전달 → 422
- 유효하지 않은 Literal 값 → 422 (FastAPI 레벨에서 차단)
- 서비스 None → 404, 값 있음 → 200

---

## 통합 테스트

testcontainers `PostgresContainer("timescale/timescaledb:latest-pg16")`로 실 DB 기동.
Redis 없음 — Repository 계층만 검증.
각 테스트는 중첩 트랜잭션(savepoint) 후 롤백으로 격리.

### `integration/db/test_collect_repository.py`

- `upsert_server`: 신규 insert + RETURNING id, 동일 machine_id upsert 시 필드 갱신 + 같은 id 반환
- `find_server_id`: 존재/비존재 분기
- `insert_metric`: ServerMetrics + ServerDiskIo + ServerNetIo + ServerMountUsage 정상 삽입

### `integration/db/test_query_repository.py`

- `list_servers`: 빈 DB, 검색 필터, 페이지네이션, `last_seen_at` — metrics 있으면 MAX(collected_at) 우선
- `get_server`: not found → None, found → 모든 필드 매핑
- `get_storage`: not found → None, mount_usage 병합
- `get_network`: not found → None, net_io 포함
- `get_collection_status`: metrics 없을 때 / 있을 때 `last_metric_at` 차이
- `latest_dashboard`: 서버 없음 → None, metrics 없음 → 빈 metrics 리스트, 2행 반환 확인
- `metric_snapshots`: 빈 결과, 커서 페이지네이션, DESC 정렬
- `metric_chart`: 빈 결과, dimension 필터, time_bucket 결과 구조

---

## 픽스처 전략

### 단위 테스트
- 파일별 `_mock_service()`, `_mock_repo()`, `_make_message()` 헬퍼 함수 (클래스 공유)
- 메시지 페이로드 상수(`_INVENTORY_MSG`, `_METRICS_MSG` 등) 파일 상단 집중 정의

### 통합 테스트
- `conftest.py` (session scope): `PostgresContainer` → `engine` → `setup_schema`
- `conftest.py` (function scope): `db_session` — 중첩 트랜잭션 + 롤백
- 각 테스트 파일: `_make_inventory(machine_id=...)`, `_make_metric(offset_minutes=...)` 헬퍼

---

## 실행

```bash
# 단위 테스트 (Docker 불필요)
pytest tests/unit/ -v

# 통합 테스트 (Docker daemon 필요)
pytest tests/integration/ -v -m integration

# 전체
pytest tests/ -v
```