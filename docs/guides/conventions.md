# 코드 규약

본 문서는 이 저장소에서 코드를 쓸 때 따르는 검사·관용구·강제 지점의 단일 진실이다. 정책의 근거는 CLAUDE.md
#F1·#F5 가 갖고, 여기서는 무엇을 어떻게 지키는지를 적는다.

## 1. 검사 도구

도구는 셋이고 설정은 전부 `pyproject.toml` 에 둔다 — 편집기와 CLI 가 같은 파일을 읽어야 편집기에서 통과한 것이
CLI 에서도 통과한다.

| 도구 | 보는 것 | 설정 | 실행 |
|------|--------|------|------|
| `ruff format` | 코드 포맷 | `[tool.ruff.format]` | `make format` (검사는 `make lint`) |
| `ruff check` | lint | `[tool.ruff.lint]` | `make lint` |
| `pyright` | 파이썬 타입 | `[tool.pyright]` | `make typecheck` |

`make typecheck` 는 파이썬 타입과 정적 JS 타입을 함께 본다. 검사 범위는 `[tool.pyright].include` 한 곳이 정하고
명령은 경로 인자를 넘기지 않는다 — 편집기와 CLI 와 워크플로가 같은 범위를 본다.

포맷은 사람이 정하지 않는다. `ruff format` 이 정본이고 검증 워크플로가 `--check` 로 게이트를 건다. markdown 안
python 코드블록도 대상이되 결정 아카이브(`docs/decisions`)와 시점 스냅샷(`docs/learning`)은 당시 그대로 두므로
제외한다.

pyright 강도는 `strict` 이고, 프리셋 위에 얹는 명시 선언은 두 묶음이다. 하나는 채택하지 않는 규칙으로, `_` 접두
심볼을 패키지 안 형제 모듈이 쓰는 관용구를 때리는 것들이다 (`reportPrivateUsage` 가 그 사용을,
`reportUnusedFunction`·`reportUnusedClass` 가 반대편에서 "안 쓰인다"고 잡는다). 다른 하나는 프리셋이 끄지만 위반이
0 이라 켜 두는 규칙으로, 죽은 코드와 낡은 억제 주석을 잡는다. 목록과 사유는 설정 파일이 갖는다.

## 2. 규약과 규칙의 대응

lint 규칙은 취향이 아니라 이 저장소의 명문 규약을 기계가 강제하도록 고른다.

| 규약 | 강제하는 규칙 |
|------|--------------|
| #F1 상위 메서드 재정의 표기 | pyright `reportImplicitOverride` |
| #F2 UTC 저장·표시 경계 변환 | `DTZ` — tzinfo 없는 datetime 생성 차단 |
| #F6 예외 타입 명시·영구 오류 재시도 금지 | `TRY`·`B` |
| #F7 loguru 단일 (print·stdlib logging 금지) | `T20`·`LOG`·`G` + `TID251` banned-api |
| #F8 시크릿 노출 금지 | `S` — 하드코딩 비밀번호·바인드 주소 |
| #F12 docstring PEP 257 | `D` (형식만, 존재 강제는 안 함) |
| 전역 표기 규칙 (키보드 직타 문자만) | `RUF001`~`RUF003` |
| 현대 관용구 | `UP`·`FURB`·`PERF`·`SIM`·`C4`·`PTH` |

제외한 규칙은 사유를 설정 주석에 남긴다. 큰 축은 셋이다 — `D1`(docstring 존재 강제)은 대상 범위를 #F12 가
정하므로 도구로 세지 않고, `PLR09xx`(인자·분기·return 개수)는 SQL 조립부와 매퍼를 나누면 흐름이 흩어지며,
`ERA001`(주석 처리된 코드)은 이 저장소의 구획 주석을 코드로 오인한다.

규칙을 새로 켤 때 기준은 하나다 — 위반을 남긴 채 켜지 않는다. 그 PR 안에서 0 으로 만들거나, 못 만들 이유를 설정
주석에 적고 제외한다.

`except Exception` 을 좁히지 않는 자리는 `BLE001` 을 코드로 억제하고 사유를 그 줄에 남긴다. consumer·worker
루프는 예외가 루프를 죽이지 않는 것이 목적이라 좁히면 종료 동작이 바뀐다.

도구가 못 잡는 규약은 사람과 리뷰가 지킨다. #C3(`safe_*` 미경유 redis 직접 호출)과 markdown 문서의 표기
규칙이 여기 해당한다.

## 3. 코드 관용구

### 인터페이스

계층 사이 인터페이스는 `typing.Protocol` 로 쓴다. 구현은 protocol 을 상속하지 않는다 — 구조적 타이핑이라 모양만
맞으면 되고, 맞는지는 구현을 protocol 타입 자리에 넘기는 composition root 에서 type checker 가 확인한다. 상속을
쓰면 구현이 인터페이스 모듈을 import 해야 해서 의존 방향이 거꾸로 선다.

이름은 역할과 기술을 가른다 — protocol 은 역할 이름(`QueryRepository`), 구현은 기술 접두(`SqlQueryRepository`).
파일도 같다: `query/server.py` 가 protocol, `query/server_sql.py` 가 구현이다.

구현 메서드에는 `@override` 를 달지 않는다. 상속이 없으므로 재정의가 아니다.

### FastAPI 의존성

의존성은 기본 인자가 아니라 `Annotated` 로 선언한다. 기본 인자 자리에 함수를 호출하는 옛 스타일은 시그니처의
"기본값" 자리에 기본값이 아닌 것을 놓아 lint 예외를 요구한다.

주입 대상은 `web/deps.py` 가 `*Dep` 별칭으로 내보내고 라우터는 그 별칭만 받는다 — `service: QueryServiceDep`.
쿼리 파라미터도 같다: `limit: Annotated[int, Query(ge=1, le=100)] = 10` 처럼 제약은 `Annotated` 안에, 기본값은
대입 자리에 둔다. 필수 파라미터는 기본값을 주지 않는다 (`= ...` 를 쓰지 않는다).

### 타입

어노테이션은 3.14 가 지연 평가한다(PEP 649). `from __future__ import annotations` 도 forward-ref 따옴표도 쓰지
않는다.

타입 별칭은 `type X = ...`(PEP 695). 런타임에 별칭 안을 들여다볼 때는 `X.__value__` 를 거친다 — `get_args(X)` 는
빈 튜플을 주므로, 이걸 놓치면 순회가 0회 돌면서 테스트가 조용히 통과한다.

상위 메서드를 덮어쓰는 자리에는 `@override`(PEP 698) 를 단다. 상위 시그니처가 바뀌면 그 자리에서 잡힌다.

런타임에 어노테이션을 읽는 자리는 셋이고, `TC` 규칙이 그 import 를 `TYPE_CHECKING` 으로 옮기면 기동이 깨진다 —
Pydantic 모델 필드, SQLAlchemy `Mapped[...]`, FastAPI endpoint·의존성 callable. 앞의 둘은
`[tool.ruff.lint.flake8-type-checking]` 의 `runtime-evaluated-*` 등록으로, 뒤는 per-file 제외로 처리한다.

### JSON 원본

wire·JSONB 원본을 담는 자리는 `assessment_engine.json_types.JsonObject` 를 쓴다. 계약 밖 필드가 도착해도
통과시켜야 하므로 원본은 열린 채로 두고 읽는 쪽이 필요한 축만 좁힌다. JSON 이 아닌 dict(MQ 큐 선언 인자·SQLAlchemy
컬럼-값 맵·in-memory 인덱스)에는 쓰지 않는다 — 이름이 거짓이 된다.

그 원본에서 중첩 배열·객체를 꺼낼 때는 같은 모듈의 `json_list`·`json_obj`·`json_str_list` 를 쓴다.
`d.get(key) or []` 로 꺼내면 빈 리터럴이 원소 타입을 잃은 채 결과 타입을 정한다.

### 기본값과 픽스처

dataclass·Pydantic 의 기본값 팩토리는 `field(default_factory=list[X])` 처럼 선언과 같은 제네릭 별칭을 쓴다. 인자
없는 `list`·`dict` 는 원소 타입이 없는 생성자라 선언과 이어지지 않는다 — 제네릭 별칭도 호출 가능해 런타임 동작은
같다.

테스트 픽스처는 dict 로 기본값을 조립해 `**` 로 넘기지 않는다. 그렇게 하면 값 타입이 전 필드의 합집합이 되어 어떤
인자도 맞지 않는다. dataclass 는 `dataclasses.replace(base, **overrides)` 로 base 를 실제 타입으로 한 번 만들고
덮어쓰기만 넘긴다. `T | None` 을 돌려주는 호출은 `assert x is not None` 으로 좁힌 뒤 쓴다.

### 억제

억제는 규칙 코드를 명시하고 한 줄 사유를 붙인다 — 파이썬 타입은 `# pyright: ignore[rule]`, lint 는
`# noqa: RULE`. 코드 없는 통짜 억제는 쓰지 않는다.

외부 패키지가 타입을 주지 않는 자리는 억제를 호출부마다 흩지 말고 타입 있는 얇은 래퍼 한 곳에 가둔다
(`tests/approx.py` 가 그 예다). 호출부마다 억제하면 그 줄의 실제 오류까지 함께 묻힌다.

## 4. 편집기와 경고 대처

저장소가 공유하는 편집기 설정은 `.vscode/` 두 파일이다. `settings.json` 은 워크스페이스 우선순위로 개인 설정을
덮으므로 팀이 통일해야 할 것만 담는다 (ruff 포맷터·저장 시 포맷·import 정렬·pytest 활성화). `extensions.json` 은
추천일 뿐 강제가 아니며, 이 저장소에 검사 대상이 있는 확장만 올린다.

| Severity | 처리 |
|----------|------|
| Error | 무조건 수정 |
| Warning | 원인 분류 후 처리 (아래 순서) |
| Info / Hint | 그대로 둔다 |

Warning 은 순서대로 시도한다.

1. 타입 어노테이션·변수 추출로 의도를 명확히 한다. type checker 가 자연스럽게 좁힐 수 있으면 그 방향이 정공이다.
2. 외부 라이브러리 stub 의 false positive 면 규칙 코드를 명시해 억제한다.
3. `cast(T, x)` 는 진짜 타입 변환 의도일 때만 쓴다 (`Any` -> 구체 타입 등). stub 한계를 덮는 용도로는 억제가 더
   솔직하다.

## 5. 강제 채널

강제는 서버와 CI 에만 둔다. 로컬 훅은 두지 않는다 — git hook 은 `--no-verify` 로 뚫리고 편집기 훅은 그 도구로
작업할 때만 돌므로, 어느 쪽도 우회 가능한 자리다. 같은 검사를 두 곳에서 유지하는 비용도 든다.

| 위반 | 강제 지점 |
|------|----------|
| 보호 브랜치 직접 push·force push·삭제 | GitHub ruleset |
| PR title Conventional Commits | `pr-title-check.yml` |
| 코드 포맷 | `ci.yml` 의 `ruff format --check` |
| lint·테스트·프론트 타입 계약·마이그레이션 drift | `ci.yml`·`alembic-check.yml` (required check 목록은 `docs/guides/ci-setup.md` 3.4) |

## 6. 자동화 변환 직후 검증

자동화 변환(sed · Edit `replace_all` · 디렉토리 mv · 일괄 스크립트) 직후에는 CLAUDE.md #F5 의 4 항목을 매번 돌리고,
변환 유형별로 다음을 더 본다.

| 유형 | 추가 검증 |
|------|---------|
| sed / `replace_all` | 들여쓰기 무관 패턴(`^[[:space:]]*`)으로 다시 훑고, 의도한 스코프 밖(함수 외부·문자열 리터럴 안)에 걸린 곳을 grep 한다. 식별자 일부가 다른 식별자에 포함되는 경우(`_x` -> `x` 가 `test_x` 를 건드리는 등)를 특히 본다 |
| 디렉토리·파일 mv | `from X`·`import X` 뿐 아니라 문자열 형태 모듈 경로(`"web.main:app"`)와 동적 import 를 함께 grep 한다 |
| DTO·모델 타입 변경 | mapper · cache serializer · 템플릿 · JS · ViewModel 체인을 한 번에 맞춘다. 한 곳이 빠지면 캐시 역직렬화나 속성 접근에서 깨진다 |
| 동시성 코드 | placeholder upsert 는 `ON CONFLICT DO NOTHING` 이어야 한다 (`DO UPDATE` 는 진짜 데이터에만). 충돌 시 다시 조회하는 경로가 있는지 본다 |
| Frontend JS | 외부 `.js` 에서 작업하고(인라인 신규 금지) `pnpm run typecheck` 를 돌린다. 엔드포인트·ViewModel 을 함께 만졌으면 `docs/reference/web/type-contract.md` 의 타입 계약 절차를 따른다 |

## 관련 문서

- CLAUDE.md #F1 — 타입 어노테이션 규약
- CLAUDE.md #F5 — 자동화 변환 책임 분담
- CLAUDE.md #F7 — 로깅 정책
- CLAUDE.md #C3 — Redis fail-open `safe_*` helper 의무
- CLAUDE.md #F9 — 변경 영향도 체크리스트
