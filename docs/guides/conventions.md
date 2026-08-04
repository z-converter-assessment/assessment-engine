# 본 repo 작업 규약

정책: CLAUDE.md #F1·#F5. 본 문서는 본 repo 코드 작업 시 따라야 할 검증 룰 단일 진실 — 정적 검사와 편집기 설정, 자동화 변환 직후 동적 검증, 서버·CI 강제를 모두 포함.

## 1. 정적 검사 (#F1 부속)

파이썬 검사 도구는 둘이고 설정을 `pyproject.toml` 에 둔다 — 편집기와 CLI 가 같은 파일을 읽어야 편집기에서 통과한 것이 CLI 에서도 통과한다.

| 도구 | 대상 | 설정 | 실행 |
|------|------|------|------|
| ruff | lint (E·F·I·B·UP) | `[tool.ruff]` | `make lint` |
| pyright | 타입 | `[tool.pyright]` | `make typecheck` |

`make typecheck` 는 파이썬 타입과 정적 JS 타입을 함께 본다. 검사 범위는 `[tool.pyright].include` 한 곳이 정하고 명령은 경로 인자를 넘기지 않는다 — 편집기와 CLI 와 워크플로가 같은 범위를 본다.

ruff 는 검증 워크플로(`ci.yml`)가 PR 마다 `ruff check` 를 돌린다 — 포맷은 게이트가 아니다.

pyright 강도는 `strict` 다. 명시 선언은 두 묶음뿐 — 이 저장소가 채택하지 않는 규칙 4개를 `none` 으로, strict 가 끄지만 위반 0 이라 켜 두는 규칙 4개를 `error` 로. 규칙별 채택·거부 사유는 ADR 0062 가 단일 진실이다. 새 규칙을 켤 때도 같은 기준을 쓴다 — 위반을 남긴 채 켜지 않는다.

외부 패키지가 타입을 주지 않는 자리는 억제를 호출부마다 흩지 말고 타입 있는 얇은 래퍼 한 곳에 가둔다 (`tests/approx.py` 가 그 예다). 호출부마다 억제하면 그 줄의 실제 오류까지 함께 묻힌다. pyright 는 이 블록에서 `typeCheckingMode` 를 받지 않으므로 프리셋 이름이 아니라 규칙을 나열한다.

wire·JSONB 원본을 담는 자리는 `assessment_engine.json_types.JsonObject` 를 쓴다. 계약 밖 필드가 도착해도 통과시켜야 하므로 원본은 열린 채로 두고 읽는 쪽이 필요한 축만 좁힌다. JSON 이 아닌 dict(MQ 큐 선언 인자·SQLAlchemy 컬럼-값 맵·in-memory 인덱스)에는 쓰지 않는다 — 이름이 거짓이 된다.

그 원본에서 중첩 배열·객체를 꺼낼 때는 같은 모듈의 `json_list`·`json_obj`·`json_str_list` 를 쓴다. `d.get(key) or []` 로 꺼내면 빈 리터럴이 원소 타입을 잃은 채 결과 타입을 정한다. 세 헬퍼는 형태가 아니면 빈 값으로 읽으므로 호출부 가드가 필요 없다.

dataclass·Pydantic 의 기본값 팩토리는 `field(default_factory=list[X])` 처럼 선언과 같은 제네릭 별칭을 쓴다. 인자 없는 `list`·`dict` 는 원소 타입이 없는 생성자라 선언과 이어지지 않는다 — 제네릭 별칭도 호출 가능해 런타임 동작은 같다.

테스트 픽스처는 dict 로 기본값을 조립해 `**` 로 넘기지 않는다. 그렇게 하면 값 타입이 전 필드의 합집합이 되어 어떤 인자도 맞지 않는다. dataclass 는 `dataclasses.replace(base, **overrides)` 로 base 를 실제 타입으로 한 번 만들고 덮어쓰기만 넘긴다. `T | None` 을 돌려주는 호출은 `assert x is not None` 으로 좁힌 뒤 쓴다 — 그 테스트가 전제하던 것을 명시하는 효과도 있다.

저장소가 공유하는 편집기 설정은 `.vscode/` 두 파일이다. `settings.json` 은 워크스페이스 우선순위로 개인 설정을 덮으므로 팀이 통일해야 할 것만 담는다 (ruff 포맷터·저장 시 포맷·import 정렬·pytest 활성화). `extensions.json` 은 추천일 뿐 강제가 아니며, 이 저장소에 검사 대상이 있는 확장만 올린다.

| Severity | 정석 |
|----------|------|
| Error | 무조건 fix |
| Warning | 원인 분류 후 처리 (아래 우선순위) |
| Info / Hint | 그대로 둠 (시각적 노이즈만 — 코드 더럽히지 않음) |

Warning 처리 우선순위:

1. 타입 어노테이션·변수 추출로 의도 명확화 — type checker가 자연스럽게 narrow 가능하면 그 방향이 가장 정석.
2. 외부 라이브러리 type stub의 false positive → `# type: ignore[specific_code]` (specific code 명시 + 이유 한 줄 주석). 무분별한 generic `# type: ignore` 금지.
3. `cast(T, x)`는 런타임 NO-OP이라 `assert`보다는 안전하지만 narrowing 의도라 stub 한계엔 `# type: ignore`가 더 솔직. cast는 진짜 "타입 변환" 의도일 때만 (예: `Any` → 구체 타입).

## 2. 강제 채널 (#F5 부속)

강제는 서버와 CI 에만 둔다. 로컬 훅은 두지 않는다 — git hook 은 `--no-verify` 로 뚫리고 편집기 훅은 그 도구로 작업할 때만 도므로, 어느 쪽도 우회 가능한 자리다. 같은 검사를 두 곳에서 유지하는 비용도 든다.

| 위반 | 강제 지점 |
|------|----------|
| 보호 브랜치 직접 push·force push·삭제 | GitHub ruleset |
| PR title Conventional Commits | `pr-title-check.yml` |
| lint·테스트·프론트 타입 계약·마이그레이션 drift | `ci.yml`·`alembic-check.yml` (required check 목록은 `docs/guides/ci-setup.md` 3.4) |

강제 채널이 없는 규약은 사람과 리뷰가 지킨다. F7(`print`·`sys.stdout.write`)·C3(`safe_*` 미경유 redis 직접 호출)·글로벌 표기 규칙(markdown bold·비키보드 unicode)이 여기 해당한다 — ruff select 대상이 아니라 CI 도 잡지 못한다. 자동화 변환 직후 자가 검증(#F5)과 develop PR 코드 리뷰가 유일한 그물이다.

설정 카탈로그는 `docs/guides/ci-setup.md`.

## 3. 자동화 변환 검증 (#F5 부속)

자동화 변환(sed · Edit `replace_all` · 디렉토리 mv · Python 일괄 갱신) 직후 메인 세션의 자가 검증 의무. CLAUDE.md #F5 4 항목 단일 진실 위에 변환 유형별 추가 검증.

### 변환 유형별 추가 체크

| 유형 | 추가 검증 |
|------|---------|
| sed / Edit `replace_all` | 들여쓰기 무관 패턴 (`^[[:space:]]*` 사용 여부), 줄 시작·끝 스코프, 문자열 리터럴 안까지 영향 위치 grep |
| 디렉토리 mv | `from X` import (들여쓰기 포함), `import X` (단순), 문자열 형태 모듈 경로 (`"web.main:app"`, target=`"X.Y"` 등), 동적 import (`importlib.import_module`) 모두 grep |
| DTO·모델 타입 변경 | mapper / cache serializer / 템플릿 / inline JS / view_models 체인 — 한 곳 누락 시 cache 역직렬화 또는 attribute access 깨짐 |
| 동시성 코드 (consumer / 핸들러) | placeholder는 `ON CONFLICT DO NOTHING` 의무 (`DO UPDATE`는 진짜 데이터에만). race 시나리오 명시 검증 |
| Frontend JS | 외부 `.js` 파일에서 작업 (inline 신규 금지). 변환 후 `pnpm run typecheck`. 엔드포인트·ViewModel 을 함께 만졌으면 `docs/reference/web/type-contract.md` 의 타입 계약 절차 |

## 4. 누적 사고 패턴 (반면교사)

### 코드 변환

- sed `^from` 패턴이 함수 안 들여쓰기 import 놓침 → `^[[:space:]]*from` 또는 별도 grep 라운드.
- sed가 함수-local 변수(예: `globalRange->capturedRange`)를 함수 외부까지 변환 → awk로 함수 경계 마킹 후 사용 위치 검증.
- 문자열 형태 모듈 경로 (`uvicorn.run("web.main:app")`) 잔존 → import 변환 후 `grep '"[a-z_.]*:'` 별도 라운드.
- placeholder upsert(`ON CONFLICT DO UPDATE`)가 진짜 inventory 덮어쓰는 race → placeholder 전용 메서드는 `ON CONFLICT DO NOTHING` + 충돌 시 다시 find.
- inline JS 변경은 도구 적용 어려움 → 외부 `.js`로 옮긴 후 변경.

### 작업 진행 규약

- 사용자 reject 후 진행 결정 시 같은 silent block 반복 금지 — 진행 단계마다 1줄 알림 + 결과 즉시 보고 + 1분 cap 룰 준수.

누락 시 사용자 회귀 사고 발견의 책임은 검증 누락에 있음. 같은 패턴 재발 시 본 절에 추가하고 CLAUDE.md F5 메인 자가 검증 절차에 누락된 단계 보강.

## 관련 문서

- CLAUDE.md #F1 — 타입 어노테이션 허용·주의 (from __future__·TYPE_CHECKING 허용, Pydantic 필드 타입 런타임 유지)
- CLAUDE.md #F5 — 자동화 변환 책임 분담 (메인 자가 검증·에이전트 채널)
- CLAUDE.md #F7 — 로깅 (`print` 금지의 근거 정책)
- CLAUDE.md #C3 — Redis fail-open `safe_*` helper 의무
- CLAUDE.md #F9 — 변경 영향도 체크리스트 (의미적 단일 진실 보장)
