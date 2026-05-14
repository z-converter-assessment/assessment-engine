---
name: pr-create
description: TRIGGER when user requests PR creation ("PR 만들어줘", "/pr-create", "open a PR"). Analyze ALL commits since base branch (not just latest), draft title/body per project commit convention, create via gh CLI with HEREDOC body. Default base branch is `develop` (NOT main) — main 직접 PR은 사용자가 명시 요청한 경우만. PR template 존재 시 그 양식·구조 그대로 따름 (헤더·체크박스·섹션).
---

# pr-create — PR 생성

브랜치 전략은 `/push` skill, 커밋 컨벤션은 `/commit` skill 참조 (단일 진실).

## Base 브랜치 정책 (본 프로젝트)

- Default base: `develop` — feature branch -> develop 통합이 표준. main 은 release-only.
- `--base main` 은 사용자가 명시적으로 "main에 PR" 요청한 경우에만.
- develop 브랜치가 원격에 없으면 발행 전에 먼저 확인 (`git ls-remote --heads origin develop`). 없으면 사용자에게 보고 후 `git push origin main:develop` 같은 초기화 절차 안내 — pr-create 가 임의 생성 안 함.

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
