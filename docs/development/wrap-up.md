# wrap-up — 기능 개발 마무리 표준 워크플로

> 본 문서는 기능 개발(feature branch) 동작 완성 후 commit·PR 전까지 거치는 5단계 마무리의 단일 진실. `.claude/skills/wrap-up/SKILL.md` 은 본 명세를 실행하는 skill (orchestrator) — 절차만 가지고, 체크리스트는 본 문서 인용.
>
> 본 워크플로의 두 가지 최상위 원칙:
> - 코드: 정석에 따른 코드 퀄리티 — canonical pattern / framework idiom / declarative 모델 우선, ad-hoc hack 0. 본 repo CLAUDE.md · `docs/*` 에 명문화된 원칙(F1~F11 · #B · #C · #E1 P1~P4 · ADR 결정·금지) 위반 0건이 정석 코드 퀄리티의 전제 조건 — 외부 일반 베스트 프랙티스보다 본 repo 명문 규약이 항상 우선.
> - 문서: 정합성 · 중복 없는 간결한 엄밀함 — 단일 진실 / "왜"만 적기 / 코드 보면 알 수 있는 사실 적지 않기 / 모호 표현 0.
>
> 두 원칙은 충돌 시 코드 퀄리티 우선. 코드가 정석 + 명문 원칙을 따르는 상태일 때만 문서 정합성이 의미가 있다.

## 0. 적용 시점 · 범위

진입 조건:
- 기능이 동작 완성 — `/run` 또는 `/verify` 또는 사용자 수동 검증 통과.
- feature branch 위 (`main`·`master` 직접 X). 미커밋 변경만 또는 clean 상태.
- commit·PR 전. commit·PR 자체는 본 워크플로 종료 후 사용자 명시 요청 시 `/commit`·`/pr-create` 별도 발동.

범위 밖:
- 기능 동작 개발 자체 (본 워크플로는 개발 후 청소).
- commit 메시지 작성·PR 본문 작성·push.
- 동작 검증 (별도 `/run`·`/verify`).

---

## 1. 진행 정책

자율 진행. 결정 필요 사항만 사용자 컨펌, 단계 경계는 알림·보고만 (다음 Stage 즉시 진입).

결정 필요 사항 = 다음 셋 중 하나에 해당:
- 스코프 확장 — 본 워크플로 범위 밖 변경 발견 시 (예: 기존 영구 문서의 위반 발견 — 본 feature 변경이 아닌 곳).
- 알려진 갭 — 사용자가 의도적으로 미루고 PR 본문 명시할지 판정 필요한 항목.
- Stage 5 큐 — 명세 자체 갱신·새 ADR·CLAUDE.md "본 절 결정" 추가 등 의식적 결정.

위 셋 외의 발견은 자율 처리 (정석 정정이 명백한 경우 즉시 적용 + 변경 카탈로그에 기록).

각 단계는 다음 5요소를 갖는다:
- 입력 — 어떤 git diff·파일·매핑을 받는가
- 목적 — 본 단계가 보장하는 invariant
- 체크리스트 — 검사 가능한 항목 (정합·중복·간결·엄밀·정석·원칙 카테고리 분류)
- 산출물 — 변경되는 파일 카탈로그
- 통과 기준 — 다음 단계로 넘어가기 위한 OK 조건

루프 처리:
- Stage 2~5 어디서 코드/문서 변경이 발생하면 영향 받는 이전 Stage 재실행 의무.
  - Stage 2 코드 수정 → Stage 1 (문서 정합) 재검
  - Stage 3 테스트 작성 중 코드 부족 발견 → Stage 2 재실행
  - Stage 4·5 문서 수정 중 코드 모순 발견 → Stage 1 재실행
- 최대 3 cycle. 4 cycle 진입 시 사용자에게 정지·재설계 제안 의무 (메모리 `feedback_one_minute_timeout.md` 정합 — stuck 즉시 보고).

진행 신호:
- 매 Stage 진입 전 1줄 알림 (`Stage N — <목적> (도구: X)`).
- 매 Stage 종료 후 (변경 파일 카탈로그 / 자율 처리 항목 / 결정 필요 사항 / 다음 Stage 큐) 보고 + 다음 Stage 즉시 진입.
- 사용자가 명시적으로 정지 또는 추가 컨펌 요청 시에만 대기.
- 1분 진행 신호 무 시 abort + 진단 (메모리 `feedback_one_minute_timeout.md`).

Self-audit 메타 인용 제외:
- 본 명세(wrap-up.md)는 금지 토큰·모호 표현·temp 인용 카탈로그를 정의하기 위해 해당 토큰을 본문에 인용함 (예: `TODO` · `향후` · `docs/temp/`).
- Stage 1 [1.3] · [1.11] · [1.14] · [1.16] 의 검사 명령은 본 명세 자체와 CLAUDE.md 전문(前文) 단일 진실 정의를 제외 — `rg ... --glob '!docs/development/wrap-up.md' --glob '!.claude/CLAUDE.md'` 또는 출력 후 메타 인용 1차 필터링.
- CLAUDE.md 본문 결정 항목은 검사 대상 유지 — 단일 진실 정의(전문)만 제외.

---

## 2. Stage 1 — 문서 정합성 정리

입력:
- `git diff <base>...HEAD` + 미커밋 변경 (`<base>` 는 PR base branch — 보통 `main`).
- 변경 파일 → 영구 문서 매핑: `.claude/skills/docs-sync/SKILL.md` 매핑 표 단일 진실 (본 명세 중복 X).

목적:
- 본 feature 가 만진 코드 영역의 영구 문서(`docs/architecture/`·`docs/operations/`·`docs/products/`·`docs/tradeoffs.md`)가 현행 코드를 정확히 반영.
- CLAUDE.md·ADR 후보는 본 단계에서 변경하지 않고 Stage 5 큐로만 적재 (영구 결정은 의식적 단계에서 처리).

체크리스트:

정합성 (5):
- [1.1] 변경된 코드 경로별 매핑 영구 문서를 모두 Read. (a) 문서가 인용한 함수·클래스·필드·env 키·routing key·상수명이 코드에 실제 존재. (b) 시그니처·타입·기본값이 문서와 일치. (c) 한 문서 안에서 동일 symbol 의 명명·표기 일관.
- [1.2] CLAUDE.md "본 절 결정" 항목 중 본 feature 가 무효화·변경·확장한 결정이 있는가. 있으면 Stage 5 큐에 (절번호 · 변경 유형 · 근거) 적재.
- [1.3] 영구 문서 안 인용 경로(`src/X/Y.py` · `docs/Z.md` · `tests/W.py`)가 실제 파일 시스템에 존재. 검사: `rg -oN '\b(src/|docs/|tests/|migrations/)[A-Za-z0-9_./-]+' docs/` 추출 후 `test -e` 일괄 확인.
- [1.4] ADR(`docs/adr/`)이 본 feature 로 Superseded·Withdrawn 돼야 하는가. 새 ADR 후보가 있는가. 둘 다 Stage 5 큐로 (ADR 번호 · 신규/Superseded 분류).
- [1.5] `docs/tradeoffs.md` T 항목 중 본 feature 가 해결·악화시킨 트레이드오프가 있는가. 새 트레이드오프가 생겼는가. 큐 적재.

중복 없음 (4):
- [1.6] 같은 사실(임계 상수·UNIQUE 키 카탈로그·routing key·환경변수 default·트랜잭션 경계 등)이 영구 문서 두 곳 이상에서 정의 0건. 단일 진실 결정 후 나머지는 포인터(`상세는 X 절`)로 변환.
- [1.7] CLAUDE.md 본문과 `docs/architecture/*` deep dive 사이 책임 혼선 0건. CLAUDE.md = 결정·원칙·금지. deep dive = 동작·흐름·매트릭스·카탈로그.
- [1.8] 본 명세(wrap-up.md)와 다른 영구 문서(`testing.md` · `conventions.md` · `docs-sync/SKILL.md` 등) 사이 중복 0건. 본 명세 = 워크플로 절차. 다른 문서 = 정책·매핑 자체.
- [1.9] 같은 사실이 카테고리 두 곳(`architecture/` vs `operations/` 등)에 분산 0건. CLAUDE.md 전문(前文) 카테고리 표 정합 (`architecture` = 모듈 deep dive / `operations` = 외부 contract / `development` = repo 안 작업 규약 / `products` = 산출물 의의).

간결 (3):
- [1.10] 영구 문서에 코드만 봐도 알 수 있는 사실(파일 디렉토리 트리·함수 시그니처 본문·import graph·라인 수 카운트)이 적혀있는가. 있으면 삭제 후 코드 경로 포인터로.
- [1.11] 영구 문서에 임시 상태(`TODO`·`FIXME`·`XXX`·"작업 중"·"향후"·"차후") 0건. 검사: `rg -i '\b(TODO|FIXME|XXX|작업중|향후|차후)\b' docs/architecture/ docs/operations/ docs/products/ docs/development/`. 임시는 `docs/temp/` 또는 PR 본문으로.
- [1.12] 동일 결정·금지가 한 문서 안에서 반복 0건. 한 문장 = 한 결정.

엄밀 (3):
- [1.13] "왜"가 적혀있는가, "무엇"만 적혀있는가. 결정·금지·트레이드오프는 반드시 이유(과거 incident · 외부 표준 · 트레이드오프 본질) 동반. 이유 없는 금지 0건.
- [1.14] 모호 표현(`가급적`·`되도록`·`적당히`·`필요시`·`등등`·`일부`·`대부분`·`향후`·`차후`) 0건. 결정은 binary, 임계는 숫자 + 단위, 예외는 명시 enum.
- [1.15] 영구 문서 안 임계·시간·크기는 명명 상수 또는 단위 동반 숫자(`14 일` · `90 %` · `300 ms`). 단위 없는 raw 숫자 0건.

원칙 (2):
- [1.16] `docs/temp/` 인용 0건 (영구 문서·코드 양방향). 검사: `rg 'docs/temp' src/ docs/architecture/ docs/operations/ docs/products/ docs/development/ docs/adr/ CLAUDE.md`.
- [1.17] 영구 문서 간 양방향 의존(A→B 이면서 B→A) 0건. 단방향 인용만 — 상위 (CLAUDE.md) → 하위 (`docs/architecture/*`) 또는 동등 계층 한쪽만.

도구:
- `/docs-sync` skill (변경 파일 → 영구 문서 매핑 + 제안 diff). 매핑 표는 `.claude/skills/docs-sync/SKILL.md` 단일 진실.

산출물:
- `docs/architecture/*` · `docs/operations/*` · `docs/products/*` · `docs/tradeoffs.md` 변경.
- CLAUDE.md · `docs/adr/*` 후보는 큐만 적재 (실제 변경은 Stage 5).

통과 기준:
- 17 항목 모두 OK 또는 사용자가 의도적으로 미루기로 한 항목은 PR 본문 알려진 갭으로 명시.

---

## 3. Stage 2 — 코드 리뷰 (정석 코드 퀄리티)

입력:
- Stage 1 통과 후 코드 + Stage 1 에서 변경된 문서 diff.

목적:
- 본 feature 코드가 정석(canonical)에 따라 짜여 있고, 본 repo 의 명문 규약(CLAUDE.md F1~F11 · #B · #C5 · #E1 P1~P4 · 관련 ADR 결정·금지)을 위반하지 않음을 보장.
- 정석 idiom 과 명문 규약은 양자 모두 충족 의무 — 정석 패턴이라도 본 repo 명문 규약 위반이면 위반으로 판정 (예: `from __future__ import annotations` 은 일반 python 베스트 프랙티스지만 본 repo F1 위반).

체크리스트:

정석 (6):
- [2.1] framework idiom 정공 사용. ad-hoc 우회 0건.
  - Pydantic: `Field(...)` · `model_validator` · `Annotated[..., Field(...)]` · `SecretStr` 정석. `__init_subclass__` · 런타임 dict 수정 등 우회 X.
  - SQLAlchemy: declarative ORM + `pg_insert(...).on_conflict_do_*` (CLAUDE.md #C2). raw SQL 은 `text()` + bound param (#C5).
  - asyncpg: 1 transaction = 1 connection. 트랜잭션 안 nested 세션 X. session 공유 X.
  - FastAPI: `Depends(...)` · `APIRouter` · 라우터 분리. 글로벌 state X.
  - Jinja2: 필터 · `{% if %}` · `{% for %}` 만 (#E1 P3). 계산 X.
  - aio-pika: `async with message.process(requeue=False)` 컨텍스트 안에서 모든 await 완료 (#F11).
- [2.2] 추상화는 정석 위치에만. `BaseSettings`(환경변수) · `Base*Repository`(repo 인터페이스) · `*Service`(orchestration) · `Base*Client`(외부 의존 추상 — `BaseLlmClient` · `BaseEmbeddingClient` · `BaseRetriever`) 외 ad-hoc abstract 0건.
- [2.3] composition root 외 위치에서 `Settings()` 인스턴스 생성 0건 (#F4 6 위치만 허용). 검사: `rg 'Settings\(\)|WebSettings\(\)|ConsumerSettings\(\)|DiagnosticSettings\(\)' src/`.
- [2.4] 중복 함수·중복 상수 0건. 같은 의미는 단일 모듈 (예: UI badge 임계 = `mappers/shared.py` / USE Method 임계 = `recommendation.py` — 의미 도메인별 단일 위치). 두 도메인 혼용 0건 (#E3).
- [2.5] 죽은 코드 0건 — unused import · unreachable branch · deprecated 미정리 · 호출처 없는 public 함수. `ruff` · IDE inspection 통과.
- [2.6] 명명 · 매직 넘버.
  - 함수·변수·클래스명이 의미를 정확히 표현. 약식 접미사(`_data` · `_temp` · `_v2` · `_new` · `_old` · `_2` · `_fix`) 0건.
  - 매직 넘버 0건 — 임계·시간·크기·정규식·HTTP status 모두 명명 상수 또는 enum (`Literal` · `IntEnum`). 단, 0/1/-1 같은 trivial 상수와 SQL `LIMIT 1` 같은 의미 자명한 경우 예외.

명문 규약 매핑 (F-policy + 계층 원칙, 9):
- [2.7] F1 — `from __future__ import annotations` · `TYPE_CHECKING` · type checker 만족용 런타임 `assert x is not None` 0건. hook 위반 시 hook 메시지 그대로 수정 (우회 X).
- [2.8] F2 — KST 변환이 표시 경계 4 함수(SSR `kst` · client `fmtLabel` · `fmtKst` · `initAnchor`) 외 위치 0건. naive datetime 0건. 인라인 KST offset 더하기(`+ 9*60*60*1000` 등) 0건.
- [2.9] F3 — 검증이 진입점(라우터 Pydantic · Consumer `model_validate_json` · `BaseSettings`) 외 위치(Service · Mapper · Repository · 사용처)에서 재실행 0건. `_VALID_*` frozenset 비교·런타임 enum 멤버십 체크 0건.
- [2.10] F4 — Service/Handler 안 구체 구현체 import 0건. `config.py` module-level instance 0건. `assessment_engine.config` 에서 `web_settings` 등 직접 import 0건 (class 만 export).
- [2.11] F6 — `except Exception` 광범위 catch 0건 (예외 타입 명시). timeout 없는 외부 호출 0건 — `asyncio.wait_for` 또는 클라이언트 timeout 옵션(`aiohttp.ClientTimeout` · asyncpg `command_timeout` · redis `socket_timeout`) 의무. 영구 오류(`IntegrityError` · 4xx) 재시도 0건.
- [2.12] F7 — `print` · stdlib `logging` · `sys.stdout.write` 혼용 0건. `logger.exception()` 은 except 블록 안에만. raw payload 로깅 0건 (식별자 + 카운트만). 신규 시그널 로그는 (레벨 · 빈도 제어 · 운영자 행동) 셋 다 명시.
- [2.13] F8 — secret 필드 `SecretStr` 누락 0건. PII (host_id · public_id · server_id 외 식별자 · 전체 payload · 접속 문자열) 응답·캐시·로그·예외 메시지 0건.
- [2.14] F10 · F11 — 평가 윈도우는 `recommendation.WINDOW_DAYS` 단일 참조 (일수 하드코딩 0건). 새 TimeRange/BucketSize 도입 시 backend Literal · SQL dispatch · `chart-utils.js` · UI 토글 4곳 동시 갱신. `signal.signal(SIGTERM, ...)` 직접 핸들러 · `os._exit()` · `message.process()` 컨텍스트 밖 await 0건.
- [2.15] #B · #C5 · #E1 — Pydantic Input `extra=ignore` 유지. hypertable 조회 `WHERE collected_at >= ?` 술어 누락 0건. f-string SQL 사용자 입력 삽입 0건. Repository 가 raw 단위 외 변환 0건 (P1). Service mapper → ViewModel 외 위치에서 percent·delta·임계 분류 0건 (P2). 템플릿 안 계산(`+`·`*`·`length`·`sort`·`selectattr`) 0건 (P3). 차트 JS 5 의무 규약(sequence counter · capture-before-await · `Array.isArray` · 404 분기 · suggestedMax 상수) 위반 0건 (P4).

중복 — 데이터 흐름 (2):
- [2.16] 같은 데이터 흐름이 두 경로로 흐르는가 (예: ViewModel 파생 필드가 mapper 와 template 둘 다에서 계산 / 같은 집계가 service A·B 에서 각자 계산). #E1 P1~P4 정공 위치 단일.
- [2.17] 같은 외부 호출이 두 위치에서 (예: 같은 LLM endpoint 를 service A 와 B 가 각자 호출 / 같은 Redis 키를 두 곳에서 set). 단일 client 또는 단일 helper 의무.

도구:
- code-reviewer 에이전트 1회 발동 (CLAUDE.md F-policy 매핑 자동).
- `/simplify` skill (중복·죽은 코드 검출 후 정리).

산출물:
- `src/` 코드 변경.
- 코드 수정 발생 시 Stage 1 재실행 트리거 큐 적재.

통과 기준:
- 에이전트 Error 0건. Warning 은 사용자 결정 위임. Info 는 보고만.
- 위 17 항목 self-grep 0건.

---

## 4. Stage 3 — 단위·모듈 테스트 수정

입력:
- Stage 2 통과 후 코드.
- 기존 `tests/unit/` · `tests/integration/` + 본 feature 가 만진 함수·핸들러·라우터.

목적:
- 본 feature 가 추가·변경한 모든 public 함수·핸들러·라우터에 대해 의미 있는 단위/모듈 테스트가 존재하고, 본 repo 테스트 패턴(`docs/development/testing.md`)을 따른다.

체크리스트:

정합 (5):
- [3.1] 본 feature 가 추가한 모든 public 함수·메서드에 단위 또는 통합 테스트 존재. private(`_prefix`)은 public 경유로 간접 검증.
- [3.2] 본 feature 가 추가·변경한 모든 라우터(`@router.get/post/...`)에 통합 테스트 — happy path + 핵심 분기(422 형식 오류 · 404 미존재 · 권한·trigger 별 분기) 명시.
- [3.3] 본 feature 가 변경한 기존 함수·핸들러의 기존 테스트가 여전히 의미를 갖는가 — signature 변경·동작 변경 반영. 단순 통과만 시키기 위한 mock 보강 0건 (의도된 동작 검증으로 재작성).
- [3.4] 본 feature 가 변경·추가한 임계 상수(`mappers/shared.py` · `recommendation.py` · `_USAGE_DANGER_PCT` 등)가 테스트에 하드코딩 0건 — 모두 import. 매직 넘버 동기화 깨짐 방지.
- [3.5] 본 feature 가 삭제한 public 함수·라우터의 deprecated 테스트 0건 (테스트도 동시 삭제). Alembic revision 추가 시 round-trip 테스트(downgrade → upgrade 무손실) 존재.

정석 (5):
- [3.6] pytest-asyncio `loop_scope=session` 적용 (`pyproject.toml` 설정). `@pytest.mark.asyncio` 명시 0건 (`asyncio_mode=auto`).
- [3.7] `tests/factories.py` `make_inventory()` · `make_metrics()` 활용. raw dict 직접 생성 0건. 신규 도메인은 factory 추가 후 활용.
- [3.8] DB 의존 테스트는 `tests/integration/conftest.py` 의 testcontainers + alembic round-trip fixture 사용. 함수 안 fixture 정의 0건 — 신규 fixture 는 `conftest.py` 에만.
- [3.9] mock 범위: 외부 의존(HTTP · LLM · 외부 큐 · ollama · embedding endpoint)에만. 본 repo 내부 모듈 mock 0건 — 실제 호출. AsyncMock(Redis)는 unit `safe_*` helper 검증 시에만, integration 은 실제 컨테이너.
- [3.10] 동일 시나리오 분기(같은 setup + 다른 입력·기대값)는 `@pytest.mark.parametrize` 활용. 중복 함수(같은 본문 + 상수만 다름) 0건.

원칙 (3):
- [3.11] 테스트 한 개 = 한 분기. assert 누락 0건. 동작 검증 없이 단순 호출만 하는 smoke-only 테스트 0건. 한 함수에 무관한 assert 다발 0건.
- [3.12] 동일 픽스처·동일 테스트 데이터 중복 0건 — fixture / factory / parametrize 활용.
- [3.13] 사용자 명시 요청 없이 pytest 자동 실행 0건 (메모리 `feedback_no_test_runs.md`). 단계 종료 시 사용자에게 "테스트 실행하시겠습니까?" 1회 옵션 제시.

도구:
- `/test-write` skill (변경 파일 → 테스트 위치 결정 + 패턴 작성).

산출물:
- `tests/unit/` · `tests/integration/` 변경.
- 테스트 작성 중 코드 부족 발견 시 Stage 2 재실행 트리거 큐 적재.

통과 기준:
- 위 13 항목 모두 OK.
- 사용자가 "테스트 실행" 명시 시 pytest 100% pass.

---

## 5. Stage 4 — README.md 갱신 (entry-first 정합)

입력:
- 본 feature 가 영향 준 사용자 가시 변경 — CLI 명령 · web URL · 환경변수 · 기능 toggle · 기동 절차 · 아키텍처 그림.

목적:
- 본 repo 처음 진입자가 README 만으로 시스템 소개 · 아키텍처 · 기동 방법을 정확히 얻는다.

체크리스트:

정합 (5):
- [4.1] 시스템 소개·아키텍처 그림에 본 feature 의 컴포넌트(새 워커·새 외부 의존·새 산출물·새 큐·새 DB 테이블) 반영. 그림 안 박스·화살표 카탈로그와 실제 코드 컴포넌트 1:1 일치.
- [4.2] CLI entry point(`pyproject.toml` `[project.scripts]` · `zce` · `assessment-engine` 등) 변경·추가 반영. 사용자가 첫 호출 시 입력할 명령 그대로 명시.
- [4.3] 사용자 가시 web URL 변경·추가 (predicate 라우팅 변경·신규 페이지·삭제된 endpoint) 반영. 라우터 path prefix 변경 반영(예: ADR 0021 — `/api/v1` → `/api`).
- [4.4] 기동 명령(`scripts/dev-up.sh` · docker-compose 인보케이션 · `uv run`) 변경 반영. 진입 절차 copy-paste 가능한 형태로 명시.
- [4.5] README 가 가리키는 외부 산출물 위치(`docs/products/*` · `docs/operations/*` · `docs/development/*`) 정확. README → docs 단방향만 — `docs/*` 가 README 본문 디테일 인용 0건 (역참조 0).

간결 (3):
- [4.6] README 가 환경변수 키 본문 dump 0건 — `docs/operations/env.md` 인용만.
- [4.7] README 가 코드 디렉토리 트리 본문 dump 0건 — `docs/README.md` 인용만.
- [4.8] README 가 deep dive(`docs/architecture/*`)에 있는 내부 동작·매트릭스·카탈로그를 본문에 dump 0건 — 포인터만.

원칙 (3):
- [4.9] 한글 본문. 다이어그램 안 텍스트는 영어 (한글 monospace 정렬·박스 모서리 깨짐 — 글로벌 CLAUDE.md).
- [4.10] markdown bold (별표 두 개 쌍 강조) · 이모지 · 비키보드 unicode 기호(절 기호 · 양방향 화살표 · 체크 표식 · 부등호 기호 등) 0건. ASCII 인쇄 가능 + 한글만.
- [4.11] AI 메타데이터(`Co-Authored-By: Claude` · `Generated with Claude Code` · "AI generated" 주석) 0건 (글로벌 CLAUDE.md).

도구:
- 수동 Read + Edit. 자동화 skill 없음 (README 는 entry voice — 사용자 결정).

산출물:
- 루트 `README.md` 변경.

통과 기준:
- 위 11 항목 모두 OK.

---

## 6. Stage 5 — CLAUDE.md · 관련 영구 문서 최종 수정

입력:
- Stage 1~4 누적 큐:
  - Stage 1 [1.2] · [1.4] · [1.5]: CLAUDE.md "본 절 결정" 변경 · ADR 신규/Superseded · tradeoffs T 신규 후보.
  - Stage 2 · 3: 본 feature 가 확립한 새 규약·금지·정석 패턴 후보.

목적:
- 영구 단일 진실(CLAUDE.md + `docs/adr/` + `docs/architecture/*` + `docs/tradeoffs.md` + `docs/README.md`) 최종 정합.
- 본 feature 가 만든 규약·결정이 모두 영구화됐거나, 의도적으로 영구화 안 함이 명시.

체크리스트:

정합 (5):
- [5.1] CLAUDE.md A→F 섹션 번호 규약 유지. 본 절 안 추가는 본 절에만 (계층 위반 0건). 계층 충돌 시 #E1 P1~P4 우선순위로 해결.
- [5.2] CLAUDE.md 본문 변경 시 영향받는 deep dive(`docs/architecture/*` · `docs/operations/*`) 동시 갱신. 양자 시그니처·임계·카탈로그 일치.
- [5.3] CLAUDE.md "본 절 결정" 신규 항목 = (원칙 → 결정 → 금지) 구조 + 외부 포인터는 "상세는 `docs/X` 절" 형식만. 본 절 안 deep dive 본문 dump 0건.
- [5.4] ADR 신규 추가 시 `docs/adr/README.md` 인덱스 갱신. 번호는 마지막 번호 + 1 단조 증가 (재사용 0건).
- [5.5] `docs/README.md` 카테고리 표 + 디렉토리 트리 양쪽 동시 갱신. 추가/제거된 영구 문서 1줄 한 줄로 (위치 · 역할 · 수명).

정석 (4):
- [5.6] ADR 은 정정만 (덮어쓰기 금지). 결정 변경은 새 ADR 추가 + 이전 ADR `Status: Superseded by 00NN` (Withdrawn 이면 사유 1줄). 본문 retroactive 수정 0건.
- [5.7] CLAUDE.md F9 "변경 영향도 체크리스트" 표에 본 feature 가 추가한 변경 유형이 빠져있는가. 빠졌으면 행 추가 — 변경 유형 1열 + 동시 갱신 위치 카탈로그 1열.
- [5.8] 본 feature 가 새 외부 의존(HTTP · LLM · 외부 큐 · embedding endpoint) 도입했다면 F6 "외부 의존 실패 모드 매트릭스"(`docs/operations/observability.md`)에 행 추가 — fail-open/close 결정 · timeout · 재시도 정책.
- [5.9] CLAUDE.md "본 절 결정" 신규 항목은 (a) 검사 가능한 형태 (`rg`·`ruff`·hook·테스트로 위반 발견 가능) (b) F9 영향도 표시 (c) 위반 시 행동 명시 — 셋 다 충족. 새 F-policy 도입 시 번호 단조 증가 (기존 번호 재사용 0건). 본 절 결정이 단순 조언(`...하는 게 좋다`)이면 채택 X — 결정·금지·매트릭스 형태만.

원칙 (3):
- [5.10] 모든 "상세는 X 절" 포인터가 가리키는 단일 진실이 실제로 존재하고 정확. 검사: `rg '상세는|단일 진실|catalog: ' CLAUDE.md docs/` 추출 후 인용 경로 존재 + 인용 절 실재 확인.
- [5.11] `docs/temp/` 인용 0건 (본 단계에서 다시 grep — 영구 문서·코드·CLAUDE.md 양방향).
- [5.12] 영구 문서 간 양방향 의존 0건 (Stage 1 [1.17] 재검). CLAUDE.md → deep dive 단방향만 보장.

도구:
- 수동 Read + Edit. 본 단계는 의식적 결정 단계 — 자동화 skill 사용 X.

산출물:
- `CLAUDE.md` · `docs/adr/*` · `docs/architecture/*` · `docs/tradeoffs.md` · `docs/README.md` · `docs/operations/observability.md` 변경.

통과 기준:
- 위 12 항목 모두 OK.
- Stage 1·2·3 의 큐가 모두 처리됨 (큐가 비었거나, 적재된 항목 모두 본 단계에서 반영).

---

## 7. 종료

5 Stage 모두 통과 + 사용자 최종 컨펌 → 본 워크플로 종료.

commit·PR 자동 트리거 X — 사용자가 명시 요청 시 `/commit` · `/push` · `/pr-create` skill 별도 발동 (메모리 `feedback_no_commit_pr_mention.md`).

본 워크플로 종료 보고 형식:
- 누적 변경 파일 카탈로그 (코드 · 문서 · 테스트 분류).
- 사용자 결정 위임된 Warning · 알려진 갭 카탈로그.
- 다음 액션 후보 (commit · PR · 추가 검증) — 사용자가 먼저 언급할 때만 제안.
