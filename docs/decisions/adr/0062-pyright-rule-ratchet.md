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
