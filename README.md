# assessment-engine

온프레미스 서버 인벤토리·메트릭을 수집·저장하고, 수집된 데이터를 기반으로 자원 사용량을 진단해 운영 의사결정을 보조하는 B2B 내부 포털.

고객사 네트워크 내에 서버 엔진이 설치되고, 네트워크 내 각 서버의 C 기반 에이전트가 메트릭을 수집해 MQ에 직접 발행한다. Consumer가 메시지를 소비해 DB에 저장하고, web 이 수집된 데이터를 규칙 기반(USE Method right-sizing)으로 분석해 진단·보고서를 생성한다. 운영자는 web UI 에서 모니터링 화면·보고서·JSON Export·원격 설치 task 산출물을 활용해 다음 단계 의사결정을 진행한다.

본 repo 는 엔진을 런타임에 띄우는 것까지만 다룬다 (애플리케이션 + 루트 docker-compose(prod base + dev override 핫리로드)). agent 가 붙는 VM·하드닝 prod 운영 (IaC — Terraform · Ansible 등 · systemd unit · k8s manifest) 은 본 repo 범위 밖 — 산출물·contract 를 외부 인프라에 통합.

---

## 아키텍처

```
 +------------------------------------------------------------------+
 |  Agent (C, separate repo: assessment-agent)                      |
 |  collector  : /proc scrape + inventory/metrics/error publish     |
 |  worker     : task.install consume + OS script exec + result     |
 +-----+----------------------------------------+-------------------+
       | inventory/metrics/error + task.result  ^ task.install.<id>
       | (server.* / worker.result)             | (agent consumes)
       v                                        |
 +------------------------------------------------------------------+
 |  RabbitMQ                                                        |
 |  - assessment exchange       : server.inventory/metrics/error    |
 |  - assessment.tasks exchange : task.install.<composite_id>       |
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
 |  - task.result -> Task UPDATE |  |  - PUB/SUB metrics.events     |
 +--------------+----------------+  +--------------+----------------+
                v                                  |
 +-------------------------------+                 |  SUBSCRIBE
 |  TimescaleDB                  |                 |
 |  - 5 timeseries tables        |                 |
 |  - server_inventory + history |                 |
 |  - tasks (audit log)          |                 |
 |  - diagnostic_jobs (report    |                 |
 |    snapshots)                 |                 |
 +--------------+----------------+                 |
                ^  read / report emit              |
                |                                  |
 +--------------+----------------------------------+---------------+
 |  FastAPI (uvicorn, port 8000)                                    |
 |  - SSR  : dashboard / detail / env+server report + history       |
 |  - REST : tasks / exports                                        |
 |  - SSE  : live metrics (Consumer PUB -> Redis -> SSE)            |
 |  - rule-based right-sizing (recommendation.py, USE Method)       |
 |  - report emit -> diagnostic_jobs static snapshot                |
 |  - publishes task.install (assessment.tasks exchange)            |
 |  - plain HTTP (dev) ; prod = external ingress (out of scope)     |
 +------------------------------------------------------------------+
```

상세 흐름: `docs/architecture/` (모듈별 deep dive).

---

## 스택

| 영역 | 기술 |
|------|------|
| 애플리케이션 | Python 3.12 · FastAPI · uvicorn · aio-pika · SQLAlchemy async · asyncpg · Jinja2 · loguru · httpx |
| DB / 캐시 / 브로커 | TimescaleDB (PostgreSQL 16) · Redis 7 · RabbitMQ 3.13 |
| Schema 관리 | Alembic 단일 진실 |
| 진단 | 규칙 기반 right-sizing (USE Method, `recommendation.py` — web 인라인 계산) |
| 관측 | loguru `LOG_FORMAT=text\|json` (구조화 로그) |
| 패키징 | uv + hatchling. CI 산출물 = Python wheel + Docker image (GHCR) |
| 정적 자원 | Chart.js (CDN) · Cytoscape.js (네트워크 토폴로지, vendored) · 외부 `.js` + `defer` |
| 에이전트 (별도 repo) | C 단일 바이너리, RabbitMQ 직접 publish |

---

## CI 파이프라인

- git flow — `feature/*`·`fix/*` → `develop` PR(squash) → `develop` → `main` PR(merge) → `main`에 `v*` tag push → release (ADR 0030).
- 버전은 git tag 단일 진실 (hatch-vcs가 빌드 시 derive) — repo에 버전 미저장, bump 커밋 없음. branch protection + Conventional Commits PR title 강제.

| workflow | trigger | 검증·작업 |
|----------|---------|------|
| `pr-title-check.yml` | PR (target main/develop) opened·edited | PR title이 Conventional Commits 형식 (`feat:`·`fix:`·`docs:` 등) 강제 |
| `ci.yml` | develop PR · main PR · develop push | lint(ruff+hadolint) → test-unit → test-integration (develop push·main PR), wheel build (main PR) |
| `alembic-check.yml` | develop PR · main PR | ORM·migrations 라운드트립 정합 |
| `codeql.yml` | main PR · 주간 cron | CodeQL SAST (SQL injection·secret leak·XSS 정적 분석, Security 탭 alert) |
| `release.yml` | `main`에 tag `v*` push · workflow_dispatch | uv build wheel + sdist (버전=tag, hatch-vcs) + SHA256SUMS + SBOM + Sigstore signature → GitHub Release + GHCR image(multi-arch) 자동 첨부 |

본 repo는 CI 영역만 (코드 quality + artifact 생성). CD(배포·secret 주입·롤백)는 외부 인프라 책임.

---

## 배포 산출물

semver tag `v*` push 시 릴리즈가 내놓는 산출물. 배포는 compose 기준(아래 배포 절) — wheel·image·systemd 등 다른 채널·토폴로지는 `docs/operations/deployment.md`.

| 산출물 | 위치 · 참고 문서 |
|--------|--------------|
| Python wheel + sdist + SHA256SUMS | GitHub Release (semver tag `v*`) · `docs/operations/release.md` |
| Docker image (multi-arch `amd64,arm64`) | GHCR `ghcr.io/z-converter-assessment/assessment-engine:0.1.0`+`:0.1`+`:0`+`:latest` (semver tag `v0.1.0` -> 이미지 태그는 `v` 없는 `0.1.0`, metadata-action) · ADR 0017 |
| SBOM (CycloneDX JSON) + Sigstore signature | wheel·sdist에 첨부 — 외부 인프라가 의존성 audit + `cosign verify-blob` 무결성 검증 |
| SBOM (SPDX, BuildKit attestation) + cosign keyless signature | image 첨부 — `cosign verify ghcr.io/z-converter-assessment/assessment-engine:0.1.0` 무결성 검증 |
| Alembic migrations·alembic.ini | wheel·image 동봉 (`hatch.force-include`) · `docs/operations/release.md` |
| `docker-compose.yml` (prod-safe base) + `env.example` | GitHub Release 첨부 — 빌드 없는 pull-and-run prod compose (build 키 없음, GHCR 이미지 핀). `docker compose up -d` 로 pull |
| 환경변수·secret contract | `docs/operations/env.md` |
| systemd unit reference | `docs/operations/deployment.md` 4절 |
| install·실행 절차 | `docs/operations/deployment.md` |

---

## 개발 (dev)

dev = base + `docker-compose.override.yml` 핫리로드. 코드 수정이 컨테이너 restart 없이 반영된다.

```bash
cp env.dev.example .env
docker compose up -d      # web http://localhost:8000
docker compose down -v    # 종료 (데이터 삭제)
```

IDE 자동완성·테스트 (compose 만 띄울 땐 불필요. 전제 `uv` 0.4+):

```bash
uv sync --group dev               # .venv 생성 + dev 그룹(pytest·ruff·types)
uv run pytest tests/unit/         # 단위
uv run pytest tests/integration/  # 통합 (testcontainers)
uv run ruff check .               # lint
uv run alembic check              # ORM·migrations 정합
```

상세: `docs/development/docker.md` · `testing.md`. agent 가 붙는 VM 은 본 repo 범위 밖(OpenStack 공급).

---

## 운영 산출물

| 산출물 | URL · 참고 문서 |
|--------|--------------|
| 모니터링 화면 | `/` (환경 개요 · 사이드바 "모니터링" 그룹) · `docs/products/dashboard.md` |
| 환경 보고서 (규칙 기반 진단 통합) | `/reports/environment?view=customer\|engineer` · `docs/products/environment-report.md` |
| 서버 보고서 (규칙 기반 진단 통합) | `/reports/servers?ids=...&view=customer\|engineer` · `docs/products/server-report.md` |
| JSON Export | `/api/exports/inventory` · `docs/products/json-export.md` |
| Install task | `docs/products/install-task.md` |

---

## 배포 (prod)

prod = base + `docker-compose.secrets.yml`(file-secret). 비번을 `./secrets/*` 파일로 주입한다 — 컨테이너 env 에 안 뜬다. 빌드 없이 GHCR 이미지를 pull.

```bash
gh release download <tag> -R z-converter-assessment/assessment-engine -D /tmp/ae
cd /tmp/ae && cp env.example .env          # COMPOSE_FILE 포함(base+secrets) · 평문 비번 없음

mkdir -p secrets                            # 비번 파일 (강 random, 권한 600)
printf '%s' "$(openssl rand -base64 32)" > secrets/postgres_password
printf '%s' "$(openssl rand -base64 32)" > secrets/rabbitmq_password
printf '%s' "$(openssl rand -base64 24)" > secrets/pgadmin_password
chmod 600 secrets/*

docker compose up -d                        # base+secrets pull-and-run. web http://localhost:8000
```

`APP_ENV=prod` 라 secret 부재·weak 면 기동 거부(fail-fast). GHCR public — 토큰 없이 pull. 외부 디스크 볼륨은 `PGDATA_HOST`/`MQ_DATA_HOST` 주입(선택).

secret 배치 상세: `secrets/README.md`. wheel+systemd·멀티노드 등 다른 토폴로지: `docs/operations/deployment.md`.

---

## 문서

| 디렉토리 | 용도 |
|----------|------|
| `docs/README.md` | 카테고리·파일 인덱스 — 어떤 문서를 언제 보는지 길잡이 |
| `docs/development/` | 본 repo 안 dev 작업·코드 규약 (docker · dependencies · testing · conventions · wrap-up · github-setup) |
| `docs/operations/` | 외부 인프라가 활용할 contract (release · deployment · env · alembic · observability) |
| `docs/products/` | 운영 산출물 의의·근거 (dashboard · 환경 보고서 · 서버 보고서 · JSON Export · Install task) |
| `docs/architecture/` | 컴포넌트별 deep dive (agent · consumer · rabbitmq · redis · right-sizing · db · web) |
| `docs/adr/` | Architecture Decision Records (0001~) — "왜 이렇게 결정했나" + 트레이드오프 |
| `docs/tradeoffs.md` | 의식적 설계 선택과 한계 (T1~T17) |
