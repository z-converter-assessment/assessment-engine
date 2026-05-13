---
name: docs-sync
description: TRIGGER when user requests doc sync after code changes ("문서 동기화", "/docs-sync", "update docs"). Analyze git diff -> map changed files to relevant docs (CLAUDE.md sections, docs/architecture/, docs/adr/, docs/operations/) -> propose specific doc edits. Don't auto-apply — wait for user approval.
---

# docs-sync — 코드 변경에 따른 문서 동기화 제안

단일 진실 정책 — 코드 변경 시 관련 문서 동시 갱신. 문서 디렉토리 분류는 CLAUDE.md 전문(前文) "문서 인덱스" 표 참조.

## 절차

1. 변경 범위 확인:
   - 미커밋: `git status`, `git diff`
   - 커밋된 변경: 사용자가 범위 지정 (예: `HEAD~3..HEAD`, `main..HEAD`) — 없으면 미커밋만 분석.

2. 변경된 파일 → 관련 문서 매핑:

| 코드 경로 | 관련 문서 |
|-----------|-----------|
| `src/assessment_engine/consumer/` | `docs/architecture/consumer.md`, CLAUDE.md #D |
| `src/assessment_engine/web/routers/` | `docs/architecture/web/routers.md` |
| `src/assessment_engine/web/services/` | `docs/architecture/web/services.md`, CLAUDE.md #E3 |
| `src/assessment_engine/web/view_models.py` | `docs/architecture/web/view-models.md`, CLAUDE.md #E4 |
| `src/assessment_engine/web/static/` / `templates/` | `docs/architecture/web/static-assets.md`, CLAUDE.md #E7·E9 |
| `src/assessment_engine/db/models/` | `docs/architecture/db/models.md`, CLAUDE.md #C1 |
| `src/assessment_engine/db/repositories/` | `docs/architecture/db/repositories.md` (+ `db/dtos.md` for DTO 변경), CLAUDE.md #C2 |
| `src/assessment_engine/db/redis.py` + 키 패턴 | `docs/architecture/redis.md`, CLAUDE.md #C3 |
| `docker-compose*.yml`, `Dockerfile*` | `docs/operations/docker.md`, `docs/operations/dev-prod.md` |
| `infra/lima/*.yaml`, `infra/agent.env`, `dev-up.sh`/`dev-down.sh` | `docs/operations/lima.md`, `docs/operations/pipeline.md`, CLAUDE.md #A4 |
| `src/assessment_engine/config.py` | `docs/operations/env.md`, `docs/operations/dev-prod.md`, CLAUDE.md #A3 |
| `src/assessment_engine/consumer/schemas.py` | `docs/architecture/agent.md`, `docs/architecture/consumer.md`, CLAUDE.md #B |
| `src/assessment_engine/consumer/handler.py` | `docs/architecture/consumer.md`, CLAUDE.md #D2·D3 |
| Pydantic Input/DTO 변경 | `docs/architecture/db/dtos.md`, CLAUDE.md #B5 |

3. 각 매핑된 문서 Read → 코드 변경이 문서에 반영돼야 할 지점 식별 (예: 새 함수/필드/메서드 시그니처, 정책 변경, 설정 키 변경).

4. 제안 출력 형식 (사용자에게):
   - 문서별로 "현재 → 제안" 형태 diff
   - 어떤 코드 변경 때문에 필요한지 명시
   - 자동 적용하지 않음 — 사용자 승인 후 Edit

## 중점 검토 항목

| 변경 유형 | 동기화 검토 |
|-----------|-------------|
| 새 ENV 키 추가 | `docs/operations/env.md` 카탈로그 추가 + `docs/operations/dev-prod.md` (해당되면) |
| 새 Pydantic 필드 | `docs/architecture/agent.md` 스키마 + `docs/architecture/db/dtos.md` + CLAUDE.md #B2 |
| 새 라우터 엔드포인트 | `docs/architecture/web/routers.md` + CLAUDE.md #E5 |
| 새 Repository 메서드 | `docs/architecture/db/repositories.md` |
| 새 ViewModel·파생 필드 | `docs/architecture/web/view-models.md` + cache_serializer `_DETAIL_DISPLAY_FIELDS` 동기화 |
| 새 Redis 키 | `docs/architecture/redis.md` 키 카탈로그 + CLAUDE.md #C3 표 |
| 큐 토폴로지 변경 | `docs/architecture/rabbitmq.md` + CLAUDE.md #B4 |

## 결정 추가 시 별도 처리

새로운 트레이드오프 / 의식적 설계 결정 발생 → `docs/tradeoffs.md` 신규 항목 (T번호) 또는 `docs/adr/` ADR 신규 파일 제안.

## 금지

- 사용자 승인 없이 문서 자동 편집 — diff 제안까지만.
- `temp` 키워드 들어간 파일은 무시 (작업 중 임시 메모).
- 코드와 문서 어긋남 발견 시 임의로 코드 기준으로 정정 — 사용자 결정 필요 (코드가 맞는지, 문서가 맞는지 모를 수 있음).