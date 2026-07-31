# 미결 작업 — 결정·검증·부채

임시 자료 — 내부 인수인계 전용, 외부 공유 대상 아님. 항목이 다 소진되면 삭제한다.

다음 세션은 이 문서만 읽고 시작할 수 있게 썼다. 순서는 의존 관계로 정했다 — 앞 항목을 정하지 않으면 뒤 항목의 작업 범위가 안 정해진다.

```bash
git fetch --all --prune
git checkout develop && git pull
cat docs/temp/pending-work.md
```

---

## 현재 상태 (2026-07-31 기준)

PR #112 `refactor: 배포·설정 채널을 표준 배치로 정리` 가 develop 에 squash 머지됐다 (`5e75c3f`). 열린 PR 없음. develop 은 main 대비 6커밋 앞서 있다.

머지 전 develop CI 5종이 통과했다. 두 job 은 develop PR 에서 안 돈다 (`if: github.base_ref == 'main'`).

```
pass       ruff + hadolint            18s
pass       pytest (unit)              5m2s     806개
pass       frontend typecheck         20s
pass       alembic-check              47s
pass       pr title + metadata        4s
skipping   wheel build                main PR 전용
skipping   pytest (integration)       main PR 전용
```

PR #112 가 한 일은 넷이다. compose 를 base + dev override + prod overlay 3파일 표준으로 세웠고, 비밀번호 검증에서 환경 분기를 걷어냈고(기본값 없는 필수 필드 + 뻔한 값 거부 + secret/env 채널 충돌 거부), rollout 성공 판정을 전체 서비스로 넓혔고, 문서·주석을 코드 실측에 맞췄다.

---

## S1. 검증 — 아직 안 돌려본 것이 develop 에 들어가 있다

develop CI 는 unit 까지만 본다. 아래 셋은 이번 작업에서 한 번도 실행되지 않았고, main 승격 PR 에서 처음 돈다. 거기서 깨지면 승격이 막히므로 미리 확인한다.

### S1-1. integration 테스트

코드 작업 규약이 명시 요청 없는 pytest 실행을 금지해서 이번 브랜치에서 한 번도 안 돌렸다. 그래서 PR #112 가 integration 을 깨뜨렸는지 아무도 모른다.

의심 지점이 하나 있다. 비밀번호를 기본값 없는 필수 필드로 바꿨는데, integration 픽스처는 alembic 을 subprocess 로 부르면서 env 를 넘긴다. 그 경로가 새 제약을 만족하는지 확인이 필요하다.

```bash
uv run pytest tests/integration -q
```

testcontainers 가 postgres 를 띄우므로 docker 가 필요하다.

### S1-2. prod 경로 실기동

PR #112 의 절반이 prod 배포에 관한 것인데 검증은 dev 기동까지다. 실제로 안 돌려본 것.

| 대상 | 지금까지 한 것 |
|------|--------------|
| `COMPOSE_FILE=...prod.yml` 기동 | `docker compose config` 로 머지 결과만 확인 |
| `bootstrap.sh` | 코드 읽기 + secret 목록 파싱 부분 재현 |
| `deploy.sh` 전체 시퀀스 | health gate 함수만 떼어 실제 컨테이너에 적용 |
| rollback 경로 | 안 함 |

최소한 secret 파일 생성부터 web 200 까지는 로컬에서 한 번 통과시키는 것이 좋다.

```bash
install -d -m 0700 secrets
printf '%s' "$(openssl rand -base64 32)" > secrets/postgres_password
printf '%s' "$(openssl rand -base64 32)" > secrets/rabbitmq_password
chmod 644 secrets/*
cp .env.example .env
# .env 에 ENGINE_IMAGE 를 실제 태그로 핀하거나 로컬 빌드 이미지로 바꾼다
docker compose up -d && curl -sS localhost:8000/health
```

`.env.example` 은 `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` 을 담고 있어 dev override 가 빠진다. 끝나면 `secrets/` 를 지운다 (gitignore 대상이지만 평문이다).

### S1-3. wheel build

CI 에서 아직 안 돌았다. 로컬에서 `env -i` 로 세 진입 모듈 import 를 확인했지만 CI 기준은 아니다. `uv build` + fresh venv install + import 까지 로컬에서 해두면 main PR 에서 놀랄 일이 준다.

---

## S2. 결정 — pyright 를 CI 게이트로 걸 것인가

지금 걸면 모든 PR 이 실패한다. 396건이 기존 부채라 새 PR 이 아무 잘못을 안 해도 빨간불이 된다. pyright 에는 baseline 기능이 없어서 "지금 상태를 기준선으로 두고 새로 늘어난 것만 막는다"를 도구가 지원하지 않는다.

396건의 성격.

| 갈래 | 건수 | 대표 유형 |
|------|------|----------|
| tests | 327 | `reportArgumentType` 277 |
| src | 69 | `reportArgumentType` 43 · `reportAttributeAccessIssue` 21 |

tests 쪽은 대부분 같은 모양이다. 픽스처를 dict 리터럴로 넘기면 값 타입이 union 으로 추론되는데 함수는 구체 타입을 받는다. 반환 타입에 `| None` 이 붙은 함수 결과에 바로 속성 접근하는 것도 걸린다. 테스트 작성자는 None 이 아닌 걸 알지만 검사기는 모른다. 실제 버그가 아니라 테스트를 쓰는 방식에서 구조적으로 나온다.

src 69건은 절반이 도구 한계고 절반이 진짜다. SQLAlchemy 스텁이 UPDATE/DELETE 결과 타입을 표현 못 해서 나는 `rowcount` 7건, Protocol 선언이 없어 나는 Mixin 상호 참조 3건은 전자다. `float` 를 `int` 파라미터에 넘기는 10건, `str` 을 Literal 타입에 넘기는 6건은 후자다.

경로 셋 중 하나를 고른다.

1. src 69건을 먼저 정리하고 검사 범위를 `src` 로 좁혀 건다. 하루치 작업이고 실제 불일치를 고치는 것 자체가 값이 있다. 단점은 그동안 테스트 코드가 검사 밖.
2. 전부 고치고 건다. 327건이 픽스처 방식 문제라 "테스트를 어떻게 쓸 것인가"(S5-2)를 먼저 정해야 한다.
3. baseline 을 지원하는 검사기로 바꾼다. 현재 상태를 파일로 고정하고 신규만 막는다. 도구 교체라 ADR 감이고 실제 동작 확인이 선행.

추천은 1번이다. 정하고 나면 그 다음 브랜치의 작업 범위가 확정된다.

---

## S3. 결정 — 릴리즈

버전이 `1.2.1` 에 멈춰 있다. develop 이 main 대비 6커밋 앞서 있고 fix 가 여럿이라 bump 가 필요하다.

두 가지를 함께 정한다.

- 얼마로 올릴 것인가. breaking 이 있는지가 관건이다 — 비밀번호를 기본값 없는 필수 필드로 바꾼 것은 기존 배포에 `.env` 나 secret 파일이 없으면 기동이 멈춘다는 뜻이라 운영자 관점에서 breaking 에 가깝다. minor 로 올릴지 patch 로 갈지 판단이 필요하다.
- 언제 올릴 것인가. 태그가 릴리즈 워크플로를 트리거하므로 develop -> main PR 직전에 bump 커밋을 넣는 것이 이 저장소의 흐름이다.

main 승격 PR 은 develop PR 보다 게이트가 강하다 — integration 의무 실행, 전 커밋 Conventional Commits, 문서 정합, 결정 변경 시 ADR. S1 을 미리 해두면 이 단계가 가벼워진다.

---

## S4. 부채 — 순서 무관, 언제든

### S4-1. pyright 잔여

S2 결정에 종속. src 69건 정리는 대략 이렇게 갈린다.

- `Result.rowcount` 7건 — `CursorResult` 캐스팅 또는 ignore + 사유
- Mixin 상호 참조 3건 — Protocol 선언
- `float` -> `period_days: int` 10건 — 실제로 SQL 에 float 가 간다. 의도인지 확인 후 시그니처 또는 호출부 수정
- `str` -> Literal 6건 — 좁히기 누락
- `ServerDetail` / `ServerDetailResponse` 혼용 — 타입 정리

### S4-2. 규약 절 번호 인라인 인용 106건

주석 규약은 "그 절이 왜 여기 적용되는지 자명하지 않을 때만" 남기라고 한다. 106건은 최소화가 아니다. 다만 파일마다 자명한지 판단이 필요해 일괄 처리가 안 된다. 완료 기준(S5-1)이 먼저다.

### S4-3. 다른 가이드 문서의 목적 혼선

`local-dev.md` 를 절차(guides)와 사양(reference)으로 갈랐다. 같은 상태인 문서가 더 있을 수 있다 — 배포·마이그레이션 가이드가 후보다. 문서 감사에서 나온 지적이 전부 "compose 를 옮겨 적은 구간"에서 나왔으므로, 설정 파일 내용을 전사한 자리를 찾는 것이 실마리다.

### S4-4. 주석 전수 미검토

파이썬 파일 137개 중 실제로 읽고 판단한 것은 주석 밀도 상위 몇 개다. 나머지는 grep 패턴에 걸린 것만 고쳤다. 패턴으로 안 잡히는 것 — 코드를 옮겨 적은 주석, 정책 서술이 섞인 주석 — 은 남아 있다.

### S4-5. `docs/temp/handoff.md`

이전 세션의 인수인계 문서인데 미결 항목 하나가 남아 있다 (마무리 가이드 Stage 1 체크리스트 압축). 처리하거나, 그 항목을 본 문서로 옮기고 삭제한다.

### S4-6. collect review S3

원래 목표였던 작업이다. consumer 부트스트랩과 inventory 핸들러 리뷰. `docs/temp/collect-review-notes.md` 에 앞선 단계 기록이 있다. 시작도 안 했다.

---

## S5. 모호한 것 — 정의가 없어서 진행이 안 되는 것

### S5-1. "주석 원칙 적용" 의 완료 기준

이번에 기계적으로 검출되는 위반(회고 서술·주석 처리된 코드)은 처리했지만, 읽어야 판단되는 것은 상위 몇 파일만 봤다. 무엇을 하면 "끝"인지가 정의되어 있지 않다.

정할 것: 전수 검토를 하는가, 아니면 변경되는 파일에서만 적용하는가. 후자면 마무리 워크플로에 항목으로 넣어야 강제된다.

### S5-2. 테스트 픽스처의 타입 표현 방식

S2 에서 2번을 택하면 반드시 먼저 정해야 한다. dict 리터럴을 계속 쓰되 함수 시그니처를 넓힐지, TypedDict 를 도입할지, 팩토리 함수를 만들지. 327건의 처리 방식이 여기서 갈린다.

### S5-3. `docs/temp` 파일의 수명

디렉토리 규약은 "받은 편지함 — 처리 후 비운다"인데, 실제로는 인수인계 문서가 계속 쌓인다. 지금 6개가 있다. 어떤 것이 아직 살아 있고 어떤 것이 처리 완료인지 표시가 없다.

정할 것: 각 파일의 처분. 최소한 처리 완료된 것은 지운다.

---

## 순서 요약

```
S1 검증 (integration -> prod 경로 -> wheel)
   |   main 승격의 선행. 여기서 깨지면 승격이 막힌다
   v
   +--> S2 pyright 게이트 경로 결정 --> S4-1 작업
   |         ^
   |         +-- (2번 선택 시) S5-2 선행
   |
   +--> S3 릴리즈 결정 (버전 + 승격 시점)
   |
   +--> S4-2~S4-6 부채 (S5-1 이 S4-2·S4-4 의 선행)
```

S1 만 순서가 강제된다. S2 와 S3 는 서로 독립이라 어느 쪽을 먼저 해도 된다. S4 는 언제든.
