# 표현계층 타입 계약 (ViewModel -> 생성 TS 타입 -> tsc --checkJs)

서버 ViewModel 과 클라이언트 JS 사이의 계약을 컴파일 타임에 강제하는 메커니즘. 무타입 JS 가 서버 응답
형태를 사람 규율로만 지키던 것을 컴파일러 강제로 옮긴다. React/Vite/번들/SSR 변경 없는 dev/CI 전용 검사
계층 — 배포 산출물(단일 Python 이미지)은 불변.

## 파이프라인

```
FastAPI 엔드포인트(response_model / return 타입)
        |  app.openapi()
        v
OpenAPI 스키마  --openapi-typescript-->  생성 TS 타입(static/js/generated/api.ts, 커밋)
                                                |  JSDoc @type import
                                                v
                              // @ts-check 클라 JS  --tsc --checkJs-->  계약 위반 = 컴파일 에러
```

핵심 강제 지점은 fetch 경계다. 클라 JS 가 `fetch('/api/...')` 응답을 생성 타입으로 annotate 하면, 서버
ViewModel 필드가 바뀔 때(rename/타입 변경) codegen 이 타입을 갱신하고 tsc 가 소비처의 불일치를 컴파일
에러로 드러낸다.

## 구성

| 파일 | 역할 |
|------|------|
| `package.json` | pnpm(packageManager 핀). devDep: typescript·openapi-typescript·chart.js·@types/cytoscape. 빌드/번들/런타임 아님 — 검사 도구. |
| `tsconfig.json` | strict + `noImplicitAny:false`(점진 채택) + `checkJs:false`(파일별 opt-in) + `moduleDetection:force`(page script 를 tsc 상 격리 모듈로 — 파일 간 전역 식별자 충돌 제거). vendor 제외. |
| `scripts/dump_openapi.py` | 서버 불요 `app.openapi()` 덤프(codegen 입력). import 시 dev 기본값 자체 주입. |
| `static/js/generated/api.ts` | openapi-typescript 생성 타입(커밋 — drift 게이트 대상). 직접 편집 금지. |
| `static/js/globals.d.ts` | 전역 lib(Chart·cytoscape) + 프로젝트 모듈 전역(ChartUtils·TableUtils·ToastUtils·EmitUtils·TaskModal) ambient 선언. |
| `pnpm run codegen` | `dump_openapi.py` -> `openapi-typescript` -> `generated/api.ts`. |
| `pnpm run typecheck` | `tsc --noEmit`. |

## 규약

- 서버 JSON 엔드포인트는 응답 타입을 선언한다 — `response_model=` 또는 return 어노테이션(`-> Foo`). FastAPI 가
  stdlib dataclass ViewModel 도 OpenAPI 스키마로 변환한다(Pydantic 필수 아님). 응답 검증도 함께 붙는다.
- 클라 JS 는 파일별 점진 채택 — 파일 최상단 `// @ts-check` 로 opt-in. `noImplicitAny:false` 라 미타입 변수는
  any 로 통과하되, (1) `fetch('/api/...')` 응답을 `/** @type {import('...generated/api').components['schemas']['<Name>'][]} */`
  로 annotate 하고 (2) strictNullChecks(DOM null 등)를 가드/캐스트로 좁힌다. 파생 계산은 서버 단일 소스(P2)
  유지 — 클라는 통계·분류·단위 변환을 재계산하지 않는다(차트 range 토글 등 인터랙션 파생만 예외, P4 정신).
- 생성 `api.ts` 는 커밋한다 — 리뷰어가 타입을 보고, CI 가 drift 를 잡는다.

## CI 게이트

`ci.yml` typecheck job(develop PR 게이트 포함): `pnpm run codegen` 재생성 후 `git diff --exit-code`(엔드포인트
변경 후 codegen 미실행 = drift 차단) + `pnpm run typecheck`(계약 위반 차단).

## 확장 방법

- 신규 JSON 엔드포인트: return 타입/`response_model` 선언 -> `pnpm run codegen` -> 커밋. 소비 JS 에서 응답
  annotate.
- ViewModel 필드 변경: mapper 등 갱신(F9) 후 `pnpm run codegen` 으로 `api.ts` 재생성 -> 커밋(drift 게이트).
- JS 파일 타입 채택: `// @ts-check` 추가 -> fetch 경계 annotate + null 가드 -> `pnpm run typecheck` 로 그
  파일 clean 확인.

## 한계·현황

- 점진 채택 상태 — `// @ts-check` 파일만 검사된다. 미채택 파일은 tsc 무검사(향후 파일별 확대).
- `noImplicitAny:false` — 내부 로직 변수는 any 허용(계약 강제의 핵심은 fetch 경계). 전역(ChartUtils 등)은
  `globals.d.ts` 실용 선언이라 일부 반환이 permissive. 정밀화는 각 모듈 // @ts-check 로 점진.
- assessment/right-sizing API 는 hand-built dict 응답이라 아직 명명 스키마가 없다(생성 타입상 unknown).
  Pydantic 봉투 모델화는 별도.
