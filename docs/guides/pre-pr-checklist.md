# PR 발행 전 체크리스트

> 기능 개발(feature branch) 동작 완성 후 거치는 5단계 마무리의 단일 진실. `.agents/skills/commit/`·`pr/`가 각자 담당 Stage를 실행한다. 스킬은 절차만 가지고 체크리스트는 본 문서를 인용한다.
>
> 최상위 원칙 둘:
> - 코드: 정석 코드 퀄리티 — canonical pattern / framework idiom / declarative 우선, ad-hoc hack 0. 본 repo 명문 규약(AGENTS.md F1~F13 · #B · #C · #E1 P1~P4 · ADR 결정·금지) 위반 0건이 전제 — 외부 일반 베스트 프랙티스보다 본 repo 명문 규약 우선.
> - 문서: 정합 · 중복 없는 간결한 엄밀함 — 단일 진실 / "왜"만 적기 / 코드로 알 수 있는 사실 적지 않기 / 모호 표현 0.
>
> 충돌 시 코드 우선 — 코드가 정석 + 명문 원칙을 따를 때만 문서 정합이 의미를 가진다.

## 0. 적용 시점 · 범위

진입 조건:
- 기능 동작 완성 — 사용자가 화면·로그로 동작을 확인한 상태. 이 동작 검증이 Stage 1 리팩토링의 회귀 안전망이다 (통과하는 검증 하에서만 구조를 바꾼다 — Fowler).
- feature branch 위 (`main`·`master` 직접 X). 미커밋 변경만 또는 clean 상태.
- PR 발행 전. 커밋 스킬은 본 워크플로와 무관하게 언제든 별도 실행한다.

검증 강도 — 되돌리기 비용에 비례해 배치한다. 커밋은 `amend`·`rebase` 로 지울 수 있고, develop 통합은 revert 로 물릴 수 있고, main 승격은 배포로 이어진다.

| 이벤트 | 게이트 | Stage | 발동 |
|--------|--------|-------|------|
| 로컬 commit | lint | - | `.agents/skills/commit/` |
| develop PR | 코드 리뷰 · 문서 정합 · ADR · 영향도 · 단위 테스트 · 파이프라인 | 1~5 | `.agents/skills/pr/` |
| 승격 직전 develop PR | 위 전부 + 릴리즈 단위 문서 검증(`origin/main..HEAD`) | 1~5 | `.agents/skills/pr/` |
| main PR | 통합 테스트 · 커밋 형식 | 검증만 | `.agents/skills/pr/`에서 base main 지정 |

커밋마다 무거운 검증을 강제하지 않는다 — 사용자의 동작 확인과 변경 직후 자가 점검(#F5)이면 충분하다.

문서를 feature 단위로 쓰는 이유는 맥락이다. 문서와 ADR 은 왜 그렇게 했는지를 담는데, 그 근거는 결정한 직후에만 정확하다. 릴리즈까지 미루면 여러 커밋 분량을 기억으로 복원해야 하고 그 사이 drift 가 쌓인다.

이 배치는 이 저장소가 순차 흐름이라 성립한다. feature 브랜치가 동시에 열리지 않으므로 다음 feature 는 앞 feature 의 문서 상태 위에서 시작하고, 같은 문단을 두 갈래가 각자 고치는 충돌이 없다. 병렬 개발로 바뀌면 이 전제가 깨지므로 배치를 다시 본다.

릴리즈 단위 문서 검증은 승격 직전 develop PR이 갖는다. 개별로는 맞는 서술이 릴리즈 단위로 모이면 어긋날 수 있고 그건 묶어 봐야 보이므로, 승격 범위(`origin/main..HEAD`) 전체에 `.agents/reviewers/doc-auditor.md`를 적용한다.

main PR 이 아니라 그 앞 develop PR 에 두는 이유는 왕복 비용이다. main PR 에서 지적이 나오면 고칠 자리가 develop 이라 되돌아가야 하고, 고친 뒤 다시 승격하면 게이트가 또 선다. 지적이 나올 확률이 낮지 않으므로 이 왕복은 예외가 아니라 기본 경로가 된다. 검증을 고칠 수 있는 자리에 두면 왕복이 사라진다.

범위 밖:
- 기능 동작 개발 자체 — 개발 시간의 대부분. 본 워크플로는 동작하는 코드를 전제로 그 위에서 정석화·정합만 수행 (make it work 후의 make it right).
- commit 메시지·PR 본문 작성·push.
- 동작 검증 — 사용자가 실행 화면으로 한다.

Stage 순서는 실제 작업 흐름과 일치한다 — 리팩토링 -> 테스트 -> 파이프라인 검증 -> 문서 -> AGENTS.md. 코드가 먼저 정석에 도달한 뒤 문서를 맞춘다 (역순이면 코드 변경 때마다 문서 재작업).

---

## 1. 진행 정책

자율 진행. 결정 필요 사항만 사용자 컨펌, 단계 경계는 알림·보고만 (다음 Stage 즉시 진입).

결정 필요 사항 = 다음 셋 중 하나:
- 스코프 확장 — 본 워크플로 범위 밖 변경 발견 (예: 본 feature 가 아닌 기존 영구 문서의 위반).
- 알려진 갭 — 의도적으로 미루고 PR 본문에 명시할지 판정 필요.
- 의식적 결정 — 명세 자체 갱신·새 ADR·AGENTS.md "본 절 결정" 추가.

위 셋 외 발견은 자율 처리 (정석 정정이 명백하면 즉시 적용 + 변경 카탈로그 기록).

루프 처리:
- 코드 변경이 발생하면 영향 받는 후속 검증을 다시 돌린다.
  - Stage 2 테스트 중 코드 부족 발견 -> Stage 1 재실행.
  - Stage 3 파이프라인 검증 실패로 코드 수정 -> Stage 1·2 재실행.
  - Stage 4·5 문서 중 코드 모순 발견 -> Stage 1 재실행.
- 최대 3 cycle. 4 cycle 진입 시 사용자에게 정지·재설계를 제안한다 — stuck 은 즉시 보고.

진행 신호:
- 매 Stage 진입 전 1줄 알림 (`Stage N — <목적> (도구: X)`).
- 매 Stage 종료 후 (변경 파일 카탈로그 / 자율 처리 항목 / 결정 필요 사항 / 다음 Stage 큐) 보고 + 다음 Stage 즉시 진입.
- 사용자가 명시적으로 정지·추가 컨펌 요청 시에만 대기.
- 진행 신호가 1분간 없으면 abort + 진단.

Self-audit 메타 인용 제외:
- 본 명세는 금지 토큰·모호 표현·temp 인용 카탈로그 정의를 위해 해당 토큰을 본문에 인용한다 (예: `TODO` · `향후` · `docs/temp/`).
- Stage 4 의 grep 검사는 본 명세와 AGENTS.md 머리말 단일 진실 정의를 제외 — `rg ... --glob '!docs/guides/pre-pr-checklist.md' --glob '!AGENTS.md'` 또는 출력 후 메타 인용 필터링. AGENTS.md 본문 결정 항목은 검사 대상 유지.

---

## 2. Stage 1 — 리팩토링 (정석 코드 퀄리티)

입력:
- 기능 동작 완성 코드 + `git diff <base>...HEAD` (`<base>` 는 PR base — 보통 `develop`).

목적:
- 본 feature 코드가 정석(canonical)으로 짜여 있고, 본 repo 명문 규약(F1~F13 · #B · #C5 · #E1 P1~P4 · 관련 ADR)을 위반하지 않음을 보장.
- 정석 idiom 과 명문 규약 양자 충족 — 정석 패턴이라도 명문 규약에 어긋나면 위반이다.
- 리팩토링은 동작 보존이 절대 — 진입 시 통과한 동작 검증이 안전망. 구조만 바꾸고 동작이 바뀌면 리팩토링이 아니라 기능 변경(범위 밖).

체크리스트:

정석 (6):
- [1.1] framework idiom 정공 사용. ad-hoc 우회 0건.
  - Pydantic: `Field(...)` · `model_validator` · `Annotated[..., Field(...)]` · `SecretStr` 정석. `__init_subclass__` · 런타임 dict 수정 X.
  - SQLAlchemy: declarative ORM + `pg_insert(...).on_conflict_do_*` (#C2). raw SQL 은 `text()` + bound param (#C5).
  - asyncpg: 1 transaction = 1 connection. nested 세션·session 공유 X.
  - FastAPI: `Annotated[..., Depends(...)]` · `APIRouter` · 라우터 분리. 글로벌 state X.
  - Jinja2: 필터 · `{% if %}` · `{% for %}` 만 (#E1 P3). 계산 X.
  - aio-pika: `async with message.process(requeue=False)` 컨텍스트 안에서 모든 await 완료 (#F11).
- [1.2] 추상화는 정석 위치에만. `BaseSettings` · repository protocol · `*Service` 외 ad-hoc abstract 0건.
- [1.3] composition root 외 위치에서 `Settings()` 인스턴스 생성 0건 (#F4 6 위치만). 검사: `rg 'Settings\(\)' src/ -l` 이 그 6 파일만 내는지.
- [1.4] 중복 함수·중복 상수 0건. 같은 의미는 단일 모듈. 임계 도메인 둘(UI badge / USE Method) 혼용 0건 (#E3).
- [1.5] 죽은 코드 0건 — unused import · unreachable branch · 호출처 없는 public 함수. `ruff` · IDE inspection 통과.
- [1.6] 명명 · 매직 넘버. 약식 접미사(`_data` · `_temp` · `_v2` · `_new` · `_old` · `_fix`) 0건. 임계·시간·크기·HTTP status 는 명명 상수 또는 enum(`Literal` · `IntEnum`). 단 0/1/-1 · `LIMIT 1` 같은 자명한 경우 예외.

명문 규약 매핑 (1):
- [1.7] 규약 조항별 검사. 금지 내용은 `AGENTS.md` 가 갖고 여기는 확인 방법만 둔다.
  패턴은 `--type py` 로 한정하고, 검출된 줄이 주석·docstring 인지 실제 코드인지 눈으로 가른다.
  worker 루프의 `except Exception` 처럼 규약이 예외를 인정한 자리는 근거가 인접 주석에 있다.

| 조항 | 검사 |
|------|------|
| F1 타입 | `rg 'pyright: ignore' src/` 억제에 사유 주석이 붙었는지 · Pydantic 필드 타입이 `TYPE_CHECKING` 블록에만 선언됐는지 · 검사기 만족용 `assert x is not None` |
| F2 시간대 | `rg 'datetime\.now\(\)' src/` 에서 tz 인자 없는 것 · `rg '9 ?\* ?60 ?\* ?60' src/` 인라인 KST offset |
| F3 검증 | `rg '_VALID_' src/` · 진입점 밖 `model_validate` 재실행 |
| F4 DI | Service/Handler 안 `Sql*` import (`Settings()` 위치는 [1.3]) |
| F6 실패 | `rg 'except Exception' src/` · timeout 인자 없는 외부 호출 · 영구 오류 재시도 |
| F7 로깅 | `rg '\bprint\(|sys\.stdout\.write|^import logging' src/` · except 밖 `logger.exception()` · payload 로깅 |
| F8 시크릿 | 신규 비밀 필드의 `SecretStr` · 응답·캐시·로그·예외의 PII |
| F10 F11 | 평가 윈도우가 `right_sizing.WINDOW_DAYS` 단일 참조 · 새 TimeRange 는 4곳 동시 갱신 · `rg 'signal\.signal|os\._exit' src/` |
| B C5 E1 | `rg 'extra="(forbid|allow)"' src/` 가 인바운드 메시지 모델에 붙었는지 (외부 계약 `TypedDict` 의 `extra=forbid` 는 정상) · hypertable 조회의 `collected_at` 술어 · 템플릿 계산(`+`·`*`·`length`·`sort`·`selectattr`) · 차트 JS 5 의무 규약 |

주석 (1):
- [1.8] 이번 브랜치가 건드린 파일의 주석만 본다. 전수 검토는 하지 않는다 — 판단이 필요해 자동화가 안 되고,
  손대지 않은 파일의 주석은 그 파일을 고칠 때 함께 본다.
  - 코드를 옮겨 적은 주석(시그니처·자명한 동작) 제거. 남길 것은 why 뿐이다.
  - 정책·규약 서술을 주석에 복제하지 않는다. 문서 위치만 가리킨다.
  - 회고형 서술("이전엔"·"~에서 전환"·"(v2)"·"구 X") 0건.
  - 규약 절 번호(`#F8` 등)는 그 절이 왜 여기 적용되는지 자명하지 않을 때만 남긴다.
  - 주석 처리된 코드 0건.

중복 — 데이터 흐름 (2):
- [1.9] 같은 데이터 흐름이 두 경로로 (ViewModel 파생 필드가 mapper·template 둘 다 계산 / 같은 집계가 service A·B 각자 계산). #E1 P1~P4 정공 위치 단일.
- [1.10] 같은 외부 호출이 두 위치에서 (같은 HTTP endpoint 를 service A·B 각자 호출 / 같은 Redis 키를 두 곳에서 set). 단일 client·helper 의무.

도구:
- `.agents/reviewers/code-reviewer.md` 1회 적용 (AGENTS.md F-policy 매핑 자동). Error 즉시 수정 / Warning 사용자 결정 위임 / Info 보고만.

산출물:
- `src/` 코드. 수정 발생 시 후속 Stage(2·3·4) 재검 트리거.

통과 기준:
- 에이전트 Error 0건. 위 10 항목 self-grep 0건.

---

## 3. Stage 2 — 단위·모듈 테스트 수정

입력:
- Stage 1 통과 코드 + 기존 `tests/unit/` · `tests/integration/`.

목적:
- 본 feature 가 추가·변경한 모든 public 함수·핸들러·라우터에 의미 있는 테스트가 존재하고, 본 repo 테스트 패턴(`docs/guides/testing.md`)을 따른다.

체크리스트:

정합 (5):
- [2.1] 추가한 모든 public 함수·메서드에 단위 또는 통합 테스트 존재. private(`_prefix`)은 public 경유 간접 검증. 자가 판단 대신 `make test-cov` 로 해당 파일 미커버 줄을 본다.
- [2.2] 추가·변경한 모든 라우터에 통합 테스트 — happy path + 핵심 분기(422 형식 · 404 미존재 · trigger 별 분기). 화면 응답은 `tests/http/` 스냅샷이 함께 고정한다.
- [2.3] 변경한 기존 함수·핸들러의 기존 테스트가 여전히 의미를 갖는가 — signature·동작 변경 반영. 통과만 시키는 mock 보강 0건.
- [2.4] 변경·추가한 임계 상수(#E3 두 도메인) 테스트 하드코딩 0건 — 모두 import.
- [2.5] 삭제한 public 함수·라우터의 deprecated 테스트 0건. Alembic revision 추가 시 라운드트립 검증은 `docs/guides/migrate.md` "신규 마이그레이션 작성 워크플로우" 4단계 수행.

정석 (5):
- [2.6] asyncio 테스트 작성 패턴이 `docs/guides/testing.md` 4절과 일치.
- [2.7] `tests/factories.py` 빌더 활용. raw dict 직접 생성 0건. 신규 도메인은 factory 추가 후 활용.
- [2.8] DB 의존 테스트는 루트 `tests/conftest.py` 의 testcontainers 컨테이너 + `db_session` 사용. repo fixture 는 `tests/integration/conftest.py` 에만 — 함수 안 fixture 정의 0건.
- [2.9] mock 범위: repository protocol 과 외부 의존 경계(Redis · HTTP · MQ)에만. 구체 구현 내부 함수 patch 0건. integration 은 실제 컨테이너.
- [2.10] 동일 시나리오 분기(같은 setup + 다른 입력·기대값)는 `@pytest.mark.parametrize`. 중복 함수 0건.

레이어 결정은 `docs/guides/testing.md` 1절.

원칙 (3):
- [2.11] 테스트 한 개 = 한 분기. assert 누락·smoke-only 0건. 한 함수에 무관한 assert 다발 0건.
- [2.12] 동일 픽스처·테스트 데이터 중복 0건 — fixture / factory / parametrize.
- [2.13] 테스트 실행 정책 준수 — 정본은 AGENTS.md #F5.

도구:
- 직접 수행 (레이어 결정 + 패턴 작성). 테스트 정책 단일 진실 `docs/guides/testing.md`.

산출물:
- `tests/unit/` · `tests/integration/`. 코드 부족 발견 시 Stage 1 재검 트리거.

통과 기준:
- 위 13 항목 OK. 사용자가 "테스트 실행" 명시 시 pytest 100% pass.

---

## 4. Stage 3 — 파이프라인 검증

입력:
- Stage 2 통과 코드 + 테스트.

목적:
- PR CI 가 회귀를 머지 전에 차단한다. 릴리즈 파이프라인은 main 머지 후 `release.yml` 이 담당한다.

체크리스트:
- [3.1] PR 발행 후 CI 결과 NG 0건. 발화 범위는 base 가 정하며 목록은 `docs/guides/ci-setup.md` 3.4 소유.
- [3.2] OIDC·GHCR 인증 필요한 step(cosign 서명 · GHCR push · SBOM/provenance attestation)은 릴리즈 워크플로(`release.yml`) 전용이라 러너에서만 성립 — 로컬 skip (그 직전까지 산출물·액션 resolve 는 검증됨).

도구:
- GitHub Actions (PR 발행 시 자동 발화).

산출물:
- 검증 결과. NG 로 코드 수정 시 Stage 1·2 재검 트리거.

통과 기준:
- PR CI NG 0건.

---

## 5. Stage 4 — 문서 정합성 정리

입력:
- `git diff <base>...HEAD` + 미커밋 변경.
- 변경 코드 -> 영구 문서 매핑: AGENTS.md #F9 "변경 영향도 체크리스트" + 머리말 "문서 인덱스" 단일 진실 (본 명세 중복 X).

목적:
- 본 feature 가 만진 영역의 영구 문서(`docs/reference/`·`docs/guides/`·`docs/explanation/products/`·`docs/explanation/tradeoffs.md`)가 현행 코드를 정확히 반영.
- AGENTS.md·ADR 변경은 Stage 5 에서 처리한다 — 같은 PR 안이라 후보만 적어 두고 넘긴다.

체크리스트:

정합 (5):
- [4.1] 변경 코드 경로별 매핑 영구 문서를 모두 읽는다. (a) 문서 인용 symbol(함수/클래스/필드/env 키/routing key/상수)이 코드에 실제 존재한다. (b) 시그니처/타입/기본값이 일치한다. (c) 한 문서 안 동일 symbol 표기가 일관된다.
- [4.2] AGENTS.md "본 절 결정" 중 본 feature 가 무효화·변경·확장한 결정이 있으면 Stage 5 큐에 (절번호 · 변경 유형 · 근거) 적재.
- [4.3] 영구 문서 안 인용 경로(`src/` · `docs/` · `tests/`)가 실제 존재. 검사: `rg -oN '\b(src/|docs/|tests/|migrations/)[A-Za-z0-9_./-]+' docs/` 추출 후 `test -e`.
- [4.4] ADR 이 본 feature 로 Superseded·Withdrawn 돼야 하는가, 새 ADR 후보가 있는가 — 판정만 하고 Stage 5 로 넘긴다. 판정 표는 `.agents/skills/docs/SKILL.md` 4절이 갖는다.
- [4.5] `docs/explanation/tradeoffs.md` T 항목 중 본 feature 가 해결·악화시킨 것, 새 트레이드오프 — 큐 적재.

중복 없음 (4):
- [4.6] 같은 사실(임계 상수·UNIQUE 키·routing key·환경변수 default·트랜잭션 경계)이 영구 문서 두 곳 이상 정의 0건. 단일 진실 후 나머지는 포인터(`상세는 X 절`).
- [4.7] AGENTS.md 본문과 `docs/reference/*` deep dive 책임 혼선 0건. AGENTS.md = 결정·원칙·금지. deep dive = 동작·흐름·매트릭스·카탈로그.
- [4.8] 본 명세와 다른 영구 문서(`testing.md` · `conventions.md`) 사이 중복 0건. 본 명세 = 워크플로 절차. 다른 문서 = 정책 자체.
- [4.9] 같은 사실이 카테고리 두 곳(`reference/` vs `guides/` vs `explanation/`)에 분산 0건. AGENTS.md 전문 카테고리 표 정합.

간결 (3):
- [4.10] 코드만 봐도 알 수 있는 사실(디렉토리 트리·함수 시그니처 본문·import graph·라인 수) 0건 — 코드 경로 포인터로.
- [4.11] 임시 상태(`TODO`·`FIXME`·`XXX`·"작업 중"·"향후"·"차후") 0건. 검사: `rg '\b(TODO|FIXME|XXX)\b|작업\s*중|향후|차후' docs/reference/ docs/guides/ -g '!pre-pr-checklist.md'`. 한글은 `\w` 라 `\b` 가 조사 앞에서 성립하지 않으므로 감싸지 않고, `XXX` 는 대소문자를 가려 CSS placeholder(`#xxx`)를 배제한다. `docs/explanation/` 은 한계·확장 트리거가 사는 자리라 대상이 아니다. 임시는 `docs/temp/` 또는 PR 본문.
- [4.12] 동일 결정·금지가 한 문서 안 반복 0건.

엄밀 (3):
- [4.13] "왜"가 적혀있는가. 결정·금지·트레이드오프는 이유(과거 incident · 외부 표준 · 트레이드오프 본질) 동반. 이유 없는 금지 0건.
- [4.14] 모호 표현(`가급적`·`되도록`·`적당히`·`필요시`·`등등`·`일부`·`대부분`·`향후`·`차후`) 0건. 결정 binary, 임계 숫자+단위, 예외 명시 enum.
- [4.15] 임계·시간·크기는 명명 상수 또는 단위 동반 숫자(`14 일` · `90 %` · `300 ms`). 단위 없는 raw 숫자 0건.

원칙 (2):
- [4.16] `docs/temp/` 인용 0건 (양방향). 검사: `rg 'docs/temp' src/ docs/reference/ docs/guides/ docs/explanation/ docs/decisions/adr/ AGENTS.md`.
- [4.17] 영구 문서 간 같은 사실의 양방향 의존(순환) 0건. 상위(AGENTS.md) → 하위(`docs/reference/*`) 단방향. 동등 계층은 다른 책임의 단일 진실 간 cross-reference 만 허용 — 같은 사실을 양쪽에 복제 금지.

도구:
- 직접 수행 (git diff 변경 코드 -> #F9 + 문서 인덱스 매핑 -> 영구 문서 제안·반영). 자동 적용 전 사용자 승인.

산출물:
- `docs/reference/*` · `docs/guides/*` · `docs/explanation/products/*` · `docs/explanation/tradeoffs.md`. AGENTS.md·ADR 후보는 큐만 (실제 변경 Stage 5). 코드 모순 발견 시 Stage 1 재검 트리거.

통과 기준:
- 17 항목 OK 또는 의도적으로 미룬 항목은 PR 본문 알려진 갭으로 명시.

---

## 6. Stage 5 — AGENTS.md · 관련 영구 문서 최종 수정

입력:
- Stage 1·2·4 에서 넘어온 후보: AGENTS.md "본 절 결정" 변경 · ADR 신규/Superseded · tradeoffs T 신규 · 본 feature 가 확립한 새 규약·금지·정석 패턴.

목적:
- 영구 단일 진실(AGENTS.md + `docs/reference/*` + `docs/explanation/tradeoffs.md` + `docs/README.md`) 최종 정합.
- 본 feature 가 만든 규약·결정이 모두 영구화됐거나, 의도적 미영구화가 명시됨.

체크리스트:

정합 (5):
- [5.1] AGENTS.md A→F 섹션 번호 규약 유지. 추가는 해당 절에만 (계층 위반 0건). 충돌 시 #E1 P1~P4 우선순위.
- [5.2] AGENTS.md 본문 변경 시 영향 deep dive(`docs/reference/*` · `docs/guides/*`) 동시 갱신. 시그니처·임계·카탈로그 일치.
- [5.3] AGENTS.md "본 절 결정" 신규 = (원칙 → 결정 → 금지) 구조 + 외부 포인터는 "상세는 `docs/X` 절" 형식만. 본 절 안 deep dive 본문 dump 0건.
- [5.4] ADR 파일·인덱스 변경분이 `.agents/skills/docs/SKILL.md` 4절 판정 표를 벗어나지 않았는지 diff 로 확인. 판정 표는 그 절, 채번·Status 어휘는 `docs/decisions/adr/README.md` 소유이고 여기서는 결과 반영만 본다.
- [5.5] `docs/README.md` 계층 역할 표 + 지도 동시 갱신. 추가/제거 영구 문서 한 줄로 (위치 · 역할 · 수명).

정석 (3):
- [5.6] AGENTS.md F9 "변경 영향도 체크리스트" 에 본 feature 가 추가한 변경 유형이 빠졌으면 행 추가 (변경 유형 + 동시 갱신 위치).
- [5.7] 새 외부 의존(HTTP · 외부 큐) 도입 시 F6 "외부 의존 실패 모드 매트릭스"(`docs/reference/observability.md`)에 행 추가 (fail-open/close · timeout · 재시도).
- [5.8] AGENTS.md "본 절 결정" 신규는 (a) 검사 가능(`rg`·`ruff`·테스트로 위반 발견) (b) F9 영향도 표시 (c) 위반 시 행동 명시 셋 다 충족. 새 F-policy 는 번호 단조 증가. 단순 조언(`...하는 게 좋다`)이면 채택 X — 결정·금지·매트릭스 형태만.

원칙 (2):
- [5.9] 모든 "상세는 X 절" 포인터가 가리키는 단일 진실이 실제 존재·정확. 검사: `rg '상세는|단일 진실' AGENTS.md docs/` 추출 후 인용 경로·절 실재 확인.
- [5.10] Stage 5 가 만진 파일에 [4.16] `docs/temp/` 인용·[4.17] 양방향 의존 재검. AGENTS.md -> deep dive 단방향만.

도구:
- 수동 검토와 편집. 의식적 결정 단계 — 자동화 X.

산출물:
- `AGENTS.md` · `docs/reference/*` · `docs/explanation/tradeoffs.md` · `docs/README.md` · `docs/reference/observability.md`.

통과 기준:
- 10 항목 OK. Stage 1·2·4 에서 넘어온 후보 모두 처리.

---

## 7. 종료

5 Stage 통과 + 사용자 최종 컨펌 → 종료.

commit·PR 자동 실행 없음. 사용자 명시 시 해당 공용 스킬을 별도 적용한다.

종료 보고 형식:
- 누적 변경 파일 카탈로그 (코드 · 문서 · 테스트 분류).
- 사용자 결정 위임된 Warning · 알려진 갭 카탈로그.
- 다음 액션 후보 (commit · PR · 추가 검증) — 사용자가 먼저 언급할 때만 제안.
