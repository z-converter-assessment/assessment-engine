#!/usr/bin/env bash
# Edit|Write 직후 PostToolUse 로 실행된다 (등록: .claude/settings.json).
# stdin 은 PostToolUse JSON, 위반 시 exit 2 + stderr 가 Claude 에게 피드백된다.
#
# 오해 없이 기계적으로 판정되는 위반만 막는다. 문맥 판단이 필요한 것(이력 서사·중복·목적 혼선)은
# doc-auditor 에이전트와 /docs skill 이 처리한다.
set -euo pipefail

INPUT=$(cat)
FILE=$(printf '%s' "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")

[ -z "$FILE" ] && exit 0
[ -f "$FILE" ] || exit 0

# `.claude/` 안 파일은 검사 패턴 자체를 카탈로그로 담고 있어 자기 자신이 매칭된다.
case "$FILE" in */.claude/*) exit 0 ;; esac

VIOLATIONS=""
add() { VIOLATIONS+=$'\n'"$1"$'\n'"$2"$'\n'; }

# ─── 전역 포맷 ───
case "$FILE" in
  *.md|*.markdown|*.html|*.htm|*.rst)
    if grep -nE '\*\*[^*]+\*\*' "$FILE" >/dev/null 2>&1; then
      add "[GLOBAL] markdown bold(**...**) 금지 — 시각 노이즈, 신호 가치 0." "$(grep -nE '\*\*[^*]+\*\*' "$FILE" | head -3)"
    fi
    ;;
esac

# 단방향 화살표·em-dash·가운뎃점은 허용이라 목록에서 뺀다.
UNI='[§↔↑↓✓✔✗✘☑⚠❌✅🤖×÷≥≤≠•◦▪▫■□●○◆°]'
if grep -nP "$UNI" "$FILE" >/dev/null 2>&1; then
  add "[GLOBAL] 비키보드 unicode 기호·이모지 금지 — ASCII/한글로 (>=·<=·!= 등)." "$(grep -nP "$UNI" "$FILE" | head -3)"
fi

# ─── .py ───
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

# ─── 라이브 docs ─── (decisions/adr 아카이브·temp 는 패턴에서 자연히 빠진다)
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
