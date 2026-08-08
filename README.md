# assessment-engine

고객사 내부 네트워크의 호스트 인벤토리·시계열 메트릭을 수집·저장하고, 규칙 기반으로 호스트별 자원 적정성(right-sizing)과 운영 신호(OS 지원종료·용량 부족 등)를 진단해 자원 재배치·용량 의사결정을 보조하는 B2B 내부 포털.

고객사 네트워크 안에 엔진이 설치되고, 각 호스트의 C 기반 에이전트가 인벤토리·메트릭을 수집해 MQ 에 직접 발행한다. Consumer 가 메시지를 소비해 DB 에 저장하고, web 이 그 데이터를 USE Method 규칙으로 분석해 보고서·대시보드로 낸다.

본 repo 는 엔진 애플리케이션, docker compose 배포 파일, 배포 대상 VM 에서 실행하는 운영 스크립트 3종(`bootstrap.sh`·`deploy.sh`·`rotate-secret.sh`)으로 구성된다.

---

## 아키텍처

```
 +------------------------------------------------------------------+
 |  Agent (C, separate repo: assessment-agent)                      |
 |  collector  : /proc scrape + inventory/metrics/error publish     |
 |  worker     : task.install consume + OS script exec + result     |
 +-----+----------------------------------------+-------------------+
       | inventory/metrics/error + task.result  ^ task.install.<agent_id>
       | (server.* / worker.result)             | (agent consumes)
       v                                        |
 +------------------------------------------------------------------+
 |  RabbitMQ                                                        |
 |  - assessment exchange       : server.inventory/metrics/error    |
 |  - assessment.tasks exchange : task.install.<agent_id>           |
 |                                + task.result -> worker.result    |
 |  - DLX/DLQ per exchange                                          |
 +--+----------------+----------------------------------------------+
    | server.*       | task.result
    v                v
 +-------------------------------+  +-------------------------------+
 |  Consumer (aio-pika)          |  |  Redis                        |
 |  - parse/idempot/persist      |  |  - cache / online TTL         |
 |  - time invariants            |  |  - idempotency SET NX         |
 |  - agent restart signals      |  |  - agent restart counter      |
 |  - task.result -> Task UPDATE |  |  - fail-open on RedisError    |
 +--------------+----------------+  +--------------+----------------+
                v                                  |
 +-------------------------------+                 |  GET/SET (cache-aside)
 |  TimescaleDB                  |                 |
 |  - 7 metric timeseries tables |                 |
 |  - server_inventory + history |                 |
 |  - tasks (audit log)          |                 |
 |  - diagnostic_jobs (report    |                 |
 |    snapshots)                 |                 |
 +--------------+----------------+                 |
                ^  read / emit / claim             |
                |                                  |
 +--------------+----------------------------------+---------------+
 |  FastAPI (uvicorn, port 8000)                                    |
 |  - SSR  : dashboard / detail / env+server report + history       |
 |  - REST : assessment / right-sizing / tasks / exports / metrics  |
 |  - charts : client-side fetch of REST (no push)                  |
 |  - rule-based right-sizing (right_sizing.py, USE Method)         |
 |  - report emit -> diagnostic_jobs (pending; worker generates)    |
 |  - publishes task.install (assessment.tasks exchange)            |
 |  - plain HTTP ; TLS termination is deployment infrastructure     |
 +------------------------------------------------------------------+
 +------------------------------------------------------------------+
 |  Worker (background process, assessment_engine.worker)           |
 |  - report job-claim (FOR UPDATE SKIP LOCKED) -> generate         |
 |    snapshot -> diagnostic_jobs (succeeded / failed)              |
 |  - install task reaper : deadline-overdue pending -> timeout     |
 |  - graceful shutdown within the configured drain budget          |
 +------------------------------------------------------------------+
```

---

## 루트 구성

저장소 루트에 평평하게 놓인 파일들이다. 대부분 도구가 루트에서 찾기 때문에 다른 곳으로 옮길 수 없다.

| 파일 | 무엇 | 왜 루트인가 |
|------|------|------------|
| `pyproject.toml` · `uv.lock` | 파이썬 의존성·빌드·lint 설정 | uv 와 빌드 백엔드가 프로젝트 루트로 인식 |
| `package.json` · `pnpm-lock.yaml` · `tsconfig.json` | 표현계층 타입 계약 도구 (dev·CI 전용, 번들·빌드 아님) | 검사 대상이 `src/` 안 정적 JS 라 도구와 대상을 갈라놓을 이유가 없다 |
| `Dockerfile` | 엔진 이미지 (web·consumer·worker·migrate 공용) | 빌드 컨텍스트가 루트 |
| `docker-compose.yml` | prod-safe base | compose 가 루트에서 자동 인식 |
| `docker-compose.override.yml` | dev 전용 (로컬 빌드·bind mount·핫리로드) | base 와 자동 머지 |
| `docker-compose.prod.yml` | prod file-secret overlay | 위와 같음 |
| `.env.example` · `.env.dev.example` | 배포·dev 환경변수 템플릿 | `cp` 해서 `.env` 로 쓰는 진입점 |
| `bootstrap.sh` | 배포 VM 1회성 구성 | raw URL 로 받는 파일이라 경로가 운영 절차에 고정 |
| `deploy.sh` | 엔진 rollout | 위와 같음 |
| `rotate-secret.sh` | DB·broker 계정 비밀번호 교체 | 위와 같음 |
| `Makefile` | 개발 명령 단일 진입점 (`make help`) | make 가 실행 디렉토리에서 찾는다 |
| `README.md` · `CONTRIBUTING.md` | 제품 소개 · 개발 참여 진입점 | GitHub 이 루트에서 렌더하고 PR 화면에 링크 |
| `AGENTS.md` · `.agents/` | 공용 에이전트 규약 · 작업 스킬 · 감사 프롬프트 | Claude Code와 Codex의 공용 정본 |
| `CLAUDE.md` · `.claude/` | Claude Code 호환 진입점 · 자동 발견 어댑터 | 공용 `AGENTS.md`와 `.agents/`만 가리키며 심링크를 쓰지 않음 |
| `.gitignore` · `.dockerignore` | 각 도구의 제외 목록 | 도구가 컨텍스트 루트에서 읽음 |

프론트엔드 설정이 섞여 보이지만 별도 프로젝트가 아니다. 번들러도 빌드 산출물도 없고, 서빙되는 JS 는 빌드를 거치지 않고 `src/assessment_engine/web/static/` 에서 그대로 나간다.

각 파일의 상세는 `docs/reference/docker.md`(compose·Dockerfile), `docs/guides/deploy.md`(배포 스크립트), `docs/reference/contracts/env.md`(환경변수), `docs/reference/web/type-contract.md`(타입 계약)가 갖는다.

---

## 스택

| 영역 | 기술 |
|------|------|
| 애플리케이션 | Python 3.14 · FastAPI · pydantic · uvicorn · aio-pika · SQLAlchemy async · asyncpg · Jinja2 · loguru · httpx |
| DB / 캐시 / 브로커 | TimescaleDB (PostgreSQL 16) · Redis 7 · RabbitMQ 3.13 |
| Schema 관리 | Alembic 단일 진실 |
| 진단 | 규칙 기반 right-sizing (USE Method, 도메인 모듈 `domain/right_sizing.py` — web·repository 공용) |
| 관측 | loguru `LOG_FORMAT=text\|json` (구조화 로그) |
| 패키징 | uv (빌드 백엔드 `uv_build`) |
| 정적 자원 | Chart.js · Cytoscape.js (네트워크 토폴로지) — 둘 다 vendored (`static/js/vendor/`, 내부망 offline) · 외부 `.js` + `defer` |

---

## 워크플로

- git flow — `feature/*`·`fix/*` → `develop` PR(squash) → `develop` → `main` PR(merge) → release(이미지 발행) → VM에서 `deploy.sh vX.Y.Z` 실행.
- 버전은 `pyproject.toml` 의 `version` 단일 진실 — 릴리즈 절차는 `docs/guides/release.md`. 브랜치·태그 보호와 required check 는 GitHub ruleset (`docs/guides/ci-setup.md`).

| workflow | 검증·작업 |
|----------|------|
| `pr-title-check.yml` | PR title 형식(Conventional Commits) + 작성 주체 메타데이터 부재 |
| `ci.yml` | lint(ruff+pyright+hadolint) · 단위 테스트 · 프론트 타입 계약, `main` 대상 PR은 wheel build · 통합 테스트 추가 |
| `alembic-check.yml` | ORM 모델과 migrations 의 drift (`alembic upgrade head` 후 `alembic check`) |
| `codeql.yml` | CodeQL SAST (Security 탭 alert) |
| `release.yml` | `main` push 시 멀티아치 엔진 이미지 빌드 → GHCR 발행 (산출물은 아래 "배포 산출물") |
| `image-scan.yml` | 주 1회 발행 이미지의 OS 패키지 CVE 점검 (Security 탭 alert) |

무엇이 언제 발화하는지는 `docs/reference/automation.md` 가 한 표로 갖는다 — 워크플로와 GitHub 플랫폼 기능(Dependabot alerts·secret scanning·ruleset)을 함께 본다.

---

## 배포 산출물

릴리즈가 내놓는 것은 GHCR 이미지 하나다 — cosign 서명·SBOM·provenance 가 이미지 attestation 으로 붙고, Alembic migrations 도 그 안에 동봉된다. 태그 체계·검증 명령은 `docs/guides/release.md`.

---

## 환경변수와 비밀번호

비밀번호를 제외한 설정은 `.env`로 주입한다. `make dev`는 `.env.dev.example`을 `.env`로 만들고 Compose 기본 파일명 규칙으로 dev 오버레이를 적용한다. `.env.example`은 `COMPOSE_FILE`로 prod 조합을 정한다. prod 비밀번호는 `secrets/` 파일로 주입한다.

비밀번호에는 기본값이 없다. 미설정·뻔한 값·채널 중복은 환경과 무관하게 기동 시점에 거부된다.

키 카탈로그·채널 우선순위·기동 검증 기준은 `docs/reference/contracts/env.md`, secret 파일 생성과 교체는 `docs/guides/deploy.md`.

---

## 개발 (dev)

dev = base + `docker-compose.override.yml` 핫리로드. 코드 수정이 컨테이너 restart 없이 반영된다.

```bash
make setup     # python + node 개발 의존성
make dev       # 기동 (web http://localhost:8000) — .env 없으면 dev 템플릿에서 생성
make help      # 명령 목록
```

작업 순서와 각 단계의 문서 위치는 `CONTRIBUTING.md` 가 갖는다.

---

## 운영 산출물

| 산출물 | URL / 설명 |
|--------|-----------|
| 모니터링 화면 | `/` (환경 개요 · 사이드바 "모니터링" 그룹) |
| 환경 보고서 (규칙 기반 진단 통합) | `/reports/environment?view=customer\|engineer` |
| 서버 보고서 (규칙 기반 진단 통합) | `/reports/servers?ids=...&view=customer\|engineer` |
| JSON Export | `POST /api/exports/inventory` |
| Install task | 서버 상세에서 원격 설치 task 발행 (`task.install`) |

---

## 배포 (prod)

배포 대상은 내부망 운영 VM 한 대다. outbound 만 열려 있어 밖에서 push 하지 않고 VM 이 GHCR 에서 이미지를 pull 한다. 빈 VM 을 `bootstrap.sh` 로 1회 구성하면 docker·cosign 이 설치되고 나머지 운영 스크립트도 함께 배치된다. 이후 배포는 버전을 올려 `main` 에 머지하고 VM 에서 `deploy.sh vX.Y.Z` 를 실행하는 두 단계를 반복한다.

`deploy.sh` 는 공급망 검증부터 health gate 까지를 한 번에 수행하고 실패하면 직전 정상 이미지로 되돌린다. 되돌리기도 이전 버전으로 같은 명령을 실행하면 된다. 단계별 상세는 `docs/guides/deploy.md` 3절.

절차·secret 배치·단일 호스트 수동 기동은 `docs/guides/deploy.md` 가 단일 진실이다.
