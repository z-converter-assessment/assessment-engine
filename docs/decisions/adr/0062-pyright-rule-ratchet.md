# ADR 0062 — 타입 검사 강도를 규칙 단위 래칫으로 올린다

상태: Accepted (2026-08-04) — Refined by ADR 0063. 규칙 단위 래칫이라는 절차는 존속하고, 도달 기준선(Python 3.14·ruff format·확장 lint·`reportImplicitOverride`)은 0063 이 정한다.

## Context

pyright 를 `basic` 으로 돌리고 있었고, 게이트(`Makefile`·`ci.yml`)는 `pyright src` 로 범위를 좁혀 실행했다. 설정의 `include` 는 `["src", "tests"]` 였다. 선언과 강제가 어긋나 tests 는 검사 대상으로 적혀 있으면서 아무도 돌리지 않았고, 그 자리에 `basic` 수준 오류 328건이 쌓여 있었다.

강도를 올리는 방법으로 `typeCheckingMode = "strict"` 한 줄을 먼저 검토했다. 그 한 줄의 비용을 실측하면 4846건이다 (src 2907 · tests 1906 · scripts 33). 이 규모를 한 번에 소진할 수 없고, 소진 전까지 게이트를 끄면 이미 확보한 지점도 함께 되돌아간다.

내역을 규칙별로 갈라 보면 분포가 고르지 않다. 위반 대부분은 다섯 규칙(`reportUnknown*` 계열과 `reportMissingParameterType`)에 몰려 있고, 나머지 규칙 대다수는 위반이 이미 0 이다.

강도를 올리려는 목적은 실제 결함을 잡는 것이다. 이 저장소에서 마지막으로 난 타입형 장애는 `metric_trend` 가 존재하지 않는 `metric_type` 을 받아 전 보고서 경로가 500 이 난 건이었는데, 그 파라미터는 `str` 로 선언돼 있었다. strict 를 켰어도 잡히지 않는다. 선언이 부정확하면 검사 강도가 답이 아니라는 뜻이다.

## Decision

강도를 프리셋 한 단계가 아니라 규칙 단위로 올린다. 판단 기준은 하나다 — 위반이 0 이면 error 로 못박고, 위반이 남은 규칙은 승격하지 않는다. 확보한 지점이 error 로 고정돼 있으면 남은 작업을 진행하는 동안 새 코드가 그 지점을 무너뜨리지 못한다.

경로마다 도달 시점이 다르므로 `[[tool.pyright.executionEnvironments]]` 로 그 경로에만 규칙을 올리며 전진하고, 전 경로가 같은 규칙을 통과하면 최상위 `typeCheckingMode` 로 합치고 블록을 지운다.

### 도달 상태

전 경로(`src`·`scripts`·`tests`)가 strict 를 통과한다. `typeCheckingMode = "strict"`, `include = ["src", "scripts", "tests"]`, `executionEnvironments` 없음. 명시 선언은 두 묶음뿐이다 — 아래 거부 목록 4개를 `none` 으로, strict 프리셋이 끄지만 위반 0 이라 켜 두는 4개(`reportUnreachable`·`reportUnnecessaryTypeIgnoreComment`·`reportPropertyTypeMismatch`·`reportUninitializedInstanceVariable`)를 `error` 로.

검사 범위는 `include` 한 곳이 정하고 명령은 인자를 넘기지 않는다. 편집기와 CLI 와 워크플로가 같은 범위를 본다.

### 거부

다음 규칙은 위반이 남아 있고 승격하지 않는다.

| 규칙 | 거부 사유 |
|------|----------|
| `reportPrivateUsage` | `_` 접두 심볼을 패키지 안 형제 모듈이 쓰는 관용구를 때린다. `_db_retry`·`_check_idempotent` 는 모듈이 아니라 패키지에 private 이다 |
| `reportUnusedFunction` | 같은 관용구의 반대편 오탐 — 형제 모듈이 import 해 쓰는데 파일 안에서 안 쓰인다고 미사용 판정 |
| `reportUnusedClass` | 동일. `_BaseQueryMixin` 은 repository 넷이 상속한다 |
| `reportUnnecessaryIsInstance` | JSONB 원본을 방어하는 `isinstance(s, dict)` 가 대상이다. 선언 타입이 dict 라 불필요 판정이 나는 것이고, JSONB 경계 타입이 더 정확해지면 다시 본다 |

`reportCallInDefaultInitializer`·`reportUnusedCallResult`·`reportImplicitOverride`·`reportImplicitStringConcatenation`·`reportMissingSuperCall`·`reportImportCycles` 는 strict 프리셋 자신이 끄므로 별도 선언이 필요 없다. 각각 FastAPI 의 `Depends()` 기본값 호출, Python 의 반환값 버리기 관용구, `@override` 전면 도입이라는 별개 결정, ruff 소관인 문자열 스타일, 상위 호출 강제, `TYPE_CHECKING` 로 순환을 끊는 규약(#F1)과 부딪힌다. 그중 `@override` 는 ADR 0063 이 채택했다.

## 소진한 것

위반의 뿌리는 값이 아니라 선언이었다. DTO 한 파일의 선언 21곳을 채우니 src 전체에서 163건이 사라진 것이 근거다 — 선언 하나가 그것을 쓰는 모든 자리로 번진다.

타입 인자 없는 `dict`·`list` 가 src 209곳이었다. 대부분 wire·JSONB 원본을 담는 자리라 별칭 `JsonObject`(`assessment_engine/json_types`)를 세워 의미를 이름으로 남겼다. 모델로 좁히지 않는 이유는 계약 밖 필드가 도착해도 통과시키라는 규약과 어긋나기 때문이다 — 원본은 열린 채로 두고 읽는 쪽이 필요한 축만 좁힌다. JSON 이 아닌 자리(MQ 큐 선언 인자·SQLAlchemy 컬럼-값 맵·in-memory 인덱스)는 별칭을 쓰지 않고 `dict[str, Any]` 로 뒀다. 이름이 거짓말하면 안 된다.

`d.get(key) or []` 관용구가 그다음이다. 왼쪽이 열린 타입이면 오른쪽 빈 리터럴이 원소 타입을 잃은 채 결과 타입을 정한다. 같은 모듈의 `json_list`·`json_obj`·`json_str_list` 로 중첩 배열·객체를 꺼내게 해 334건이 사라졌다. 셋 다 `object` 를 받아 형태가 아니면 빈 값으로 읽으므로 호출부에 가드가 필요 없다 — 가드를 두면 그 narrow 결과가 다시 Unknown 을 만든다.

`field(default_factory=list)` 87곳. 인자 없는 `list`·`dict` 는 원소 타입이 없는 생성자라 선언한 `list[X]` 와 이어지지 않는다. `default_factory=list[X]` 로 바꿨고 제네릭 별칭도 호출 가능해 런타임 동작은 같다.

어노테이션 없는 파라미터 130여 곳(src)과 tests 의 `**` spread kwargs 빌더(541건). tests 픽스처는 conftest 의 반환 타입을 정직하게 만든 뒤 이름으로 역전파했고, parametrize 파라미터는 값 리터럴에서 타입을 유도했다.

## 드러난 결함

승격이 잡은 것들이다. 검사 강도를 올린 값이 여기 있다.

`agent_id` 를 `MessageBase` 가 `UUID` 로 선언하는데 `task.result` 가 `None` 을 담고 있었다. 계층을 갈라(`AgentMessageBase`) `_log_time_invariants` 의 전제(agent 가 직접 발행한 메시지)가 타입이 됐다. wire 계약은 그대로다 — 구상 모델별 필드 집합이 바뀌지 않는다.

`MetricSeries.value` 가 `float | None` 인데 SQL `avg`·`sum` 은 numeric 을 `Decimal` 로 준다. 매퍼 주석이 이미 그 사실을 적고 있었고 환경 보고서 경로는 `float(v)` 로 변환하는데 `to_metric_series_item` 은 변환 없이 ViewModel 에 실었다. JSON API 응답 타입(codegen 원천)이 `float` 라 선언과 실물이 갈렸다. 선언을 정정하고(raw 그대로 싣는 P1) 변환을 매퍼로 옮겼다(P2).

설명 주석이 억제 지시자 형태를 그대로 인용해 그 줄의 실제 억제로 걸려 있었다. 그 자리에 진짜 오류가 나면 조용히 삼켰다. `reportUnnecessaryTypeIgnoreComment` 가 켜자마자 이것과, starlette stub 이 고쳐진 뒤로 무의미해진 `# type: ignore` 하나를 함께 잡았다.

stale `# noqa: F401 (re-export)` 가 가리고 있던 죽은 re-export 둘. 스토리지 트리의 `visited` 를 `set[str]` 로 뒀으나 블록 디바이스 `id` 는 계약상 nullable 이다 — 같은 키를 쓰는 `array_home` 은 이미 `str | None` 이었다. 환경 개요의 `util is not None` 폴백은 `environment_utilization` 의 non-null 반환으로 닿지 않는 분기였다.

이름만 보고 붙인 어노테이션의 오류도 그 자리에서 되돌려 세웠다. `build_memory_breakdown`·`build_cpu_breakdown` 의 `raw` 는 `ReportRowRaw` 가 아니라 각각 `MemoryBreakdownRaw`·`CpuBreakdownRaw` 였고, topology 의 `host` 는 `HostAssessment` 가 아니라 `SubnetHost` 였다.

## Consequences

게이트가 선언과 일치한다. 새 코드는 strict 를 통과해야 들어온다.

외부 패키지가 타입을 주지 않는 자리는 남는다. redis 는 `ConnectionPool.from_url` 의 `**kwargs` 를, SQLAlchemy 는 declarative 가 런타임에 붙이는 `__table__` 을, jinja2 는 `Environment.globals`·`filters` 의 값 타입을, pytest 는 `approx` 의 파라미터를, jsonschema 는 `iter_errors` 를, testcontainers 는 패키지 전체를 타입 없이 준다. 호출부가 여럿인 것(pytest·jsonschema)은 타입 있는 얇은 래퍼(`tests/approx.py`·`_schema_errors`) 한 곳에 가두고 그 자리에서만 억제했다 — 호출부마다 억제하면 그 줄의 실제 오류까지 함께 묻힌다.

거부 목록 넷은 프리셋과 무관하게 유지된다. pyright 버전이 올라 새 규칙이 strict 에 들어오면 그때 같은 기준(위반 0)으로 판단한다.

`executionEnvironments` 의 `root` 는 진단 범위만이 아니라 import 해석 기준도 바꾼다. `root = "tests"` 로 두면 `tests.factories` 가 해석되지 않고, `extraPaths` 로 저장소 루트를 보태면 이번엔 `assessment_engine` 이 로컬 소스가 아닌 라이브러리로 잡혀 스텁을 요구한다. 경로별 강도가 필요한 동안만 쓰고 도달 후 지우는 것이 이 부작용도 함께 없앤다.
