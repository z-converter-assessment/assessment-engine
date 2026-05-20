# Prod Contract

정책: CLAUDE.md #A0·#F8. 본 문서는 외부 인프라가 본 엔진을 prod 운영할 때 충족해야 할 환경변수·secret contract 단일 진실. 키 catalog는 `docs/operations/env.md`. install·실행 단계별 가이드는 `docs/operations/deployment.md`.

본 repo는 prod 운영 자체를 책임지지 않으며 (CLAUDE.md #A0), 본 문서의 contract만 충족하면 외부 인프라가 어떤 도구·OS·secret 채널을 쓰든 본 엔진이 정상 동작. 본 프로젝트는 12-Factor III Config 원칙 기준 — 코드·산출물은 환경 무관, 환경별 동작은 환경변수·secret 채널만으로 결정.

dev 환경 운영은 별도 — `docs/development/docker.md` (dev compose 명세) + 루트 `README.md` "Quick Start" 절. 본 문서는 prod 한정.

---

## 1. 원칙

| # | 원칙 | 본 프로젝트 적용 |
|---|------|---------------|
| P1 | Config을 환경변수로 분리 | `pydantic-settings` `BaseSettings`. 코드에 환경별 값 박지 않음 |
| P2 | 같은 이미지를 모든 환경에서 사용 | `Dockerfile` 1개. 환경 차이는 환경변수·compose 파일 override·secrets로만 |
| P3 | secret과 일반 config 분리 | dev: `.env` 평문 / prod: 외부 인프라 자유 채널 (env·systemd `EnvironmentFile`·Vault·k8s Secret·Docker secrets 등) |
| P4 | Fail-fast 검증 | `src/assessment_engine/config.py` `model_validator(mode="after")`가 `app_env=prod`일 때 약한 default 거부 |
| P5 | dev에 안전한 default + prod에서 거부 | `_WEAK_VALUES` 집합 (`assessment`/`password`/`admin` 등) — prod에서 unset 또는 기본값 시작 차단 |
| P6 | secret을 코드·이미지·git에 박지 않음 | `.dockerignore`에 `.env`, `.gitignore`에 `.env`/`dev/agent.env` |

---

## 2. 변수 분류 — config vs secret

같은 환경변수처럼 보여도 secret과 일반 config는 다르게 다뤄야 한다.

| 분류 | 정의 | 본 프로젝트 예시 | 보관 |
|------|------|--------------|------|
| config | 노출돼도 시스템이 즉시 위태롭지 않은 운영 값 | `POSTGRES_HOST`, `RABBITMQ_VHOST`, `WEB_PORT`, `RABBITMQ_EXCHANGE`, `APP_ENV`, `LOG_FORMAT` | `.env` 평문 OK, git 커밋 가능(.env.example) |
| secret | 노출 시 즉시 무단 접근 가능한 자격 | `POSTGRES_PASSWORD`, `RABBITMQ_PASSWORD`, 향후 도입할 API token / TLS key | dev 한정 `.env` 평문, prod는 외부 인프라 자유 채널 (env·systemd `EnvironmentFile`·Vault·k8s Secret·Docker secrets 등) |

경계 케이스:
- `POSTGRES_USER`: 보통 config. 단 user 자체가 권한 분리 키이면 secret 취급 — 본 프로젝트는 config로 분류하되 prod 검증 시 약한 default(`assessment`)는 거부.
- `RABBITMQ_VHOST`: config. 단 vhost가 마이크로서비스별 격리 단위이면 routing 키와 함께 계약(contract) 일부.

규칙: 어떤 변수가 config인지 secret인지 헷갈리면 secret으로 간주. 잘못 분류해 secret을 평문 노출하는 비용이 그 반대보다 크다.

코드 측 의무 (CLAUDE.md F8):
- secret으로 분류된 필드는 `Settings`에서 `SecretStr` 타입 의무 — `__repr__`이 자동 마스킹해 로그·예외·디버거 출력에 평문 노출 차단. 신규 secret 도입 시 추가 의무.
- 사용 시점에서만 `.get_secret_value()`로 평문 추출 — 변수에 담아 재사용 금지 (재사용 시 마스킹 우회).

---

## 3. 우선순위 체인

`pydantic-settings`의 BaseSettings는 다음 순서로 값을 결정한다 — 뒤에 있을수록 우선.

```
[lowest]  코드 default (config.py)
       v
       .env 파일 (cwd 또는 /app/.env)
       v
       secrets_dir 파일 (<SECRETS_DIR>/<field_name>, default `/run/secrets`)
       v
       OS 환경변수 (systemd Environment / docker-compose env / orchestrator inject)
[highest] 명시적 init kwargs (테스트용)
```

본 프로젝트의 `src/assessment_engine/config.py`:

```python
_SECRETS_DIR = os.environ.get("SECRETS_DIR", "/run/secrets")
_SECRETS_DIR = _SECRETS_DIR if os.path.isdir(_SECRETS_DIR) else None

model_config = SettingsConfigDict(
    env_file=".env",
    secrets_dir=_SECRETS_DIR,
    extra="ignore",
)
```

`SECRETS_DIR` env로 secrets 디렉토리 경로 override 가능 (default `/run/secrets`). 디렉토리가 존재할 때만 활성 — dev 호스트에선 보통 None이라 pydantic이 file read 단계 skip.

중요: secret 파일명은 pydantic 필드명과 정확히 일치해야 한다.
- 외부 인프라가 secret을 파일 채널로 주입한다면 `<SECRETS_DIR>/postgres_password` → pydantic `postgres_password: SecretStr` 필드 자동 매핑.
- env 채널이면 `POSTGRES_PASSWORD=<value>`로 환경변수 주입 — 본 채널이 secrets_dir보다 우선.

---

## 4. APP_ENV 마커

코드가 자기 환경을 알아야 분기 가능한 동작이 있다. 본 프로젝트는 단 두 곳에서만 분기:

| 분기점 | 동작 |
|------|------|
| `src/assessment_engine/config.py` `model_validator` | `app_env=prod`일 때 약한 default 거부 (fail-fast) |
| `src/assessment_engine/web/main.py` `lifespan` | (이전 결정 — ADR 0005로 lifespan create_all 제거됨. 본 행은 historical record) |

원칙: 코드 분기는 최소화. "환경 자체가 환경변수로 결정"되는 게 이상이고, APP_ENV 분기는 운영 정책 변경(검증 강도·schema 관리 주체)에만 사용한다. 비즈니스 로직 분기는 금지.

값:

| APP_ENV | 의도 |
|---------|------|
| `dev` | 로컬 개발. 평문 .env, 자동 schema, 약한 default 허용 |
| `staging` | prod 유사 환경. dev와 동일 동작 (현재는 분리 정책 없음) |
| `prod` | 프로덕션. fail-fast, Alembic schema, secret 채널 강제 |

---

## 5. dev/prod 차이 매트릭스

dev는 본 repo가 제공(docker-compose 단일 호스트). prod는 외부 인프라 책임(CLAUDE.md #A0) — 본 표는 외부 인프라가 인프라 도구로 어떤 contract를 만족해야 하는가의 reference.

| 항목 | dev (본 repo) | prod (외부 인프라 책임) |
|------|--------------|----------------------|
| 기동 방식 | `docker compose up` (단일 docker-compose.yml) | systemd + wheel install (`docs/operations/deployment.md` 4절 inline 예시 — ADR 0012) 또는 외부 인프라 자유 |
| `APP_ENV` | `dev` (override가 강제) | `prod` 명시 (env var 또는 EnvironmentFile) |
| 코드 마운트 (`./:/app`) | OK 빠른 반복 | NG 이미지·wheel 불변성 |
| 백킹 서비스 포트 외부 노출 | OK 5432·5672·6379·15672 | NG web만 (또는 reverse proxy 뒤) |
| Password 주입 | `.env` 평문 (`env_file: .env`) | 자유 채널 — env var·systemd `EnvironmentFile`·Vault·k8s Secret·Docker secrets 등. 본 repo는 결과(weak default 거부)만 검증 |
| Schema 관리 | `migrate` init-container가 `alembic upgrade head` 1회 (ADR 0005) | 외부 인프라 ansible task 또는 systemd one-shot으로 `alembic upgrade head` 사전 실행 (wheel 안 `_alembic.ini` 활용) |
| Fail-fast 검증 | 약한 default 허용 | `_WEAK_VALUES` 거부 → `Settings()` 생성 시점 `ValueError` |
| restart 정책 | `unless-stopped` (현재) | `always` 권장 (systemd `Restart=always`) |
| Logging | text(colorized·grep 친화) — `LOG_FORMAT=text` default | json(외부 log aggregator indexing) — `LOG_FORMAT=json` 권장. CLAUDE.md #F7 |
| engine web (모든 endpoint) | plain HTTP port 8000 (ADR 0009) | HTTPS 외부 ingress (nginx·envoy 등) 종단, 앱은 plain |
| broker AMQP | plain (port 5672) | AMQPS (port 5671) 권장 |

---

## 6. 파일 레이아웃

```
.
├── .env.example              ← dev 카탈로그 + 안전 default (커밋)
├── .env                      ← 로컬 dev 실값 (.gitignore)
├── docker-compose.yml        ← dev 단일 compose (커밋)
├── dev/
│   ├── agent.env.example     ← 에이전트 secret 카탈로그 (커밋)
│   ├── agent.env             ← 에이전트 실값 (.gitignore)
│   ├── lima/                 ← Lima VM 정의 (macOS dev 파이프라인, arm64)
│   ├── pgadmin-servers.json  ← pgadmin dev tool 자동 등록
│   └── bin/assessment-agent  ← agent 사전 빌드 바이너리 (Linux arm64 ELF, 커밋)
├── docs/operations/deployment.md ← 4절에 systemd unit·multi-node inject 예시 inline (ADR 0012)
└── config.py                 ← BaseSettings + model_validator (`_validate_prod_*` weak default 거부)
```

prod 운영 파일·secret 디렉토리·prod compose 변형 등은 본 repo에 두지 않는다 (CLAUDE.md #A0, ADR 0012). 외부 인프라가 자기 repo·도구로 자유 결정.

왜 .env.dev / .env.prod 같은 환경별 .env 파일을 두지 않나:
- 본 repo는 dev `.env` 단일. prod는 외부 인프라가 자기 secret 채널로 주입.
- dev secret과 prod secret이 한 디렉토리에 공존하는 사고 위험 0.

---

## 7. Secret 채널 (외부 인프라 선택)

본 repo는 어떤 채널이든 지원 — pydantic-settings가 OS env 우선, `secrets_dir` fallback. `SECRETS_DIR` env로 secrets 디렉토리 경로 override 가능.

| 채널 | 어떻게 동작 | 적합 환경 |
|------|---------|---------|
| `.env` 평문 | `env_file` 또는 `--env-file` | 로컬 dev (본 repo 채택) |
| 환경변수 직접 | systemd `Environment=`·shell export | 작은 prod, 모든 OS |
| systemd `EnvironmentFile=` | 파일 1개에 KEY=VALUE | VM + systemd 운영 (ADR 0012 정합) |
| Docker secrets | `/run/secrets/*` 마운트 + pydantic `secrets_dir`(default `/run/secrets`) | Docker 운영자 결정 시 |
| SOPS/age + git | git에 암호화 커밋, 운영 시 복호화 후 env 또는 file 주입 | GitOps |
| Vault / AWS Secrets Manager / k8s External Secrets | 외부 secret manager → env 또는 file 주입 | 다중 환경·동적 회전 |

본 repo 책임 한계:
- pydantic-settings가 OS env·`secrets_dir` 둘 다 지원 — 외부 인프라 채널 선택 자유
- `_validate_prod_*`가 결과(weak default 거부)만 검증 — 채널 자체는 본 repo 무관

---

## 8. Fail-fast 검증 (weak default 거부)

prod 환경에서 secret 누락·약한 default 통과를 막기 위해 본 repo 코드에 1 layer 강제. secret 주입 채널 자체는 외부 인프라 책임(CLAUDE.md #A0) — 본 repo는 결과(weak default 거부)만 검증.

| 위치 | 검증 대상 | 강제 시점 |
|------|---------|---------|
| `config.py` `_validate_prod_*` model_validator | secret weak default (`""`/`assessment`/`password` 등) + ZDM 좌표 dev default (`192.168.3.94` / `admin@zconverter.com`) 거부 | 앱 import 직후 (`Settings()` 인스턴스 생성 시) |

```python
@model_validator(mode="after")
def _validate_prod_web_secrets(self) -> "WebSettings":
    if self.app_env != "prod":
        return self
    if self.postgres_password.get_secret_value() in _WEAK_VALUES:
        raise ValueError(
            "POSTGRES_PASSWORD is unset or uses a dev default in prod. "
            "Provide via env var or secret channel (systemd EnvironmentFile·Vault·k8s Secret 등)."
        )
    if self.postgres_user in _WEAK_VALUES:
        raise ValueError("POSTGRES_USER must be set to a non-default value in prod.")
    # ZDM 좌표 dev default 거부 — install task 가 잘못된 ZDM 으로 발행되는 사고 방지.
    if self.zdm_default_ip == _ZDM_DEV_DEFAULT_IP:
        raise ValueError("ZDM_DEFAULT_IP is unset or uses the dev default in prod.")
    if self.zdm_default_user == _ZDM_DEV_DEFAULT_USER:
        raise ValueError("ZDM_DEFAULT_USER is unset or uses the dev default in prod.")
    return self
```

효과:
- `APP_ENV=prod`로 기동 시 password가 누락/약한 default면 web/consumer/diagnostic 진입 시점 `ValueError`로 즉시 종료 → systemd가 unhealthy/restart로 노출 → 배포자가 즉시 인지.
- secret 주입 채널(env var·systemd `EnvironmentFile`·Vault·k8s Secret·Docker secrets 등) 중 어떤 채널이든 결과만 만족하면 통과.

확장 포인트 (필요 시):
- `SECRET_KEY` (FastAPI session 등) 도입 시 동일 패턴 적용.
- `REDIS_PASSWORD` 도입 시 동일.
- pydantic `Field(min_length=N)`로 password 길이 강제 (외부 인프라가 약한 길이 secret을 주입해도 차단).

---

## 9. 에이전트 secret 채널 분리

에이전트는 엔진과 다른 노드(VM)에서 실행되며, 엔진과 다른 secret 라이프사이클을 가진다 (배포 도구·재기동 시점 다름). 따라서 secret 채널을 명시적으로 분리.

| 항목 | 엔진 | 에이전트 |
|------|------|---------|
| secret 파일 | `.env` (dev) / 외부 인프라 자유 채널 (prod) | `dev/agent.env` (Lima dev), prod에선 별도 프로비저닝 도구 (Ansible vault 등) |
| 주입 경로 | docker-compose env / secrets | pipeline-up.sh가 `dev/agent.env` source → limactl shell heredoc → `/etc/assessment-agent.env` (VM 안), `EnvironmentFile=` |
| 호스트 (broker) | docker 서비스명 `rabbitmq` | `host.lima.internal` (Lima user-mode network alias) |

왜 분리하나:
- 엔진 `.env` 변경이 에이전트 동작에 의도치 않게 영향 미치는 경로 차단.
- 에이전트의 secret 라이프사이클(VM 재프로비저닝 시점)이 엔진(컨테이너 재기동 시점)과 독립.
- prod에서는 에이전트가 K8s/Docker 내부에 있지 않으므로 Docker secrets 적용 불가 — 별도 secret 도구 필요.

현재 dev: `dev/agent.env.example` 복사 → 운영 값으로 수정 → `./scripts/pipeline-up.sh`.
향후 prod: Ansible vault 또는 SaltStack pillar로 `/etc/assessment-agent.env` 생성 — Lima 구성은 dev 전용으로 격리.

---

## 10. 운영 체크리스트 (외부 인프라가 prod 배포 시)

본 repo 범위 밖이지만 외부 인프라가 따라야 할 contract 카탈로그. 인프라 ansible role·k8s manifest·기타 도구가 이 contract만 충족하면 본 엔진 정상 기동.

- [ ] `APP_ENV=prod` 환경변수 명시
- [ ] `POSTGRES_PASSWORD`·`RABBITMQ_PASSWORD` 강한 random secret 주입 (채널은 자유 — env·systemd `EnvironmentFile`·Vault·k8s Secret·Docker secrets 등)
- [ ] `POSTGRES_USER`·`RABBITMQ_USER`도 dev default("assessment") 아닌 값 (weak default 거부 대상)
- [ ] Alembic 마이그레이션 사전 적용 — wheel 안 `assessment_engine/_alembic.ini` + `_migrations/` 활용. 큰 schema 변경은 사전에 `alembic history`·`alembic current` 검토 (`docs/operations/alembic.md`)
- [ ] DB·MQ·Redis 외부 포트 노출 없음 (reverse proxy 뒤)
- [ ] web 노출 결정 (reverse proxy 또는 직접) — `/metrics`는 외부 노출 금지 (ADR 0011)
- [ ] `LOG_FORMAT=json` 권장 — 외부 log aggregator indexing (`docs/operations/observability.md`)
- [ ] 에이전트 secret 채널은 엔진과 독립 — Ansible vault·SaltStack pillar 등 별도 도구
- [ ] 기동 직후 `Settings()` 생성 시점 `ValueError` 발생 없음 확인 — `_validate_prod_*`이 weak default 차단했다는 의미면 secret 채널 점검 필요

---

## 11. 안티패턴 (금지)

- 코드(`src/assessment_engine/config.py`)에 `if env == "prod"` 비즈니스 분기 도입 — 정책 분기(2개 지점)만 허용.
- `.env.production` / `.env.development`를 같은 호스트에 동시 보유 — 활성 파일 모호.
- prod에서 `volumes: ./:/app` 유지 — 컨테이너 안 `.env` 노출 + 코드 변조 위험.
- 본 repo에 prod 운영용 docker compose·secret 디렉토리 추가 — CLAUDE.md #A0 위반. prod 운영은 외부 인프라 책임.
- secret을 git 저장소에 커밋 — `.dockerignore` / `.gitignore`의 `.env`/`dev/agent.env` 항목 절대 제거 금지.
- 컨테이너 안에서 `/app/.env`를 직접 read하는 코드 추가 — pydantic-settings의 `env_file` 폴백 외 직접 read 금지.
- `secrets_dir` 강제 활성화 — 디렉토리 부재 시 noisy 경고. `os.path.isdir` 분기로 None fallback 유지.

---

## 12. 정리

본 전략의 핵심 의도:

1. secret을 git에 커밋하지 않는다 — `.env`·`dev/agent.env` 모두 .gitignore. prod secret은 외부 인프라의 secret 채널.
2. dev 편의성을 prod 안전성과 거래하지 않는다 — `.env` 평문은 dev에만, prod는 secrets 채널 강제.
3. 약한 default를 prod로 흘려보내지 않는다 — APP_ENV=prod의 fail-fast 검증.
4. 이미지는 환경 무관 — Dockerfile은 1개, 차이는 compose override + secret 주입으로만.
5. 에이전트 secret을 엔진 secret과 분리 — 각자 독립적 라이프사이클.

이 다섯이 깨지는 PR은 reject 대상.