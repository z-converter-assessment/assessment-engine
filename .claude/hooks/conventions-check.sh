#!/bin/bash
# PostToolUse hook: Edit/Write 직후 프로젝트 컨벤션 위반 차단.
# 위반 발견 시 exit 2 + stderr -> Claude에 system-reminder 피드백.
#
# 검사 카탈로그 (CLAUDE.md 단일 진실 위치):
#   F1   .py    `from __future__ import annotations` 금지
#   F11  .py    `print(` / `sys.stdout.write` 금지 (loguru 일관성)
#   C3   .py    `safe_*` 미경유 redis 클라이언트 직접 호출 금지 (db/redis.py 본인 제외)
#   글로벌 *  `**...**` markdown bold 금지
#   글로벌 *  비키보드 unicode 기호·이모지 금지 (§ ↔ ↑↓ >= <= != X OK 등으로 대체)

set -euo pipefail

INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")

[ -z "$FILE" ] && exit 0
[ -f "$FILE" ] || exit 0

# 정책 정의 영역 self-skip — `.claude/` 안 파일(CLAUDE.md / hooks / agents / skills /
# settings)은 자기가 검사하는 패턴을 본문에 카탈로그로 가진다. 정책 정의 자체가
# 위반 예시로 매칭되는 게 자연스러우므로 검사 대상에서 제외. hook은 src/docs만 강제.
case "$FILE" in
  */.claude/*) exit 0 ;;
esac

VIOLATIONS=""

# ─── 글로벌: ** ** markdown bold 금지 ───────────────────────────────────────
# markdown 계열(.md/.html/.rst)만 검사 — Python의 `10**11`(거듭제곱)·`**kwargs`,
# SQL의 `**` 등 코드 안 합법 표기 false positive 방지.
case "$FILE" in
  *.md|*.markdown|*.html|*.htm|*.rst)
    if grep -nE '\*\*[^*]+\*\*' "$FILE" >/dev/null 2>&1; then
      MATCH=$(grep -nE '\*\*[^*]+\*\*' "$FILE" | head -5)
      VIOLATIONS+=$'\n'"[GLOBAL] markdown bold (asterisk pair) is forbidden — 시각적 노이즈, 신호 가치 없음."$'\n'"$MATCH"$'\n'
    fi
    ;;
esac

# ─── 글로벌: 비키보드 unicode 기호·이모지 금지 ──────────────────────────────
# 허용 예외: 단방향 화살표(->, →), em-dash(—). 본문 흐름의 자연스러운 일부.
if grep -nP '[§↔↑↓✓✗✅⚠❌🤖×÷≥≤≠•◦▪▫■□●○]' "$FILE" >/dev/null 2>&1; then
  MATCH=$(grep -nP '[§↔↑↓✓✗✅⚠❌🤖×÷≥≤≠•◦▪▫■□●○]' "$FILE" | head -5)
  VIOLATIONS+=$'\n'"[GLOBAL] non-keyboard unicode chars detected. ASCII/한글로 대체 (>= 대신 >=, etc):"$'\n'"$MATCH"$'\n'
fi

# ─── .py 전용 검사 ─────────────────────────────────────────────────────────
if [[ "$FILE" == *.py ]]; then
  # F1: from __future__ import annotations 금지
  if grep -nE '^[[:space:]]*from __future__ import .*annotations' "$FILE" >/dev/null 2>&1; then
    MATCH=$(grep -nE '^[[:space:]]*from __future__ import .*annotations' "$FILE")
    VIOLATIONS+=$'\n'"[F1] 'from __future__ import annotations' is forbidden project-wide."$'\n'"$MATCH"$'\n'"  Reason: Python 3.12 evaluates annotations eagerly. Deferred annotations break Pydantic/dataclass introspection + cause NameError on TYPE_CHECKING-only refs."$'\n'
  fi

  # F11: print( / sys.stdout.write 금지 (loguru 일관성)
  # 정규식: 줄 시작 또는 공백 다음 print(, 또는 sys.stdout.write
  if grep -nE '(^|[^a-zA-Z_.])print\(|sys\.stdout\.write' "$FILE" >/dev/null 2>&1; then
    MATCH=$(grep -nE '(^|[^a-zA-Z_.])print\(|sys\.stdout\.write' "$FILE" | head -5)
    VIOLATIONS+=$'\n'"[F11] print() / sys.stdout.write is forbidden — use loguru (logger.info/warning/error/exception)."$'\n'"$MATCH"$'\n'
  fi

  # C3: safe_* 미경유 redis 클라이언트 직접 호출 금지
  # db/redis.py 본인은 제외 (safe_* helper 정의 위치).
  if [[ "$FILE" != *"db/redis.py" ]]; then
    if grep -nE '\bredis\.(set|get|delete|publish|incr|exists|mget|expire|setnx|set_nx)\(' "$FILE" >/dev/null 2>&1; then
      MATCH=$(grep -nE '\bredis\.(set|get|delete|publish|incr|exists|mget|expire|setnx|set_nx)\(' "$FILE" | head -5)
      VIOLATIONS+=$'\n'"[C3] direct redis client calls are forbidden — use safe_* helpers from db/redis.py (fail-open guarantee)."$'\n'"$MATCH"$'\n'
    fi
  fi
fi

if [ -n "$VIOLATIONS" ]; then
  {
    echo "Convention violations in $FILE:"
    printf '%s' "$VIOLATIONS"
    echo
    echo "  Single source of truth: .claude/CLAUDE.md (#F1 / #F11 / #C3 / global)."
  } >&2
  exit 2
fi

exit 0
