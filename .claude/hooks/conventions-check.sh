#!/bin/bash
# PostToolUse hook: Edit/Write 직후 .py 파일의 프로젝트 컨벤션(F1) 위반 차단.
# 현재 검사: `from __future__ import annotations` 추가 금지 (CLAUDE.md F1).
# 위반 발견 시 exit 2 + stderr → Claude에 system-reminder 피드백.

set -euo pipefail

INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")

[ -z "$FILE" ] && exit 0
[[ "$FILE" == *.py ]] || exit 0
[ -f "$FILE" ] || exit 0

# F1: `from __future__ import annotations` 전역 금지
# 들여쓰기 허용(if 블록 안에 두는 경우도 차단), 다른 future import와 함께 있어도 잡음
if grep -nE '^[[:space:]]*from __future__ import .*annotations' "$FILE" >/dev/null; then
  MATCH=$(grep -nE '^[[:space:]]*from __future__ import .*annotations' "$FILE")
  {
    echo "F1 violation in $FILE:"
    echo
    echo "$MATCH"
    echo
    echo "  'from __future__ import annotations' is forbidden project-wide (CLAUDE.md F1)."
    echo "  Reason: Python 3.12 evaluates annotations eagerly. Deferred annotations break runtime"
    echo "  introspection (Pydantic, dataclasses) and produce NameError on TYPE_CHECKING-only refs."
    echo "  Action: remove the line. Use PEP 604 union syntax (X | None) directly."
  } >&2
  exit 2
fi

exit 0
