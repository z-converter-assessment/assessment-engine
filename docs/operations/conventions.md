# 코딩 규약 보조

정책: CLAUDE.md #F1. 본 문서는 IDE 경고 분류 절차와 hook 강제 채널 카탈로그 단일 진실.

## IDE 경고 대처

| Severity | 정석 |
|----------|------|
| Error | 무조건 fix |
| Warning | 원인 분류 후 처리 (아래 우선순위) |
| Info / Hint | 그대로 둠 (시각적 노이즈만 — 코드 더럽히지 않음) |

Warning 처리 우선순위:

1. 타입 어노테이션·변수 추출로 의도 명확화 — type checker가 자연스럽게 narrow 가능하면 그 방향이 가장 정석.
2. 외부 라이브러리 type stub의 false positive → `# type: ignore[specific_code]` (specific code 명시 + 이유 한 줄 주석). 무분별한 generic `# type: ignore` 금지.
3. `cast(T, x)`는 런타임 NO-OP이라 `assert`보다는 안전하지만 narrowing 의도라 stub 한계엔 `# type: ignore`가 더 솔직. cast는 진짜 "타입 변환" 의도일 때만 (예: `Any` → 구체 타입).

ruff 위반(E501 line-too-long · F841 unused · I001 import 정렬 등)은 hook 자동 차단 채널 없음 — PyCharm IDE 경고 또는 수동 `.venv/bin/ruff check <file>` 실행으로 검증. 위 표의 Warning 우선순위로 처리.

## Hook 강제 채널

`.claude/hooks/` PostToolUse hook이 강제하는 위반(exit 2 → system-reminder 피드백)은 위 Warning 우선순위와 별개의 강제 채널 — 즉시 수정. IDE Info-Hint와 달리 Claude 컨텍스트로 직접 피드백되므로 묻힐 위험 없음.

| 위반 | 적용 범위 | Hook |
|------|----------|------|
| F1 — `from __future__ import annotations` | `.py` | `conventions-check.sh` |
| F7 — `print(` / `sys.stdout.write` | `.py` | `conventions-check.sh` |
| C3 — `safe_*` 미경유 redis 클라이언트 직접 호출 (`redis.set/get/delete/publish/incr/exists/mget/expire/setnx`) | `.py` (`db/redis.py` 본인 제외) | `conventions-check.sh` |
| 글로벌 — markdown asterisk-pair bold (굵게 강조 문법) | 모든 파일 | `conventions-check.sh` |
| 글로벌 — 비키보드 unicode 기호·이모지 (예시: 절기호, 양방향 화살표, 체크/엑스 표식, 부등호 기호, 가운뎃점 글머리표 등) | 모든 파일 | `conventions-check.sh` |

hook 파일 자체(`.claude/hooks/*`)는 패턴 정의를 포함하므로 self-skip — `.claude/hooks/` 경로는 검사 안 함.

## 관련 문서

- CLAUDE.md #F1 — 타입 어노테이션 금지·의무 (단일 진실)
- CLAUDE.md #F5 — 자동화 변환 책임 분담 (hook 채널 분담)
- CLAUDE.md #F7 — 로깅 (`print` 금지의 근거 정책)
- CLAUDE.md #C3 — Redis fail-open `safe_*` helper 의무
