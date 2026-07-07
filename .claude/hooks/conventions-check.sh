#!/usr/bin/env bash
# PostToolUse guard (Edit|Write 직후): 프로젝트 규약을 결정론적으로 차단.
#
# 설계 원칙 — "확실한 것만 hard-block". 오해 없이 명확한 위반(포맷·코드·ADR 번호·옛 경로·
# 날짜 박힌 정정)만 막는다. 판단이 필요한 #F12 이력 서사(옛/폐기가 present-state 개념일 수도)는
# 훅이 아니라 /ship 스킬·doc-auditor 에이전트가 문맥으로 처리한다 (기계적=훅, 판단=스킬).
#
# 검사 카탈로그 (CLAUDE.md + docs/README.md 4원칙 단일 진실):
#   전역(docs)  bold(**...**) 금지 / 비키보드 unicode 금지
#   .py         F7 print()·sys.stdout.write 금지 / C3 safe_* 미경유 redis 직접 호출 금지
#   라이브 docs ADR 번호 참조 금지 / 옛 doc 경로 금지 / 날짜 박힌 회고형 정정 금지
#
# stdin = PostToolUse JSON. 위반 시 exit 2 + stderr (Claude 피드백).
set -euo pipefail

INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")

[ -z "$FILE" ] && exit 0
[ -f "$FILE" ] || exit 0

# 정책 정의 self-skip — `.claude/` 안 파일(CLAUDE.md 등)은 검사 패턴을 카탈로그로 담아
# 자기 자신이 위반 예시로 매칭된다. 훅은 src/·docs/ 만 강제.
case "$FILE" in */.claude/*) exit 0 ;; esac

VIOLATIONS=""
add() { VIOLATIONS+=$'\n'"$1"$'\n'"$2"$'\n'; }

# ─── 전역 포맷 (docs 계열) ──────────────────────────────────────────────────
case "$FILE" in
  *.md|*.markdown|*.html|*.htm|*.rst)
    if grep -nE '\*\*[^*]+\*\*' "$FILE" >/dev/null 2>&1; then
      add "[GLOBAL] markdown bold(**...**) 금지 — 시각 노이즈, 신호 가치 0." "$(grep -nE '\*\*[^*]+\*\*' "$FILE" | head -3)"
    fi
    ;;
esac

# 비키보드 unicode·이모지 — 허용: 단방향 화살표(-> ->)·em-dash·가운뎃점 구분자(·).
# 금지는 양방향(↔)·상하(↑↓) 화살표만 — 단방향(-> <-)은 전역 규칙상 허용.
UNI='[§↔↑↓✓✔✗✘☑⚠❌✅🤖×÷≥≤≠•◦▪▫■□●○◆°]'
if grep -nP "$UNI" "$FILE" >/dev/null 2>&1; then
  add "[GLOBAL] 비키보드 unicode 기호·이모지 금지 — ASCII/한글로 (>=·<=·!= 등)." "$(grep -nP "$UNI" "$FILE" | head -3)"
fi

# ─── .py 전용 ───────────────────────────────────────────────────────────────
if [[ "$FILE" == *.py ]]; then
  if grep -nE '(^|[^a-zA-Z_.])print\(|sys\.stdout\.write' "$FILE" >/dev/null 2>&1; then
    add "[F7] print()·sys.stdout.write 금지 — loguru(logger.*) 사용." "$(grep -nE '(^|[^a-zA-Z_.])print\(|sys\.stdout\.write' "$FILE" | head -3)"
  fi
  if [[ "$FILE" != *"cache/redis.py" ]]; then
    if grep -nE '\bredis\.(set|get|delete|publish|incr|exists|mget|expire|setnx|set_nx)\(' "$FILE" >/dev/null 2>&1; then
      add "[C3] redis 직접 호출 금지 — cache/redis.py 의 safe_* 경유(fail-open)." "$(grep -nE '\bredis\.(set|get|delete|publish|incr|exists|mget|expire|setnx|set_nx)\(' "$FILE" | head -3)"
    fi
  fi
fi

# ─── 라이브 docs (reference/guides/explanation) — 4원칙 결정론 강제 ──────────
# decisions/adr(아카이브)·temp(임시)는 예외.
case "$FILE" in
  *docs/reference/*|*docs/guides/*|*docs/explanation/*)
    if grep -nE 'ADR [0-9]{4}' "$FILE" >/dev/null 2>&1; then
      add "[DOC 4원칙] 라이브 문서는 ADR 번호 참조 금지 — 사실은 인라인, 이력은 decisions/adr 아카이브." "$(grep -nE 'ADR [0-9]{4}' "$FILE" | head -3)"
    fi
    if grep -nE 'docs/(architecture|operations|development|products)/|docs/tradeoffs' "$FILE" >/dev/null 2>&1; then
      add "[DOC 경로] 옛 doc 경로 — 새 구조(reference/guides/explanation)로." "$(grep -nE 'docs/(architecture|operations|development|products)/|docs/tradeoffs' "$FILE" | head -3)"
    fi
    if grep -nE '정정.{0,3}\(?20[0-9]{2}' "$FILE" >/dev/null 2>&1; then
      add "[DOC 선언성] 날짜 박힌 회고형 정정 금지 — 현재 상태만 선언. 경위는 decisions/adr." "$(grep -nE '정정.{0,3}\(?20[0-9]{2}' "$FILE" | head -3)"
    fi
    ;;
esac

if [ -n "$VIOLATIONS" ]; then
  {
    echo "규약 위반 in $FILE:"
    printf '%s' "$VIOLATIONS"
    echo
    echo "  단일 진실: .claude/CLAUDE.md + docs/README.md (문서 4원칙)."
  } >&2
  exit 2
fi
exit 0
