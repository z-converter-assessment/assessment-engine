---
name: pr-create
description: TRIGGER when user requests PR creation ("PR 만들어줘", "/pr-create", "open a PR"). Analyze ALL commits since base branch (not just latest), draft title/body per project commit convention, create via gh CLI with HEREDOC body. Default base branch is `develop` (NOT main) — main 직접 PR은 사용자가 명시 요청한 경우만. PR template 존재 시 그 양식·구조 그대로 따름 (헤더·체크박스·섹션).
---

# pr-create — PR 생성

커밋 컨벤션은 `/commit` skill 참조. 브랜치 전략: main(배포 — 직접 push 금지) / develop(통합 — PR 머지) / feature·fix·chore(작업). main/master 직접 push 는 `.githooks/pre-push` 가 차단.

## Base 브랜치 정책 (본 프로젝트)

- Default base: `develop` — feature branch -> develop 통합이 표준. main 은 release-only.
- `--base main` 은 사용자가 명시적으로 "main에 PR" 요청한 경우에만. 본 경로는 hotfix 또는 release ceremony 한정.
- develop 브랜치가 원격에 없으면 발행 전에 먼저 확인 (`git ls-remote --heads origin develop`). 없으면 사용자에게 보고 후 `git push origin main:develop` 같은 초기화 절차 안내 — pr-create 가 임의 생성 안 함.

### main PR 추가 강화 (`--base main` 분기)

main 은 배포 branch — 강화 의무 (운영자가 main PR 시 적용):

1. main PR 정상 경로 = `develop` → `main` 승격(merge method, ADR 0030) 또는 `hotfix/*`. feature/* 등 다른 prefix 직접 main PR 이면 사용자에게 의도 재확인. (릴리즈 자체는 main 머지 후 main 에 `v*` tag push — PR 아님)
2. Pre-check 확장 (아래 Pre-check 절 기본 + main 추가):
   - `uv run pytest tests/integration -q` 의무 (사용자 confirm 없이 — main 안전 우선). 단위만으로 부족
   - `git log origin/main..HEAD --oneline` 의 모든 commit message 가 Conventional Commits 형식 의무 확인 (history·GitHub release notes 일관)
   - breaking change 의도 시 (`feat!:` 또는 body `BREAKING CHANGE:`) → 사용자에게 ADR 신설 의무 확인 (`docs/adr/NNNN-*.md`)
   - `git diff origin/main...HEAD -- docs/adr/` 가 비어 있고 type prefix 가 `feat!:` 또는 `BREAKING CHANGE:` 면 ADR 누락 차단
3. PR body 추가 명시:
   - "main PR 사유" 절 (hotfix · release · 기타) 강제
   - hotfix 면 영향 receive 운영자 가시화
4. CI 통과 의무 검증: `bash scripts/local-ci.sh main` 으로 전체 로컬 재현 (워크플로 카탈로그는 `.github/workflows/` 단일 진실). codeql 은 CLI 미설치 시 skip — CI 가 최종 SAST.

## PR template 우선 (본 프로젝트 의무)

`gh pr create --body` 로 본문을 명시 주면 `.github/PULL_REQUEST_TEMPLATE.md` 가 자동 적용되지 않는다. body 명시 호출자가 template 양식을 그대로 따라 채워야 한다.

절차:
1. template 존재 확인:
   ```bash
   ls .github/PULL_REQUEST_TEMPLATE.md .github/PULL_REQUEST_TEMPLATE/ 2>/dev/null
   ```
2. template 있으면 본문 전체를 `cat .github/PULL_REQUEST_TEMPLATE.md` 로 읽어 그 헤더·섹션·체크박스 구조 그대로 유지. placeholder(`- [ ] 작업 내용 1`, `* Resolves: #`, 빈 줄 등) 를 본 PR 변경 내용으로 채움.
3. template 의 이모지·기호는 사용자 의도된 양식이라 그대로 보존 — 글로벌 "이모지 금지" 룰의 예외 (프로젝트 PR template 한정).
4. template 없으면 fallback 으로 `## Summary` + `## Test plan` 2 섹션 한글 작성.

template 의 placeholder 채우기 원칙:
- 작업 내용 체크박스 `- [ ]` 는 본 PR 변경 카테고리별 한 줄.
- "PR 유형" 체크박스는 본 PR 의 type prefix 와 정합 1개만 `[x]`, 나머지 그대로 `[ ]`.
- "테스트 방법" 은 리뷰어가 재현 가능한 명령·URL 명시.
- "체크리스트" 는 본 PR 에서 실제 수행한 것만 `[x]`, 안 한 것은 `[ ]` 유지 (강제 체크 금지).
- "관련 이슈" / "스크린샷" / "기타 참고" 빈 항목은 그대로 비워둠 또는 `-` (양식 보존).

## Pre-check (PR 발행 직전 의무)

CI 실패 + 이메일 폭탄 회피. PR 발행 직전 본 검증이 의무 — 누락 시 GitHub Actions runner 가 발견하는 비용 + 협업자 알림 noise.

### A. PR title format (gh pr create 호출 전 의무)

`.github/workflows/pr-title-check.yml` 이 `amannn/action-semantic-pull-request` 로 검증. 본 repo 정합 패턴:

1. `cat .github/workflows/pr-title-check.yml` 로 현재 정합 패턴 직접 추출 (`types`·`subjectPattern`·`subjectPatternError`)
2. PR title 검증 항목 (본 repo 현재 정책):
   - `<type>: <subject>` 형식. type 은 `feat`·`fix`·`docs`·`chore`·`refactor`·`perf`·`test`·`build`·`ci`·`style`·`revert` 중 하나
   - subject 첫 글자 소문자 또는 비-알파벳 (한글 등) — `subjectPattern: ^[^A-Z].+$`. 약어 (ZDM·SQL 등) 가 subject 첫 글자면 fail → 한국어 시작 또는 소문자 약어 사용
   - breaking change 의도면 `feat!:` / `fix!:` 또는 body 안 `BREAKING CHANGE:`
3. 위 정합 안 맞으면 `gh pr create` 호출 안 함 — title 수정 후 재시도

PR title 작성 패턴 (정합):
- OK: `feat: 운영자 자율 선택 + src 정석화` (한글 시작)
- OK: `fix: handle null hostname` (소문자 시작)
- OK: `refactor: src 정석화` (소문자 + 한글 혼합)
- NG: `feat: ZDM 직접 fetch` (Z 대문자 시작)
- NG: `feat: Add new endpoint` (A 대문자 시작)

### B. 코드 검증 — base PR 대상 모드로 `scripts/local-ci.sh` 실행

base=develop -> `bash scripts/local-ci.sh develop` (ruff·hadolint·unit·alembic·integration). base=main -> `bash scripts/local-ci.sh main` (전부 + release 산출물). 검증 범위는 스크립트 단일 진실. NG 항목 있으면 PR 발행 차단 — 원인 수정 + 새 commit 후 재시도.

## 사전 분석 (병렬 Bash)

1. `git status` — 미커밋 변경 확인 (있으면 사용자에게 먼저 commit 권유)
2. `git diff` — staged/unstaged 잔존
3. 원격 추적 + 최신성:
   ```bash
   git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null
   git log --oneline @{u}..HEAD 2>/dev/null
   ```
4. base 브랜치 (`develop`) 대비 전체 변경:
   ```bash
   git ls-remote --heads origin develop      # 원격 develop 존재 확인
   git fetch origin develop
   git log origin/develop..HEAD --oneline
   git diff origin/develop...HEAD --stat
   ```
5. PR template 확인 (위 "PR template 우선" 절):
   ```bash
   cat .github/PULL_REQUEST_TEMPLATE.md 2>/dev/null
   ```

## 작성 원칙

- 제목: 70자 미만. type prefix(feat/fix/chore/refactor/test). 한글 설명.
- 본문: template 있으면 template 구조 따름 (위 절). 없으면 `## Summary` + `## Test plan`.
- 단일 커밋이 아닌 브랜치의 ALL 커밋 분석 — 가장 최근 커밋만 보고 작성하면 누락.

## PR 생성

push 안 됐으면 `git push -u origin <current-branch>` 먼저.

template 양식 따르는 예시 (`.github/PULL_REQUEST_TEMPLATE.md` 가 다음과 같다고 가정):

```markdown
## PR 요약
## 관련 이슈
* Resolves: #

## 작업 내용
- [ ] 작업 내용 1

## PR 유형
- [ ] 버그 수정
- [ ] 새로운 기능
- [ ] 리팩토링

## 변경 사항
-

## 테스트 방법
1.

## 체크리스트
- [ ] 로컬에서 동작 확인
- [ ] README 업데이트 (필요 시)
```

채워서 발행:

```bash
gh pr create --base develop --title "feat: 본 PR 한 줄 요약" --body "$(cat <<'EOF'
## PR 요약
본 PR 핵심 변경 1~2 문장.

## 관련 이슈
* Resolves: #123

## 작업 내용
- [x] 변경 카테고리 1
- [x] 변경 카테고리 2

## PR 유형
- [ ] 버그 수정
- [x] 새로운 기능
- [ ] 리팩토링

## 변경 사항
- 파일·모듈별 변경 한 줄씩

## 테스트 방법
1. 명령 또는 URL
2. 기대 결과

## 체크리스트
- [x] 로컬에서 동작 확인
- [ ] README 업데이트 (필요 시)
EOF
)"
```

`--base develop` 명시 — main 이 default 가 아님을 PR 생성 시점에도 명확히.

AI 메타데이터 footer 추가 금지 — "Generated with Claude Code" / "Co-Authored-By: Claude ..." 등 본 프로젝트 산출물 일체 포함 안 함 (글로벌 CLAUDE.md).

## 결과

`gh pr create` 가 반환하는 PR URL 을 사용자에게 그대로 노출.

## 금지

- gh interactive flag 사용 금지 (`-i` 등)
- `--base main` 자동 사용 금지 — 사용자가 명시 요청한 경우만
- develop 브랜치 부재 시 임의 생성 금지 — 사용자 확인 후 진행
- `.github/PULL_REQUEST_TEMPLATE.md` 무시하고 임의 양식 작성 금지 — template 있으면 그 구조 의무
