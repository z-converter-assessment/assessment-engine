# 환경변수 관리 (Environment Variables)

본 문서는 운영 env 관리 단일 진실 — 정책·secret 채널·주입 흐름·전체 키 카탈로그·운영 체크리스트. 외부 인프라가 본 엔진을 prod 운영할 때 충족해야 할 contract 와 dev 분기 매트릭스 한 곳.

정책 출처: CLAUDE.md #A0 (외부 인프라 책임 분리) · #F8 (secret·PII 노출 금지). 본 repo 는 결과 (weak default 거부) 만 검증, secret 주입 채널 자체는 외부 인프라 자유.

---

## 1. 원칙 (12-Factor + 본 프로젝트 적용)

| # | 원칙 | 본 프로젝트 적용 |
|---|------|----------------|
| P1 | Config 을 환경변수로 분리 | `pydantic-settings` `BaseSettings`. 코드에 환경별 값 박지 않음 |
| P2 | 같은 이미지를 모든 환경에서 사용 | `Dockerfile` 1개. 환경 차이는 환경변수·compose override·secret 채널로만 |
| P3 | secret 과 일반 config 분리 | dev `.env` 평문 / prod 외부 인프라 자유 채널 (env·systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등) |
| P4 | Fail-fast 검증 | `config.py` `model_validator` 가 `APP_ENV=prod` 일 때 약한 default 거부 |
| P5 | dev 에 안전한 default + prod 에서 거부 | `_WEAK_VALUES` 집합 + ZDM dev default 거부 — prod 에서 시작 차단 |
| P6 | secret 을 코드·이미지·git 에 박지 않음 | `.dockerignore`·`.gitignore` 에 `.env` / `dev/agent.env` / `dev/.env` 명시 |

---

## 2. config vs secret 분류

같은 환경변수처럼 보여도 secret 과 일반 config 는 다르게 다뤄야 한다.

| 분류 | 정의 | 본 프로젝트 예시 | 보관 |
|------|------|----------------|------|
| config | 노출돼도 시스템이 즉시 위태롭지 않은 운영 값 | `POSTGRES_HOST`·`RABBITMQ_VHOST`·`WEB_PORT`·`APP_ENV`·`LOG_FORMAT` | `.env` 평문 OK, git 커밋 가능 (`.env.example`) |
| secret | 노출 시 즉시 무단 접근 가능한 자격 | `POSTGRES_PASSWORD`·`RABBITMQ_PASSWORD`·향후 API token / TLS key | dev 한정 `.env` 평문, prod 는 외부 인프라 자유 채널 |

경계 케이스:
- `POSTGRES_USER` — 보통 config. 단 user 자체가 권한 분리 키이면 secret. 본 프로젝트는 config 분류하되 prod 검증 시 약한 default (`assessment`) 거부.
- `ZDM_DEFAULT_IP` / `ZDM_DEFAULT_USER` — config 이지만 dev default (`192.168.3.94` · `admin@zconverter.com`) 가 prod 에 그대로 흘러가면 잘못된 ZDM 으로 install task 발행 위험. prod 검증 거부 대상.

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

docker-compose `environment:` 블록은 `env_file:` 보다 후순위라 마지막 덮어쓰기 — 컨테이너 안에서는 `environment:` 가 항상 우선.

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

dev 는 본 repo 가 제공 (`dev/docker-compose.yml`). prod 는 외부 인프라 책임 — 본 표는 외부 인프라가 만족해야 할 contract reference.

| 항목 | dev (본 repo) | prod (외부 인프라) |
|------|--------------|---------------------|
| 기동 방식 | `docker compose -f dev/docker-compose.yml up` | systemd + wheel install (`docs/operations/deployment.md`) 또는 외부 인프라 자유 (ADR 0012) |
| `APP_ENV` | `dev` | `prod` 명시 (env var 또는 EnvironmentFile) |
| 코드 마운트 (`../:/app`) | OK 빠른 반복 | NG 이미지·wheel 불변성 |
| 백킹 서비스 포트 외부 노출 | OK 5432·5672·6379·15672 | NG web 만 (또는 reverse proxy 뒤) |
| Password 주입 | `dev/.env` 평문 | 자유 채널 — env var·systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등 |
| Schema 관리 | `migrate` init-container 가 `alembic upgrade head` 1회 (ADR 0005) | 외부 인프라 ansible task 또는 systemd one-shot 으로 사전 실행 (wheel 안 `_alembic.ini` 활용) |
| Fail-fast 검증 | 약한 default 허용 | `_WEAK_VALUES` 거부 → `Settings()` 생성 시점 `ValueError` |
| restart 정책 | `unless-stopped` | `always` 권장 (systemd `Restart=always`) |
| Logging | `LOG_FORMAT=text` (colorized·grep 친화) | `LOG_FORMAT=json` 권장 (외부 log aggregator indexing) |
| web 노출 | plain HTTP port 8000 | HTTPS 외부 ingress (nginx·envoy 등) 종단, 앱은 plain |
| broker AMQP | plain (port 5672) | AMQPS (port 5671) 권장 |

---

## 6. Fail-fast 검증 (weak default 거부)

prod 환경에서 secret 누락·약한 default 통과 차단. secret 주입 채널 자체는 외부 인프라 책임 — 본 repo 는 결과만 검증.

| 위치 | 검증 대상 | 강제 시점 |
|------|---------|---------|
| `config.py` `_validate_prod_*` model_validator | `_WEAK_VALUES`(`""`/`assessment`/`password`/`admin`/`root`/`changeme`) 거부 + ZDM 좌표 dev default (`192.168.3.94`·`admin@zconverter.com`) 거부 | 앱 import 직후 (`Settings()` 인스턴스 생성 시) |

```python
@model_validator(mode="after")
def _validate_prod_web_secrets(self) -> "WebSettings":
    if self.app_env != "prod":
        return self
    if self.postgres_password.get_secret_value() in _WEAK_VALUES:
        raise ValueError("POSTGRES_PASSWORD is unset or uses a dev default in prod. ...")
    if self.postgres_user in _WEAK_VALUES:
        raise ValueError("POSTGRES_USER must be set to a non-default value in prod.")
    if self.zdm_default_ip == _ZDM_DEV_DEFAULT_IP:
        raise ValueError("ZDM_DEFAULT_IP is unset or uses the dev default in prod. ...")
    if self.zdm_default_user == _ZDM_DEV_DEFAULT_USER:
        raise ValueError("ZDM_DEFAULT_USER is unset or uses the dev default in prod. ...")
    return self
```

발동 위치 (multi-node 분리 시):
- web 노드: `WebSettings` + `DiagnosticSettings` → POSTGRES·RABBITMQ password weak default + ZDM_DEFAULT_IP/USER dev default 거부
- consumer 노드: `ConsumerSettings` → POSTGRES·RABBITMQ password weak default 거부
- diagnostic-worker·scheduler 노드: `DiagnosticSettings` → POSTGRES·RABBITMQ password weak default 거부

효과:
- prod 에서 `.env` 미주입·dev default 잔존 시 `Settings()` 호출이 즉시 `ValueError` → 컨테이너 crash·systemd unit fail.
- 운영자가 secret 채널 점검 신호 즉시 수신.

---

## 7. Secret 채널 (외부 인프라 선택)

본 repo 는 어떤 채널이든 지원 — pydantic-settings 가 OS env 우선, `secrets_dir` fallback.

| 채널 | 어떻게 동작 | 적합 환경 |
|------|----------|---------|
| `.env` 평문 | `env_file` 또는 `--env-file` | 로컬 dev (본 repo 채택) |
| 환경변수 직접 | systemd `Environment=`·shell export | 작은 prod, 모든 OS |
| systemd `EnvironmentFile=` | 파일 1개에 KEY=VALUE | VM + systemd 운영 (ADR 0012 정합) |
| Docker secrets | `/run/secrets/*` 마운트 + pydantic `secrets_dir` | Docker 운영자 결정 시 |
| SOPS/age + git | git 에 암호화 커밋, 운영 시 복호화 후 env 또는 file 주입 | GitOps |
| Vault / AWS Secrets Manager / k8s External Secrets | 외부 secret manager → env 또는 file 주입 | 다중 환경·동적 회전 |

본 repo 책임 한계:
- pydantic-settings 가 OS env·`secrets_dir` 둘 다 지원 — 외부 인프라 채널 선택 자유
- `_validate_prod_*` 가 결과 (weak default 거부) 만 검증 — 채널 자체는 본 repo 무관

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
   docker-compose env_file        config.py BaseSettings        pipeline-up.sh source dev/agent.env
   → 컨테이너 환경변수 주입       → Python 인스턴스 필드        → /etc/assessment-agent.env
   → environment: 블록이          → 환경변수 > .env > default   → Lima VM 안 에이전트로 전달
     일부 키 강제 override        (cwd /app/.env 도 read)       → RABBITMQ_HOST 는 별도 주입
                │
                └─ (4) 컨테이너 안 Python 시작 시 (1)+(2) 결합:
                       환경변수가 이미 주입돼 있으므로 (2) 의 .env read 는 redundant
                       (호스트 직접 실행 시에만 (2) 의 .env 가 의미 있음 — fallback)
```

---

## 9. 컴포넌트별 read 매트릭스 (multi-node 분리 시)

본 repo 의 4 컴포넌트 (web · consumer · diagnostic-worker · diagnostic-scheduler) 가 각자 다른 키 집합을 read. multi-node 분리 배포 시 어느 노드에 어느 키 inject 할지 reference.

| 키 그룹 | web | consumer | diagnostic-worker | diagnostic-scheduler |
|--------|:---:|:--------:|:-----------------:|:--------------------:|
| `APP_ENV`·`LOG_FORMAT` | 의무 | 의무 | 의무 | 의무 |
| `POSTGRES_*` | 의무 | 의무 | 의무 | 의무 |
| `REDIS_*` | 의무 | 의무 | 의무 | 의무 |
| `RABBITMQ_*` (broker 접속) | 의무 (진단 publish) | 의무 (consume) | 의무 (consume) | 의무 (publish) |
| `RABBITMQ_ROUTING_KEY_*`·`RABBITMQ_EXCHANGE` | 의무 | 의무 | 의무 | 의무 |
| `WORKER_*` (worker.result·task.install) | 의무 (task.install publish) | 의무 (worker.result consume) | 선택 | 선택 |
| `WEB_PORT`·`INSTALL_TIMEOUT_SEC`·`ZDM_*`·`ZDM_PACKAGE_*`·`AGENT_RESTART_ALERT_THRESHOLD` | 의무 | 사용 안 함 | 사용 안 함 | 사용 안 함 |
| `LLM_*`·`OLLAMA_*` | 사용 안 함 | 사용 안 함 | 의무 | 사용 안 함 |
| `DIAGNOSTIC_ENABLED`·`DIAGNOSTIC_QUEUE_*`·`DIAGNOSTIC_ROUTING_KEY` | 의무 (publish gate) | 사용 안 함 | 의무 (consume gate) | 의무 (cron gate + publish) |
| `DIAGNOSTIC_SCHEDULE_CRON`·`DIAGNOSTIC_RETENTION_DAYS`·`DIAGNOSTIC_ACTIVE_SERVER_WINDOW_HOURS` | 사용 안 함 | 사용 안 함 | 사용 안 함 | 의무 |
| `WORKER_JOB_TIMEOUT_SECONDS` | 사용 안 함 | 사용 안 함 | 의무 | 사용 안 함 |
| `SQLALCHEMY_ECHO` | 의무 | 의무 | 의무 | 의무 |

코드 단일 진실 (Composition Root, CLAUDE.md #F4):
- `src/assessment_engine/config.py` — class 정의만 (인스턴스 0)
- `src/assessment_engine/web/settings.py` — WebSettings + DiagnosticSettings
- `src/assessment_engine/consumer/settings.py` — ConsumerSettings
- `src/assessment_engine/diagnostic/settings.py` — DiagnosticSettings
- `src/assessment_engine/db/session.py`·`cache/redis.py` — 자체 WebSettings (공통 db layer)

---

## 10. 정석 주입 패턴 (운영 복잡도 단계별)

| 단계 | 패턴 | 적합 환경 | 외부 인프라 구현 |
|------|------|----------|---------------|
| A. 단일 `.env` 모든 노드 동일 inject | 한 파일 전부 — 단순 | 단일 host 또는 dev | docker-compose `env_file`·systemd `EnvironmentFile=/etc/assessment-engine.env` |
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
- prod 에서는 에이전트가 K8s/Docker 내부에 있지 않으므로 Docker secrets 적용 불가 — 별도 secret 도구 필요.

현재 dev: `dev/agent.env.example` 복사 → 운영 값 수정 → `./dev/pipeline-up.sh` 가 Lima VM 안 `/etc/assessment-agent.env` 로 옮김. RABBITMQ_HOST 는 pipeline-up.sh 가 `host.lima.internal` 상수로 별도 주입.

prod: Ansible vault·SaltStack pillar 등으로 `/etc/assessment-agent.env` 생성 — Lima 구성은 dev 한정으로 격리.

호스트명 정책 (dev compose 한정):

기본값의 호스트명 (`postgres`·`rabbitmq`·`redis`) 은 docker-compose 서비스명. compose network 내부에서만 해석. 분산 배포 (VM 별 다른 호스트) 에는 인식 안 됨 — 외부 인프라가 실제 host (IP 또는 FQDN) 로 override 의무.

---

## 12. 전체 키 카탈로그 (`.env.example` 순서)

| 키 | 기본값 | 사용처 | 설명 |
|----|--------|--------|------|
| `APP_ENV` | `dev` | config.py / docker-compose | 환경 마커. `dev`/`staging`/`prod`. `prod` 일 때 weak default 거부 |
| `LOG_FORMAT` | `text` | config.py / 각 entry `setup_logging()` | 로그 출력 format. `text`(dev colorized·grep) 또는 `json`(외부 log aggregator). prod 는 `json` 권장 |
| `POSTGRES_HOST` | `postgres` | config.py / dev compose | PostgreSQL 호스트 (docker-compose 서비스명). prod 는 실제 host 명시 |
| `POSTGRES_PORT` | `5432` | config.py / dev compose | |
| `POSTGRES_DB` | `assessment` | config.py / dev compose | |
| `POSTGRES_USER` | `assessment` | config.py / dev compose | prod 에서 weak default 거부 |
| `POSTGRES_PASSWORD` | `assessment` | config.py / dev compose | prod 에서 weak default 거부 (의무 강한 secret) |
| `RABBITMQ_HOST` | `rabbitmq` | config.py | 컨슈머 broker 접속 (docker-compose 서비스명). 에이전트는 본 키 안 씀 — pipeline-up.sh 가 `host.lima.internal` 별도 주입 |
| `RABBITMQ_PORT` | `5672` | config.py / dev compose | |
| `RABBITMQ_VHOST` | `/assessment` | config.py / dev compose / pipeline-up.sh | 전용 vhost. 에이전트와 동일 값. AMQP URL 의 `/` 는 `%2F` 인코딩 (config.py `broker_url` 자동) |
| `RABBITMQ_USER` | `assessment` | config.py / dev compose / dev/agent.env | prod 에서 weak default 거부 |
| `RABBITMQ_PASSWORD` | `assessment` | config.py / dev compose / dev/agent.env | prod 에서 weak default 거부 (의무 강한 secret) |
| `RABBITMQ_MANAGEMENT_PORT` | `15672` | dev compose | RabbitMQ 관리 콘솔 포트 노출 (config.py 미사용) |
| `RABBITMQ_EXCHANGE` | `assessment` | config.py / dev/agent.env | 에이전트 ↔ consumer routing 계약. 변경 시 양쪽 동기화 |
| `RABBITMQ_ROUTING_KEY_INVENTORY` | `server.inventory` | config.py / dev/agent.env | 동일 |
| `RABBITMQ_ROUTING_KEY_METRICS` | `server.metrics` | config.py / dev/agent.env | 동일 |
| `RABBITMQ_ROUTING_KEY_ERROR` | `server.error` | config.py / dev/agent.env | 동일 |
| `RABBITMQ_WORKER_USER` | `assessment` | dev/agent.env | 원격 호스트 worker 가 사용할 AMQP user. 비어 있으면 worker 자동 비활성 (collector 만 동작) |
| `RABBITMQ_WORKER_PASSWORD` | `assessment` | dev/agent.env | `RABBITMQ_WORKER_USER` 의 암호. heredoc 안에서 `RABBITMQ_WORKER_PASS` 매핑 |
| `WORKER_TASK_EXCHANGE` | `assessment.tasks` | config.py / dev/agent.env | task.install/task.result 전용 exchange. collector exchange 와 분리 |
| `WORKER_TASK_QUEUE_PREFIX` | `agent.tasks` | dev/agent.env | 원격 호스트별 큐 prefix. full name = `<prefix>.<machine_id>` |
| `WORKER_TASK_RESULT_KEY` | `task.result` | dev/agent.env | 원격 호스트 → 엔진 결과 보고 routing key |
| `WORKER_DOWNLOAD_ALLOWED_HOSTS` | `host.lima.internal` | dev/agent.env | task.install download.url 의 host 화이트리스트 (case-insensitive 정확 매치) |
| `REDIS_HOST` | `redis` | config.py | (docker-compose 서비스명). prod 는 실제 host |
| `REDIS_PORT` | `6379` | config.py | |
| `REDIS_MAXMEMORY` | `256mb` | dev compose (redis command) | Redis maxmemory cap. prod 튜닝 가능 |
| `REDIS_MAXMEMORY_POLICY` | `volatile-lru` | dev compose (redis command) | maxmemory 도달 시 eviction policy. TTL 키 우선 evict |
| `WEB_PORT` | `8000` | config.py / dev compose | Web UI 접속 포트 |
| `INSTALL_TIMEOUT_SEC` | `600` | config.py | install.sh wall-clock timeout. 원격 host worker 가 SIGTERM/SIGKILL |
| `ZDM_DEFAULT_IP` | `192.168.3.94` | config.py | ZDM 서버 기본 좌표. install 모달 default. POST `/tasks/install` 의 `zdm_ip` 누락 시 fallback. install.sh 의 `-s` 인자 + agent download.url host. prod 에서 dev default 그대로면 거부 |
| `ZDM_DEFAULT_USER` | `admin@zconverter.com` | config.py | ZDM 관리자 계정 기본값. POST body `zdm_user` 누락 시 fallback. install.sh 의 `-u` 인자. prod 에서 dev default 그대로면 거부 |
| `ZDM_PACKAGE_PATH` | `/download/ZConverter_CloudSource_Setup_Linux.tar.gz` | config.py | ZDM 호스트의 본체 패키지 URL path. task.install download.url 은 `http://{ZDM_IP}{ZDM_PACKAGE_PATH}` 조립 |
| `ZDM_PACKAGE_SCRIPT` | `zconverter_install_source/install.sh` | config.py | tar 추출 후 실행할 스크립트 경로 |
| `ZDM_META_CONNECT_TIMEOUT_SEC` | `5.0` | config.py | ZDM 메타 조회 HTTP connect timeout |
| `ZDM_META_TOTAL_TIMEOUT_SEC` | `120.0` | config.py | ZDM 메타 조회 HTTP total timeout (HEAD + GET full). 44MB 가정, 동일 LAN 1~2s |
| `REDIS_TTL_ZDM_PACKAGE_SHA256` | `21600` (6h) | config.py | ETag 기반 sha256 cache TTL |
| `AGENT_RESTART_ALERT_THRESHOLD` | `3` | config.py | 1h 슬라이딩 윈도우 내 에이전트 재시작 횟수 임계값. attention 신호 카드 + consumer 부가 시그널 |
| `SQLALCHEMY_ECHO` | `false` | config.py | SQLAlchemy 엔진 SQL 로깅. dev 디버깅 시 true (운영 환경은 false 유지 — 로그 폭증·secret 노출 위험) |
| `PGADMIN_PORT` | `5050` | dev compose override | pgAdmin GUI 포트 (dev 전용) |
| `DIAGNOSTIC_ENABLED` | `false` | config.py | 진단 워크플로 활성 flag (ADR 0004 + 0010). false 면 POST 503 + scheduler no-op |
| `DIAGNOSTIC_ROUTING_KEY` | `diagnostic.request` | config.py | engine 내부 routing key (web·worker·scheduler 공통) |
| `DIAGNOSTIC_QUEUE_TTL_MS` | `86400000` | config.py | 큐 메시지 TTL 24h |
| `DIAGNOSTIC_QUEUE_MAX_LEN` | `100000` | config.py | 큐 max length |
| `DIAGNOSTIC_RETENTION_DAYS` | `90` | config.py | diagnostic_jobs 보존 일수 — 스케줄러 발화 시 함께 DELETE |
| `DIAGNOSTIC_SCHEDULE_CRON` | `0 3 * * *` | config.py | 스케줄러 cron (KST 03시 매일) |
| `DIAGNOSTIC_ACTIVE_SERVER_WINDOW_HOURS` | `24` | config.py | 활성 서버 정의 — last_seen_at 윈도우 |
| `LLM_PROVIDER` | `mock` | config.py | 진단 narrative 합성 client. `mock` (결정론) 또는 `ollama` (stub). 외부 LLM 도입은 ADR 0010 정정 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | config.py | `LLM_PROVIDER=ollama` 시 사용 |
| `OLLAMA_MODEL` | `llama3.1:8b` | config.py | ollama 모델명 |
| `LLM_TIMEOUT_SECONDS` | `60` | config.py | LLM 호출 cap |
| `LLM_MOCK_LATENCY_SECONDS` | `2.0` | config.py | mock client 응답 sleep (UI progress 확인용) |
| `WORKER_JOB_TIMEOUT_SECONDS` | `300` | config.py | 워커 진단 1건 전체 cap (클라이언트 polling timeout 과 정렬) |

### `.env.example` 에 없고 config.py default 만 정의된 키

다음 키는 운영 변경 빈도가 낮아 의도적으로 env 노출 안 함 — `.env.example` 미수록. 필요 시 운영자가 env 로 override 가능 (BaseSettings 필드라 자동 인식).

- `redis_ttl_idempotent` (24h)·`redis_ttl_online` (5min)·`redis_ttl_token` (1h)
- `redis_ttl_last_agent_start` (24h)·`redis_ttl_agent_restarts` (1h 슬라이딩 윈도우)·`redis_ttl_time_invariant_warned` (1h)
- `redis_key_*` 패턴 (cache:*·idempotent·online·token·last_agent_start·agent_restarts·time_invariant_warned)
- `redis_channel_metrics`

---

## 13. 운영 체크리스트 (외부 인프라가 prod 배포 시)

본 repo 범위 밖이지만 외부 인프라가 충족해야 할 contract. ansible role·k8s manifest 등이 본 contract 만 충족하면 본 엔진 정상 기동.

- [ ] `APP_ENV=prod` 환경변수 명시
- [ ] `POSTGRES_PASSWORD`·`RABBITMQ_PASSWORD` 강한 random secret 주입 (채널은 자유 — env·systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등)
- [ ] `POSTGRES_USER`·`RABBITMQ_USER` 도 dev default(`assessment`) 아닌 값 (weak default 거부 대상)
- [ ] `ZDM_DEFAULT_IP`·`ZDM_DEFAULT_USER` 고객사 ZDM 좌표로 override (dev default 그대로면 fail)
- [ ] `ZDM_PACKAGE_*` 운영 ZDM 측 패키지 layout 과 일치 (path·script)
- [ ] Alembic 마이그레이션 사전 적용 — wheel 안 `assessment_engine/_alembic.ini` + `_migrations/` 활용 (`docs/operations/alembic.md`)
- [ ] DB·MQ·Redis 외부 포트 노출 없음 (reverse proxy 뒤)
- [ ] web 노출 결정 (reverse proxy 또는 직접) — `/metrics` 는 외부 노출 금지 (ADR 0011)
- [ ] `LOG_FORMAT=json` 권장 — 외부 log aggregator indexing (`docs/operations/observability.md`)
- [ ] 에이전트 secret 채널은 엔진과 독립 — Ansible vault·SaltStack pillar 등 별도 도구
- [ ] 기동 직후 `Settings()` 생성 시점 `ValueError` 발생 없음 — fail 시 secret 채널 점검 필요

---

## 14. 안티패턴 (금지)

- 코드 (`config.py`) 에 `if env == "prod"` 비즈니스 분기 도입 — 정책 분기 1개 지점만 허용
- `.env.production` / `.env.development` 같은 환경별 .env 동시 보유 — 활성 파일 모호
- prod 에서 `volumes: ./:/app` 코드 마운트 유지 — 컨테이너 안 `.env` 노출 + 코드 변조 위험
- 본 repo 에 prod 운영용 docker compose·secret 디렉토리 추가 — CLAUDE.md #A0 위반. prod 운영은 외부 인프라 책임
- secret 을 git 저장소에 커밋 — `.dockerignore`·`.gitignore` 의 `.env`·`dev/.env`·`dev/agent.env` 항목 절대 제거 금지
- 컨테이너 안에서 `/app/.env` 를 직접 read 하는 코드 추가 — pydantic-settings 의 `env_file` 폴백 외 직접 read 금지
- `secrets_dir` 강제 활성화 — 디렉토리 부재 시 noisy 경고. `os.path.isdir` 분기로 None fallback 유지

---

## 15. 핵심 의도 요약

1. secret 을 git 에 커밋하지 않는다 — `.env`·`dev/agent.env`·`dev/.env` 모두 .gitignore. prod secret 은 외부 인프라의 secret 채널.
2. dev 편의성을 prod 안전성과 거래하지 않는다 — `.env` 평문은 dev 에만, prod 는 secret 채널 강제.
3. 약한 default 를 prod 로 흘려보내지 않는다 — `APP_ENV=prod` fail-fast 검증.
4. 이미지는 환경 무관 — `Dockerfile` 1개, 차이는 환경변수·compose override·secret 주입으로만.
5. 에이전트 secret 을 엔진 secret 과 분리 — 각자 독립적 라이프사이클.

이 다섯이 깨지는 PR 은 reject 대상.

## 관련 문서

- `docs/operations/deployment.md` — release artifact 활용 단계별 install·systemd 가이드
- `docs/operations/observability.md` — `LOG_FORMAT` toggle + Prometheus metrics
- `docs/operations/alembic.md` — schema migrate contract
- `docs/operations/release.md` — CI release artifact 카탈로그
- CLAUDE.md #A0·#F8 — secret·PII 노출 금지 원칙
