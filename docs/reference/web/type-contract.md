# 표현계층 타입 계약 (ViewModel -> 생성 TS 타입 -> tsc --checkJs)

서버 ViewModel 과 클라이언트 JS 사이의 계약을 컴파일 타임에 강제하는 메커니즘. 무타입 JS 가 서버 응답
형태를 사람 규율로만 지키던 것을 컴파일러 강제로 옮긴다. React/Vite/번들/SSR 변경 없는 dev/CI 전용 검사
계층 — 배포 산출물(단일 Python 이미지)은 불변.

## 파이프라인

```
FastAPI 엔드포인트(return 어노테이션)
        |  app.openapi()
        v
OpenAPI 스키마  --openapi-typescript-->  생성 TS 타입(static/js/generated/api.ts, 커밋)
                                                |  JSDoc @type import
                                                v
                              vendor 제외 전 클라 JS  --tsc --checkJs-->  계약 위반 = 컴파일 에러
```

핵심 강제 지점은 fetch 경계다. 클라 JS 가 `fetch('/api/...')` 응답을 생성 타입으로 annotate 하면, 서버
ViewModel 필드가 바뀔 때(rename/타입 변경) codegen 이 타입을 갱신하고 tsc 가 소비처의 불일치를 컴파일
에러로 드러낸다.

## 구성

| 파일 | 역할 |
|------|------|
| `package.json` | pnpm(packageManager 핀). devDep 은 검사 도구(typescript·openapi-typescript·chart.js·@types/cytoscape) + 화면 캡처용 playwright(`docs/guides/local-dev.md` "화면 캡처"). 빌드/번들/런타임 산출물 없음. |
| `tsconfig.json` | strict + `noImplicitAny`(전-strict) + `checkJs:true`(include 범위 전 `.js` 대상) + `moduleDetection:force`(page script 를 tsc 상 격리 모듈로 — 파일 간 전역 식별자 충돌 제거). vendor 제외. |
| `scripts/dump_openapi.py` | 서버 불요 `app.openapi()` 덤프(codegen 입력). `Settings` 인스턴스화가 요청·기동 시점이라 env 값 없이 import·덤프가 성립한다. |
| `static/js/generated/api.ts` | openapi-typescript 생성 타입(커밋 — drift 게이트 대상). 직접 편집 금지. |
| `static/js/globals.d.ts` | 전역 lib(Chart·cytoscape) + 프로젝트 모듈 전역(각 util 이 `window.X` 로 노출하는 것들) ambient 선언. |
| `pnpm run codegen` | `dump_openapi.py` -> `openapi-typescript` -> `generated/api.ts`. |
| `pnpm run typecheck` | `tsc --noEmit`. |

## 규약

- 서버 JSON 엔드포인트는 응답 타입을 return 어노테이션(`-> Foo`)으로 선언한다. `response_model=` 은 쓰지 않는다
  — 같은 일을 데코레이터 인자로 하면 시그니처가 반환 타입을 숨긴다. FastAPI 가 stdlib dataclass ViewModel 도
  OpenAPI 스키마로 변환한다(Pydantic 필수 아님). 응답 검증도 함께 붙는다.
- 클라 JS 는 vendor 를 뺀 전부가 tsc 대상이다(`checkJs:true`). strict + noImplicitAny 라
  함수 파라미터·로컬까지 타입 강제 — (1) `fetch('/api/...')` 응답을 `/** @type {import('...generated/api').components['schemas']['<Name>'][]} */`
  로 annotate(계약 핵심) (2) 함수 파라미터·콜백은 JSDoc `@param` (3) strictNullChecks(DOM null 등)를 가드/캐스트로
  좁힌다. 파생 계산은 서버 단일 소스(P2) 유지 — 클라는 통계·분류·단위 변환을 재계산하지 않는다(차트 range 토글
  등 인터랙션 파생만 예외, P4 정신).
- 생성 `api.ts` 는 커밋한다 — 리뷰어가 타입을 보고, CI 가 drift 를 잡는다.

## CI 게이트

`ci.yml` typecheck job: `pnpm run codegen` 재생성 후 `git diff --exit-code`(엔드포인트
변경 후 codegen 미실행 = drift 차단) + `pnpm run typecheck`(계약 위반 차단).

## 확장 방법

- 신규 JSON 엔드포인트: return 어노테이션 선언 -> `pnpm run codegen` -> 커밋. 소비 JS 에서 응답
  annotate.
- ViewModel 필드 변경: mapper 등 갱신(F9) 후 `pnpm run codegen` 으로 `api.ts` 재생성 -> 커밋(drift 게이트).
- 신규 JS 파일: 만들면 곧바로 검사 대상이다 -> fetch 경계 annotate + null 가드 -> `pnpm run typecheck` 로 clean 확인.

## 한계·현황

- vendor(*.min.js) 외 전 클라 JS 가 noImplicitAny-clean(파라미터·로컬까지 타입 강제). 파일별 pragma 를 두지
  않으므로 신규 JS 가 표시를 빠뜨려 조용히 검사 밖에 놓이는 경로가 없다 — CI typecheck 가 tsc error 0 을 강제한다.
- 전역(ChartUtils 등)은 `globals.d.ts` 선언 — 차트 데이터셋 빌더 등 일부 반환은 elaborate Chart.js 타입 대신
  permissive(any). 명시적 캐스트라 noImplicitAny 위반은 아니고, 필요 시 각 소비처가 로컬로 좁힌다.

## frozen dict 응답 (assessment/right-sizing) — schema-only 패턴

`/api/assessment`·`/api/right-sizing` 은 매퍼가 hand-built dict 를 반환하는 배포된 frozen 외부 계약이다.
FastAPI 의 응답 모델 검증·재구성은 이질 구조(예: sizing.axes 의 cpu/mem vs disk)에 null 키를 더하거나 필드를
stripping 해 출력을 바꿀 수 있어, 이 둘은 응답 타입을 선언하지 않는다. 대신:
- `view_models/assessment_api.py`·`view_models/right_sizing_api.py` 에 계약 전체를 `TypedDict` 로 선언
  (`__pydantic_config__` 로 `extra=forbid`). 규약대로 필드는 present + nullable 이고, 실제로 생략되는 키만
  `NotRequired` 다.
- 매퍼 함수가 그 `TypedDict` 를 반환 타입으로 단다 — 매퍼가 만드는 것이 dict 이므로 선언과 조립이 같은 타입을
  공유하고, 키 이름·타입 불일치가 pyright 에서 걸린다. `BaseModel` 로 두면 매퍼 쪽 dict 리터럴은 검사 밖이었다.
- 라우터 `responses={200: {"model": ...}}` 로 OpenAPI 스키마만 문서화 — 실 응답은 매퍼 dict 그대로(재구성 0).
  `TypedDict` 도 `BaseModel` 과 같은 스키마(properties·required·additionalProperties)를 낸다.
- 실행 시점 drift 가드는 그대로다 — 테스트(`test_assessment_api_properties` property·`test_right_sizing_api`
  시나리오)가 매퍼 출력을 `TypeAdapter(...).validate_python` 에 태운다. 동적 키로 조립하는 자리는 pyright 가
  증명하지 못하므로 이 실행 시점 검증이 여전히 필요하다.
