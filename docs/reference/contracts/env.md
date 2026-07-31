# 환경변수 관리 (Environment Variables)

본 문서는 운영 env 관리 단일 진실 — 정책·secret 채널·주입 흐름·전체 키 카탈로그·운영 체크리스트. 외부 인프라가 본 엔진을 prod 운영할 때 충족해야 할 contract 와 dev 분기 매트릭스 한 곳.

정책 출처: CLAUDE.md #A0 (외부 인프라 책임 분리) · #F8 (secret·PII 노출 금지). 본 repo 는 결과 (weak default 거부) 만 검증, secret 주입 채널 자체는 외부 인프라 자유.

---

## 1. 원칙 (12-Factor + 본 프로젝트 적용)

| # | 원칙 | 본 프로젝트 적용 |
|---|------|----------------|
| 1 | Config 을 환경변수로 분리 | `pydantic-settings` `BaseSettings`. 코드에 환경별 값 박지 않음 |
| 2 | 같은 이미지를 모든 환경에서 사용 | `Dockerfile` 1개. 환경 차이는 환경변수·compose override·secret 채널로만 |
| 3 | secret 과 일반 config 분리 | dev `.env` 평문 / prod 외부 인프라 자유 채널 (env·systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등) |
| 4 | Fail-fast 검증 | `config.py` `model_validator` 가 `APP_ENV=prod` 일 때 약한 default 거부 |
| 5 | dev 에 안전한 default + prod 에서 거부 | `_WEAK_VALUES` 집합 (POSTGRES·RABBITMQ password·user) 거부 — prod 에서 시작 차단 |
| 6 | secret 을 코드·이미지·git 에 박지 않음 | `.dockerignore`·`.gitignore` 에 `.env` 명시 (env.example·env.dev.example 카탈로그만 commit) |

---

## 2. config vs secret 분류

같은 환경변수처럼 보여도 secret 과 일반 config 는 다르게 다뤄야 한다.

| 분류 | 정의 | 본 프로젝트 예시 | 보관 |
|------|------|----------------|------|
| config | 노출돼도 시스템이 즉시 위태롭지 않은 운영 값 | `POSTGRES_HOST`·`RABBITMQ_VHOST`·`WEB_PORT`·`APP_ENV`·`LOG_FORMAT` | `.env` 평문 OK, git 커밋 가능 (`env.example`) |
| secret | 노출 시 즉시 무단 접근 가능한 자격 | `POSTGRES_PASSWORD`·`RABBITMQ_PASSWORD`·향후 API token / TLS key | dev 한정 `.env` 평문, prod 는 외부 인프라 자유 채널 |

경계 케이스:
- `POSTGRES_USER` — 보통 config. 단 user 자체가 권한 분리 키이면 secret. 본 프로젝트는 config 분류하되 prod 검증 시 약한 값(빈값·password·admin·root·changeme) 거부 — `assessment`(dev default)는 운영 편의로 허용.
- `ZDM_DEFAULT_IP` / `ZDM_DEFAULT_USER` — config (secret 아님, 노출 무해). startup 거부 안 함 — 잘못된 ZDM 발행은 런타임 (`HttpZdmPackageResolver` 메타 도달 실패 시 503) + agent host whitelist (`WORKER_DOWNLOAD_ALLOWED_HOSTS`, `url_not_allowed` reject) 가 방어 (빈값 default 정상 동작, startup 거부 없음).

규칙: config 인지 secret 인지 헷갈리면 secret 으로 간주. 잘못 분류해 secret 을 평문 노출하는 비용이 그 반대보다 크다.

코드 측 의무 (CLAUDE.md F8):
- secret 으로 분류된 필드는 `Settings` 에서 `SecretStr` 타입 의무 — `__repr__` 이 자동 마스킹해 로그·예외·디버거 출력에 평문 노출 차단.
- 사용 시점에서만 `.get_secret_value()` 로 평문 추출 — 변수에 담아 재사용 금지 (마스킹 우회 위험).

---

## 3. 우선순위 체인 (pydantic-settings)

```
[lowest]  코드 default (config.py)
       v
       .env 파일 (cwd 기준 — 컨테이너 안에서는 /app/.env)
       v
       secrets_dir 파일 (<SECRETS_DIR>/<field_name>, default /run/secrets)
       v
       OS 환경변수 (systemd Environment / docker-compose env / orchestrator inject)
[highest] 명시적 init kwargs (테스트용)
```

본 프로젝트 `config.py`:

```python
_SECRETS_DIR = os.environ.get("SECRETS_DIR", "/run/secrets")
_SECRETS_DIR = _SECRETS_DIR if os.path.isdir(_SECRETS_DIR) else None

model_config = SettingsConfigDict(
    env_file=".env",
    secrets_dir=_SECRETS_DIR,
    extra="ignore",
)
```

`SECRETS_DIR` env 로 secrets 디렉토리 경로 override 가능. 디렉토리가 존재할 때만 활성 — dev 호스트에선 보통 None 으로 file read 단계 skip.

secret 파일명은 pydantic 필드명과 정확히 일치 의무. 외부 인프라가 file 채널이면 `<SECRETS_DIR>/postgres_password` → pydantic `postgres_password: SecretStr` 자동 매핑. env 채널이면 `POSTGRES_PASSWORD=<value>` 환경변수 (env 가 secrets_dir 보다 우선).

docker-compose `environment:` 블록은 `env_file:` 보다 우선이라 컨테이너 안 값을 덮어쓴다.

docker-compose 에서 secret 의 OS env override 동작: `env_file:` 만으론 호스트 OS env 가 컨테이너에 전달되지 않아 `.env` 값이 고정된다(override 불가). 그래서 secret(`*_PASSWORD`)은 `environment:` 에 `${VAR:-changeme}` 보간으로 명시한다 — 호스트 OS env 있으면 우선(override) > compose 가 읽는 `.env` > `changeme`(미설정 시 prod 거부). 앱·DB 컨테이너가 동일 소스를 봐 항상 일치한다. dev 는 `cp env.dev.example .env` 로 루트 `.env` 를 만들어 보간 소스를 `env_file` 과 통일한다.

---

## 4. APP_ENV 마커

코드가 자기 환경을 알아야 분기 가능한 동작이 있다. 본 프로젝트는 단 한 곳에서만 분기:

| 분기점 | 동작 |
|--------|------|
| `config.py` `model_validator` | `APP_ENV=prod` 일 때 약한 default 거부 (fail-fast) |

원칙: 코드 분기는 최소화. "환경 자체가 환경변수로 결정" 되는 게 이상이고, APP_ENV 분기는 운영 정책 변경 (검증 강도) 에만 사용. 비즈니스 로직 분기 금지.

값:

| APP_ENV | 의도 |
|---------|------|
| `dev` | 로컬 개발. 평문 .env, 약한 default 허용 |
| `staging` | prod 유사 환경. 현재 dev 와 동일 동작 (분리 정책 미도입) |
| `prod` | 프로덕션. fail-fast, secret 채널 강제 |

---

## 5. dev/prod 차이 매트릭스

compose 는 prod-safe base(`docker-compose.yml`) + dev override(`docker-compose.override.yml`) + prod file-secret overlay(`docker-compose.secrets.yml`). dev 는 base+override 자동 머지, prod 는 base+secrets(`deploy.sh` rollout 또는 수동 compose). 본 표는 dev/prod 구성 차이.

| 항목 | dev (본 repo) | prod (외부 인프라) |
|------|--------------|---------------------|
| 기동 방식 | `docker compose up` (base + override.yml 머지, 로컬 빌드) | base+secrets pull-and-run — `deploy.sh` rollout 또는 수동 `docker compose up -d` |
| compose 이미지 | override.yml 로컬 빌드(`assessment-engine:local`) | base 의 GHCR 핀(`ENGINE_IMAGE` 또는 기본 핀) pull |
| `APP_ENV` | `dev` | `prod` 명시 (env var 또는 EnvironmentFile) |
| 코드 마운트 (bind mount) | OK override.yml 의 `./src` bind mount, 빠른 반복 | NG base 는 bind mount 없음 — 이미지·wheel 불변성 |
| 영속 볼륨 | named volume(`postgres_data`·`rabbitmq_data`) | `PGDATA_HOST`·`MQ_DATA_HOST` 로 외부 디스크 bind(Cinder 등) |
| 백킹 서비스 포트 외부 노출 | OK 5432·5672·6379·15672 | NG web 만 (또는 reverse proxy 뒤) |
| Password 주입 | `.env`(env.dev.example 복사) 평문 | file-secret 단일(`docker-compose.secrets.yml` + `./secrets/*` 644) — `/run/secrets/*` 마운트, env 노출 회피 |
| Schema 관리 | `migrate` init-container 가 `alembic upgrade head` 1회 | 동일 — base compose `migrate` init-container 가 앱 서비스 기동 전 실행 (deploy.sh rollout 내재) |
| Fail-fast 검증 | 약한 default 허용 | `_WEAK_VALUES` 거부 → `Settings()` 생성 시점 `ValueError` |
| restart 정책 | `unless-stopped` | `unless-stopped` (base compose `restart:`) |
| Logging | `LOG_FORMAT=text` (colorized·grep 친화) | `LOG_FORMAT=json` 권장 (외부 log aggregator indexing) |
| web 노출 | plain HTTP port 8000 | HTTPS 외부 ingress (nginx·envoy 등) 종단, 앱은 plain |
| broker AMQP | plain (port 5672) | AMQPS (port 5671) 권장 |

---

## 6. Fail-fast 검증 (weak default 거부)

prod 환경에서 secret 누락·약한 default 통과 차단. secret 주입 채널 자체는 외부 인프라 책임 — 본 repo 는 결과만 검증.

| 위치 | 검증 대상 | 강제 시점 |
|------|---------|---------|
| `config.py` `_validate_prod_*` model_validator | `_WEAK_VALUES`(`""`/`password`/`admin`/`root`/`changeme`) 거부 (POSTGRES·RABBITMQ password·user). `assessment`(dev default)는 허용 — 명시하면 USER·PASSWORD 둘 다 통과. PASSWORD default 는 `changeme`(weak)라 미설정 시 거부 | 앱 import 직후 (`Settings()` 인스턴스 생성 시) |

```python
@model_validator(mode="after")
def _validate_prod_web_secrets(self) -> "WebSettings":
    if self.app_env != "prod":
        return self
    if self.postgres_password.get_secret_value() in _WEAK_VALUES:
        raise ValueError("POSTGRES_PASSWORD is unset or uses a dev default in prod. ...")
    if self.postgres_user in _WEAK_VALUES:
        raise ValueError("POSTGRES_USER must be set to a non-default value in prod.")
    return self
```

발동 위치 (컴포넌트별):
- web: `WebSettings` + `DiagnosticSettings` → POSTGRES·RABBITMQ password weak default 거부
- consumer: `ConsumerSettings` → POSTGRES·RABBITMQ password weak default 거부

효과:
- prod 에서 `.env` 미주입·dev default 잔존 시 `Settings()` 호출이 즉시 `ValueError` → 컨테이너 crash (fail-fast).
- 운영자가 secret 채널 점검 신호 즉시 수신.

---

## 7. Secret 채널 (외부 인프라 선택)

본 repo 는 어떤 채널이든 지원 — pydantic-settings 가 OS env 우선, `secrets_dir` fallback.

| 채널 | 어떻게 동작 | 적합 환경 |
|------|----------|---------|
| `.env` 평문 | `env_file` 또는 `--env-file` | 로컬 dev (본 repo 채택) |
| 환경변수 직접 | systemd `Environment=`·shell export | 작은 prod, 모든 OS |
| systemd `EnvironmentFile=` | 파일 1개에 KEY=VALUE | 비-compose 운영 (엔진은 env 채널도 지원) |
| Docker compose file-secret (compose 배포 표준) | `docker-compose.secrets.yml` 이 `./secrets/*`(권한 644 — Compose file-secret 은 Swarm 과 달리 호스트 파일 권한을 컨테이너에 그대로 반영하는데 postgres 공식 이미지가 non-root 유저로 읽어 600이면 Permission denied) -> `/run/secrets/*` 마운트. app 은 `secrets_dir`, postgres 는 `*_FILE`, rabbitmq 는 `*_FILE` 미지원이라 entrypoint wrapper 가 파일을 읽어 주입 | 단일 호스트 compose prod (유일 정석) |
| SOPS/age + git | git 에 암호화 커밋, 운영 시 복호화 후 env 또는 file 주입 | GitOps |
| Vault / AWS Secrets Manager / k8s External Secrets | 외부 secret manager → env 또는 file 주입 | 다중 환경·동적 회전 |

본 repo 책임 한계:
- pydantic-settings 가 OS env·`secrets_dir` 둘 다 지원 — 외부 인프라 채널 선택 자유
- `_validate_prod_*` 가 결과 (weak default 거부) 만 검증 — 채널 자체는 본 repo 무관
- compose 배포는 file-secret 채널 단일 — `docker-compose.secrets.yml` + `./secrets/*`(`secrets/README.md`). `env.example` 에 평문 password 없고 `COMPOSE_FILE` 이 base+secrets 자동 머지. 단일 호스트 non-swarm 에서 env 노출 회피가 핵심 이득(호스트 디스크 평문은 `secrets/` 디렉토리 권한 0700(root 소유)으로 보호(파일 자체는 postgres non-root 유저 호환 위해 644)). 엔진은 env·secrets_dir 어느 채널도 읽으나(위 표) 배포 매체는 compose file-secret 단일.

---

## 8. 주입 흐름

본 repo 의 환경변수를 읽는 주체:

```
                        ┌─────────────────────────────────────┐
   .env 또는 EnvironmentFile│  POSTGRES_HOST=...                  │
                        └────────┬──────────┬──────────┬──────┘
                                 │          │          │
                ┌────────────────┘          │          └──────────────────┐
                │ (1)                       │ (2)                         │ (3)
                v                           v                             v
   docker-compose env_file        config.py BaseSettings        외부 인프라가 agent env 구성
   → 컨테이너 환경변수 주입       → Python 인스턴스 필드        → /etc/assessment-agent.env
   → environment: 블록이          → env > .env > secrets_dir    → agent 프로세스로 전달
     일부 키 강제 override          > default (cwd /app/.env)     → RABBITMQ_HOST 등 broker 좌표 주입
                │
                └─ (4) 컨테이너 안 Python 시작 시 (1)+(2) 결합:
                       환경변수가 이미 주입돼 있으므로 (2) 의 .env read 는 redundant
                       (호스트 직접 실행 시에만 (2) 의 .env 가 의미 있음 — fallback)
```

---

## 9. 컴포넌트별 read 매트릭스 (multi-node 분리 시)

본 repo 의 3 컴포넌트 (web · consumer · worker) 가 각자 다른 키 집합을 read. multi-node 분리 배포 시 어느 노드에 어느 키 inject 할지 reference.

| 키 그룹 | web | consumer | worker |
|--------|:---:|:--------:|:------:|
| `APP_ENV`·`LOG_FORMAT` | 의무 | 의무 | 의무 |
| `POSTGRES_*` | 의무 | 의무 | 의무 |
| `REDIS_*` | 의무 | 의무 | 의무 |
| `RABBITMQ_*` (broker 접속) | 의무 | 의무 (consume) | 사용 안 함 (DB job-claim) |
| `RABBITMQ_ROUTING_KEY_*`·`RABBITMQ_EXCHANGE` | 의무 | 의무 | 사용 안 함 |
| `RABBITMQ_TASK_*` (task exchange·queue·keys) | 의무 (task.install publish) | 의무 (worker.result consume) | 사용 안 함 |
| `WEB_PORT`·`WEB_RELOAD`·`INSTALL_TIMEOUT_SEC`·`INSTALL_TASK_DEADLINE_SEC`·`ZDM_*` | 의무 | 사용 안 함 | 사용 안 함 |
| `REPORT_WORKER_*`·`INSTALL_REAPER_*` | 사용 안 함 | 사용 안 함 | 의무 |
| `SQLALCHEMY_ECHO` | 의무 | 의무 | 의무 |

코드 단일 진실 (Composition Root, CLAUDE.md #F4):
- `src/assessment_engine/config.py` — class 정의만 (인스턴스 0)
- `src/assessment_engine/web/settings.py` — WebSettings + DiagnosticSettings (보고서 발행 publish 용 broker)
- `src/assessment_engine/consumer/settings.py` — ConsumerSettings
- `src/assessment_engine/worker/settings.py` — WorkerSettings (보고서 생성 + install reaper 전용 워커, broker 미사용·DB job-claim)
- `src/assessment_engine/db/session.py`·`cache/redis.py` — 자체 WebSettings (공통 db layer)

---

## 10. 정석 주입 패턴 (운영 복잡도 단계별)

| 단계 | 패턴 | 적합 환경 | 외부 인프라 구현 |
|------|------|----------|---------------|
| A. 단일 `.env` 모든 노드 동일 inject | 한 파일 전부 — 단순 | 단일 host 또는 dev | docker-compose `env_file`·systemd `EnvironmentFile=/etc/assessment-engine.env` |
| A2. compose file-secret (compose prod 표준) | config 는 `.env`, 비번만 `./secrets/*`(644) -> `/run/secrets/*` | 단일 host compose prod (유일 정석) | `env.example` 의 `COMPOSE_FILE` 로 base+secrets 자동 -> `docker compose up -d` (`secrets/README.md`) |
| B. 컴포넌트별 `.env` 분리 | 노드별 자기 키만 | small multi-node | systemd unit 별 `EnvironmentFile=/etc/<component>.env` |
| C. 계층화 — 공통 + 컴포넌트별 (권장) | `shared.env` (DB·MQ·Redis·LOG_FORMAT) + `<component>.env` (특화 키) | 4 node 분리 prod | Ansible `group_vars`(shared) + `host_vars`(component별). systemd `EnvironmentFile=` 여러 줄 |
| D. 중앙 secret store | Vault·Consul·AWS Parameter Store·k8s ConfigMap·External Secrets | 다중 환경·동적 회전 | 인프라 측 자체 운영 |

본 매트릭스는 reference — 실제 채널 선택·노드 분리 토폴로지는 외부 인프라 결정 (CLAUDE.md #A0).

---

## 11. 에이전트 secret 채널 분리

에이전트 (C 바이너리) secret 은 엔진 secret 과 독립 — 별도 라이프사이클·별도 채널.

이유:
- 엔진 `.env` 변경이 에이전트 동작에 의도치 않게 영향 미치는 경로 차단.
- 에이전트 secret 라이프사이클 (VM 재프로비저닝 시점) 이 엔진 (컨테이너 재기동 시점) 과 독립.
- 에이전트가 K8s/Docker 내부에 있지 않으므로 Docker secrets 적용 불가 — 별도 secret 도구 필요.

agent env 구성은 본 repo 범위 밖(agent repo + 외부 인프라): Ansible vault·SaltStack pillar 등으로 VM 안 `/etc/assessment-agent.env` 생성. `RABBITMQ_HOST` 는 엔진 broker 에 도달하는 host(IP·FQDN)로 주입.

호스트명 정책 (dev compose 한정):

기본값의 호스트명 (`postgres`·`rabbitmq`·`redis`) 은 docker-compose 서비스명. compose network 내부에서만 해석. 분산 배포 (VM 별 다른 호스트) 에는 인식 안 됨 — 외부 인프라가 실제 host (IP 또는 FQDN) 로 override 의무.

---

## 12. 전체 키 카탈로그 (`env.example` 순서)

| 키 | 기본값 | 사용처 | 설명 |
|----|--------|--------|------|
| `APP_ENV` | `dev` | config.py / docker-compose | 환경 마커. `dev`/`staging`/`prod`. `prod` 일 때 weak default 거부 |
| `LOG_FORMAT` | `text` | config.py / 각 entry `setup_logging()` | 로그 출력 format. `text`(dev colorized·grep) 또는 `json`(외부 log aggregator). prod 는 `json` 권장 |
| `ENGINE_IMAGE` | base 기본 핀 (`ghcr.io/z-converter-assessment/assessment-engine:<version>`) | compose base | 앱 서비스 이미지. config.py 미사용 — compose 전용. 미설정 시 base `docker-compose.yml` 기본값(release CI 가 태그 semver 로 핀한 GHCR 이미지). dev override.yml 은 `assessment-engine:local`(로컬 빌드)로 덮음. GHCR public — 토큰 없이 pull |
| `PGDATA_HOST` | `postgres_data` (named volume) | compose base | postgres 영속 경로. host 절대경로 주입 시 bind mount(infra Cinder `/mnt/pgdata`), 미설정 시 named volume |
| `MQ_DATA_HOST` | `rabbitmq_data` (named volume) | compose base | rabbitmq 영속 경로. 주입 시 host bind(`/mnt/mqdata`), 미설정 시 named volume |
| `POSTGRES_HOST` | `postgres` | config.py / dev compose | PostgreSQL 호스트 (docker-compose 서비스명). prod 는 실제 host 명시 |
| `POSTGRES_PORT` | `5432` | config.py / dev compose | |
| `POSTGRES_DB` | `assessment` | config.py / dev compose | |
| `POSTGRES_USER` | `assessment` | config.py / compose | assessment 허용 — 빈값·password·admin·root·changeme 만 prod 거부 |
| `POSTGRES_PASSWORD` | `changeme` | config.py / compose | default `changeme`(weak)라 미설정 시 prod 거부. 명시 `assessment` 는 허용. 강한 secret 권장 |
| `RABBITMQ_HOST` | `rabbitmq` | config.py | 컨슈머 broker 접속 (docker-compose 서비스명). 에이전트는 본 키 안 씀 — 외부 인프라가 broker 도달 host 별도 주입 |
| `RABBITMQ_PORT` | `5672` | config.py / dev compose | |
| `RABBITMQ_VHOST` | `assessment` | config.py / compose | 전용 vhost (무슬래시). 에이전트와 동일 값. 이름에 `/` 없어 인코딩 무영향(config.py `broker_url` 은 슬래시 포함 vhost 를 `%2F`로 자동 인코딩) |
| `RABBITMQ_USER` | `assessment` | config.py / compose | assessment 허용 — 빈값·password·admin·root·changeme 만 prod 거부 |
| `RABBITMQ_PASSWORD` | `changeme` | config.py / compose | default `changeme`(weak)라 미설정 시 prod 거부. 명시 `assessment` 는 허용. 강한 secret 권장 |
| `RABBITMQ_MANAGEMENT_PORT` | `15672` | dev compose | RabbitMQ 관리 콘솔 포트 노출 (config.py 미사용) |
| `RABBITMQ_EXCHANGE` | `assessment` | config.py / agent env (repo 밖) | 에이전트·consumer routing 계약. 변경 시 양쪽 동기화 |
| `RABBITMQ_ROUTING_KEY_INVENTORY` | `server.inventory` | config.py / agent env (repo 밖) | 동일 |
| `RABBITMQ_ROUTING_KEY_METRICS` | `server.metrics` | config.py / agent env (repo 밖) | 동일 |
| `RABBITMQ_ROUTING_KEY_ERROR` | `server.error` | config.py / agent env (repo 밖) | 동일 |
| `RABBITMQ_TASK_EXCHANGE` | `assessment.tasks` | config.py | 엔진 발행 task.install + worker.result 소비 전용 exchange (collector `RABBITMQ_EXCHANGE` 와 분리). agent `WORKER_TASK_EXCHANGE` 와 값 일치 의무 |
| `RABBITMQ_TASK_QUEUE_PREFIX` | `agent.tasks` | config.py | task.install 발행 대상 호스트별 큐 prefix. full = `<prefix>.<agent_id>` (`agent_task_queue()`). agent `WORKER_TASK_QUEUE_PREFIX` 와 일치 |
| `RABBITMQ_TASK_INSTALL_KEY_PREFIX` | `task.install` | config.py | task.install 호스트별 routing key prefix. full = `<prefix>.<agent_id>` (`task_install_routing_key()`) |
| `RABBITMQ_ROUTING_KEY_TASK_RESULT` | `task.result` | config.py | worker.result 큐 바인딩 routing key (원격 호스트 결과 보고 수신). agent `WORKER_TASK_RESULT_KEY` 와 일치 의무 |
| `RABBITMQ_QUEUE_WORKER_RESULT` | `worker.result` | config.py | 엔진이 task.result 를 소비하는 단일 결과 큐 이름 |
| `TASK_INSTALL_SUCCESS_EXIT_CODES` | `{"windows":[2],"rocky:9":[3],"almalinux:9":[3],"ol:9":[3],"centos:9":[3]}` | config.py | task.result 성공 보정 정책 (`task_policy.effective_task_result`). 키 -> 성공으로 취급할 추가 exit code. 키 규약(os_family 분기): Windows = family-level `"windows"`(빌드번호 아님) / Linux = `"os_id:major"`(예 `"rocky:9"`). 기본값 근거: Windows installer 는 설치 성공에도 exit 2, EL9(rocky/almalinux/ol/centos major 9)는 systemd start-limit 로 exit 3 이나 설치·ZDM 등록 성공 -> 보정. `status=failure`+`failure_reason=script_failed`+키 일치+exit_code 포함일 때만. JSON override 예: `{"windows":[2],"rocky:9":[3]}` |
| `RABBITMQ_WORKER_USER` | `assessment` | agent env (repo 밖) | 원격 호스트 worker 가 사용할 AMQP user. 비어 있으면 worker 자동 비활성 (collector 만 동작) |
| `RABBITMQ_WORKER_PASSWORD` | `assessment` | agent env (repo 밖) | `RABBITMQ_WORKER_USER` 의 암호. heredoc 안에서 `RABBITMQ_WORKER_PASS` 매핑 |
| `WORKER_TASK_EXCHANGE` | `assessment.tasks` | agent env (repo 밖) | task.install/task.result 전용 exchange. collector exchange 와 분리. 엔진 `RABBITMQ_TASK_EXCHANGE` 와 값 일치 의무 |
| `WORKER_TASK_QUEUE_PREFIX` | `agent.tasks` | agent env (repo 밖) | 원격 호스트별 큐 prefix. full name = `<prefix>.<agent_id>`. 엔진 `RABBITMQ_TASK_QUEUE_PREFIX` 와 일치 |
| `WORKER_TASK_RESULT_KEY` | `task.result` | agent env (repo 밖) | 원격 호스트 → 엔진 결과 보고 routing key. 엔진 `RABBITMQ_ROUTING_KEY_TASK_RESULT` 와 일치 |
| `WORKER_DOWNLOAD_ALLOWED_HOSTS` | `""` (빈값) | agent env (repo 밖) | task.install download.url 의 host 화이트리스트 (case-insensitive 정확 매치). 빈 whitelist 면 전부 거부 — 운영자가 ZDM_DEFAULT_IP host 를 등록 |
| `REDIS_HOST` | `redis` | config.py | (docker-compose 서비스명). prod 는 실제 host |
| `REDIS_PORT` | `6379` | config.py | |
| `REDIS_MAXMEMORY` | `256mb` | dev compose (redis command) | Redis maxmemory cap. prod 튜닝 가능 |
| `REDIS_MAXMEMORY_POLICY` | `volatile-lru` | dev compose (redis command) | maxmemory 도달 시 eviction policy. TTL 키 우선 evict |
| `WEB_PORT` | `8000` | config.py / dev compose | Web UI 접속 포트 |
| `WEB_RELOAD` | `false` | config.py / dev compose | uvicorn auto-reload. dev hot-reload 전용 (dev compose 가 `true` 주입, `./:/app` bind mount 와 짝). prod 미설정 → false (코드 변경 감시 프로세스 불필요·bind mount 없는 wheel/image 배포에 무의미) |
| `INSTALL_TIMEOUT_SEC` | `600` | config.py | install.sh wall-clock timeout (픽업 후 스크립트 실행 예산). 원격 host worker 가 SIGTERM/SIGKILL |
| `INSTALL_TASK_DEADLINE_SEC` | `3600` | config.py | install task 배달/마감 창(초). engine `tasks.deadline_at` + broker `agent.tasks.<agent_id>` 큐 `x-message-ttl` 동일 창(오프라인 호스트 store-and-forward 유예). `INSTALL_TIMEOUT_SEC`(600) 와 별개 개념 |
| `ZDM_DEFAULT_IP` | `""` (빈값) | config.py | ZDM 서버 기본 좌표. install 모달 default. POST `/tasks/install` 의 `zdm_ip` 누락 시 fallback. install.sh 의 `-s` 인자 + agent download.url host. 운영자가 real ZDM 좌표 주입. startup 거부 없음 — 잘못된 ZDM 발행은 런타임 503 + agent host whitelist 가 방어 |
| `ZDM_DEFAULT_USER` | `admin@zconverter.com` | config.py | ZDM 관리자 계정 기본값. POST body `zdm_user` 누락 시 fallback. install.sh 의 `-u` 인자. startup 거부 없음 (secret 아님) |
| `ZDM_META_CONNECT_TIMEOUT_SEC` | `5.0` | config.py | ZDM 메타 조회 HTTP connect timeout |
| `ZDM_META_TOTAL_TIMEOUT_SEC` | `120.0` | config.py | ZDM 메타 조회 HTTP total timeout (HEAD + GET full). 44MB 가정, 동일 LAN 1~2s |
| `REDIS_TTL_ZDM_PACKAGE_SHA256` | `21600` (6h) | config.py | ETag 기반 sha256 cache TTL |
| `SQLALCHEMY_ECHO` | `false` | config.py | SQLAlchemy 엔진 SQL 로깅. dev 디버깅 시 true (운영 환경은 false 유지 — 로그 폭증·secret 노출 위험) |

### `env.example` 에 없고 config.py default 만 정의된 키

다음 키는 운영 변경 빈도가 낮아 의도적으로 env 노출 안 함 — `env.example` 미수록. 필요 시 운영자가 env 로 override 가능 (BaseSettings 필드라 자동 인식).

- `redis_ttl_idempotent` (24h)·`redis_ttl_online` (5min)·`redis_ttl_token` (1h)
- `redis_ttl_last_agent_start` (24h)·`redis_ttl_agent_restarts` (1h 슬라이딩 윈도우)·`redis_ttl_time_invariant_warned` (1h)
- `redis_ttl_cache_metrics` (1min — 대시보드 스냅샷 cache-aside)·`redis_ttl_cache_detail` (5min — 서버 상세 ViewModel cache-aside)
- `redis_key_*` 패턴 (cache:*·idempotent·online·token·last_agent_start·agent_restarts·time_invariant_warned)
- WorkerSettings 전용 (worker 프로세스만): `report_worker_poll_interval_sec` (2s)·`report_worker_stale_seconds` (600 — running 잔류 회수)·`report_worker_shutdown_timeout_sec` (10s)·`install_reaper_interval_sec` (60s)·`install_reaper_shutdown_timeout_sec` (5s)
- `zdm_package_path`·`zdm_package_script`·`zdm_package_path_windows` — ZDM 제품 패키지 layout 상수 (거의 안 바뀜, ZDM 버전업 시에나). 배포 변동값 아니라 미수록 — task.install download.url 은 `http://{ZDM_IP}{zdm_package_path}` 조립
- `agent_restart_alert_threshold` (기본 3) — 에이전트 재시작 alert 임계값(1h 윈도우). 운영 alert 튜닝 노브, 평소 default 유지 — 필요 시 env override

---

## 13. 운영 체크리스트 (외부 인프라가 prod 배포 시)

본 repo 범위 밖이지만 외부 인프라가 충족해야 할 contract. ansible role·k8s manifest 등이 본 contract 만 충족하면 본 엔진 정상 기동.

- [ ] `APP_ENV=prod` 환경변수 명시
- [ ] `POSTGRES_PASSWORD`·`RABBITMQ_PASSWORD` 강한 random secret 주입 (채널은 자유 — env·systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등)
- [ ] `POSTGRES_USER`·`RABBITMQ_USER` 강한 값 권장 (assessment 는 허용 — 빈값·password·admin·root·changeme 만 거부)
- [ ] `ZDM_DEFAULT_IP`·`ZDM_DEFAULT_USER` 고객사 ZDM 좌표로 override (startup 거부는 없음 — 미설정 시 첫 install 발행이 런타임 503 또는 agent `url_not_allowed` 로 실패)
- [ ] `ZDM_PACKAGE_*` 운영 ZDM 측 패키지 layout 과 일치 (path·script)
- [ ] Alembic 마이그레이션 사전 적용 — wheel 안 `assessment_engine/_alembic.ini` + `migrations/` 활용 (`docs/guides/migrate.md`)
- [ ] DB·MQ·Redis 외부 포트 노출 없음 (reverse proxy 뒤)
- [ ] web 노출 결정 (reverse proxy 또는 직접) — `/metrics` 엔드포인트 외부 노출 안 함 (Prometheus 스크레이핑 미채택, 로그(loguru) 단일 관측 채널)
- [ ] `LOG_FORMAT=json` 권장 — 외부 log aggregator indexing (`docs/reference/observability.md`)
- [ ] 에이전트 secret 채널은 엔진과 독립 — Ansible vault·SaltStack pillar 등 별도 도구
- [ ] 기동 직후 `Settings()` 생성 시점 `ValueError` 발생 없음 — fail 시 secret 채널 점검 필요

---

## 14. 안티패턴 (금지)

- 코드 (`config.py`) 에 `if env == "prod"` 비즈니스 분기 도입 — 정책 분기 1개 지점만 허용
- `.env.production` / `.env.development` 같은 환경별 .env 동시 보유 — 활성 파일 모호
- prod 에서 `volumes: ./:/app` 코드 마운트 유지 — 컨테이너 안 `.env` 노출 + 코드 변조 위험
- 본 repo 에 prod 환경 전체를 가르는 docker compose(`docker-compose.prod.yml`) 추가 — #A0 위반(base 자체가 prod). 단 prod-safe base·file-secret overlay(`docker-compose.secrets.yml`)·`secrets/` placeholder 는 의식적 편의 제공 — 실제 secret 파일은 `secrets/*` ignore(commit 금지)
- secret 을 git 저장소에 커밋 — `.dockerignore`·`.gitignore` 의 `.env` 항목 절대 제거 금지 (카탈로그 env.example·env.dev.example 만 commit)
- 컨테이너 안에서 `/app/.env` 를 직접 read 하는 코드 추가 — pydantic-settings 의 `env_file` 폴백 외 직접 read 금지
- `secrets_dir` 강제 활성화 — 디렉토리 부재 시 noisy 경고. `os.path.isdir` 분기로 None fallback 유지

---

## 15. 핵심 의도 요약

1. secret 을 git 에 커밋하지 않는다 — `.env` 는 .gitignore (카탈로그 env.example·env.dev.example 만 commit). prod secret 은 외부 인프라의 secret 채널.
2. dev 편의성을 prod 안전성과 거래하지 않는다 — `.env` 평문은 dev 에만, prod 는 secret 채널 강제.
3. 약한 default 를 prod 로 흘려보내지 않는다 — `APP_ENV=prod` fail-fast 검증.
4. 이미지는 환경 무관 — `Dockerfile` 1개, 차이는 환경변수·compose override·secret 주입으로만.
5. 에이전트 secret 을 엔진 secret 과 분리 — 각자 독립적 라이프사이클.

이 다섯이 깨지는 PR 은 reject 대상.

## 관련 문서

- `docs/guides/deploy.md` — release artifact 활용 단계별 install·systemd 가이드
- `docs/reference/observability.md` — `LOG_FORMAT` toggle
- `docs/guides/migrate.md` — schema migrate contract
- `docs/guides/release.md` — CI release artifact 카탈로그
- CLAUDE.md #A0·#F8 — secret·PII 노출 금지 원칙
