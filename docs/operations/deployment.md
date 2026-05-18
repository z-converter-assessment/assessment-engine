# 배포 가이드 — 외부 인프라 관점

본 문서는 외부 인프라(VM + Linux + systemd 또는 동등 환경)가 본 repo의 release artifact를 활용해 엔진을 운영하는 단계별 가이드. OS 독립·도구 독립 — Ansible·SaltStack·수동 SSH 어느 방식이든 본 단계만 충족하면 본 엔진 동작.

본 repo 책임 한계: artifact 게시 + contract 정의. 실제 VM provisioning·서브넷·SG·secret 주입 채널·log aggregator stack은 외부 인프라 결정 (CLAUDE.md #A0).

## 1. CI 산출물

artifact 카탈로그·생성 trigger·무결성 검증·다운로드 채널: `docs/operations/release.md` 단일 진실. 본 문서는 release artifact를 받은 상태를 전제로 install·실행 절차를 다룬다.

## 2. 사전 준비

| 항목 | 필요 |
|------|------|
| Python | 3.12 이상 (본 repo `pyproject.toml` `requires-python = ">=3.12"`) |
| 외부 의존 서비스 | PostgreSQL 16 + TimescaleDB · RabbitMQ 3.13+ · Redis 7+ |
| 백킹 서비스 배포 | host package(`apt install postgresql-16` 등) 또는 컨테이너·매니지드 — 외부 인프라 자유 결정 |
| 네트워크 | 엔진 host에서 위 3 서비스 도달 가능. 에이전트가 엔진 RabbitMQ broker 도달 가능 |

## 3. 단계별 흐름

### 3.1. wheel 다운로드 + 검증

GitHub Release page 또는 `gh release download` 활용:

```bash
gh release download v1.2.3 --repo z-converter-assessment/assessment-engine \
  --pattern '*.whl' --pattern 'SHA256SUMS' --dir /tmp/release

cd /tmp/release && sha256sum -c SHA256SUMS
```

또는 사내 mirror에서 받음 (Nexus·devpi·MinIO 등 자유).

### 3.2. Python 환경 + wheel install

권장 패턴: `/opt/assessment-engine/venv` 같은 격리 venv. 또는 시스템 Python 직접 install — 자유.

```bash
python3.12 -m venv /opt/assessment-engine/venv
/opt/assessment-engine/venv/bin/pip install /tmp/release/assessment_engine-1.2.3-py3-none-any.whl
```

### 3.3. 환경변수·secret 주입

환경변수 catalog: `docs/operations/env.md`. secret/config 분류·dev/prod 분기: `docs/operations/prod-contract.md`.

채널 자유 (env·systemd `EnvironmentFile`·Vault·k8s Secret·Docker secrets 등). 본 repo `_validate_prod_*`가 결과(weak default 거부)만 검증.

가장 단순한 systemd 패턴:

```bash
sudo install -m 0640 -o root -g assessment /dev/stdin /etc/assessment-engine.env <<'EOF'
APP_ENV=prod
LOG_FORMAT=json
POSTGRES_HOST=db.internal
POSTGRES_USER=engine_prod
POSTGRES_PASSWORD=<strong-random>
POSTGRES_DB=assessment
RABBITMQ_HOST=mq.internal
RABBITMQ_USER=engine_prod
RABBITMQ_PASSWORD=<strong-random>
RABBITMQ_VHOST=/assessment
REDIS_HOST=cache.internal
EOF
```

(secret 부분은 Vault·EnvironmentFile 분리·systemd `LoadCredential=` 등 강화 가능 — 외부 인프라 결정)

### 3.4. Alembic migration 1회 실행

wheel 안 동봉된 alembic.ini 경로를 동적으로 해석:

```bash
ALEMBIC_INI=$(/opt/assessment-engine/venv/bin/python -c \
  'from importlib.resources import files; print(files("assessment_engine") / "_alembic.ini")')

/opt/assessment-engine/venv/bin/python -m alembic -c "$ALEMBIC_INI" upgrade head
```

큰 schema 변경 (DROP·대량 ALTER) 시 사전 검토: `docs/operations/alembic.md`.

### 3.5. systemd unit 등록

본 repo는 systemd unit example을 git tree에 두지 않는다 (#A0 — prod 운영 방식 contract 외부 책임). 4절 inline 예시(`multi-node 분리 inject 예시`)를 자기 운영 도구로 templating.

각 unit이 가정하는 contract:
- `WorkingDirectory=/opt/assessment-engine` (또는 자유 경로)
- `EnvironmentFile=` 한 줄 이상 (4절 layered 패턴)
- `ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.<module>` — module은 `web`·`consumer`·`diagnostic`·`diagnostic.scheduler` 중 하나
- `User=` system user (Linux distro 정합 임의 이름)
- `KillSignal=SIGTERM` + `TimeoutStopSec` 충분 (#F11 graceful shutdown)
- `Restart=always` 권장

reference unit 형태:

```ini
[Unit]
Description=Assessment Engine Web
After=network-online.target

[Service]
Type=simple
User=assessment
WorkingDirectory=/opt/assessment-engine
EnvironmentFile=/etc/assessment-engine/shared.env
EnvironmentFile=/etc/assessment-engine/web.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.web
Restart=always
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
```

다른 컴포넌트(consumer·diagnostic-worker·diagnostic-scheduler)는 `ExecStart` 모듈만 교체. 외부 인프라가 자체 Ansible template·자체 운영 도구로 생성.

### 3.6. 헬스 확인

```bash
curl -fsS http://<engine-host>:8000/health
# {"status":"ok"} 응답이면 정상
```

추가 endpoint(`/metrics`·`/docs`·web UI 등) 카탈로그: README "URL 카탈로그" 절.

## 4. multi-node 분리 inject 예시

본 repo는 4 컴포넌트(web·consumer·diagnostic-worker·diagnostic-scheduler)를 같은 host 또는 분리 host에 배포 가능. 컴포넌트별 필요 키는 `docs/operations/env.md` "컴포넌트별 read 매트릭스" 참조.

### 단일 host (모든 컴포넌트 같이)

가장 단순. 한 `.env` 또는 `EnvironmentFile`에 모든 키 주입. 4 systemd unit이 같은 파일을 `EnvironmentFile=/etc/assessment-engine.env`로 read.

```ini
# /etc/systemd/system/assessment-engine-web.service
[Service]
EnvironmentFile=/etc/assessment-engine.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.web
# ... (consumer·diagnostic-worker·diagnostic-scheduler도 동일 EnvironmentFile)
```

### 4 host 분리 (계층화 — 권장 패턴 C)

공통 키와 컴포넌트별 키 분리. systemd는 `EnvironmentFile=`을 여러 줄 지원 — 공통 + 자기 컴포넌트 파일 2 단계.

```
공통 (모든 노드 동일):     /etc/assessment-engine/shared.env
  APP_ENV=prod
  LOG_FORMAT=json
  POSTGRES_HOST=db.internal
  POSTGRES_USER=engine_prod
  POSTGRES_PASSWORD=<strong>
  POSTGRES_DB=assessment
  REDIS_HOST=cache.internal
  RABBITMQ_HOST=mq.internal
  RABBITMQ_USER=engine_prod
  RABBITMQ_PASSWORD=<strong>
  RABBITMQ_VHOST=/assessment

web 노드:                  /etc/assessment-engine/web.env
  WEB_PORT=8000
  INSTALL_BUNDLE_URL=http://web.internal:8000/zconverter.tar.gz
  DIAGNOSTIC_ROUTING_KEY=diagnostic.request   # web도 진단 publish

consumer 노드:             /etc/assessment-engine/consumer.env
  WORKER_TASK_EXCHANGE=assessment.tasks
  WORKER_TASK_QUEUE_PREFIX=agent.tasks

diagnostic-worker 노드:    /etc/assessment-engine/diagnostic-worker.env
  LLM_PROVIDER=mock
  WORKER_JOB_TIMEOUT_SECONDS=300
  DIAGNOSTIC_QUEUE_TTL_MS=86400000
  DIAGNOSTIC_QUEUE_MAX_LEN=100000

diagnostic-scheduler 노드: /etc/assessment-engine/diagnostic-scheduler.env
  DIAGNOSTIC_SCHEDULE_CRON=0 3 * * *
  DIAGNOSTIC_RETENTION_DAYS=90
  DIAGNOSTIC_ACTIVE_SERVER_WINDOW_HOURS=24
```

각 노드의 systemd unit:
```ini
[Service]
EnvironmentFile=/etc/assessment-engine/shared.env
EnvironmentFile=/etc/assessment-engine/<component>.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.<module>
```

Ansible 표준 패턴과 정합:
- `group_vars/all/shared.yml` → `shared.env` 생성 (모든 host에 배포)
- `host_vars/<host>.yml` → `<component>.env` 생성 (해당 host에만)

### 효과

- secret 분포 최소 — 노드 침해 시 그 노드 키만 leak. 공통 키(DB·MQ)는 모든 노드에 있지만 컴포넌트 한정 키(LLM·DIAGNOSTIC·INSTALL_*)는 해당 노드만
- prod 검증 (`_validate_prod_*`) 컴포넌트별 — web 노드는 `WebSettings`·`DiagnosticSettings` 인스턴스화, consumer 노드는 `ConsumerSettings`만. 노드가 안 쓰는 키 검증 skip
- drift 차단 — 공통 키는 한 파일만 갱신, 모든 노드 자동 동기화. Ansible group_vars/host_vars로 자동화 친화

## 5. 운영 contract 한눈

| contract | 본 repo 위치 | 외부 인프라가 충족할 것 |
|---------|-----------|-------------------|
| 환경변수 | `docs/operations/env.md` | 모든 키를 어떤 채널이든 inject |
| Secret | `config.py` `_validate_prod_*` | `APP_ENV=prod`에서 weak default 거부 — strong random 주입 |
| Schema | wheel 안 `_alembic.ini` + `_migrations/` | `alembic upgrade head` 사전 실행 |
| graceful shutdown | F11 (`docs/architecture/consumer.md`·`diagnostic.md`) | systemd `KillSignal=SIGTERM` + `TimeoutStopSec` 충분히 |
| 헬스 endpoint | `GET /health` (web) | systemd `Restart=always` + watchdog 외부 모니터 |
| 관측 | `/metrics` (Prometheus, ADR 0011) + `LOG_FORMAT=json` (F7) | Prometheus scrape · log aggregator collector |
| reverse proxy | `/metrics` 외부 노출 금지 (ADR 0011) | nginx·envoy 등에서 internal-only 라우트 |

## 6. 트러블슈팅 (자주 발견되는 사고)

- `Settings()` 진입 시점 `ValueError` — weak default 거부. secret 주입 채널 점검
- `alembic upgrade head` 실패 — PostgreSQL TimescaleDB extension 누락. `CREATE EXTENSION IF NOT EXISTS timescaledb` 사전 실행
- consumer가 broker 연결 실패 반복 — `RABBITMQ_HOST`/`RABBITMQ_VHOST`/auth 검토. `docs/architecture/rabbitmq.md` "vhost·권한 모델"
- `/metrics` endpoint가 외부 노출 — reverse proxy 차단 누락. ADR 0011 한계 절 참조

## 7. 관련 문서

- release artifact: `docs/operations/release.md`
- 환경변수 catalog: `docs/operations/env.md`
- dev/prod 환경 정책: `docs/operations/prod-contract.md`
- Alembic 운영: `docs/operations/alembic.md`
- 관측: `docs/operations/observability.md`
- 본 repo 범위 결정: CLAUDE.md #A0
- 의사결정 history: `docs/adr/` (특히 0005·0010·0011·0012)
