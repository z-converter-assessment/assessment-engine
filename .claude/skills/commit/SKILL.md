---
name: commit
description: TRIGGER when user requests commit ("커밋", "/commit", "commit it"). Drafts a commit per project convention (한글 설명, type prefix), safely stages specific files, commits with HEREDOC body. AI 메타데이터 footer(Co-Authored-By / Generated with Claude Code / AI 이모지) 추가 금지.
---

# commit — 프로젝트 규약 기반 커밋

## 커밋 컨벤션

설명은 한글. type prefix 는 `pr-title-check.yml` + `.githooks/commit-msg` 가 강제하는 Conventional Commits set 단일 진실 — 본 skill 이 별도 표로 복제하지 않는다:

`feat`(기능) · `fix`(버그) · `docs`(문서) · `refactor`(동작 동일·구조 변경) · `perf`(성능) · `test`(테스트) · `build`(빌드·의존성) · `ci`(워크플로·스크립트) · `chore`(기타 설정·잡무) · `style`(포맷팅) · `revert`(되돌리기). breaking change 는 `<type>!:` 또는 body `BREAKING CHANGE:`.

## 절차

0. Pre-check (선택 — 기본은 생략, 단순 커밋): 일반 커밋은 바로 1로 진행한다. NG 게이트로 commit 을 막지 않는다.
   - lint 자가검증이 필요하면 빠른 `uv run ruff format . && uv run ruff check .` 만 (선택). 전체 회귀 검증(`scripts/local-ci.sh`)은 매 커밋 의무가 아니라 PR 생성/푸시 시점(develop·main 대상)에 수행 — wrap-up Stage 3·CI 책임.
   - 최종 게이트는 git hook(`.githooks/commit-msg` type prefix·AI 메타·이모지 / `pre-push` main 직접 차단) — 스킬은 작성 가이드(opt-in).

1. 다음 3개를 병렬 Bash로 실행:
   - `git status` (`-uall` 금지 — 메모리 폭주)
   - `git diff` (staged + unstaged)
   - `git log -n 10 --oneline` (스타일 참조)

2. 변경 분석 → 메시지 초안:
   - 위 컨벤션 set 중 변경 본질로 정확히 분류 (기능 `feat` / 버그 `fix` / 문서 `docs` / 워크플로 `ci` / 구조 `refactor` 등).
   - 한글 설명. 1~2문장. "왜"에 초점, "무엇"은 부수.
   - 예: `refactor: D1 실패 처리 매트릭스를 docs/architecture/consumer.md로 이전, CLAUDE.md는 원칙만 유지`

3. 시크릿 검사: `.env`, `credentials.json` 등이 staged면 사용자에게 경고.

4. 스테이징:
   - `git add <file1> <file2>` 명시 (절대 `git add -A` 또는 `git add .` 금지)
   - 의도 외 파일 포함 위험 방지

5. 커밋 (HEREDOC 사용):
   ```bash
   git commit -m "$(cat <<'EOF'
   refactor: ...
   EOF
   )"
   ```

   Co-Authored-By footer 추가 금지 — 본 프로젝트 정책. Claude·Anthropic·LLM이 작성했다는
   메타데이터(Co-Authored-By, "Generated with Claude Code", AI 이모지 등) 일체 포함하지 않음 (글로벌 CLAUDE.md).

6. `git status`로 결과 확인.

## 금지

- `git config` 수정 금지
- `--no-verify`, `--no-gpg-sign` 등 hook/signing 스킵 금지 (사용자 명시 요청 없으면)
- `--amend` 금지 (사용자 명시 요청 없으면) — 새 커밋 생성이 기본

git hook 강제 게이트: `.githooks/commit-msg`가 type prefix 컨벤션·AI 메타데이터·이모지를 검사, `.githooks/pre-push`가 main 직접 push 차단. `core.hooksPath`는 `scripts/local-ci.sh`가 자동 설정. hook 실패 시 우회(`--no-verify`) 금지 — 원인 수정 후 새 커밋. 본 skill 은 작성 가이드(opt-in), hook 은 최종 게이트.

## push

push 는 사용자 명시 요청 시에만 — feature 브랜치는 `git push -u origin <branch>`. main/master 직접 push 는 `.githooks/pre-push` 가 차단 (PR 경유).