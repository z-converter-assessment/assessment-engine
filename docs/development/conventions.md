# 본 repo 작업 규약

정책: CLAUDE.md #F1·#F5. 본 문서는 본 repo 코드 작업 시 따라야 할 검증 룰 단일 진실 — 정적 차단(IDE·hook)과 동적 검증(자동화 변환 직후) 모두 포함.

## 1. IDE 경고 대처 (#F1 부속)

| Severity | 정석 |
|----------|------|
| Error | 무조건 fix |
| Warning | 원인 분류 후 처리 (아래 우선순위) |
| Info / Hint | 그대로 둠 (시각적 노이즈만 — 코드 더럽히지 않음) |

Warning 처리 우선순위:

1. 타입 어노테이션·변수 추출로 의도 명확화 — type checker가 자연스럽게 narrow 가능하면 그 방향이 가장 정석.
2. 외부 라이브러리 type stub의 false positive → `# type: ignore[specific_code]` (specific code 명시 + 이유 한 줄 주석). 무분별한 generic `# type: ignore` 금지.
3. `cast(T, x)`는 런타임 NO-OP이라 `assert`보다는 안전하지만 narrowing 의도라 stub 한계엔 `# type: ignore`가 더 솔직. cast는 진짜 "타입 변환" 의도일 때만 (예: `Any` → 구체 타입).

ruff 위반(E501 line-too-long · F841 unused · I001 import 정렬 등)은 hook 자동 차단 채널 없음 — PyCharm IDE 경고 또는 수동 `uv run ruff check <file>` 실행으로 검증. CI(`.github/workflows/ci.yml`)가 PR마다 전체 ruff check 자동 — 본 단계가 최종 안전망. 위 Warning 우선순위로 처리.

## 2. Hook 강제 채널 (#F1 부속)

두 종류의 hook이 강제한다 — Claude Code PostToolUse hook(편집 시점)과 git hook(commit/push 시점). 둘 다 skill(opt-in 가이드)과 별개의 게이트 — 누가 어떤 경로로 작업하든 적용.

### Claude Code hook (`.claude/hooks/`, 편집 시점)

PostToolUse hook이 강제하는 위반(exit 2 → system-reminder 피드백)은 위 Warning 우선순위와 별개의 강제 채널 — 즉시 수정. IDE Info-Hint와 달리 Claude 컨텍스트로 직접 피드백되므로 묻힐 위험 없음.

| 위반 | 적용 범위 | Hook |
|------|----------|------|
| F1 — `from __future__ import annotations` | `.py` | `conventions-check.sh` |
| F7 — `print(` / `sys.stdout.write` | `.py` | `conventions-check.sh` |
| C3 — `safe_*` 미경유 redis 클라이언트 직접 호출 (`redis.set/get/delete/publish/incr/exists/mget/expire/setnx`) | `.py` (`cache/redis.py` 본인 제외) | `conventions-check.sh` |
| 글로벌 — markdown asterisk-pair bold (굵게 강조 문법) | 모든 파일 | `conventions-check.sh` |
| 글로벌 — 비키보드 unicode 기호·이모지 (예시: 절기호, 양방향 화살표, 체크/엑스 표식, 부등호 기호, 가운뎃점 글머리표 등) | 모든 파일 | `conventions-check.sh` |

hook 파일 자체(`.claude/hooks/*`)는 패턴 정의를 포함하므로 self-skip — `.claude/hooks/` 경로는 검사 안 함.

### git hook (`.githooks/`, commit/push 시점)

`core.hooksPath = .githooks` (dev/local-ci.sh가 idempotent하게 자동 설정 — clone별 로컬 설정).

| 위반 | 시점 | Hook |
|------|------|------|
| 커밋 메시지 type prefix 컨벤션 위반 | commit-msg | `commit-msg` |
| AI 메타데이터 (Co-Authored-By: Claude / Generated with Claude Code) | commit-msg | `commit-msg` |
| 이모지·장식 기호 | commit-msg | `commit-msg` |
| main/master 직접 push | pre-push | `pre-push` |

불가피 시 `--no-verify` 우회 가능하나 권장 안 함 — CI(`pr-title-check.yml`)가 PR title을 별도 강제.

## 3. 자동화 변환 검증 (#F5 부속)

자동화 변환(sed · Edit `replace_all` · 디렉토리 mv · Python 일괄 갱신) 직후 메인 세션의 자가 검증 의무. CLAUDE.md #F5 4 항목 단일 진실 위에 변환 유형별 추가 검증.

### 변환 유형별 추가 체크

| 유형 | 추가 검증 |
|------|---------|
| sed / Edit `replace_all` | 들여쓰기 무관 패턴 (`^[[:space:]]*` 사용 여부), 줄 시작·끝 스코프, 문자열 리터럴 안까지 영향 위치 grep |
| 디렉토리 mv | `from X` import (들여쓰기 포함), `import X` (단순), 문자열 형태 모듈 경로 (`"web.main:app"`, target=`"X.Y"` 등), 동적 import (`importlib.import_module`) 모두 grep |
| DTO·모델 타입 변경 | mapper / cache serializer / 템플릿 / inline JS / view_models 체인 — 한 곳 누락 시 cache 역직렬화 또는 attribute access 깨짐 |
| 동시성 코드 (consumer / 핸들러) | placeholder는 `ON CONFLICT DO NOTHING` 의무 (`DO UPDATE`는 진짜 데이터에만). race 시나리오 명시 검증 |
| Frontend JS | 외부 `.js` 파일에서 작업 (inline 신규 금지). 변환 후 `node --check` + 사용자 IDE에서 경고 0건 |

## 4. 누적 사고 패턴 (반면교사)

### 코드 변환

- sed `^from` 패턴이 함수 안 들여쓰기 import 놓침 → `^[[:space:]]*from` 또는 별도 grep 라운드.
- sed가 함수-local 변수(예: `globalRange->capturedRange`)를 함수 외부까지 변환 → awk로 함수 경계 마킹 후 사용 위치 검증.
- 문자열 형태 모듈 경로 (`uvicorn.run("web.main:app")`) 잔존 → import 변환 후 `grep '"[a-z_.]*:'` 별도 라운드.
- placeholder upsert(`ON CONFLICT DO UPDATE`)가 진짜 inventory 덮어쓰는 race → placeholder 전용 메서드는 `ON CONFLICT DO NOTHING` + 충돌 시 다시 find.
- inline JS 변경은 도구 적용 어려움 → 외부 `.js`로 옮긴 후 변경.

### dev VM provisioning

dev VM(libvirt) 프로비저닝 측 교훈 (자동화 변환·post-provision 검증):

- distro별 패키지명·init 규약 다름 — `redis-server`(apt) vs `redis`(dnf), `postgresql`(apt 자동 init) vs `postgresql-server`(RPM family 수동 `postgresql-setup --initdb`). dispatch case 추가 시 apt·dnf family 모두 검증 의무.
- post-provision 은 멱등 의무 — 바이너리·env·unit 변경 없으면 restart 건너뜀 (`agent_started_at` 갱신 → attention.agent_unstable false positive 회피).
- 합성 부하·시연 트리거(swap·restart-demo)는 dev-up.sh post-provision 함수로 흡수 — VM 정의(distro/service/mode)는 dispatch 함수가 단일 진실.

### 단일 진실 sync

- `dev-down.sh`가 VM 목록을 `source dev-up.sh`로 `VMS` 단일 진실 공유 (hardcoded 시 sync 깨짐 → BASH_SOURCE source guard로 main 자동 실행 안 함).
- 사용자 reject 후 진행 결정 시 같은 silent block 반복 금지 — 진행 단계마다 1줄 알림 + 결과 즉시 보고 + 1분 cap 룰 준수.

누락 시 사용자 회귀 사고 발견의 책임은 검증 누락에 있음. 같은 패턴 재발 시 본 절에 추가하고 CLAUDE.md F5 메인 자가 검증 절차에 누락된 단계 보강.

## 관련 문서

- CLAUDE.md #F1 — 타입 어노테이션 금지·의무 (단일 진실)
- CLAUDE.md #F5 — 자동화 변환 책임 분담 (Hook·메인·에이전트 채널)
- CLAUDE.md #F7 — 로깅 (`print` 금지의 근거 정책)
- CLAUDE.md #C3 — Redis fail-open `safe_*` helper 의무
- CLAUDE.md #F9 — 변경 영향도 체크리스트 (의미적 단일 진실 보장)
- `.claude/hooks/conventions-check.sh` — Hook 강제 위반 패턴 카탈로그
