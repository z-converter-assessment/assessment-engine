# 테스트

pytest 테스트의 실행·작성 단일 진실. agent 가 붙는 실 VM 을 동반한 E2E 는 본 repo 범위 밖이다 (OpenStack 공급 환경에서 수행).

## 1. 계층

| 계층 | 위치 | 인프라 | 격리 단위 |
|------|------|--------|---------|
| Unit | `tests/unit/` | 없음 — `tests/fakes.py` 대역 · `AsyncMock` | 외부 의존 없음 |
| HTTP 경계 | `tests/http/` | 없음 — 대역 주입 + lifespan 미실행 | 응답 전문 스냅샷 대조 |
| Integration | `tests/integration/` | `testcontainers[postgres]` 로 TimescaleDB 컨테이너 | function-scope `db_session` transaction rollback |

hypothesis property 기반 테스트는 unit 안에 있다 — 생성 횟수 배율은 2절.

HTTP 계층의 목적은 리팩토링이 화면과 API 응답을 바꾸지 않았음을 저장소 안에서 재현 가능하게 보이는 것이라, 단언을 손으로 쓰지 않고 응답을 통째로 스냅샷과 대조한다. 최초 캡처를 만든 뒤에는 사람이 전 파일을 눈으로 읽고 "이게 지금 화면이 맞다" 를 확인한 다음 커밋한다 — 이 검수를 건너뛰면 안전망이 아니라 버그 고정 장치가 된다. lifespan 을 돌리지 않으므로 `app.state` 에 의존하는 엔드포인트는 캡처 대상에서 빠진다.

## 2. 실행

일상 실행은 `make test`(전체)·`make test-unit`·`make test-http`·`make test-integration` 이다 (`make help`). Docker 가 필요한 것은 통합 계층이고, testcontainers 가 TimescaleDB 를 자동으로 띄운다 — 전체를 도는 `make test` 도 같은 전제다. `make test-cov` 는 unit·http 커버리지를 재며 게이트가 아니다.

아래는 make 타깃이 없는 변형이다.

```bash
# property 생성 횟수 1/10 — develop 워크플로와 같은 배율
HYPOTHESIS_SCALE=0.1 uv run pytest tests/unit/

# 스냅샷 재기록 — 사람이 검수한 뒤 커밋한다
SNAPSHOT_UPDATE=1 uv run pytest tests/http/

# 단일 파일 / 단일 함수
uv run pytest tests/integration/test_query_repository.py
uv run pytest tests/integration/test_query_repository.py::test_metric_chart_dispatcher_all_types
```

unit 소요는 property 테스트가 지배한다. `HYPOTHESIS_SCALE` 은 각 테스트가 선언한 `max_examples` 에 곱하는 배율이고(기본 1, 하한 50 예제 — `tests/hypothesis_scale.py`), 검증 워크플로는 이 값을 base 브랜치로 가른다 — main 승격은 선언값 전량, develop 통합은 1/10.

PR 을 올리면 `.github/workflows/ci.yml` 이 테스트를 돌린다. base 별 발화 범위는 `docs/guides/ci-setup.md` 3.4.

## 3. 설정·Fixture

pytest 설정은 `pyproject.toml` `[tool.pytest.ini_options]`. 세 가지를 엄격하게 둔다.

- `filterwarnings = ["error"]` — 의존성이 낸 deprecation 을 다음 major 에서 깨질 때가 아니라 지금 본다.
- `--strict-markers` — 오타 마커가 조용히 무시되지 않는다.
- `--strict-config` — 오타 설정 키가 조용히 무시되지 않는다.

Fixture 는 세 층이다 — 루트 `tests/conftest.py`(컨테이너·엔진·세션 + 비밀번호 env 주입과 Composition Root 캐시 초기화 autouse), `tests/http/conftest.py`(HTTP 경계 대역 배선·스냅샷 대조), `tests/integration/conftest.py`(repo 별 function fixture, TRUNCATE 격리).

공유 데이터 빌더는 방향으로 갈린다. `tests/factories.py` 는 wire 계약 형태의 inbound 데이터를, `tests/builders.py` 는 repository 가 돌려주는 outbound DTO 를 만든다. 후자를 한곳에 모으는 이유는 분류 입력이다 — `rollup_host` 가 포화 3축·steal·run-queue·이력 길이를 함께 읽어 판정하므로, 파일마다 기본값이 다르면 같은 이름의 테스트가 서로 다른 baseline 위에서 통과한다.

```python
inv = make_inventory(composite_id="m1", hostname="h1", cpu_cores=4)
m = make_metrics(collected_at=ts, cpu_user_s=1000.0)
```

전 인자 keyword-only. 미지정 필드는 안전한 default 라 필요한 필드만 명시한다. 단위는 wire 규약(시간 s, 크기 By).

## 4. 테스트 작성

asyncio 마커는 붙이지 않는다 — `asyncio_mode=auto` 라 async 테스트가 자동 수집된다.

외부 의존 대역은 둘 중 하나를 고른다. Repository·Service 처럼 Protocol 이 있는 자리는 `tests/fakes.py` 의 in-memory 대역을 쓴다 — `AsyncMock` 은 어떤 속성 접근도 통과시켜 pyright strict 에서 계약 검사가 되지 않고, 대역 모듈은 끝의 정적 단언으로 Protocol 만족을 컴파일 시점에 못박는다. Redis·HTTP 클라이언트처럼 표면이 좁은 자리만 `AsyncMock`/`MagicMock` 을 쓴다.

Integration 은 `collect_repo`/`query_repo`/`diagnostic_repo`/`db_session` fixture 를 자동 주입받는다. 각 테스트 끝에 transaction rollback 으로 격리하고, 명시 commit 을 쓰는 `diagnostic_repo` 만 TRUNCATE 로 격리한다.

```python
# tests/integration/test_my_repo.py
async def test_something(collect_repo: SqlCollectRepository):
    sid = await collect_repo.upsert_server(make_inventory(composite_id="t-001"))
    assert sid > 0
```

dispatcher 처럼 카탈로그 전량을 훑어야 하는 대상은 parametrize 로 편다 — `MetricType` 전 값을 도는 `test_metric_chart_dispatcher_all_types` 가 누락 타입을 즉시 드러낸다.

## 5. 저장소에 두지 않은 검증 수단

아래 둘은 상시 필요하지 않아 `tests/` 에 두지 않는다. 해당 계층을 건드릴 때 만들어 쓰고 버린다.
같은 검증이 반복해서 필요해지면 그때 `tests/` 로 승격한다.

브라우저 오라클 — 클라이언트 JS 를 바꿀 때 쓰는 유일한 런타임 검증이다. `tests/http/conftest.py` 의
대역 배선(`app.dependency_overrides` + `lifespan="off"`)을 그대로 써서 uvicorn 을 띄우고, playwright 로
전 페이지를 돌며 콘솔 에러·canvas 렌더 수·전역 표면을 JSON 으로 덤프해 변경 전후를 대조한다.
HTTP 스냅샷은 서버가 낸 HTML 구조만 보므로 브라우저에서 나는 오류는 잡지 못한다.

SQL 대조 — repository 의 dispatch 를 재배치할 때 쓴다. repo 메서드에 recorder 세션을 주입해 렌더된
SQL 문자열과 bound parameter 를 전 Literal 조합에 대해 덤프하고 전후를 비교한다. 통합 테스트는 결과를
보지만 이건 쿼리 자체가 같은지를 본다.

## 6. 원칙

- 새 코드 추가 시 테스트도 함께 작성 — 코드 리뷰 시 누락 지적
- 리팩토링은 테스트 통과 baseline 위에서만 진행 — 회귀 즉시 식별
- 에이전트의 pytest 실행 정책은 CLAUDE.md #F5
