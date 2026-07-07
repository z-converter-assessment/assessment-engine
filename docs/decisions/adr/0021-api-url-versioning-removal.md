# ADR 0021: API URL prefix 단순화 (`/api/v1` → `/api`)

Status: Accepted (2026-05-23)

Supersedes: `docs/reference/web/routers.md` "Breaking change 진화 절차" 절 (v1/v2 versioning 정책)

## Context

본 프로젝트 라우터 5 모듈 (`api` / `tasks` / `exports` / `diagnostics` / `discovery`) 이 모두 `/api/v1/...` prefix. front-end JS 다수가 `fetch('/api/v1/...')` 호출.

`/v1/` versioning 의 본래 의도:
- backward-incompatible 변경 시 `/v2/` 신규 + `/v1/` 한시적 유지 → 외부 client 가 옮길 시간 확보.
- RFC 8594 `Deprecation` / `Sunset` 응답 헤더로 제거 예정 명시.

본 프로젝트 맥락:
- B2B 내부 포털 — 외부 client (별도 운영자 시스템 / 파트너 / 모바일 앱 등) 0.
- API consumer = 본 repo 안 front-end JS 만. breaking change 시 라우터 + JS 같이 정정 (단일 atomic commit).
- versioning prefix 가 가치 = "외부 client 가 옮길 시간 확보" — 본 프로젝트는 그 시나리오 없음.
- `/api/v1/` 의 `/v1/` 는 자리만 차지 + 가독성·타이핑 비용.

## Decision

URL versioning prefix 폐기. 모든 JSON API 는 `/api/...` 직접 사용.

| 변경 전 | 변경 후 |
|---------|---------|
| `/api/v1/servers/...` | `/api/servers/...` |
| `/api/v1/tasks/...` | `/api/tasks/...` |
| `/api/v1/exports/...` | `/api/exports/...` |
| `/api/v1/diagnostics/...` | `/api/diagnostics/...` |
| `/api/v1/discovery/...` | `/api/discovery/...` |

영향:
- 라우터 5 파일 prefix 정정 (`prefix="/api/v1/X"` → `prefix="/api/X"`)
- front-end JS 다수 (`fetch('/api/v1/...')` → `fetch('/api/...')`)
- 영구 docs (architecture / products / CLAUDE.md) 다수 인용 정정
- ADR historical 인용 (ADR 0001 / 0004 / 0015) 은 정정 안 함 — ADR 영구·불변 정책 (정정만, 덮어쓰기 금지). 작성 당시 URL 그대로 보존

breaking change 절차:
- 외부 client 없음 → 라우터 + JS + docs 동시 정정 (본 repo 안 atomic commit) 으로 처리.
- 외부 contract (외부 인프라가 API 호출하는 시나리오) 도입 시 별도 결정 — 그 시점에 versioning 재도입 검토.

## Consequences

긍정:
- URL 가독성·타이핑 비용 감소.
- 자리만 차지하는 dead policy (`/v1/`) 제거 — front-end 코드 안 fetch URL 도 단순.
- `routers.md` 의 versioning / breaking change 절차 절 폐기 → 문서 단순화.
- 본 프로젝트 사상 (#A0 — 기능 개발 환경만, 외부 contract 의무 없음) 과 정합.

부정:
- 외부 client 도입 시 versioning 재도입 의무 — 그 시점 별도 ADR + 라우터 prefix 재변경 + 외부 client 측 정정 의무. 본 시점에는 외부 client 시나리오 0 이라 비용 0.
- 본 결정 자체가 1 회 breaking change — 본 repo 안 라우터 + JS + docs 동시 정정으로 atomic 처리.

미정: 없음.
