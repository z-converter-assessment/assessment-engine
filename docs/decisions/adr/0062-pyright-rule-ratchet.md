# ADR 0062 — 타입 검사 강도를 규칙 단위 래칫으로 올린다

상태: Accepted (2026-08-04)

## Context

pyright 를 `basic` 으로 돌리고 있었고, 게이트(`Makefile`·`ci.yml`)는 `pyright src` 로 범위를 좁혀 실행했다. 설정의 `include` 는 `["src", "tests"]` 였다. 선언과 강제가 어긋나 tests 는 검사 대상으로 적혀 있으면서 아무도 돌리지 않았고, 그 자리에 `basic` 수준 오류 328건이 쌓여 있었다.

강도를 올리는 방법으로 `typeCheckingMode = "strict"` 한 줄을 먼저 검토했다. 실측하면 src 2907 · tests 1906 · scripts 33 건이 나온다. 이 규모를 한 번에 소진할 수 없고, 소진 전까지 게이트를 끄면 이미 확보한 지점도 함께 되돌아간다.

내역을 규칙별로 갈라 보면 분포가 고르지 않다. 위반 대부분은 다섯 규칙(`reportUnknown*` 계열과 `reportMissingParameterType`)에 몰려 있고, 이들은 어노테이션이 빠진 파라미터와 타입 인자 없는 `dict`·`list` 에서 파생한다. 나머지 규칙 대다수는 위반이 이미 0 이다.

강도를 올리려는 목적은 실제 결함을 잡는 것이다. 이 저장소에서 마지막으로 난 타입형 장애는 `metric_trend` 가 존재하지 않는 `metric_type` 을 받아 전 보고서 경로가 500 이 난 건이었는데, 그 파라미터는 `str` 로 선언돼 있었다. strict 를 켰어도 잡히지 않는다. 선언이 부정확하면 검사 강도가 답이 아니라는 뜻이다.

## Decision

강도를 프리셋 한 단계가 아니라 규칙 단위로 올린다. 판단 기준은 하나다 — 위반이 0 이면 error 로 못박고, 위반이 남은 규칙은 승격하지 않는다.

### 프리셋 기준선

`standard` 로 올린다. pyright 가 새 프로젝트에 적용하는 기본 프리셋이고, `basic` 대비 비용이 위반 1건이다. 이 한 단계로 `reportFunctionMemberAccess`·`reportIncompatibleMethodOverride`·`reportOverlappingOverload`·`reportPossiblyUnboundVariable` 이 error 가 된다.

### 검사 범위

`include` 를 게이트가 실제로 강제하는 범위와 일치시킨다. 명령은 인자 없이 `pyright` 를 부르고 범위는 설정 한 곳이 정한다. 위반이 남은 디렉토리는 소진한 뒤 이 목록에 넣는다.

### 승격

strict 프리셋 규칙 중 src 위반이 0 인 것 26개를 error 로 선언한다. `typeCheckingMode` 를 strict 로 바꾸는 날 이 선언들은 중복이 되어 지운다 — 그때까지 확보한 지점을 지키는 것이 목적이다.

strict 프리셋이 끄는 규칙 중에서도 위반이 0 이면 켠다. `reportUnreachable`·`reportUnnecessaryTypeIgnoreComment`·`reportPropertyTypeMismatch`·`reportUninitializedInstanceVariable` 넷이 해당한다. `reportUnnecessaryTypeIgnoreComment` 는 켜자마자 낡은 억제 둘을 잡았다 — starlette stub 이 고쳐진 뒤로 무의미해진 `# type: ignore` 하나, 그리고 억제 지시자 형태를 그대로 인용한 설명 주석 하나. 후자는 그 줄에 실제 억제로 걸려 있어서 그 자리에 진짜 오류가 나면 조용히 삼켰다.

### 거부

다음 규칙은 위반이 남아 있고 승격하지 않는다. 각각 이유가 다르다.

| 규칙 | src 위반 | 거부 사유 |
|------|---------|----------|
| `reportPrivateUsage` | 66 | `_` 접두 심볼을 패키지 안 형제 모듈이 쓰는 관용구를 때린다. `_db_retry`·`_check_idempotent` 는 모듈이 아니라 패키지에 private 이다 |
| `reportUnusedFunction` | 9 | 같은 관용구의 반대편 오탐 — 형제 모듈이 import 해 쓰는데 파일 안에서 안 쓰인다고 미사용 판정 |
| `reportUnusedClass` | 4 | 동일. `_BaseQueryMixin` 은 repository 4개가 상속한다 |
| `reportCallInDefaultInitializer` | 124 | FastAPI 의 `Depends()`·`Query()` 는 기본값 자리 호출이 프레임워크 인터페이스다. ruff 도 같은 이유로 B008 을 면제한다 |
| `reportUnusedCallResult` | 46 | 반환값 버리기는 Python 관용구다. strict 프리셋도 끈다 |
| `reportImplicitOverride` | 55 | `@override` 데코레이터 전면 도입은 별개 결정이다. 어느 프리셋에도 없다 |
| `reportImplicitStringConcatenation` | 8 | 문자열 스타일은 ruff 소관이다. 두 도구가 같은 것을 잡으면 갈라진다 |
| `reportMissingSuperCall` | 7 | strict 프리셋도 끈다 |
| `reportImportCycles` | 1 | `from __future__ import annotations` + `TYPE_CHECKING` 로 순환을 의도적으로 끊는 규약(#F1)과 충돌 |
| `reportUnnecessaryIsInstance` | 3 | JSONB 원본을 방어하는 `isinstance(s, dict)` 가 대상이다. 선언 타입이 dict 라 불필요 판정이 나는 것이고, JSONB 경계 타입이 정확해지면 다시 본다 |

`reportUnknown*` 4종·`reportMissingParameterType`·`reportMissingTypeArgument`·`reportUnknownLambdaType` 은 거부가 아니라 남은 작업이다. 이들을 소진하면 `typeCheckingMode = "strict"` 를 켤 수 있다.

## Consequences

게이트가 선언과 일치한다. `pyright` 를 인자 없이 부르면 설정의 범위가 그대로 검사 범위다.

되돌림이 막힌다. 위반 0 인 지점이 error 로 고정돼 있어 남은 작업을 진행하는 동안 새 코드가 확보한 지점을 무너뜨리지 못한다.

승격된 규칙을 지키느라 코드가 바뀐 곳이 있다. `agent_id` 를 `MessageBase` 에서 `AgentMessageBase` 로 내려 `task.result` 가 nullable override 를 하지 않게 했다 — 기반 클래스가 `UUID` 라 선언하는데 자식이 `None` 을 담는 상태였고, `_log_time_invariants` 가 그 기반 타입으로 `agent_id` 를 읽는다. 계층 분리로 이 함수의 전제(agent 가 직접 발행한 메시지)가 타입이 됐다. wire 계약은 그대로다 — 구상 모델별 필드 집합이 바뀌지 않는다.

tests 는 여전히 검사 밖이다. 328건을 소진하기 전까지 그 자리는 회귀를 잡지 못한다.

pyright 버전이 오르면 프리셋 구성이 바뀔 수 있다. 명시 선언한 규칙은 프리셋과 무관하게 유지되고, 새로 프리셋에 들어온 규칙은 다음 래칫에서 같은 기준(위반 0)으로 판단한다.

## 정정 (2026-08-04)

`scripts` 를 `include` 에 넣었다. 본 ADR 이 세운 기준(위반 0)을 그대로 적용하면 이 디렉토리는 `standard` + 승격 규칙을 이미 통과하고, `json.load` 가 낳는 bare `dict` 를 경계 타입 별칭 둘(`Release` = 값이 좁혀지지 않는 응답 dict, `Entry` = 값이 전부 문자열인 카탈로그 항목)로 대체하니 strict 도 통과한다. 산출 카탈로그는 커밋본과 바이트 동일하다.

경로별 강도 차이는 `executionEnvironments` 로 표현한다. src 에 아직 위반이 남은 strict 규칙 8개(`reportUnknown*` 4종·`reportMissingParameterType`·`reportMissingTypeArgument`·`reportUnknownLambdaType`·`reportPrivateUsage`)를 `root = "scripts"` 아래에서만 error 로 올린다. pyright 1.1.411 은 이 블록 안에서 `typeCheckingMode` 를 받지 않아 규칙을 직접 나열한다 — 프리셋 이름으로는 경로별 강도를 표현할 수 없다.

src 가 같은 규칙을 통과하면 이 블록의 규칙들을 최상위 목록으로 올리고 블록을 지운다.

## 정정 (2026-08-04, tests 편입)

`tests` 를 `include` 에 넣었다. 현재 강도에서 331건이 나왔고 전부 소진했다. 뿌리는 셋이다.

빌더가 dict 로 기본값을 조립해 `**` 로 dataclass 생성자에 넘기고 있었다. pyright 는 그 dict 의 값 타입을 전 필드의 합집합으로 좁히므로 어떤 인자도 어떤 파라미터에도 맞지 않는다. `dataclasses.replace(base, **overrides)` 로 바꾸면 base 를 실제 타입으로 한 번 만들고 덮어쓰기만 넘기게 되어 합집합이 생기지 않는다. 빌더 6개가 이 형태였고 331건 중 232건이 여기서 나왔다.

파라미터 어노테이션이 없는 빌더는 기본값에서 타입이 추론돼 `None` 기본값이 곧 `None` 타입이 됐다. DTO 필드 타입에서 역으로 어노테이션을 채웠다.

`T | None` 을 돌려주는 호출 결과를 좁히지 않고 그대로 다음 인자로 넘기거나 속성 접근하고 있었다. `assert x is not None` 을 앞에 세웠다 — 검사기를 만족시키는 동시에 그 테스트가 실제로 전제하던 것을 명시한다.

이 과정에서 src 결함 하나가 드러났다. `MetricSeries.value` 가 `float | None` 으로 선언돼 있는데 SQL `avg`·`sum` 은 numeric 을 `Decimal` 로 준다 — 매퍼 주석이 이미 그 사실을 적고 있었고 환경 보고서 경로는 `float(v)` 로 변환하는데 `to_metric_series_item` 은 변환 없이 그대로 ViewModel 에 실었다. JSON API 응답 타입(codegen 원천)이 `float` 라 선언과 실물이 갈렸다. 선언을 `float | Decimal | None` 으로 정정하고(raw 그대로 싣는 P1) 변환은 매퍼로 옮겼다(P2).

`scripts` 처럼 strict 로 고정하지는 않았다. tests 에는 strict 규칙 위반이 아직 1906건 남아 있고, 지배적 원인은 픽스처의 미어노테이션 파라미터다.

## 정정 (2026-08-04, src 선언 채움)

src 의 strict 위반 2903건 중 2903건을 소진해 선언 관련 규칙 넷(`reportMissingParameterType`·`reportMissingTypeArgument`·`reportUnknownParameterType`·`reportUnknownLambdaType`)을 `root = "src"` 아래에서 error 로 고정했다. strict 를 켜면 남는 것은 값 추론에서 오는 Unknown 과 거부 목록뿐이다.

위반의 뿌리는 선언 두 종류였다. 실측으로 확인했다 — DTO 한 파일의 선언 21곳을 채우니 src 전체에서 163건이 사라졌다. 선언 하나가 그것을 쓰는 모든 자리로 번지기 때문이다.

첫째는 타입 인자 없는 `dict`·`list` 209곳이다. 대부분 wire·JSONB 원본을 담는 자리라 별칭 `JsonObject`(`assessment_engine/json_types`)를 세워 그 의미를 이름으로 남겼다. 모델로 좁히지 않는 이유는 계약 밖 필드가 도착해도 통과시키라는 규약과 어긋나기 때문이다 — 원본은 열린 채로 두고 읽는 쪽이 필요한 축만 좁힌다. JSON 이 아닌 자리(MQ 큐 선언 인자·SQLAlchemy 컬럼-값 맵·in-memory 인덱스)는 별칭을 쓰지 않고 `dict[str, Any]` 로 두어 이름이 거짓말하지 않게 했다.

둘째는 어노테이션 없는 파라미터 130여 곳이다. 타입은 호출부에서 확정했다.

승격이 내가 붙인 어노테이션의 오류를 그 자리에서 잡았다. `build_memory_breakdown`·`build_cpu_breakdown` 의 `raw` 는 `ReportRowRaw` 가 아니라 각각 `MemoryBreakdownRaw`·`CpuBreakdownRaw` 였고, topology 의 `host` 는 `HostAssessment` 가 아니라 `SubnetHost` 였다. 이름만 보고 붙인 타입을 검사기가 되돌려 세웠다.

동시에 코드 쪽 사실도 둘 드러났다. 스토리지 트리의 `visited` 를 `set[str]` 로 뒀는데 블록 디바이스 `id` 는 계약상 nullable 이라 `set[str | None]` 이 맞다 — 같은 키를 쓰는 `array_home` 은 이미 그렇게 선언돼 있었다. 그리고 환경 개요의 `util is not None` 폴백은 `environment_utilization` 이 non-null 을 돌려주므로 닿지 않는 분기였다.

`/reports/{job_id}/status` 는 반환 어노테이션이 없어 생성 타입이 `unknown` 이었다. 채우니 폴링 JS 가 보는 응답 타입이 실제 형태로 좁혀졌다.

`reportPrivateUsage`·`reportUnusedFunction`·`reportUnusedClass` 는 거부 목록 그대로다. `reportUnknownMemberType`·`reportUnknownArgumentType`·`reportUnknownVariableType` 은 남은 작업이며, 소진하면 `typeCheckingMode = "strict"` 를 켠다.

## 정정 (2026-08-04, src strict 도달)

src 가 strict 전 규칙을 통과한다. 거부 목록(`reportPrivateUsage`·`reportUnusedFunction`·`reportUnusedClass`·`reportUnnecessaryIsInstance`)만 예외이고, 나머지 strict 규칙 전부를 `root = "src"` 아래 error 로 고정했다.

값 추론이 낳던 Unknown 423건의 뿌리는 셋이었다.

`d.get(key) or []` 관용구다. 왼쪽이 열린 타입이면 오른쪽 빈 리터럴이 원소 타입을 잃은 채 결과 타입을 정한다. `json_types` 에 `json_list`·`json_obj`·`json_str_list` 를 두어 중첩 배열·객체를 꺼내는 자리가 그 자리에서 형태를 확정하게 했다. 세 헬퍼는 `object` 를 받아 형태가 아니면 빈 값으로 읽으므로 호출부에 가드가 필요 없다. 334건이 여기서 사라졌다.

`field(default_factory=list)` 다. 인자 없는 `list`·`dict` 는 원소 타입이 없는 생성자라 선언한 `list[X]` 와 이어지지 않는다. 87곳을 `default_factory=list[X]` 로 바꿨다 — 제네릭 별칭도 호출 가능해 런타임 동작은 같다.

어노테이션 없는 dict·list 리터럴이다. 라우터 컨텍스트·MQ payload·bulk INSERT 행처럼 값 종류가 섞이는 자리가 대상이고, 선언을 붙여 좁혔다.

외부 패키지가 타입을 주지 않는 세 자리는 이유를 적고 그 줄만 억제했다. redis 는 `ConnectionPool.from_url` 의 `**kwargs` 를 타입 없이 선언하고, SQLAlchemy 의 `__table__` 은 declarative 매핑이 런타임에 붙이는 속성이라 선언에 없으며, jinja2 는 `Environment.globals`·`filters` 의 값 타입을 소비자가 볼 수 있는 형태로 주지 않는다.

남은 것은 tests 뿐이다. tests 가 같은 지점에 닿으면 `typeCheckingMode = "strict"` 를 켜고 거부 목록만 `none` 으로 남긴 뒤 `executionEnvironments` 블록을 지운다.
