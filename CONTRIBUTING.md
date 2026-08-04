# 개발 참여 안내

이 저장소에서 작업하는 순서와 각 단계의 문서 위치를 모아 둔다. 명령은 `make help` 가, 상세는 `docs/guides/` 가 갖는다.

## 1. 환경 구성

전제는 셋이다 — Docker(compose plugin 포함), [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/). Python 과 Node 는 uv·pnpm 이 각자 준비한다.

```bash
make setup    # python + node 개발 의존성 설치
make dev      # 컨테이너 기동 (web http://localhost:8000)
```

`make dev` 는 `.env` 가 없으면 dev 템플릿에서 만든다. 첫 기동은 의존성 설치와 TimescaleDB 이미지 pull 로 5분쯤 걸린다.

상세는 `docs/guides/local-dev.md`.

## 2. 작업

코드를 고치면 컨테이너 재시작 없이 반영된다. 반영되지 않는 변경(의존성·Dockerfile·ORM 모델)의 목록과 대처는 `docs/guides/local-dev.md` "코드 변경 반영" 이 갖는다.

작업 유형별 진입점이다.

| 하려는 것 | 문서 |
|-----------|------|
| 코드 규약·정적 검사 | `docs/guides/conventions.md` |
| 테스트 작성·실행 | `docs/guides/testing.md` |
| DB 스키마 변경 | `docs/guides/migrate.md` |
| 의존성 추가·갱신 | `docs/guides/dependencies.md` |
| 클라이언트 JS 타입 계약 | `docs/reference/web/type-contract.md` |

서버 응답 타입(엔드포인트·ViewModel)을 고쳤으면 `make codegen` 으로 클라이언트 타입을 재생성해 같은 커밋에 넣는다. 빠뜨리면 워크플로가 막는다.

## 3. 검증

```bash
make lint         # ruff format --check + ruff check
make format       # ruff format 적용
make typecheck    # pyright + tsc(정적 JS)
make test         # 전체
```

로컬 커밋은 lint 만 건다. develop PR 에서 코드 리뷰·테스트와 함께 문서·ADR 을 맞추고, 승격 직전 develop PR 이 릴리즈 단위 문서 검증까지 한다. main PR 은 통합 테스트와 커밋 형식만 본다. 배치 근거는 `docs/guides/pre-pr-checklist.md` 0절이 갖는다.

## 4. 커밋과 PR

feature 브랜치에서 작업하고 `develop` 으로 PR 을 낸다. `main` 직접 커밋은 하지 않는다.

커밋 메시지는 Conventional Commits 의 type 접두를 쓰고 현재 상태를 선언형으로 적는다. 과거형이나 경위 서술은 쓰지 않는다.

```
feat: 환경 보고서에 자원 적정성 분포를 넣는다
fix: 자식 시계열 INSERT 가 충돌 대상을 지정하게 한다
```

PR 발행 전 체크리스트는 `docs/guides/pre-pr-checklist.md`.

## 5. 문서를 고칠 때

문서는 목적으로 나뉜다 — 고치기 전에 그 사실이 어느 목적에 속하는지 `docs/README.md` 에서 확인한다.

같은 사실을 두 문서에 쓰지 않는다. 이미 있으면 그 문서를 가리킨다. 관리 규칙 전체는 `docs/README.md`.

## 6. 자주 쓰는 명령

`make help` 가 목록을 낸다. 각 명령의 옵션과 맥락은 위 표의 문서가 갖는다.
