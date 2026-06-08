# ADR 0035 — compose base(prod) + override(dev) 분리, 빌드 없는 prod base 를 릴리즈 에셋으로

상태: Accepted (2026-06-08)

## Context

infra 배포 파이프라인(별도 레포)은 "GHCR 선빌드 이미지 pull -> compose up" 모델이라, 릴리즈로 받은 compose 가 빌드 없이 단일 인스턴스 VM 에서 그대로 떠야 한다. infra 인바운드 요청 요건:

- 앱 서비스가 `build:` 없이 GHCR 이미지 pull 만. 소스 bind mount 없음(VM 에 `./src` 없어 빈 디렉토리가 패키지를 덮으면 컨테이너 즉사).
- postgres·rabbitmq 영속 경로를 외부 볼륨(Cinder /mnt/pgdata·/mnt/mqdata)에 bind 가능.
- diagnostic-worker 포함, Ollama 좌표는 env 주입식.
- GHCR 접근 방식·env 정규 키·에셋명·이미지 태그 규약 확정.

제약 두 가지가 분리 방식을 정한다:

- Dockerfile 은 단일 multi-stage 이미지를 CI/release·systemd·k8s·compose 가 공유한다(dev-prod parity, release artifact 의미). 따라서 dev/prod 차이는 Dockerfile 이 아니라 compose 레이어에만 둔다.
- 릴리즈가 첨부하는 compose 가 곧 prod 진입점이다. 따라서 그 base 파일이 prod 정의여야 하고, dev 편의는 그 위에 얹힌다.

## Decision

루트 `docker-compose.yml` 은 prod-safe base, `docker-compose.override.yml` 은 dev override. `docker compose up` 은 둘을 자동 머지한다. base 가 곧 릴리즈 에셋 = 빌드 없는 pull-and-run prod compose.

### 1. base + override 구성

base `docker-compose.yml` (prod-safe, 릴리즈 에셋):
- 앱 서비스 4종(web·consumer·migrate·diagnostic-worker) `build:` 키 없음, `image:` 만.
- `image: ${ENGINE_IMAGE:-ghcr.io/z-converter-assessment/assessment-engine:__ENGINE_VERSION__}` — immutable pin. `__ENGINE_VERSION__` 은 release CI 가 태그 semver(예 `0.3.1`)로 치환(5절). `:local`·`:latest` fallback 없음.
- bind mount 없음. `WEB_RELOAD=false`, consumer/diagnostic 은 `python -m` 직접 실행.
- 영속 볼륨 env 바인딩: `${PGDATA_HOST:-postgres_data}:/home/postgres/pgdata/data`, `${MQ_DATA_HOST:-rabbitmq_data}:/var/lib/rabbitmq`. 미주입 시 named volume, host 절대경로 주입 시 bind.
- diagnostic-worker 포함. ollama 서비스 미포함 — `OLLAMA_BASE_URL`·`OLLAMA_MODEL` env 주입식. 미주입/미도달 시 narrative pending(graceful degrade).

override `docker-compose.override.yml` (dev only):
- 앱 서비스 `build: context: .`(+`APP_VERSION` arg), `image: ${ENGINE_IMAGE:-assessment-engine:local}` — 로컬 빌드가 base 핀을 덮음.
- `./src/assessment_engine`·`./migrations` bind mount, `WEB_RELOAD=true`, consumer/diagnostic watchfiles 래퍼 — hot reload.

### 2. 기동 경로 (레포 루트 기본 = dev)

- dev / 퀵스타트 / 평가(소스 clone): `docker compose up` -> base + override 자동 머지 = 로컬 빌드·bind mount·dev-grade. 레포 루트의 무설정 기본은 dev 다 — override 가 Docker 의 dev 편의 관례이자, base 가 prod 라 dev 가 그 위에 얹히는 자연 귀결.
- prod (infra): `gh release download` 로 base `docker-compose.yml` 만 받음(override 미첨부) -> 자동 머지 대상 없어 base 단독 = GHCR pull-and-run. `ENGINE_IMAGE`(선택)·`PGDATA_HOST`·`MQ_DATA_HOST`·`OLLAMA_*` 주입.
- 소스에서 prod 검증: `docker compose -f docker-compose.yml up` (override 제외).
- `dev/dev-up.sh` 는 `COMPOSE_FILE` 미지정(base+override 자동 머지), `COMPOSE_PROJECT_NAME=dev` 고정.

### 3. GHCR 접근 — private + fine-grained PAT

이미지 패키지 private 유지(소스 private 과 경계 일치). infra 가 `read:packages` 스코프 fine-grained PAT(엔진 패키지 한정)를 secret 으로 주입해 pull — infra 워크플로 기본 `GITHUB_TOKEN` 은 cross-repo pull 불가. 토큰 발급·로테이션은 조직 admin/infra, 엔진 레포 책임은 contract 문서화(`release.md`·`deployment.md`).

정정 (2026-06-08): 본 3절 결정(private + PAT) 철회 — 이미지 패키지를 public 으로 전환. 사유: 고객사·infra 가 토큰 없이 pull (cross-repo PAT 발급·로테이션 부담 제거), 내부망 B2B 배포라 이미지 자체 민감도 낮다고 판단. pull 토큰 불요, cosign keyless 서명 검증은 그대로 유지. 실제 visibility 변경은 GitHub 패키지 설정(Package -> Settings -> Change visibility -> Public) — repo 코드로 강제 불가.

### 4. env 정규 키

정규 키는 `RABBITMQ_*` (`config.py` 필드명 대문자, `env_prefix` 없음). `RABBITMQ_TASK_EXCHANGE`·`RABBITMQ_ROUTING_KEY_TASK_RESULT` 등. infra 의 `WORKER_*` 키는 미인식 — infra 가 `.env.j2` 를 정규 키로 맞춘다(엔진은 alias 미수용, 검증 단일 경로 #F3). task prefix 3종은 default 보유라 선택이나 agent 발행 값과 일치 필수.

### 5. 릴리즈 에셋

- env 카탈로그 에셋명 `.env.example` 단일(점 prefix). 정정 (ADR 0038, 2026-06-08): GitHub Release 가 점 prefix 를 `default.env.example` 로 변환하므로 에셋명을 `env.example`(점 없이)로 변경.
- `release.yml` 이 base `docker-compose.yml` 의 `__ENGINE_VERSION__` 을 태그 semver 로 sed 치환 후 첨부. GHCR 이미지 태그·wheel 버전은 `v` 없는 semver(`0.3.1`), git tag 만 `v0.3.1`.
- `SHA256SUMS` 에 compose·env 포함(무결성 검증 대상).

## Consequences

- base 가 prod-safe 라 릴리즈 첨부 compose 가 그대로 pull-and-run prod 진입점 — 추가 에셋·소스 clone 불요. 공통부가 base 한 곳이라 dev/prod drift 가 구조적으로 작다.
- base 는 빌드 없는 pull-and-run 까지. hardened prod(APP_ENV=prod·강 secret·LOG_FORMAT=json·HTTPS ingress)는 base 가 강제하지 않고 infra env 주입으로 달성 — 소스 clone bare up 은 dev-grade(weak secret 허용)이고, `APP_ENV=prod` 주입 시 `_validate_prod_*` 가 weak default 를 거부해 이중 안전장치.
- GHCR 는 public (3절 정정) — 토큰 없이 pull, infra 토큰 관리 부담 0. 이미지 레이어 노출은 내부망 B2B 라 감수.

## 관계

- ADR 0033 의 "override 파일 0·정의 1곳" 결정을 supersede(루트 compose 가 prod-safe base 로 역할 전환, dev 편의는 override 로 이동). 단일 이미지(Dockerfile)·퀵스타트 가치는 존속.
- ADR 0012 1-4절(wheel + GitHub Release)·ADR 0017(image artifact)·ADR 0030(tag-derived 버전) 위에 prod-safe compose base 를 얹는다.
- #F9 동시 갱신: `docker-compose.yml`·`docker-compose.override.yml`·`dev/dev-up.sh`·`release.yml`·`docs/operations/{release,deployment,env}.md`·`docs/development/docker.md`·`README.md`·CLAUDE.md #A0·ADR 0033 정정 note.
