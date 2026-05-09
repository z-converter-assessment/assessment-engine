#!/bin/bash
# DISABLED — `.claude/settings.json` PostToolUse 배열에서 unregister됨 (현재 미발동).
# 사유: ruff 자동 검증이 PyCharm IDE 경고와 중복 + E501(line-too-long) 같은 사소한 위반이
#       주석/문자열 작업 흐름을 차단하는 역효과. ruff 검증은 IDE 경고 또는 code-reviewer
#       에이전트 호출 시 `.venv/bin/ruff check <files>`로 수동 실행 (CLAUDE.md F9 채널 표 참조).
# 활성화: settings.json hooks.PostToolUse 배열에 다음 항목 추가
#        { "type": "command", "command": ".claude/hooks/ruff-lint.sh", "timeout": 30 }
#
# 원래 동작 ↓
# PostToolUse hook: Edit/Write 직후 .py 파일에 ruff format + check --fix 일괄 적용.
# 1. ruff format <file>          — 포맷터 (whitespace·줄바꿈·따옴표 등)
# 2. ruff check --fix <file>     — 자동수정 가능한 lint 위반(I001 import 정렬·F401 unused 등) 즉시 fix
# 3. 자동수정 후 남은 위반이 있으면 stderr로 출력 + exit 2 → Claude에 system-reminder 피드백
# - 모두 깨끗하면 exit 0 (조용)
# - .py 아니거나 파일 없음 → exit 0

set -euo pipefail

# stdin JSON 파싱: {"tool_name": "Edit", "tool_input": {"file_path": "..."}, ...}
INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")

# 가드
[ -z "$FILE" ] && exit 0
[[ "$FILE" == *.py ]] || exit 0
[ -f "$FILE" ] || exit 0

# ruff 바이너리 결정 (.venv 우선, fallback PATH)
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUFF="$PROJECT_ROOT/.venv/bin/ruff"
if [ ! -x "$RUFF" ]; then
  RUFF=$(command -v ruff || true)
fi
[ -n "$RUFF" ] || exit 0  # ruff 미설치면 silent skip

# 1. format (best-effort, 결과 무시)
"$RUFF" format "$FILE" >/dev/null 2>&1 || true

# 2. check --fix (자동수정 후 남은 위반 점검)
if OUTPUT=$("$RUFF" check --fix "$FILE" 2>&1); then
  exit 0
fi

# 자동수정 후에도 남은 위반 — Claude에 피드백
{
  echo "ruff issues remain in $FILE (after auto-format + auto-fix):"
  echo
  echo "$OUTPUT"
} >&2
exit 2
