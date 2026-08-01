# wrap-up — 기능 개발 마무리 표준 워크플로

> 기능 개발(feature branch) 동작 완성 후 거치는 5단계 마무리의 단일 진실. `/commit`·`/pr` skill 이 각자 담당 Stage 를 실행한다 — skill 은 절차만 가지고 체크리스트는 본 문서를 인용한다.
>
> 최상위 원칙 둘:
> - 코드: 정석 코드 퀄리티 — canonical pattern / framework idiom / declarative 우선, ad-hoc hack 0. 본 repo 명문 규약(CLAUDE.md F1~F12 · #B · #C · #E1 P1~P4 · ADR 결정·금지) 위반 0건이 전제 — 외부 일반 베스트 프랙티스보다 본 repo 명문 규약 우선.
> - 문서: 정합 · 중복 없는 간결한 엄밀함 — 단일 진실 / "왜"만 적기 / 코드로 알 수 있는 사실 적지 않기 / 모호 표현 0.
>
> 충돌 시 코드 우선 — 코드가 정석 + 명문 원칙을 따를 때만 문서 정합이 의미를 가진다.

## 0. 적용 시점 · 범위

진입 조건:
- 기능 동작 완성 — `/run` 또는 수동 검증 통과. 이 동작 검증이 Stage 1 리팩토링의 회귀 안전망이다 (통과하는 검증 하에서만 구조를 바꾼다 — Fowler).
- feature branch 위 (`main`·`master` 직접 X). 미커밋 변경만 또는 clean 상태.
- commit·PR 전. commit·PR 자체는 본 워크플로 종료 후 사용자 명시 시 `/commit`·`/pr` 별도 발동.

검증 강도 — 되돌리기 비용에 비례해 배치한다. 커밋은 `amend`·`rebase` 로 지울 수 있고, develop 통합은 revert 로 물릴 수 있고, main 승격은 배포로 이어진다.

| 이벤트 | 게이트 | Stage | 발동 |
|--------|--------|-------|------|
| 로컬 commit | lint | — | `/commit` |
| develop PR | 코드 리뷰 · 단위 테스트 · 파이프라인 | 1·2·3 | `/pr` |
| main PR | 문서 정합 · ADR · 영향도 · 통합 테스트 | 4·5 | `/pr --base main` |

Stage 4·5(문서)는 릴리즈 주기와 별개로도 실행한다. drift 는 기능 단위가 아니라 시간이 지나며 쌓이므로, 코드 현황과 문서를 대조하는 작업 자체는 `/docs` 로 언제든 발동한다. main PR 게이트는 그 skill 을 승격 대상 영역에 대해 호출하는 것이다.

커밋마다 무거운 검증을 강제하지 않는다 — 동작 검증(`/run`)과 변경 직후 자가 점검(#F5)이면 충분하다.

문서(Stage 4·5)를 main PR 로 미루는 이유는 응집도다. 문서는 무엇이 릴리즈되는가에 대한 서술이라, feature 마다 쓰면 develop 에 여러 갈래가 모였을 때 서로 어긋난다. 릴리즈 단위로 한 번에 쓰면 중복도 재작업도 없다.

범위 밖:
- 기능 동작 개발 자체 — 개발 시간의 대부분. 본 워크플로는 동작하는 코드를 전제로 그 위에서 정석화·정합만 수행 (make it work 후의 make it right).
- commit 메시지·PR 본문 작성·push.
- 동작 검증 (`/run`).

Stage 순서는 실제 작업 흐름과 일치: 리팩토링 -> 테스트 -> 파이프라인 검증 -> 문서 -> CLAUDE.md. 코드가 먼저 정석에 도달한 뒤 문서를 맞춘다 (역순이면 코드 변경 때마다 문서 재작업).

---

## 1. 진행 정책

자율 진행. 결정 필요 사항만 사용자 컨펌, 단계 경계는 알림·보고만 (다음 Stage 즉시 진입).

결정 필요 사항 = 다음 셋 중 하나:
- 스코프 확장 — 본 워크플로 범위 밖 변경 발견 (예: 본 feature 가 아닌 기존 영구 문서의 위반).
- 알려진 갭 — 의도적으로 미루고 PR 본문에 명시할지 판정 필요.
- Stage 5 큐 — 명세 자체 갱신·새 ADR·CLAUDE.md "본 절 결정" 추가 등 의식적 결정.

위 셋 외 발견은 자율 처리 (정석 정정이 명백하면 즉시 적용 + 변경 카탈로그 기록).

각 Stage 는 입력 / 목적 / 체크리스트 / 산출물 / 통과 기준 5요소를 가진다.

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
- 1분 진행 신호 무 시 abort + 진단.

Self-audit 메타 인용 제외:
- 본 명세는 금지 토큰·모호 표현·temp 인용 카탈로그 정의를 위해 해당 토큰을 본문에 인용한다 (예: `TODO` · `향후` · `docs/temp/`).
- Stage 4 의 grep 검사는 본 명세와 CLAUDE.md 전문(前文) 단일 진실 정의를 제외 — `rg ... --glob '!docs/guides/wrap-up.md' --glob '!.claude/CLAUDE.md'` 또는 출력 후 메타 인용 필터링. CLAUDE.md 본문 결정 항목은 검사 대상 유지.

---

## 2. Stage 1 — 리팩토링 (정석 코드 퀄리티)

입력:
- 기능 동작 완성 코드 + `git diff <base>...HEAD` (`<base>` 는 PR base — 보통 `develop`).

목적:
- 본 feature 코드가 정석(canonical)으로 짜여 있고, 본 repo 명문 규약(F1~F12 · #B · #C5 · #E1 P1~P4 · 관련 ADR)을 위반하지 않음을 보장.
- 정석 idiom 과 명문 규약 양자 충족 — 정석 패턴이라도 명문 규약에 어긋나면 위반이다.
- 리팩토링은 동작 보존이 절대 — 진입 시 통과한 동작 검증이 안전망. 구조만 바꾸고 동작이 바뀌면 리팩토링이 아니라 기능 변경(범위 밖).

체크리스트:

정석 (6):
- [1.1] framework idiom 정공 사용. ad-hoc 우회 0건.
  - Pydantic: `Field(...)` · `model_validator` · `Annotated[..., Field(...)]` · `SecretStr` 정석. `__init_subclass__` · 런타임 dict 수정 X.
  - SQLAlchemy: declarative ORM + `pg_insert(...).on_conflict_do_*` (#C2). raw SQL 은 `text()` + bound param (#C5).
  - asyncpg: 1 transaction = 1 connection. nested 세션·session 공유 X.
  - FastAPI: `Depends(...)` · `APIRouter` · 라우터 분리. 글로벌 state X.
  - Jinja2: 필터 · `{% if %}` · `{% for %}` 만 (#E1 P3). 계산 X.
  - aio-pika: `async with message.process(requeue=False)` 컨텍스트 안에서 모든 await 완료 (#F11).
- [1.2] 추상화는 정석 위치에만. `BaseSettings` · `Base*Repository` · `*Service` 외 ad-hoc abstract 0건.
- [1.3] composition root 외 위치에서 `Settings()` 인스턴스 생성 0건 (#F4 6 위치만). 검사: `rg 'Settings\(\)|WebSettings\(\)|ConsumerSettings\(\)|DiagnosticSettings\(\)' src/`.
- [1.4] 중복 함수·중복 상수 0건. 같은 의미는 단일 모듈 (UI badge 임계 = `mappers/shared.py` / USE Method 임계 = `recommendation.py`). 두 도메인 혼용 0건 (#E3).
- [1.5] 죽은 코드 0건 — unused import · unreachable branch · 호출처 없는 public 함수. `ruff` · IDE inspection 통과.
- [1.6] 명명 · 매직 넘버. 약식 접미사(`_data` · `_temp` · `_v2` · `_new` · `_old` · `_fix`) 0건. 임계·시간·크기·HTTP status 는 명명 상수 또는 enum(`Literal` · `IntEnum`). 단 0/1/-1 · `LIMIT 1` 같은 자명한 경우 예외.

명문 규약 매핑 (9):
- [1.7] F1 — Pydantic 모델 필드 타입이 `TYPE_CHECKING` 블록에만 있는지 0건(런타임 resolve 라 `NameError`). type checker 만족용 런타임 `assert x is not None` 0건. `# type: ignore[return-value]` 로 덮은 거짓 시그니처 0건.
- [1.8] F2 — KST 변환이 표시 경계 4 함수(SSR `kst` · client `fmtLabel` · `fmtKst` · `initAnchor`) 외 0건. naive datetime · 인라인 KST offset 더하기 0건.
- [1.9] F3 — 검증이 진입점(라우터 Pydantic · Consumer `model_validate_json` · `BaseSettings`) 외 위치에서 재실행 0건. `_VALID_*` frozenset·런타임 enum 멤버십 체크 0건.
- [1.10] F4 — Service/Handler 안 구체 구현체 import 0건. `config.py` module-level instance 0건. `assessment_engine.config` 에서 Settings 인스턴스 import 0건.
- [1.11] F6 — `except Exception` 광범위 catch 0건. timeout 없는 외부 호출 0건 (`asyncio.wait_for` 또는 클라이언트 timeout 옵션 의무). 영구 오류(`IntegrityError` · 4xx) 재시도 0건.
- [1.12] F7 — `print` · stdlib `logging` · `sys.stdout.write` 혼용 0건. `logger.exception()` 은 except 블록 안에만. raw payload 로깅 0건 (식별자 + 카운트만). 신규 시그널 로그는 (레벨 · 빈도 제어 · 운영자 행동) 셋 다 명시.
- [1.13] F8 — secret 필드 `SecretStr` 누락 0건. PII(composite_id · public_id 외 식별자 · 전체 payload · 접속 문자열) 응답·캐시·로그·예외 0건.
- [1.14] F10 · F11 — 평가 윈도우는 `recommendation.WINDOW_DAYS` 단일 참조. 새 TimeRange/BucketSize 는 backend Literal · SQL dispatch · `chart-utils.js` · UI 토글 4곳 동시 갱신. `signal.signal(SIGTERM, ...)` 직접 핸들러 · `os._exit()` · `message.process()` 컨텍스트 밖 await 0건.
- [1.15] #B · #C5 · #E1 — Pydantic Input `extra=ignore` 유지. hypertable 조회 `WHERE collected_at >= ?` 누락 0건. f-string SQL 사용자 입력 삽입 0건. Repository raw 단위 외 변환 0건(P1). mapper → ViewModel 외 위치 percent·delta·임계 분류 0건(P2). 템플릿 계산(`+`·`*`·`length`·`sort`·`selectattr`) 0건(P3). 차트 JS 5 의무 규약 위반 0건(P4).

중복 — 데이터 흐름 (2):
- [1.16] 같은 데이터 흐름이 두 경로로 (ViewModel 파생 필드가 mapper·template 둘 다 계산 / 같은 집계가 service A·B 각자 계산). #E1 P1~P4 정공 위치 단일.
- [1.17] 같은 외부 호출이 두 위치에서 (같은 HTTP endpoint 를 service A·B 각자 호출 / 같은 Redis 키를 두 곳에서 set). 단일 client·helper 의무.

도구:
- code-reviewer 에이전트 1회 발동 (CLAUDE.md F-policy 매핑 자동). Error 즉시 수정 / Warning 사용자 결정 위임 / Info 보고만.

산출물:
- `src/` 코드. 수정 발생 시 후속 Stage(2·3·4) 재검 트리거.

통과 기준:
- 에이전트 Error 0건. 위 17 항목 self-grep 0건.

---

## 3. Stage 2 — 단위·모듈 테스트 수정

입력:
- Stage 1 통과 코드 + 기존 `tests/unit/` · `tests/integration/`.

목적:
- 본 feature 가 추가·변경한 모든 public 함수·핸들러·라우터에 의미 있는 테스트가 존재하고, 본 repo 테스트 패턴(`docs/guides/testing.md`)을 따른다.

체크리스트:

정합 (5):
- [2.1] 추가한 모든 public 함수·메서드에 단위 또는 통합 테스트 존재. private(`_prefix`)은 public 경유 간접 검증.
- [2.2] 추가·변경한 모든 라우터에 통합 테스트 — happy path + 핵심 분기(422 형식 · 404 미존재 · trigger 별 분기).
- [2.3] 변경한 기존 함수·핸들러의 기존 테스트가 여전히 의미를 갖는가 — signature·동작 변경 반영. 통과만 시키는 mock 보강 0건.
- [2.4] 변경·추가한 임계 상수(`mappers/shared.py` · `recommendation.py` · `_USAGE_DANGER_PCT` 등) 테스트 하드코딩 0건 — 모두 import.
- [2.5] 삭제한 public 함수·라우터의 deprecated 테스트 0건. Alembic revision 추가 시 라운드트립 검증은 `docs/guides/migrate.md` "신규 마이그레이션 작성 워크플로우" 4단계 수행.

정석 (5):
- [2.6] asyncio 테스트 작성 패턴이 `docs/guides/testing.md` 4절과 일치.
- [2.7] `tests/factories.py` 빌더 활용. raw dict 직접 생성 0건. 신규 도메인은 factory 추가 후 활용.
- [2.8] DB 의존 테스트는 루트 `tests/conftest.py` 의 testcontainers 컨테이너 + `db_session` 사용. repo fixture 는 `tests/integration/conftest.py` 에만 — 함수 안 fixture 정의 0건.
- [2.9] mock 범위: 추상 인터페이스(`Base*Repository`)와 외부 의존 경계(Redis · HTTP · MQ)에만. 구체 구현 내부 함수 patch 0건. integration 은 실제 컨테이너.
- [2.10] 동일 시나리오 분기(같은 setup + 다른 입력·기대값)는 `@pytest.mark.parametrize`. 중복 함수 0건.

레이어 결정 (test-write 흡수):
- unit (`tests/unit/`): DB·Redis·외부 의존 없는 함수·dataclass·계산.
- integration (`tests/integration/`): Repository · DB query · Schema 통합.
- 실 VM 동반 E2E: 본 repo 범위 밖 (OpenStack 공급 환경, pytest 범위 외).

원칙 (3):
- [2.11] 테스트 한 개 = 한 분기. assert 누락·smoke-only 0건. 한 함수에 무관한 assert 다발 0건.
- [2.12] 동일 픽스처·테스트 데이터 중복 0건 — fixture / factory / parametrize.
- [2.13] 테스트 실행 정책 준수 — 정본은 CLAUDE.md #F5.

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
- [3.2] OIDC·GHCR 인증 필요한 step(cosign 서명 · GHCR push · SBOM/provenance attestation)은 본질적으로 CI 전용 — 로컬 skip (그 직전까지 산출물·액션 resolve 는 검증됨).

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
- 변경 코드 -> 영구 문서 매핑: CLAUDE.md #F9 "변경 영향도 체크리스트" + 전문(前文) "문서 인덱스" 단일 진실 (본 명세 중복 X).

목적:
- 본 feature 가 만진 영역의 영구 문서(`docs/reference/`·`docs/guides/`·`docs/explanation/products/`·`docs/explanation/tradeoffs.md`)가 현행 코드를 정확히 반영.
- CLAUDE.md·ADR 후보는 변경하지 않고 Stage 5 큐로만 적재.

체크리스트:

정합 (5):
- [4.1] 변경 코드 경로별 매핑 영구 문서를 모두 Read. (a) 문서 인용 symbol(함수·클래스·필드·env 키·routing key·상수)이 코드에 실제 존재. (b) 시그니처·타입·기본값 일치. (c) 한 문서 안 동일 symbol 표기 일관.
- [4.2] CLAUDE.md "본 절 결정" 중 본 feature 가 무효화·변경·확장한 결정이 있으면 Stage 5 큐에 (절번호 · 변경 유형 · 근거) 적재.
- [4.3] 영구 문서 안 인용 경로(`src/` · `docs/` · `tests/`)가 실제 존재. 검사: `rg -oN '\b(src/|docs/|tests/|migrations/)[A-Za-z0-9_./-]+' docs/` 추출 후 `test -e`.
- [4.4] ADR 이 본 feature 로 Superseded·Withdrawn 돼야 하는가, 새 ADR 후보가 있는가 — 둘 다 Stage 5 큐로.
- [4.5] `docs/explanation/tradeoffs.md` T 항목 중 본 feature 가 해결·악화시킨 것, 새 트레이드오프 — 큐 적재.

중복 없음 (4):
- [4.6] 같은 사실(임계 상수·UNIQUE 키·routing key·환경변수 default·트랜잭션 경계)이 영구 문서 두 곳 이상 정의 0건. 단일 진실 후 나머지는 포인터(`상세는 X 절`).
- [4.7] CLAUDE.md 본문과 `docs/reference/*` deep dive 책임 혼선 0건. CLAUDE.md = 결정·원칙·금지. deep dive = 동작·흐름·매트릭스·카탈로그.
- [4.8] 본 명세와 다른 영구 문서(`testing.md` · `conventions.md`) 사이 중복 0건. 본 명세 = 워크플로 절차. 다른 문서 = 정책 자체.
- [4.9] 같은 사실이 카테고리 두 곳(`reference/` vs `guides/` vs `explanation/`)에 분산 0건. CLAUDE.md 전문 카테고리 표 정합.

간결 (3):
- [4.10] 코드만 봐도 알 수 있는 사실(디렉토리 트리·함수 시그니처 본문·import graph·라인 수) 0건 — 코드 경로 포인터로.
- [4.11] 임시 상태(`TODO`·`FIXME`·`XXX`·"작업 중"·"향후"·"차후") 0건. 검사: `rg -i '\b(TODO|FIXME|XXX|작업중|향후|차후)\b' docs/reference/ docs/guides/ docs/explanation/`. 임시는 `docs/temp/` 또는 PR 본문.
- [4.12] 동일 결정·금지가 한 문서 안 반복 0건.

엄밀 (3):
- [4.13] "왜"가 적혀있는가. 결정·금지·트레이드오프는 이유(과거 incident · 외부 표준 · 트레이드오프 본질) 동반. 이유 없는 금지 0건.
- [4.14] 모호 표현(`가급적`·`되도록`·`적당히`·`필요시`·`등등`·`일부`·`대부분`·`향후`·`차후`) 0건. 결정 binary, 임계 숫자+단위, 예외 명시 enum.
- [4.15] 임계·시간·크기는 명명 상수 또는 단위 동반 숫자(`14 일` · `90 %` · `300 ms`). 단위 없는 raw 숫자 0건.

원칙 (2):
- [4.16] `docs/temp/` 인용 0건 (양방향). 검사: `rg 'docs/temp' src/ docs/reference/ docs/guides/ docs/explanation/ docs/decisions/adr/ .claude/CLAUDE.md`.
- [4.17] 영구 문서 간 같은 사실의 양방향 의존(순환) 0건. 상위(CLAUDE.md) → 하위(`docs/reference/*`) 단방향. 동등 계층은 다른 책임의 단일 진실 간 cross-reference 만 허용 — 같은 사실을 양쪽에 복제 금지.

도구:
- 직접 수행 (git diff 변경 코드 -> #F9 + 문서 인덱스 매핑 -> 영구 문서 제안·반영). 자동 적용 전 사용자 승인.

산출물:
- `docs/reference/*` · `docs/guides/*` · `docs/explanation/products/*` · `docs/explanation/tradeoffs.md`. CLAUDE.md·ADR 후보는 큐만 (실제 변경 Stage 5). 코드 모순 발견 시 Stage 1 재검 트리거.

통과 기준:
- 17 항목 OK 또는 의도적으로 미룬 항목은 PR 본문 알려진 갭으로 명시.

---

## 6. Stage 5 — CLAUDE.md · 관련 영구 문서 최종 수정

입력:
- Stage 1·2·4 누적 큐: CLAUDE.md "본 절 결정" 변경 · ADR 신규/Superseded · tradeoffs T 신규 · 본 feature 가 확립한 새 규약·금지·정석 패턴 후보.

목적:
- 영구 단일 진실(CLAUDE.md + `docs/decisions/adr/` + `docs/reference/*` + `docs/explanation/tradeoffs.md` + `docs/README.md`) 최종 정합.
- 본 feature 가 만든 규약·결정이 모두 영구화됐거나, 의도적 미영구화가 명시됨.

체크리스트:

정합 (5):
- [5.1] CLAUDE.md A→F 섹션 번호 규약 유지. 추가는 해당 절에만 (계층 위반 0건). 충돌 시 #E1 P1~P4 우선순위.
- [5.2] CLAUDE.md 본문 변경 시 영향 deep dive(`docs/reference/*` · `docs/guides/*`) 동시 갱신. 시그니처·임계·카탈로그 일치.
- [5.3] CLAUDE.md "본 절 결정" 신규 = (원칙 → 결정 → 금지) 구조 + 외부 포인터는 "상세는 `docs/X` 절" 형식만. 본 절 안 deep dive 본문 dump 0건.
- [5.4] ADR 신규 시 `docs/decisions/adr/README.md` 인덱스 갱신. 번호는 마지막 + 1 단조 증가 (재사용 0건).
- [5.5] `docs/README.md` 카테고리 표 + 디렉토리 트리 동시 갱신. 추가/제거 영구 문서 한 줄로 (위치 · 역할 · 수명).

정석 (4):
- [5.6] ADR 은 정정만 (덮어쓰기 금지). 결정 변경은 새 ADR + 이전 ADR `Status: Superseded by 00NN` (Withdrawn 이면 사유 1줄). retroactive 수정 0건.
- [5.7] CLAUDE.md F9 "변경 영향도 체크리스트" 에 본 feature 가 추가한 변경 유형이 빠졌으면 행 추가 (변경 유형 + 동시 갱신 위치).
- [5.8] 새 외부 의존(HTTP · 외부 큐) 도입 시 F6 "외부 의존 실패 모드 매트릭스"(`docs/reference/observability.md`)에 행 추가 (fail-open/close · timeout · 재시도).
- [5.9] CLAUDE.md "본 절 결정" 신규는 (a) 검사 가능(`rg`·`ruff`·테스트로 위반 발견) (b) F9 영향도 표시 (c) 위반 시 행동 명시 셋 다 충족. 새 F-policy 는 번호 단조 증가. 단순 조언(`...하는 게 좋다`)이면 채택 X — 결정·금지·매트릭스 형태만.

원칙 (3):
- [5.10] 모든 "상세는 X 절" 포인터가 가리키는 단일 진실이 실제 존재·정확. 검사: `rg '상세는|단일 진실|catalog: ' CLAUDE.md docs/` 추출 후 인용 경로·절 실재 확인.
- [5.11] `docs/temp/` 인용 0건 (재 grep — 양방향).
- [5.12] 영구 문서 간 양방향 의존 0건 (Stage 4 [4.17] 재검). CLAUDE.md → deep dive 단방향만.

도구:
- 수동 Read + Edit. 의식적 결정 단계 — 자동화 X.

산출물:
- `CLAUDE.md` · `docs/decisions/adr/*` · `docs/reference/*` · `docs/explanation/tradeoffs.md` · `docs/README.md` · `docs/reference/observability.md`.

통과 기준:
- 12 항목 OK. Stage 1·2·4 큐 모두 처리.

---

## 7. 종료

5 Stage 통과 + 사용자 최종 컨펌 → 종료.

commit·PR 자동 트리거 X — 사용자 명시 시 `/commit` · `/pr` 별도 발동.

종료 보고 형식:
- 누적 변경 파일 카탈로그 (코드 · 문서 · 테스트 분류).
- 사용자 결정 위임된 Warning · 알려진 갭 카탈로그.
- 다음 액션 후보 (commit · PR · 추가 검증) — 사용자가 먼저 언급할 때만 제안.
