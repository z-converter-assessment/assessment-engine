---
name: pr-create
description: TRIGGER when user requests PR creation ("PR 만들어줘", "/pr-create", "open a PR"). Analyze ALL commits since base branch (not just latest), draft title/body per project commit convention, create via gh CLI with HEREDOC body.
---

# pr-create — PR 생성

브랜치 전략은 `/push` skill, 커밋 컨벤션은 `/commit` skill 참조 (단일 진실).

## 사전 분석 (병렬 Bash)

1. `git status` — 미커밋 변경 확인 (있으면 사용자에게 먼저 commit 권유)
2. `git diff` — staged/unstaged 잔존
3. 원격 추적 + 최신성:
   ```bash
   git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null
   git log --oneline @{u}..HEAD 2>/dev/null
   ```
4. base 브랜치(보통 `main`) 대비 전체 변경:
   ```bash
   git log main..HEAD --oneline
   git diff main...HEAD --stat
   ```

## 작성 원칙

- 제목: 70자 미만. type prefix(feat/fix/chore/refactor/test). 한글 설명.
- 본문에 Summary + Test plan 구조.
- 단일 커밋이 아닌 브랜치의 ALL 커밋 분석 — 가장 최근 커밋만 보고 작성하면 누락.

## PR 생성

push 안 됐으면 `git push -u origin <current-branch>` 먼저.

```bash
gh pr create --title "fix: cache 역직렬화 시 datetime 복원 누락" --body "$(cat <<'EOF'
## Summary
- cache_serializer가 ISO 문자열을 그대로 dict에 두어 kst 필터에서 strftime 실패
- datetime.fromisoformat()로 복원 후 enrich 재호출

## Test plan
- [ ] /servers/<id> 진입 시 last_seen_at 정상 표시
- [ ] tests/unit/test_cache_serializer.py 통과
- [ ] cache MISS / SET / HIT 사이클 수동 검증
EOF
)"
```

AI 메타데이터 footer 추가 금지 — "Generated with Claude Code" / "Co-Authored-By: Claude ..." / AI 이모지 등 본 프로젝트 산출물 일체 포함 안 함 (글로벌 CLAUDE.md).

## 결과

`gh pr create`가 반환하는 PR URL을 사용자에게 그대로 노출.

## 금지

- gh interactive flag 사용 금지 (`-i` 등)
- 사용자가 명시적으로 base 변경 요청 안 했으면 `--base` 생략 (기본 main)