# 표현계층 타입 계약 (ViewModel -> 생성 TS 타입 -> tsc --checkJs)

서버 ViewModel 과 클라이언트 JS 사이의 계약을 컴파일 타임에 강제하는 메커니즘. 서버 응답 형태를 사람 규율이
아니라 컴파일러가 지킨다. dev/CI 전용 검사 계층이라 번들러도 SSR 변경도 없고, 배포 산출물은 단일 Python
이미지 그대로다.

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
| `package.json` | pnpm(packageManager 핀). devDep 은 검사 도구 + 화면 캡처용 playwright(`docs/guides/local-dev.md` "화면 캡처"). 빌드/번들/런타임 산출물 없음. |
| `tsconfig.json` | 검사 강도(strict + `noImplicitAny`)와 검사 범위(vendor 제외 전 `.js`·`.ts`)를 정한다. `moduleDetection:force` 는 import·export 가 없는 page script(`sidebar.js` 등)까지 격리 모듈로 만들어 파일 간 전역 식별자 충돌을 없앤다. `paths` 는 base.html importmap 과 1:1 이다 — 브라우저와 tsc 가 같은 bare specifier 를 다른 파일로 해석하면 통과가 아무것도 보증하지 못한다. |
| `scripts/dump_openapi.py` | 서버 불요 `app.openapi()` 덤프(codegen 입력). `Settings` 인스턴스화가 요청·기동 시점이라 env 값 없이 import·덤프가 성립한다. 키 정렬 출력이라 같은 라우트가 늘 같은 바이트를 낸다(drift 대조 전제). |
| `static/js/generated/api.ts` | openapi-typescript 생성 타입(커밋 — drift 게이트 대상). 직접 편집 금지. |
| `static/js/globals.d.ts` | `<script>` 로 window 에 실리는 vendor UMD 전역(Chart·cytoscape) ambient 선언만. 프로젝트 모듈은 ESM 이라 tsc 가 구현에서 타입을 직접 추론한다 — 손으로 미러링한 선언을 두면 구현과 어긋나도 통과한다. |
| `pnpm run codegen` | `dump_openapi.py` -> `openapi-typescript` -> `generated/api.ts`. |
| `pnpm run typecheck` | `tsc --noEmit`. |

## 규약

`response_model=` 대신 return 어노테이션을 쓴다는 결정과 클라 재계산 금지(P2)는 CLAUDE.md #E6 이 갖는다. 그 결정이
성립하는 근거가 FastAPI 쪽에 둘 있다 — stdlib dataclass ViewModel 도 OpenAPI 스키마로 변환되므로 Pydantic 이
필수가 아니고, return 어노테이션만으로 응답 검증도 함께 붙는다.

클라 JS 가 지는 의무는 셋이다.

1. `fetch('/api/...')` 응답을 `/** @type {import('...generated/api').components['schemas']['<Name>'][]} */` 로
   annotate 한다. 계약의 핵심 강제 지점이다.
2. 함수 파라미터·콜백에 JSDoc `@param` 을 단다. noImplicitAny 가 로컬까지 타입을 요구한다.
3. strictNullChecks 가 걸리는 자리(DOM null 등)를 가드나 캐스트로 좁힌다.

## CI 게이트

`ci.yml` typecheck job: `pnpm run codegen` 재생성 후 `git diff --exit-code`(엔드포인트
변경 후 codegen 미실행 = drift 차단) + `pnpm run typecheck`(계약 위반 차단).

## 확장 방법

신규 엔드포인트·ViewModel 필드 변경 시 밟는 순서(codegen -> 커밋 -> 소비 JS annotate)는 CLAUDE.md #E6 이
갖는다. 여기 고유한 것은 신규 JS 파일이다.

- 만들면 곧바로 검사 대상이다(opt-in 표시 없음) -> fetch 경계 annotate + null 가드 -> `pnpm run typecheck` 로
  clean 확인.
- 여러 페이지가 공유할 모듈이면 base.html importmap 과 `tsconfig.json` 의 `paths` 에 같은 bare specifier 를
  동시에 추가한다. 한쪽만 넣으면 브라우저나 tsc 중 한쪽이 모듈을 찾지 못한다.

## 한계·현황

- vendor(`*.min.js`) 외 전 클라 JS 가 noImplicitAny-clean 이다(파라미터·로컬까지 타입 강제). CI typecheck 가
  tsc error 0 을 강제한다.
- 차트 데이터셋 빌더 등 일부 시그니처는 elaborate Chart.js 타입 대신 permissive(`any`) 를 쓴다. 명시적 캐스트라
  noImplicitAny 위반은 아니고, 필요 시 각 소비처가 로컬로 좁힌다.

## frozen dict 응답 (assessment/right-sizing) — schema-only 패턴

`/api/assessment`·`/api/right-sizing` 은 매퍼가 hand-built dict 를 반환하는 배포된 frozen 외부 계약이다.
FastAPI 의 응답 모델 검증·재구성은 이질 구조(예: sizing.axes 의 cpu/mem vs disk)에 null 키를 더하거나 필드를
stripping 해 출력을 바꿀 수 있어, 이 둘은 응답 타입을 선언하지 않는다. 대신:
- `view_models/assessment_api.py`·`view_models/right_sizing_api.py` 에 계약 전체를 `TypedDict` 로 선언
  (`__pydantic_config__` 로 `extra=forbid`). 규약대로 필드는 present + nullable 이고, 실제로 생략되는 키만
  `NotRequired` 다.
- 매퍼 함수가 그 `TypedDict` 를 반환 타입으로 단다 — 매퍼가 만드는 것이 dict 이므로 선언과 조립이 같은 타입을
  공유하고, 키 이름·타입 불일치가 pyright 에서 걸린다. `BaseModel` 로 선언하면 매퍼 쪽 dict 리터럴이 검사 밖에
  놓인다.
- 라우터 `responses={200: {"model": ...}}` 로 OpenAPI 스키마만 문서화 — 실 응답은 매퍼 dict 그대로(재구성 0).
  `TypedDict` 도 `BaseModel` 과 같은 스키마(properties·required·additionalProperties)를 낸다.
- 실행 시점 drift 가드를 함께 둔다 — 테스트(`tests/unit/test_assessment_api_properties.py` property·`tests/unit/test_right_sizing_api.py`
  시나리오)가 매퍼 출력을 `TypeAdapter(...).validate_python` 에 태운다. 동적 키로 조립하는 자리는 pyright 가
  증명하지 못하므로 이 실행 시점 검증이 필요하다.
