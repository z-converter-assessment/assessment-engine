# 환경변수·인프라 전략

> 위치: 본 문서는 정책이다. "키가 무엇인가"는 `docs/operations/env.md`, 트레이드오프는 `docs/tradeoffs.md`, 호환되는 dev 운영 명령은 `README.md` 참조.

본 프로젝트는 12-Factor App의 III. Config 원칙을 기준으로 환경변수와 dev/prod 분리를 설계한다. 코드·이미지는 환경에 무관하게 동일하고, 환경별 동작은 환경변수와 secret 주입 채널로만 결정된다.

---

## 1. 원칙

| # | 원칙 | 본 프로젝트 적용 |
|---|------|---------------|
| P1 | Config을 환경변수로 분리 | `pydantic-settings` `BaseSettings`. 코드에 환경별 값 박지 않음 |
| P2 | 같은 이미지를 모든 환경에서 사용 | `Dockerfile` 1개. 환경 차이는 환경변수·compose 파일 override·secrets로만 |
| P3 | secret과 일반 config 분리 | dev: `.env` 평문 / prod: Docker secrets (`/run/secrets/*`) |
| P4 | Fail-fast 검증 | `src/assessment_engine/config.py` `model_validator(mode="after")`가 `app_env=prod`일 때 약한 default 거부 |
| P5 | dev에 안전한 default + prod에서 거부 | `_WEAK_VALUES` 집합 (`assessment`/`password`/`admin` 등) — prod에서 unset 또는 기본값 시작 차단 |
| P6 | secret을 코드·이미지·git에 박지 않음 | `.dockerignore`에 `.env`, `.gitignore`에 `.env`/`infra/agent.env`/`secrets/*` |

---

## 2. 변수 분류 — config vs secret

같은 환경변수처럼 보여도 secret과 일반 config는 다르게 다뤄야 한다.

| 분류 | 정의 | 본 프로젝트 예시 | 보관 |
|------|------|--------------|------|
| config | 노출돼도 시스템이 즉시 위태롭지 않은 운영 값 | `POSTGRES_HOST`, `RABBITMQ_VHOST`, `WEB_PORT`, `RABBITMQ_EXCHANGE`, `APP_ENV` | `.env` 평문 OK, git 커밋 가능(.env.example) |
| secret | 노출 시 즉시 무단 접근 가능한 자격 | `POSTGRES_PASSWORD`, `RABBITMQ_PASSWORD`, 향후 도입할 API token / TLS key | dev 한정 `.env` 평문, prod는 별도 채널 (Docker secrets / Vault / K8s Secret) |

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
       secrets_dir 파일 (/run/secrets/<field_name>)
       v
       OS 환경변수 (docker-compose env / orchestrator inject)
[highest] 명시적 init kwargs (테스트용)
```

본 프로젝트의 `src/assessment_engine/config.py`:

```python
model_config = SettingsConfigDict(
    env_file=".env",
    secrets_dir="/run/secrets" if os.path.isdir("/run/secrets") else None,
    extra="ignore",
)
```

`secrets_dir`은 디렉토리가 존재할 때만 설정. dev 호스트에선 `/run/secrets`가 없으니 None — pydantic이 secret 파일 read 단계를 skip. prod 컨테이너에선 docker-compose의 `secrets:` 블록이 마운트하면 자동 read.

중요: secret 파일명은 pydantic 필드명과 정확히 일치해야 한다.
- `secrets/postgres_password` → 마운트 후 `/run/secrets/postgres_password` → pydantic의 `postgres_password: SecretStr` 필드에 자동 매핑.

docker-compose의 `${VAR}` 치환은 별개 레이어: compose는 YAML 파싱 시점에 호스트 `.env`를 읽어 placeholder를 채우고, 그 결과를 OS env로 주입. pydantic의 우선순위 체인과는 다른 단계.

---

## 4. APP_ENV 마커

코드가 자기 환경을 알아야 분기 가능한 동작이 있다. 본 프로젝트는 단 두 곳에서만 분기:

| 분기점 | 동작 |
|------|------|
| `src/assessment_engine/config.py` `model_validator` | `app_env=prod`일 때 약한 default 거부 (fail-fast) |
| `src/assessment_engine/web/main.py` `lifespan` | `app_env=prod`일 때 schema 자동 생성 skip (Alembic 위임) |

원칙: 코드 분기는 최소화. "환경 자체가 환경변수로 결정"되는 게 이상이고, APP_ENV 분기는 운영 정책 변경(검증 강도·schema 관리 주체)에만 사용한다. 비즈니스 로직 분기는 금지.

값:

| APP_ENV | 의도 |
|---------|------|
| `dev` | 로컬 개발. 평문 .env, 자동 schema, 약한 default 허용 |
| `staging` | prod 유사 환경. dev와 동일 동작 (현재는 분리 정책 없음) |
| `prod` | 프로덕션. fail-fast, Alembic schema, secret 채널 강제 |

---

## 5. dev/prod 차이 매트릭스

| 항목 | dev | prod |
|------|-----|------|
| Compose 파일 | `docker-compose.yml` + `docker-compose.override.yml` (자동) | `docker-compose.yml` + `docker-compose.prod.yml` (명시 호출) |
| Compose 호출 | `docker compose up` | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` |
| `APP_ENV` | `dev` (override가 강제) | `prod` (prod.yml이 강제) |
| 코드 마운트 (`./:/app`) | OK 빠른 반복 | NG 이미지 불변성 |
| 백킹 서비스 포트 외부 노출 | OK 5432·5672·6379·15672 | NG web만 (또는 reverse proxy 뒤) |
| Password 주입 | `.env` 평문 (`env_file: .env`) | Docker secrets (`/run/secrets/*` + `_FILE` 환경변수) |
| Schema 생성 | web `lifespan`이 자동 | Alembic 사전 적용 (lifespan skip) |
| Fail-fast 검증 | 약한 default 허용 | `_WEAK_VALUES` 거부 → 시작 자체 실패 |
| restart 정책 | `unless-stopped` (현재) | `always` 권장 (운영 안정성) |
| Logging | 상세·human-readable | json·level 기준 (별도 도입 시) |

---

## 6. 파일 레이아웃

```
.
├── .env.example              ← 카탈로그 + dev 안전 default (커밋)
├── .env                      ← 로컬 dev 실값 (.gitignore)
├── docker-compose.yml        ← prod-safe baseline (커밋)
├── docker-compose.override.yml  ← dev 자동 적용 (커밋)
├── docker-compose.prod.yml   ← prod 명시 호출용 (커밋)
├── secrets/
│   ├── README.md             ← 작성 방법 (커밋)
│   ├── .gitignore            ← `*` 제외, README/.gitignore만 커밋
│   ├── postgres_password     ← prod 한정. .gitignore로 제외
│   └── rabbitmq_password     ← prod 한정. .gitignore로 제외
├── infra/
│   ├── agent.env.example     ← 에이전트 secret 카탈로그 (커밋)
│   └── agent.env             ← 에이전트 실값 (.gitignore)
└── config.py                 ← BaseSettings + model_validator
```

왜 .env.dev / .env.prod 같은 환경별 .env 파일을 두지 않나:
- dev secret과 prod secret이 한 디렉토리에 공존 → 휴먼 에러로 prod에 dev secret이 들어가는 사고 발생.
- prod secret은 어차피 docker-compose secrets 또는 외부 secret manager로 주입되어야 하므로 `.env`에 둘 이유 없음.
- dev는 단일 `.env`, prod는 secret 채널 — 책임이 명확.

---

## 7. Secret 정책 — 단계별

운영 단계에 따라 secret 보호 강도를 올린다. 본 프로젝트는 현재 Stage 2.

| Stage | secret 채널 | 적합 환경 | 운영 비용 |
|-------|----------|---------|---------|
| 1. `.env` 평문 | `.env` 파일 | 로컬 dev | 0 |
| 2. Docker secrets (현재) | `secrets/*` 파일 + compose `secrets:` | 단일 호스트 prod, 소규모 B2B 배포 | 낮음. root 0400 권한 관리 |
| 3. SOPS/age + Git | git에 암호화 커밋, 운영 시 복호화 | GitOps 기반 운영 | 중. 키 관리 필요 |
| 4. Vault / AWS Secrets Manager | 외부 secret manager | 다중 환경, 동적 secret 회전 | 높음. 인프라 의존 |
| 5. K8s Secret + External Secrets Operator | K8s 네이티브 | K8s 클러스터 운영 | 높음. K8s 운영 전제 |

현재 Stage 2 구현 디테일:
- secret 파일은 호스트 `secrets/*` (root 0400). docker-compose가 컨테이너 안 `/run/secrets/*` 에 read-only 마운트.
- PostgreSQL/RabbitMQ 공식 이미지: `_FILE` suffix 환경변수 (예: `POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`)로 secret 파일 경로 지정. 이미지가 시작 시 파일 read.
- Python 앱 (web/consumer): pydantic `secrets_dir="/run/secrets"`가 자동 read. 필드명 일치 필수.

Stage 4·5로의 전환 트리거:
- 다중 host / 다중 region 운영
- secret 자동 회전 정책 도입
- compliance 요구 (SOC2 등)

---

## 8. Fail-fast 검증 (3 layer defense in depth)

prod 환경에서 secret 사고를 막기 위해 3 layer로 분리. 한 layer가 뚫려도 다음 layer가 잡는다.

| Layer | 위치 | 검증 대상 | 강제 시점 |
|-------|------|-----------|-----------|
| 1 | `./scripts/check-prod-secrets.sh` (호스트 측) | 호스트 `secrets/*.txt` 파일의 mode·git tracking·최소 길이 | 운영자가 `compose up` 직전 수동 실행 (`docs/operations/dev-prod.md` 10절 체크리스트) |
| 2 | `config.py` `model_validator` — `/run/secrets` 마운트 존재 | prod인데 secret 디렉토리 부재 시 `ValueError`로 즉시 fail | 컨테이너 진입 시점 (앱 import 직후) |
| 3 | `config.py` `model_validator` — `_WEAK_VALUES` 거부 | prod인데 password가 dev default(`assessment` 등)면 fail | 컨테이너 진입 시점 |

```python
@model_validator(mode="after")
def _validate_prod_web_secrets(self) -> "WebSettings":
    if self.app_env != "prod":
        return self
    # Layer 2: secrets 마운트 존재
    if _SECRETS_DIR is None:
        raise ValueError(
            "APP_ENV=prod but /run/secrets is not mounted. "
            "Use `docker compose -f docker-compose.yml -f docker-compose.prod.yml up`."
        )
    # Layer 3: 값 약함
    if self.postgres_password.get_secret_value() in _WEAK_VALUES:
        raise ValueError(
            "POSTGRES_PASSWORD is unset or uses a dev default in prod."
        )
    return self
```

효과:
- 운영자가 `docker compose up`(prod.yml 누락)으로 잘못 기동 → Layer 2가 `/run/secrets` 부재 잡고 fail-fast (이전엔 env·default로 fallback해 weak default 통과 가능했음).
- `APP_ENV=prod`로 기동 시 password가 누락/약한 default면 web/consumer 컨테이너가 즉시 `ValueError`로 종료 → docker-compose가 unhealthy로 표시 → 배포자가 즉시 인지.
- 호스트 측 secret 파일이 world-readable이거나 git tracked면 Layer 1이 사전에 차단.

확장 포인트 (필요 시):
- `SECRET_KEY` (FastAPI session 등) 도입 시 동일 패턴 적용 + `check-prod-secrets.sh`의 `REQUIRED_SECRETS` 배열에 추가.
- `REDIS_PASSWORD` 도입 시 동일.
- pydantic `Field(min_length=N)`로 password 길이 강제 (현재는 Layer 1의 `MIN_LENGTH=32`로 호스트 측에서 강제).

---

## 9. 에이전트 secret 채널 분리

에이전트는 엔진과 다른 노드(VM)에서 실행되며, 엔진과 다른 secret 라이프사이클을 가진다 (배포 도구·재기동 시점 다름). 따라서 secret 채널을 명시적으로 분리.

| 항목 | 엔진 | 에이전트 |
|------|------|---------|
| secret 파일 | `.env` (dev) / `secrets/*` (prod) | `infra/agent.env` (Lima dev), prod에선 별도 프로비저닝 도구 (Ansible vault 등) |
| 주입 경로 | docker-compose env / secrets | dev-up.sh가 `infra/agent.env` source → limactl shell heredoc → `/etc/assessment-agent.env` (VM 안), `EnvironmentFile=` |
| 호스트 (broker) | docker 서비스명 `rabbitmq` | `host.lima.internal` (Lima user-mode network alias) |

왜 분리하나:
- 엔진 `.env` 변경이 에이전트 동작에 의도치 않게 영향 미치는 경로 차단.
- 에이전트의 secret 라이프사이클(VM 재프로비저닝 시점)이 엔진(컨테이너 재기동 시점)과 독립.
- prod에서는 에이전트가 K8s/Docker 내부에 있지 않으므로 Docker secrets 적용 불가 — 별도 secret 도구 필요.

현재 dev: `infra/agent.env.example` 복사 → 운영 값으로 수정 → `./dev-up.sh`.
향후 prod: Ansible vault 또는 SaltStack pillar로 `/etc/assessment-agent.env` 생성 — Lima 구성은 dev 전용으로 격리.

---

## 10. 운영 체크리스트 (prod 배포 전)

- [ ] `secrets/postgres_password`, `secrets/rabbitmq_password` 파일 작성 (강한 random, root 0400, trailing newline 없음)
- [ ] `./scripts/check-prod-secrets.sh` 실행 — 파일 존재·mode(0400/0600 only)·git untracked·최소 32바이트 자동 검증 (defense in depth: config.py의 weak default 검증 외 호스트 측 layer)
- [ ] `docker-compose.prod.yml`의 `secrets:` 블록과 `secrets/` 파일명 일치
- [ ] `APP_ENV=prod` 환경변수 또는 `docker-compose.prod.yml`의 `environment` 블록으로 명시
- [ ] Alembic 마이그레이션 사전 적용 — `docker-compose.prod.yml`의 `migrate` 컨테이너가 자동 실행하지만, 큰 schema 변경은 사전에 `alembic history` / `alembic current` 검토 권장. 상세 절차는 `docs/operations/alembic.md`
- [ ] `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` 로 머지 결과 검증
- [ ] DB·MQ·Redis 외부 포트 노출 없음 확인 (`docker compose ... ps` / `netstat`)
- [ ] web만 reverse proxy 뒤 또는 직접 노출 결정
- [ ] 약한 default가 어디서도 새지 않는지 확인 (`grep -r "assessment" docker-compose.prod.yml secrets/` 결과 없어야)
- [ ] 에이전트는 별도 채널 (Lima 안 쓰면 Ansible 등) — `.env`에 의존 없음 확인

---

## 11. 안티패턴 (금지)

- 코드(`src/assessment_engine/config.py`)에 `if env == "prod"` 비즈니스 분기 도입 — 정책 분기(2개 지점)만 허용.
- `.env.production` / `.env.development`를 같은 호스트에 동시 보유 — 활성 파일 모호.
- prod에서 `volumes: ./:/app` 유지 — 컨테이너 안 `.env` 노출 + 코드 변조 위험.
- `docker-compose.prod.yml`을 base 풀 복제 — 단일 진실 깨지고 동기화 비용 발생. diff만.
- secret을 git 저장소에 커밋 — `.dockerignore` / `.gitignore`의 `.env`/`secrets/*`/`infra/agent.env` 항목 절대 제거 금지.
- 컨테이너 안에서 `/app/.env`를 직접 read하는 코드 추가 — pydantic-settings의 `env_file` 폴백 외 직접 read 금지.
- `secrets_dir`을 항상 `/run/secrets`로 하드코딩 — 디렉토리 부재 시 일부 환경에서 noisy 경고. `os.path.isdir` 분기 유지.

---

## 12. 정리

본 전략의 핵심 의도:

1. secret을 git에 커밋하지 않는다 — `.env`/`secrets/*`/`infra/agent.env` 모두 .gitignore.
2. dev 편의성을 prod 안전성과 거래하지 않는다 — `.env` 평문은 dev에만, prod는 secrets 채널 강제.
3. 약한 default를 prod로 흘려보내지 않는다 — APP_ENV=prod의 fail-fast 검증.
4. 이미지는 환경 무관 — Dockerfile은 1개, 차이는 compose override + secret 주입으로만.
5. 에이전트 secret을 엔진 secret과 분리 — 각자 독립적 라이프사이클.

이 다섯이 깨지는 PR은 reject 대상.