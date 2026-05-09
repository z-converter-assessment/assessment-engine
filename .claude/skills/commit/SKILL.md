---
name: commit
description: TRIGGER when user requests commit ("커밋", "/commit", "commit it"). Drafts a commit per project convention (한글 설명, type prefix), safely stages specific files, commits with HEREDOC body and Co-Authored-By footer.
---

# commit — 프로젝트 규약 기반 커밋

## 커밋 컨벤션

설명은 한글. type prefix 사용.

| 타입 | 설명 |
|------|------|
| feat | 새 기능 |
| fix | 버그 수정 |
| chore | 설정·패키지 변경 |
| refactor | 리팩토링 |
| test | 테스트 코드 |

## 절차

1. 다음 3개를 병렬 Bash로 실행:
   - `git status` (`-uall` 금지 — 메모리 폭주)
   - `git diff` (staged + unstaged)
   - `git log -n 10 --oneline` (스타일 참조)

2. 변경 분석 → 메시지 초안:
   - 타입 prefix: `feat` / `fix` / `chore` / `refactor` / `test` 중 정확한 분류
     - `feat` = 새 기능 (기존 기능 확장 X)
     - `fix` = 버그 수정
     - `chore` = 설정·패키지 변경
     - `refactor` = 동작 동일, 구조 변경
     - `test` = 테스트 코드만
   - 한글 설명. 1~2문장. "왜"에 초점, "무엇"은 부수.
   - 예: `refactor: D4 실패 처리 매트릭스를 docs/architecture/consumer.md로 이전, CLAUDE.md는 원칙만 유지`

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

   **Co-Authored-By footer 절대 추가 금지** — 본 프로젝트 정책. Claude가 작성했다는
   메타데이터(Co-Authored-By, "Generated with Claude Code", 🤖 이모지 등) 일체 포함하지 않음.

6. `git status`로 결과 확인.

## 금지

- `git config` 수정 금지
- `--no-verify`, `--no-gpg-sign` 등 hook/signing 스킵 금지 (사용자 명시 요청 없으면)
- `--amend` 금지 (사용자 명시 요청 없으면) — 새 커밋 생성이 기본

(참고: 본 프로젝트 현재 `.pre-commit-config.yaml` 없음. 추후 git pre-commit hook 도입 시 "hook 실패 → 원인 수정 + 새 커밋" 정책 적용.)

## 사용자 명시 요청 없으면 push 금지

push는 별도 워크플로우 (`/push`).