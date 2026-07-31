---
name: pr
description: TRIGGER when the user requests a PR ("PR 만들어줘", "/pr", "open a PR"). Analyzes ALL commits since base (not just latest), drafts title/body per the project PR template + Conventional Commits, creates via gh CLI with HEREDOC body. Base is `develop` by default (NOT main) — `--base main` only when the user explicitly asks. No AI metadata in title/body.
---

# /pr — PR 생성

브랜치 전략: main(배포 — 직접 push 금지) / develop(통합 — PR 머지) / feature·fix·chore(작업). 보호 브랜치 직접 push 는 GitHub ruleset 이 차단.

## Base 브랜치 정책

- Default base = `develop`. feature -> develop 통합이 표준.
- `--base main` 은 사용자가 명시적으로 "main에 PR" 요청한 경우만 (release 승격 또는 hotfix).
- 원격 `develop` 부재 시 임의 생성 X — `git ls-remote --heads origin develop` 확인 후 사용자에게 보고.

### base = `develop` 게이트 (통합 branch)

코드 품질을 기능 단위로 판정하는 지점. 문서는 여기서 손대지 않는다 — develop 에 여러 feature 가 모인 뒤 main 승격 시점에 한 번에 맞춘다.

1. code-reviewer 에이전트 1회 (`Agent(subagent_type='code-reviewer')`) — 정석 idiom + 명문 규약(P1-P4·F1-F11·#B·#C5). Error 즉시 수정 / Warning 위임 / Info 보고.

lint·단위 테스트·타입 계약·alembic drift 는 PR 발행 후 CI 가 돌린다 — 로컬 재현 없이 CI 결과를 확인한다.

### `--base main` 추가 강화 (배포 branch)

릴리즈 단위가 확정되는 지점. 문서·ADR 정합을 여기서 일괄 처리한다.

1. 정상 경로 = `develop -> main` 승격 또는 `hotfix/*`. 다른 prefix 직접 main PR 이면 의도 재확인.
2. `uv run pytest tests/integration -q` 의무 (사용자 confirm 없이 — main 안전 우선).
3. `git log origin/main..HEAD --oneline` 전 커밋이 Conventional Commits 형식 의무.
4. 문서 정합 — `docs` skill 을 승격 대상 영역으로 실행. 코드 현황 대조·문서 갱신·doc-auditor 검증까지 그 skill 이 담당한다. 변경 유형이 결합 목록에 걸리면 `change-impact` skill 로 동시 갱신 위치를 먼저 확인한다.
5. 결정이 바뀌었으면(기존 ADR 을 뒤집거나 breaking) `docs/decisions/adr/NNNN-*.md` 신설 + 이전 ADR `Superseded by` + 인덱스 행 추가 의무 — 누락 시 차단.
6. PR body 에 "main PR 사유"(release·hotfix) 절 강제.

## Pre-check (발행 직전 의무 — CI 실패·알림 noise 회피)

### A. PR title 형식
`.github/workflows/pr-title-check.yml`(semantic PR)이 검증. `cat` 으로 현재 패턴 추출 후 정합:
- `<type>: <subject>` — type in feat·fix·docs·chore·refactor·perf·test·build·ci·style·revert.
- subject 첫 글자 대문자 금지 (`^[^A-Z].+$`) — 약어(ZDM·SQL) 첫 글자면 fail. 한글 시작 또는 소문자.
- breaking 이면 `feat!:`/`fix!:` 또는 body `BREAKING CHANGE:`.
- OK: `feat: 운영자 자율 선택` / `fix: handle null hostname`. NG: `feat: ZDM 직접 fetch`(대문자).

### B. 코드 검증
발행 후 CI 결과를 확인한다. 실패하면 수정 + 새 commit 을 올려 재검한다.

## PR template 우선 (의무)

`--body` 로 본문을 명시하면 `.github/PULL_REQUEST_TEMPLATE.md` 가 자동 적용 안 되니 호출자가 그 양식을 채운다:
1. `cat .github/PULL_REQUEST_TEMPLATE.md` 로 헤더·섹션·체크박스 구조 읽기.
2. 그 구조 그대로 유지 — placeholder 를 본 PR 내용으로 채움. template 의 이모지·기호는 사용자 의도 양식이라 보존 (전역 이모지 금지의 template 예외).
3. 체크박스: PR 유형은 type prefix 와 정합 1개만 `[x]`. 체크리스트는 실제 수행한 것만 `[x]` (강제 체크 금지). 빈 항목(관련 이슈·스크린샷)은 그대로.
4. template 없으면 fallback `## Summary` + `## Test plan` (한글).

## 분석 (병렬 Bash)

1. `git status` — 미커밋 있으면 사용자에게 `/commit` 권유.
2. `git fetch origin develop` + `git log origin/develop..HEAD --oneline` + `git diff origin/develop...HEAD --stat` — 브랜치의 ALL 커밋 분석 (최신 커밋만 보면 누락).

## 생성

- push 안 됐으면 `git push -u origin <branch>` 먼저.
- `gh pr create --base develop --title "<type>: 한 줄 요약" --body "$(cat <<'EOF' ... EOF)"` — HEREDOC body.
- 제목 70자 미만. body 는 template 구조 채움 (현재 상태 선언, 브랜치 전체 요약).
- AI 메타데이터 footer(Generated with·Co-Authored-By) 절대 금지.
- 반환 PR URL 사용자에게 노출.

## 금지

- gh interactive flag(`-i`).
- `--base main` 자동 — 사용자 명시 시만.
- develop 부재 시 임의 생성.
- template 무시하고 임의 양식.
- AI 메타데이터.
