# 환경변수 관리 (Environment Variables)

env 관리 단일 진실 — 정책·secret 채널·주입 흐름·키 카탈로그·운영 체크리스트. 외부 인프라가 본 엔진을 prod 운영할 때 충족해야 할 contract 다.

정책 출처: AGENTS.md #A0 (외부 인프라 책임 분리) · #F8 (secret·PII 노출 금지). 본 repo 는 결과만 검증하고 secret 주입 채널 자체는 외부 인프라 자유.

---

## 1. 원칙

12-Factor III(Config) 를 따른다 — 환경별 값을 코드에 박지 않고 이미지 하나를 모든 환경에서 쓴다. 비밀번호는 기본값을 두지 않고 뻔한 값을 거부하며(6절), secret 은 git·이미지에 들어가지 않는다(14절).

---

## 2. config vs secret 분류

같은 환경변수처럼 보여도 secret 과 일반 config 는 다르게 다뤄야 한다.

| 분류 | 정의 | 본 프로젝트 예시 | 보관 |
|------|------|----------------|------|
| config | 노출돼도 시스템이 즉시 위태롭지 않은 운영 값 | `POSTGRES_HOST`·`RABBITMQ_VHOST`·`WEB_PORT`·`APP_ENV`·`LOG_FORMAT` | `.env` 평문 OK, git 커밋 가능 (`.env.example`) |
| secret | 노출 시 즉시 무단 접근 가능한 자격 | `POSTGRES_PASSWORD`·`RABBITMQ_PASSWORD`. API token·TLS key 도 같은 분류 | dev 한정 `.env` 평문, prod 는 외부 인프라 자유 채널 |

경계 케이스:
- `POSTGRES_USER` — 보통 config. 단 user 자체가 권한 분리 키이면 secret. 본 프로젝트는 config 분류하되 뻔한 값(password·admin·root·changeme)은 거부한다 — `assessment`(dev 카탈로그 값)는 허용.
- `ZDM_DEFAULT_IP` / `ZDM_DEFAULT_USER` — config (secret 아님, 노출 무해). startup 거부 안 함 — 잘못된 ZDM 발행은 런타임 (`HttpZdmPackageResolver` 메타 도달 실패 시 503) + agent host whitelist (`WORKER_DOWNLOAD_ALLOWED_HOSTS`, `url_not_allowed` reject) 가 방어 (빈값 default 정상 동작, startup 거부 없음).

규칙: config 인지 secret 인지 헷갈리면 secret 으로 간주. 잘못 분류해 secret 을 평문 노출하는 비용이 그 반대보다 크다.

코드 측 의무 (AGENTS.md F8):
- secret 으로 분류된 필드는 `Settings` 에서 `SecretStr` 타입 의무 — `__repr__` 이 자동 마스킹해 로그·예외·디버거 출력에 평문 노출 차단.
- 사용 시점에서만 `.get_secret_value()` 로 평문 추출 — 변수에 담아 재사용 금지 (마스킹 우회 위험).

---

## 3. 우선순위 체인 (pydantic-settings)

```
[lowest]  코드 default (config.py)
       v
       secrets_dir 파일 (<SECRETS_DIR>/<field_name>, default /run/secrets)
       v
       .env 파일 (cwd 기준 — 컨테이너 안에서는 /app/.env)
       v
       OS 환경변수 (systemd Environment / docker-compose env / orchestrator inject)
[highest] 명시적 init kwargs (테스트용)
```

`SECRETS_DIR` env 로 secrets 디렉토리 경로 override 가능. 디렉토리가 존재할 때만 활성 — dev 호스트에선 보통 None 으로 file read 단계 skip.

secret 파일명은 pydantic 필드명과 정확히 일치 의무. 외부 인프라가 file 채널이면 `<SECRETS_DIR>/postgres_password` → pydantic `postgres_password: SecretStr` 자동 매핑. env 채널이면 `POSTGRES_PASSWORD=<value>` 환경변수 (env 가 secrets_dir 보다 우선).

docker-compose `environment:` 블록은 `env_file:` 보다 우선이라 컨테이너 안 값을 덮어쓴다.

base compose 에는 비밀번호 설정이 없다 — dev override 가 env 채널을, prod overlay 가 file 채널을 각자 채운다. dev 는 `make dev` 가 `.env.dev.example` 에서 만든 `.env` 를 `env_file` 이 컨테이너에 주입하고, rabbitmq 만 키 이름이 달라(`RABBITMQ_DEFAULT_PASS`) override 가 매핑한다.

---

## 4. APP_ENV 마커

코드가 자기 환경을 알아야 분기 가능한 동작이 있다. 실제 분기는 한 곳이다.

| 분기점 | 동작 |
|--------|------|
| web lifespan | `dev` 면 정적 자원 캐시 무효화가 켜진다 (동작 상세는 `docs/reference/docker.md`) |

원칙: 코드 분기는 최소화. "환경 자체가 환경변수로 결정" 되는 게 이상이다. 비밀번호 검증은 이 값을 보지 않는다 (6절) — 환경 마커로 보안 강도를 가르지 않는다. 비즈니스 로직 분기 금지.

값:

| APP_ENV | 의도 |
|---------|------|
| `dev` | 로컬 개발. 정적 자원 캐시 무효화 활성 |
| `staging` | prod 유사 환경. 현재 prod 와 동일 동작 (분리 정책 미도입) |
| `prod` | 프로덕션. 정적 자원 버전 고정 |

---

## 5. dev/prod 차이 매트릭스

compose 는 공통 base(`docker-compose.yml`) + dev override(`docker-compose.override.yml`) + prod overlay(`docker-compose.prod.yml`). dev 는 base+override 자동 머지, prod 는 base+prod.yml(`deploy.sh` rollout 또는 수동 compose). 본 표는 dev/prod 구성 차이 — 포트 바인딩처럼 base 가 정해 양쪽이 같은 것은 `docs/reference/docker.md` 가 갖는다.

| 항목 | dev (본 repo) | prod (외부 인프라) |
|------|--------------|---------------------|
| 기동 방식 | `docker compose up` (base + override.yml 머지, 로컬 빌드) | base+prod.yml pull-and-run — `deploy.sh` rollout 또는 수동 `docker compose up -d` |
| compose 이미지 | override.yml 로컬 빌드(`assessment-engine:local`) | `.env` 의 `ENGINE_IMAGE` 핀 pull (미설정이면 compose 가 기동 거부) |
| `APP_ENV` | `dev` (정적 자원 캐시 무효화) | `prod` 명시 (정적 자원 버전 고정) |
| 코드 마운트 (bind mount) | OK override.yml 의 `./src` bind mount, 빠른 반복 | NG base 는 bind mount 없음 — 이미지·wheel 불변성 |
| 영속 볼륨 | named volume(`postgres_data`·`rabbitmq_data`) | `PGDATA_HOST`·`MQ_DATA_HOST` 로 외부 디스크 bind(Cinder 등) |
| Password 주입 | `.env`(.env.dev.example 복사) 평문 | file-secret 단일(`docker-compose.prod.yml` + `./secrets/*` 644) — `/run/secrets/*` 마운트, env 노출 회피 |
| Logging | `LOG_FORMAT=text` (colorized·grep 친화) · `LOG_LEVEL=DEBUG` | `LOG_FORMAT=json` 권장 (외부 log aggregator indexing) · `LOG_LEVEL=INFO` |
| web 노출 | plain HTTP port 8000 | HTTPS 외부 ingress (nginx·envoy 등) 종단, 앱은 plain |

---

## 6. Fail-fast 검증

비밀번호 미설정·뻔한 값·채널 충돌을 기동 시점에 차단한다. 환경으로 강도를 가르지 않는다 — dev 카탈로그도 뻔한 값을 쓰지 않으므로 같은 기준이 통한다. secret 주입 채널 자체는 외부 인프라 책임이고 본 repo 는 결과만 본다.

| 위치 | 검증 대상 | 강제 시점 |
|------|---------|---------|
| `Field(min_length=1)` 필드 제약 | 비밀번호 미설정·빈값 거부. 기본값이 없어 어느 환경에서도 값을 줘야 한다 | Settings 인스턴스화 |
| `config.py` `_validate_*_secrets` model_validator | `_WEAK_VALUES`(`password`/`admin`/`root`/`changeme`) 거부 (POSTGRES·RABBITMQ password·user). `assessment`(dev 카탈로그 값)는 허용 | Settings 인스턴스화 |
| `config.py` `_reject_env_shadowing_secret` | secret 파일과 같은 이름의 환경변수가 함께 있으면 거부 | 위와 같음 |

세 번째 검사는 채널 충돌을 잡는다. 우선순위가 `OS env > .env > secrets_dir` 이라 secret 파일을 두고도 같은 이름의 환경변수가 있으면 파일이 조용히 무시되고, 노출을 피하려던 값이 컨테이너 env 에 그대로 뜬다. 실패도 경고도 없어 운영자가 알 방법이 없으므로 기동을 막는다. 컨테이너는 compose `env_file` 이 값을 환경변수로 주입하므로 이 검사에 걸린다.

검사의 사각 하나 — 호스트에서 pydantic 이 `.env` 를 직접 읽는 경로는 값이 환경변수를 거치지 않아 걸리지 않는다.

발동 위치 (컴포넌트별):
- web: `WebSettings` + `DiagnosticSettings` → POSTGRES·RABBITMQ password 검증
- consumer: `ConsumerSettings` → POSTGRES·RABBITMQ password 검증
- worker: `WorkerSettings`(`WebSettings` 상속) → POSTGRES password 만 검증. broker 를 쓰지 않아 `rabbitmq_password` 필드 자체가 없다

효과: 값이 없거나 뻔하면 `Settings()` 호출이 즉시 `ValueError` 를 던져 컨테이너가 뜨지 않는다. 운영자가 secret 채널 점검 신호를 즉시 받는다.

---

## 7. Secret 채널 (외부 인프라 선택)

본 repo 는 어떤 채널이든 지원 — pydantic-settings 가 OS env 우선, `secrets_dir` fallback.

| 채널 | 어떻게 동작 | 적합 환경 |
|------|----------|---------|
| `.env` 평문 | `env_file` 또는 `--env-file` | 로컬 dev (본 repo 채택) |
| 환경변수 직접 | systemd `Environment=`·shell export | 작은 prod, 모든 OS |
| systemd `EnvironmentFile=` | 파일 1개에 KEY=VALUE | 비-compose 운영 (엔진은 env 채널도 지원) |
| Docker compose file-secret (compose 배포 표준) | `docker-compose.prod.yml` 이 `./secrets/*`(권한 644 — Compose file-secret 은 Swarm 과 달리 호스트 파일 권한을 컨테이너에 그대로 반영하는데 postgres 공식 이미지가 non-root 유저로 읽어 600이면 Permission denied) -> `/run/secrets/*` 마운트. app 은 `secrets_dir`, postgres 는 `*_FILE`, rabbitmq 는 `*_FILE` 미지원이라 entrypoint wrapper 가 파일을 읽어 주입 | 단일 호스트 compose prod (유일 정석) |
| SOPS/age + git | git 에 암호화 커밋, 운영 시 복호화 후 env 또는 file 주입 | GitOps |
| Vault / AWS Secrets Manager / k8s External Secrets | 외부 secret manager → env 또는 file 주입 | 다중 환경·동적 회전 |

본 repo 책임 한계 — 엔진은 위 표 어느 채널로 들어온 값이든 읽고 결과만 검증한다. 채널 선택은 외부 인프라 자유다. 다만 compose 배포 매체는 file-secret 단일이다 — 단일 호스트 non-swarm 에서 env 노출 회피가 핵심 이득이고, 호스트 디스크 평문은 `secrets/` 디렉토리 권한 0700(root 소유)으로 보호한다(파일 자체는 postgres non-root 유저 호환 위해 644).

---

## 8. 주입 흐름

본 repo 의 환경변수를 읽는 주체:

```
             .env / EnvironmentFile  (POSTGRES_HOST=... etc)
                          |
        +-----------------+-----------------+
        | (1)             | (2)             | (3)
        v                 v                 v
  compose env_file   config.py         external infra writes
                     BaseSettings      the agent env file
```

(1) compose `env_file` 이 컨테이너 환경변수로 주입한다. `environment:` 블록이 일부 키를 강제로 덮어쓴다.

(2) `config.py` 의 `BaseSettings` 가 Python 인스턴스 필드를 채운다 (우선순위는 3절).

(3) 외부 인프라가 `/etc/assessment-agent.env` 를 구성해 agent 프로세스에 전달한다 — `RABBITMQ_HOST` 등 broker
좌표가 여기 들어간다.

컨테이너 안에서는 (1)과 (2)가 겹친다. 환경변수가 이미 주입돼 있으므로 (2)의 `.env` 읽기는 결과에 영향을 주지
않는다 — 호스트에서 직접 기동할 때만 그 fallback 이 의미를 갖는다.

---

## 9. 컴포넌트별 read 매트릭스 (multi-node 분리 시)

본 repo 의 3 컴포넌트 (web · consumer · worker) 가 각자 다른 키 집합을 read. multi-node 분리 배포 시 어느 노드에 어느 키 inject 할지 reference.

| 키 그룹 | web | consumer | worker |
|--------|:---:|:--------:|:------:|
| `APP_ENV`·`LOG_FORMAT`·`LOG_LEVEL` | 의무 | 의무 | 의무 |
| `POSTGRES_*` | 의무 | 의무 | 의무 |
| `REDIS_*` | 의무 | 의무 | 의무 |
| `RABBITMQ_*` (broker 접속) | 의무 | 의무 (consume) | 사용 안 함 (DB job-claim) |
| `RABBITMQ_ROUTING_KEY_*`·`RABBITMQ_EXCHANGE` | 의무 | 의무 | 사용 안 함 |
| `RABBITMQ_TASK_*` (task exchange·queue·keys) | 의무 (task.install publish) | 의무 (worker.result consume) | 사용 안 함 |
| `WEB_PORT`·`WEB_RELOAD`·`INSTALL_TIMEOUT_SEC`·`INSTALL_TASK_DEADLINE_SEC`·`ZDM_*` | 의무 | 사용 안 함 | 사용 안 함 |
| `REPORT_WORKER_*`·`INSTALL_REAPER_*` | 사용 안 함 | 사용 안 함 | 의무 |
| `SQLALCHEMY_ECHO` | 의무 | 의무 | 의무 |

Settings 인스턴스를 만들 수 있는 위치는 AGENTS.md #F4 가 정한다.

---

## 10. 정석 주입 패턴 (운영 복잡도 단계별)

| 단계 | 패턴 | 적합 환경 | 외부 인프라 구현 |
|------|------|----------|---------------|
| A. 단일 `.env` 모든 노드 동일 inject | 한 파일 전부 — 단순 | 단일 host 또는 dev | docker-compose `env_file`·systemd `EnvironmentFile=/etc/assessment-engine.env` |
| A2. compose file-secret (compose prod 표준) | config 는 `.env`, 비번만 `./secrets/*`(644) -> `/run/secrets/*` | 단일 host compose prod (유일 정석) | `.env.example` 의 `COMPOSE_FILE` 로 base+prod 자동 -> `docker compose up -d` |
| B. 컴포넌트별 `.env` 분리 | 노드별 자기 키만 | small multi-node | systemd unit 별 `EnvironmentFile=/etc/<component>.env` |
| C. 계층화 — 공통 + 컴포넌트별 (권장) | `shared.env` (DB·MQ·Redis·LOG_FORMAT) + `<component>.env` (특화 키) | 4 node 분리 prod | Ansible `group_vars`(shared) + `host_vars`(component별). systemd `EnvironmentFile=` 여러 줄 |
| D. 중앙 secret store | Vault·Consul·AWS Parameter Store·k8s ConfigMap·External Secrets | 다중 환경·동적 회전 | 인프라 측 자체 운영 |

본 매트릭스는 reference — 실제 채널 선택·노드 분리 토폴로지는 외부 인프라 결정 (AGENTS.md #A0).

---

## 11. 에이전트 secret 채널 분리

에이전트 (C 바이너리) secret 은 엔진 secret 과 독립 — 별도 라이프사이클·별도 채널.

이유:
- 엔진 `.env` 변경이 에이전트 동작에 의도치 않게 영향 미치는 경로 차단.
- 에이전트 secret 라이프사이클 (VM 재프로비저닝 시점) 이 엔진 (컨테이너 재기동 시점) 과 독립.
- 에이전트가 K8s/Docker 내부에 있지 않으므로 Docker secrets 적용 불가 — 별도 secret 도구 필요.

agent env 구성은 본 repo 범위 밖(agent repo + 외부 인프라): Ansible vault·SaltStack pillar 등으로 VM 안 `/etc/assessment-agent.env` 생성. `RABBITMQ_HOST` 는 엔진 broker 에 도달하는 host(IP·FQDN)로 주입.

호스트명 정책:

기본값의 호스트명 (`postgres`·`rabbitmq`·`redis`) 은 docker-compose 서비스명이라 compose network 안에서만 해석된다. 엔진 밖(agent VM·별도 노드)에서는 인식되지 않으므로 실제 host (IP 또는 FQDN) 를 주입한다.

---

## 12. 전체 키 카탈로그

compose 예약 변수 — compose CLI 가 이름을 알고 읽는다. compose 파일이 `${...}` 로 참조하지 않아도 동작이 바뀌므로 애플리케이션 변수와 층위가 다르다.

| 키 | 기본값 | 사용처 | 설명 |
|----|--------|--------|------|
| `COMPOSE_FILE` | 없음 (compose 기본 규칙 = base + override) | compose CLI | 합칠 compose 파일 목록. `.env.example` 은 `docker-compose.yml:docker-compose.prod.yml` 로 dev override 를 뺀다 |
| `COMPOSE_PROJECT_NAME` | 디렉토리명 | compose CLI | 컨테이너·네트워크·볼륨 이름 접두 |

애플리케이션 변수. `*_PUBLISH_PORT` 의 바인딩 주소(loopback 여부)는 `docs/reference/docker.md` 가 갖는다.

| 키 | 기본값 | 사용처 | 설명 |
|----|--------|--------|------|
| `APP_ENV` | `dev` | config.py / docker-compose | 환경 마커. `dev`/`staging`/`prod`. 정적 자원 캐시 무효화만 가른다 (4절) |
| `LOG_FORMAT` | `text` | config.py / 각 entry `setup_logging()` | 로그 출력 format. `text`(dev colorized·grep) 또는 `json`(외부 log aggregator). prod 는 `json` 권장 |
| `LOG_LEVEL` | `INFO` | config.py / 각 entry `setup_logging()` | 최소 로그 수준(`DEBUG`·`INFO`·`WARNING`·`ERROR`). 루프 내부 흐름은 DEBUG 라 운영에서는 기본값을 쓴다 |
| `ENV_FILE` | `.env` | compose base `env_file:` | 서비스에 주입할 env 파일 경로. compose 가 `${ENV_FILE:-.env}` 로 참조하는 평범한 보간 변수다 |
| `ENGINE_IMAGE` | 없음 (미설정 시 compose 가 기동 거부) | compose base | 앱 서비스·migrate 이미지. config.py 미사용 — compose 전용. `deploy.sh vX.Y.Z` 가 `.env` 의 이 줄을 갱신한다. dev override.yml 은 `assessment-engine:local`(로컬 빌드)로 덮음. GHCR public — 토큰 없이 pull |
| `PGDATA_HOST` | `postgres_data` (named volume) | compose base | postgres 영속 경로. host 절대경로 주입 시 bind mount(infra Cinder `/mnt/pgdata`), 미설정 시 named volume |
| `MQ_DATA_HOST` | `rabbitmq_data` (named volume) | compose base | rabbitmq 영속 경로. 주입 시 host bind(`/mnt/mqdata`), 미설정 시 named volume |
| `WEB_PUBLISH_PORT` | `8000` | compose base | web 을 호스트에 퍼블리시할 포트. 컨테이너 안 listen 포트는 `WEB_PORT`. `deploy.sh`·`rotate-secret.sh` 의 health 체크가 이 값을 읽는다 |
| `RABBITMQ_PUBLISH_PORT` | `5672` | compose base | AMQP 퍼블리시 포트 (agent 발행 통로) |
| `RABBITMQ_MANAGEMENT_PUBLISH_PORT` | `15672` | compose base | RabbitMQ 관리 UI 퍼블리시 포트 |
| `POSTGRES_PUBLISH_PORT` | `5432` | compose base | psql 직접 접속용 퍼블리시 포트 |
| `REDIS_PUBLISH_PORT` | `6379` | compose base | redis-cli 직접 접속용 퍼블리시 포트 |
| `SECRETS_DIR` | `/run/secrets` | config.py | secret 파일 디렉토리 (3절). BaseSettings 필드가 아니라 모듈 로드 시점 `os.environ` 으로 읽는다 — 컨테이너는 `env_file` 이 `.env` 를 환경변수로 올려 통하지만, 호스트 직접 기동은 환경변수로 줘야 한다 |
| `POSTGRES_HOST` | `postgres` | config.py / compose base(서비스명 고정) | PostgreSQL 호스트. compose 배포에서는 base 의 `environment:` 가 서비스명으로 고정해 `.env` 값이 무시되고, compose 밖에서 기동할 때만 `.env` 값이 쓰인다 |
| `POSTGRES_PORT` | `5432` | config.py | 컨테이너가 접속할 서버 포트 — 호스트 퍼블리시 포트는 `POSTGRES_PUBLISH_PORT` |
| `POSTGRES_DB` | `assessment` | config.py / compose base | |
| `POSTGRES_USER` | `assessment` | config.py / compose | assessment 허용 — 빈값·password·admin·root·changeme 는 거부 |
| `POSTGRES_PASSWORD` | 없음 (필수) | config.py / compose | 기본값이 없어 미설정·빈값이면 기동이 멈춘다. 명시 `assessment` 는 허용. 강한 secret 권장 |
| `RABBITMQ_HOST` | `rabbitmq` | config.py / compose base(서비스명 고정) | 컨슈머 broker 접속. `POSTGRES_HOST` 와 같은 고정 규칙. 에이전트는 본 키 안 씀 — 외부 인프라가 broker 도달 host 별도 주입 |
| `RABBITMQ_PORT` | `5672` | config.py | 컨테이너가 접속할 broker 포트 — 호스트 퍼블리시 포트는 `RABBITMQ_PUBLISH_PORT` |
| `RABBITMQ_VHOST` | `assessment` | config.py / compose | 전용 vhost (무슬래시). 에이전트와 동일 값. 이름에 `/` 없어 인코딩 무영향(config.py `broker_url` 은 슬래시 포함 vhost 를 `%2F`로 자동 인코딩) |
| `RABBITMQ_USER` | `assessment` | config.py / compose | assessment 허용 — 빈값·password·admin·root·changeme 는 거부 |
| `RABBITMQ_PASSWORD` | 없음 (필수) | config.py / compose | 기본값이 없어 미설정·빈값이면 기동이 멈춘다. 명시 `assessment` 는 허용. 강한 secret 권장 |
| `RABBITMQ_EXCHANGE` | `assessment` | config.py / agent env (repo 밖) | 에이전트·consumer routing 계약. 변경 시 양쪽 동기화 |
| `RABBITMQ_ROUTING_KEY_INVENTORY` | `server.inventory` | config.py / agent env (repo 밖) | 동일 |
| `RABBITMQ_ROUTING_KEY_METRICS` | `server.metrics` | config.py / agent env (repo 밖) | 동일 |
| `RABBITMQ_ROUTING_KEY_ERROR` | `server.error` | config.py / agent env (repo 밖) | 동일 |
| `RABBITMQ_TASK_EXCHANGE` | `assessment.tasks` | config.py | 엔진 발행 task.install + worker.result 소비 전용 exchange (collector `RABBITMQ_EXCHANGE` 와 분리). agent `WORKER_TASK_EXCHANGE` 와 값 일치 의무 |
| `RABBITMQ_TASK_QUEUE_PREFIX` | `agent.tasks` | config.py | task.install 발행 대상 호스트별 큐 prefix. full = `<prefix>.<agent_id>` (`agent_task_queue()`). agent `WORKER_TASK_QUEUE_PREFIX` 와 일치 |
| `RABBITMQ_TASK_INSTALL_KEY_PREFIX` | `task.install` | config.py | task.install 호스트별 routing key prefix. full = `<prefix>.<agent_id>` (`task_install_routing_key()`) |
| `RABBITMQ_ROUTING_KEY_TASK_RESULT` | `task.result` | config.py | worker.result 큐 바인딩 routing key (원격 호스트 결과 보고 수신). agent `WORKER_TASK_RESULT_KEY` 와 일치 의무 |
| `RABBITMQ_QUEUE_WORKER_RESULT` | `worker.result` | config.py | 엔진이 task.result 를 소비하는 단일 결과 큐 이름 |
| `TASK_INSTALL_SUCCESS_EXIT_CODES` | `{"windows":[2],"rocky:9":[3],"almalinux:9":[3],"ol:9":[3],"centos:9":[3]}` | config.py | task.result 성공 보정 정책 (`task_policy.effective_task_result`). 키 -> 성공으로 취급할 추가 exit code. 상세는 아래 "TASK_INSTALL_SUCCESS_EXIT_CODES 운영 규칙" |
| `RABBITMQ_WORKER_USER` | `assessment` | agent env (repo 밖) | 원격 호스트 worker 가 사용할 AMQP user. 비어 있으면 worker 자동 비활성 (collector 만 동작) |
| `RABBITMQ_WORKER_PASSWORD` | `assessment` | agent env (repo 밖) | `RABBITMQ_WORKER_USER` 의 암호. heredoc 안에서 `RABBITMQ_WORKER_PASS` 매핑 |
| `WORKER_TASK_EXCHANGE` | `assessment.tasks` | agent env (repo 밖) | task.install/task.result 전용 exchange. collector exchange 와 분리. 엔진 `RABBITMQ_TASK_EXCHANGE` 와 값 일치 의무 |
| `WORKER_TASK_QUEUE_PREFIX` | `agent.tasks` | agent env (repo 밖) | 원격 호스트별 큐 prefix. full name = `<prefix>.<agent_id>`. 엔진 `RABBITMQ_TASK_QUEUE_PREFIX` 와 일치 |
| `WORKER_TASK_RESULT_KEY` | `task.result` | agent env (repo 밖) | 원격 호스트 → 엔진 결과 보고 routing key. 엔진 `RABBITMQ_ROUTING_KEY_TASK_RESULT` 와 일치 |
| `WORKER_DOWNLOAD_ALLOWED_HOSTS` | `""` (빈값) | agent env (repo 밖) | task.install download.url 의 host 화이트리스트 (case-insensitive 정확 매치). 빈 whitelist 면 전부 거부 — 운영자가 ZDM_DEFAULT_IP host 를 등록 |
| `REDIS_HOST` | `redis` | config.py / compose base(서비스명 고정) | Redis 호스트. `POSTGRES_HOST` 와 같은 고정 규칙 |
| `REDIS_PORT` | `6379` | config.py | 컨테이너가 접속할 서버 포트 — 호스트 퍼블리시 포트는 `REDIS_PUBLISH_PORT` |
| `REDIS_MAXMEMORY` | `256mb` | compose base (redis command) | Redis maxmemory cap. prod 튜닝 가능 |
| `REDIS_MAXMEMORY_POLICY` | `volatile-lru` | compose base (redis command) | maxmemory 도달 시 eviction policy. TTL 키 우선 evict |
| `WEB_PORT` | `8000` | config.py / compose base | 컨테이너 안 uvicorn listen 포트 + web healthcheck 대상. 호스트 접속 포트는 `WEB_PUBLISH_PORT` |
| `WEB_RELOAD` | `false` | config.py / dev compose | uvicorn auto-reload. dev hot-reload 전용 (dev override 가 `true` 주입, 패키지 bind mount 와 짝). prod 미설정 → false (코드 변경 감시 프로세스 불필요·bind mount 없는 wheel/image 배포에 무의미) |
| `INSTALL_TIMEOUT_SEC` | `600` | config.py | install.sh wall-clock timeout (픽업 후 스크립트 실행 예산). 원격 host worker 가 SIGTERM/SIGKILL |
| `INSTALL_TASK_DEADLINE_SEC` | `3600` | config.py | install task 배달/마감 창(초). engine `tasks.deadline_at` + broker `agent.tasks.<agent_id>` 큐 `x-message-ttl` 동일 창(오프라인 호스트 store-and-forward 유예). `INSTALL_TIMEOUT_SEC`(600) 와 별개 개념. 값을 바꾸면 이미 선언된 큐의 TTL 과 어긋나 큐 재선언이 `PRECONDITION_FAILED` 로 깨진다 — broker 의 기존 `agent.tasks.*` 큐를 수동 삭제해야 한다 |
| `ZDM_DEFAULT_IP` | `""` (빈값) | config.py | ZDM 서버 기본 좌표. install 모달 default. POST `/tasks/install` 의 `zdm_ip` 누락 시 fallback. install.sh 의 `-s` 인자 + agent download.url host. 운영자가 real ZDM 좌표 주입. startup 거부 없음 — 잘못된 ZDM 발행은 런타임 503 + agent host whitelist 가 방어. 허용 형식은 아래 "ZDM 좌표 값 공간" |
| `ZDM_DEFAULT_USER` | `admin@zconverter.com` | config.py | ZDM 관리자 계정 기본값. POST body `zdm_user` 누락 시 fallback. install.sh 의 `-u` 인자. startup 거부 없음 (secret 아님) |
| `ZDM_META_CONNECT_TIMEOUT_SEC` | `5.0` | config.py | ZDM 메타 조회 HTTP connect timeout |
| `ZDM_META_TOTAL_TIMEOUT_SEC` | `120.0` | config.py | ZDM 메타 조회 HTTP total timeout (HEAD + GET full). 44MB 가정, 동일 LAN 1~2s |
| `REDIS_TTL_ZDM_PACKAGE_SHA256` | `21600` (6h) | config.py | ETag 기반 sha256 cache TTL |
| `SQLALCHEMY_ECHO` | `false` | config.py | SQLAlchemy 엔진 SQL 로깅. dev 디버깅 시 true (운영 환경은 false 유지 — 로그 폭증·secret 노출 위험) |

### ZDM 좌표 값 공간

`ZDM_DEFAULT_IP` 와 POST `/api/tasks/install` 의 `zdm_ip` 는 같은 값 공간을 쓴다. 운영 환경마다 ZDM 이 IP·FQDN·HTTP endpoint 어느 형태로도 떠서 하나로 강제하지 않고 아래를 모두 받는다.

| 형식 | 예 |
|------|-----|
| IPv4 | `192.168.3.94` |
| IPv4:port | `192.168.3.94:8080` |
| hostname·FQDN (trailing dot 허용) | `zdm.example.com` |
| hostname:port | `zdm.example.com:8000` |
| http·https URL | `http://zdm.example.com:8443/download/x.tar.gz` |

최대 길이는 2048 (RFC 3986 권장 URL 상한). 공백·제어문자와 shell metachar(`;` `|` `&` `` ` `` `$` `<` `>`)는 거부한다 — task params 가 task.install body 로 그대로 전파되기 때문이다.

IPv6 는 받지 않는다 — raw(`::1`)도 bracket(`[::1]:8000`)도 거부다. agent 의 `download_url_extract_host`(download.c)가 `:` 를 host 종료 문자로 처리해 bracket 표기를 파싱하지 못한다. IPv6 ZDM 좌표를 쓰려면 agent 변경이 선행돼야 한다.

### TASK_INSTALL_SUCCESS_EXIT_CODES 운영 규칙

키 규약은 os_family 로 갈린다 — Windows 는 family-level `"windows"` 한 키, Linux 는 `"os_id:major"`(예 `"rocky:9"`). Windows 를 빌드번호별 키(예 2008R2=7601)로 두면 누락에 취약해 family 로 묶는다.

보정은 `status=failure` + `failure_reason=script_failed` + 키 일치 + exit_code 포함일 때만 걸린다. JSON override 예: `{"windows":[2],"rocky:9":[3]}`.

기본값 근거 — Windows installer 는 설치에 성공해도 exit 2 로 끝나고, EL9(rocky·almalinux·ol·centos major 9)는 systemd start-limit 로 exit 3 을 낸다. 두 경우 모두 설치·ZDM 등록은 성공했고, 해당 호스트 services 에 `ZConCloudAgent`(RUNNING)가 등장하는 것으로 확인했다. 같은 EL9 계열이라도 rhel9 는 미해당이라 목록에서 빼고, centos-stream8 은 os_id 가 centos8 과 구분되지 않아 보류한다.

이 allowlist 는 `task_policy` 미보고(null) task 의 폴백 전용이다. 신규 OS 대응을 allowlist 확장으로 처리하지 않는다 — 에이전트가 `task_policy` 를 발행하게 하는 것이 정공이다.

### 표에 두지 않는 내부 튜닝 키

다음 키는 운영 변경 빈도가 낮아 표에 행을 두지 않는다. BaseSettings 필드라 필요하면 env 로 override 된다.

- `redis_ttl_idempotent` (24h)·`redis_ttl_online` (5min)
- `redis_ttl_last_agent_start` (24h)·`redis_ttl_agent_restarts` (1h)·`redis_ttl_time_invariant_warned` (1h)
- `redis_ttl_cache_metrics` (1min — 대시보드 스냅샷 cache-aside)·`redis_ttl_cache_detail` (5min — 서버 상세 ViewModel cache-aside)
- `redis_key_*` 패턴 (cache:*·idempotent·online·last_agent_start·agent_restarts·time_invariant_warned)
- WorkerSettings 전용 (worker 프로세스만): `report_worker_poll_interval_sec` (2s)·`report_worker_stale_seconds` (600 — running 잔류 회수)·`report_worker_shutdown_timeout_sec` (10s)·`install_reaper_interval_sec` (60s)·`install_reaper_shutdown_timeout_sec` (5s)
- `zdm_package_path`·`zdm_package_script`·`zdm_package_path_windows` — ZDM 제품 패키지 layout 상수 (거의 안 바뀜, ZDM 버전업 시에나). 배포 변동값 아니라 미수록 — task.install download.url 은 `http://{ZDM_IP}{zdm_package_path}` 조립
- `agent_restart_alert_threshold` (기본 3) — consumer warning과 web attention 공용 재시작 횟수 임계값. 필요 시 env override

---

## 13. 운영 체크리스트 (외부 인프라가 prod 배포 시)

본 repo 범위 밖이지만 외부 인프라가 충족해야 할 contract. ansible role·k8s manifest 등이 본 contract 만 충족하면 본 엔진 정상 기동.

- [ ] `APP_ENV=prod` 환경변수 명시
- [ ] `POSTGRES_PASSWORD`·`RABBITMQ_PASSWORD` 강한 random secret 주입 (채널은 자유 — env·systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등)
- [ ] `POSTGRES_USER`·`RABBITMQ_USER` 강한 값 권장 (assessment 는 허용 — 빈값·password·admin·root·changeme 만 거부)
- [ ] `ZDM_DEFAULT_IP`·`ZDM_DEFAULT_USER` 고객사 ZDM 좌표로 override (startup 거부는 없음 — 미설정 시 첫 install 발행이 런타임 503 또는 agent `url_not_allowed` 로 실패)
- [ ] `ZDM_PACKAGE_*` 운영 ZDM 측 패키지 layout 과 일치 (path·script)
- [ ] 마이그레이션은 base compose 의 `migrate` init-container 가 앱 기동 전 적용 — 별도 사전 실행 불요 (`docs/guides/migrate.md`)
- [ ] DB·Redis·MQ 관리 UI 는 loopback 바인딩 유지, AMQP 5672 만 외부 노출(agent 발행 통로) — 바인딩은 base compose 가 정한다 (`docs/reference/docker.md`)
- [ ] web 노출 결정 (reverse proxy 또는 직접)
- [ ] `LOG_FORMAT=json` 권장 — 외부 log aggregator indexing (`docs/reference/observability.md`)
- [ ] `LOG_LEVEL` 기본 `INFO` 유지 — DEBUG 는 메시지별 흐름까지 찍는다
- [ ] 에이전트 secret 채널은 엔진과 독립 — Ansible vault·SaltStack pillar 등 별도 도구
- [ ] 기동 직후 `Settings()` 생성 시점 `ValueError` 발생 없음 — fail 시 secret 채널 점검 필요

---

## 14. 안티패턴 (금지)

- 코드에 `if env == "prod"` 분기 도입 — 4절 한 지점 외 금지. 보안 강도를 환경으로 가르지 않는다
- `.env.production` / `.env.development` 같은 환경별 .env 동시 보유 — 활성 파일 모호
- prod 에서 코드 bind mount 유지 — 컨테이너 안 `.env` 노출 + 코드 변조 위험
- base 에 환경 색 담기 — base 는 dev·prod 공통 정의만 갖는다. env 채널은 dev override 가, file-secret 채널은 `docker-compose.prod.yml` 이 각자 채운다
- secret 을 git·이미지 컨텍스트에 커밋 — `.gitignore` 의 `.env` 무시 규칙과 `.dockerignore` 의 allowlist 를 무너뜨리지 않는다
- 컨테이너 안에서 `/app/.env` 를 직접 read 하는 코드 추가 — pydantic-settings 의 `env_file` 폴백 외 직접 read 금지
- `secrets_dir` 강제 활성화 — 디렉토리 부재 시 noisy 경고. `os.path.isdir` 분기로 None fallback 유지

---

## 관련 문서

- `docs/guides/deploy.md` — VM 부트스트랩·compose rollout 절차
- `docs/reference/observability.md` — `LOG_FORMAT`·`LOG_LEVEL`
- `docs/guides/migrate.md` — schema migrate contract
- `docs/guides/release.md` — 릴리즈 artifact 카탈로그
- AGENTS.md #A0·#F8 — secret·PII 노출 금지 원칙
