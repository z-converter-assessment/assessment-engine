# 배포 가이드 — 외부 인프라 관점

본 문서는 외부 인프라(VM + Linux + systemd 또는 동등 환경)가 본 repo의 release artifact를 활용해 엔진을 운영하는 단계별 가이드. OS 독립·도구 독립 — Ansible·SaltStack·수동 SSH 어느 방식이든 본 단계만 충족하면 본 엔진 동작.

본 repo 책임 한계: artifact 게시 + contract 정의. 실제 VM provisioning·서브넷·SG·secret 주입 채널·log aggregator stack은 외부 인프라 결정 (CLAUDE.md #A0).

## 1. CI 산출물

artifact 카탈로그·생성 trigger·무결성 검증·다운로드 채널: `docs/operations/release.md` 단일 진실. 본 문서는 release artifact를 받은 상태를 전제로 install·실행 절차를 다룬다.

운영자 선택권 (ADR 0017):
- (A) wheel + venv + systemd unit — 본 문서 3절 기본 시나리오
- (B) Docker image (GHCR) + docker compose 또는 k8s — 본 문서 4절 multi-node 분리 inject 예시 (image 패턴 동등)
- (C) 단일 호스트 배포 (compose all-in-one) — 릴리즈 base `docker-compose.yml` + `env.example` 채워 한 줄 기동 (ADR 0036). 평가·내부망 소규모·단일 노드용. 아래 0절.

토폴로지 자율 — 모두 동일 환경변수 contract (`docs/operations/env.md`) + Alembic migration 절차.

## 0. 단일 호스트 배포 (compose all-in-one)

릴리즈 base `docker-compose.yml` + `env.example`(배포 템플릿) 한 세트로 단일 호스트에 엔진 3 컴포넌트 + 의존 인프라(TimescaleDB·Redis·RabbitMQ) 기동. 평가(PoC)·내부망 소규모·단일 노드 운영용 (ADR 0036).

compose 2 파일 (ADR 0035·0036): 루트 `docker-compose.yml` = prod-safe base(build 키 없음, GHCR 이미지 pull), `docker-compose.override.yml` = dev 전용. 릴리즈는 base 만 첨부(override 미배포)라 배포는 base 단독으로 동작. dev 검증은 `dev/`(dev-up.sh + dev/.env.example).

GitHub Release 첨부 `docker-compose.yml`(prod-safe base) + `env.example`(배포 템플릿) 받아:
```bash
cp env.example .env
# [필수] POSTGRES/RABBITMQ secret(changeme placeholder) · ENGINE_IMAGE · PGDATA_HOST 등 채움 (APP_ENV=prod 기본)
docker compose up -d              # build 키 없음 -> GHCR 이미지 pull. web: http://localhost:8000
# ENGINE_IMAGE 미설정 시 base 기본 = release CI 가 핀한 GHCR 정확 버전. 다른 버전·레지스트리면 override.
```
GHCR public(ADR 0035 3절 정정) — 토큰 없이 pull. 영속 볼륨을 외부 디스크에 두려면 `PGDATA_HOST`/`MQ_DATA_HOST` 주입(미설정 시 named volume).

`APP_ENV=prod` 기본이라 weak secret 은 기동 거부(fail-fast) — `.env` 의 `changeme` placeholder 를 진짜 secret 으로 채워야 뜬다.

한계 (ADR 0036): 본 절은 단일 호스트 compose 까지. 인터넷 노출 hardened prod 는 HTTPS ingress(reverse proxy)·외부 secret 채널 추가 — wheel+systemd(3절) 또는 멀티노드(4절). install(ZDM)·AI 진단(LLM)은 외부 좌표 주입 전까지 비활성.

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

환경변수 catalog: `docs/operations/env.md`. secret/config 분류·dev/prod 분기: `docs/operations/env.md`.

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
- `ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.<module>` — module은 `web`·`consumer`·`diagnostic` 중 하나 (ADR 0023: scheduler 폐기)
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

다른 컴포넌트(consumer·diagnostic-worker)는 `ExecStart` 모듈만 교체. 외부 인프라가 자체 Ansible template·자체 운영 도구로 생성.

### 3.6. 헬스 확인

```bash
curl -fsS http://<engine-host>:8000/health
# {"status":"ok"} 응답이면 정상
```

추가 endpoint(`/docs`·web UI 등) 카탈로그: README "URL 카탈로그" 절.

## 4. multi-node 분리 inject 예시

본 repo는 3 컴포넌트(web·consumer·diagnostic-worker)를 같은 host 또는 분리 host에 배포 가능. ADR 0023: scheduler cron 폐기로 4 컴포넌트 → 3 컴포넌트. 컴포넌트별 필요 키는 `docs/operations/env.md` "컴포넌트별 read 매트릭스" 참조.

### 단일 host (모든 컴포넌트 같이)

가장 단순. 한 `.env` 또는 `EnvironmentFile`에 모든 키 주입. 3 systemd unit이 같은 파일을 `EnvironmentFile=/etc/assessment-engine.env`로 read.

```ini
# /etc/systemd/system/assessment-engine-web.service
[Service]
EnvironmentFile=/etc/assessment-engine.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.web
# ... (consumer·diagnostic-worker도 동일 EnvironmentFile)
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
  ZDM_DEFAULT_IP=<ZDM_IP>
  ZDM_DEFAULT_USER=<ZDM_USER>
  RABBITMQ_ROUTING_KEY_DIAGNOSTIC=diagnostic.request   # web도 진단 publish

consumer 노드:             /etc/assessment-engine/consumer.env
  WORKER_TASK_EXCHANGE=assessment.tasks
  WORKER_TASK_QUEUE_PREFIX=agent.tasks

diagnostic-worker 노드:    /etc/assessment-engine/diagnostic-worker.env
  OLLAMA_BASE_URL=http://<ollama-host>:11434
  OLLAMA_MODEL=llama3.1:8b
  RABBITMQ_DIAGNOSTIC_QUEUE_TTL_MS=86400000
  RABBITMQ_DIAGNOSTIC_QUEUE_MAX_LEN=100000
```

ADR 0023: scheduler cron 폐기. 본 절 안 4 host 분리는 3 host 분리 정공 (web + consumer + diagnostic-worker).

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

### Docker image 패턴 (ADR 0017 — wheel 대안)

운영자가 venv·systemd 대신 컨테이너 토폴로지 선택 시. 동일 환경변수 + Alembic migration 절차 — 다른 점은 단지 실행 매체.

GHCR pull (외부망 또는 사내 mirror via `docker save`):
```bash
docker pull ghcr.io/zconverter/assessment-engine:1.2.3
cosign verify ghcr.io/zconverter/assessment-engine:1.2.3 \
  --certificate-identity-regexp='https://github\.com/zconverter/assessment-engine/.*' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com'
```

Alembic migration (1회):
```bash
docker run --rm --env-file /etc/assessment-engine.env \
  --entrypoint /bin/sh ghcr.io/zconverter/assessment-engine:1.2.3 \
  -c 'ALEMBIC_INI=$(python -c "from importlib.resources import files; print(files(\"assessment_engine\") / \"_alembic.ini\")"); python -m alembic -c "$ALEMBIC_INI" upgrade head'
```

docker compose 운영 예시 (직접 작성 시 최소 형태. 릴리즈 첨부 prod-safe base `docker-compose.yml`(ADR 0035)을 받아 쓰면 backing 서비스·migrate·볼륨까지 포함되므로 보통 그쪽이 우선):
```yaml
services:
  web:
    image: ghcr.io/zconverter/assessment-engine:0.1
    env_file: /etc/assessment-engine.env
    ports: ["8000:8000"]
    restart: unless-stopped
  consumer:
    image: ghcr.io/zconverter/assessment-engine:0.1
    env_file: /etc/assessment-engine.env
    command: assessment_engine.consumer
    restart: unless-stopped
  diagnostic-worker:
    image: ghcr.io/zconverter/assessment-engine:0.1
    env_file: /etc/assessment-engine.env
    command: assessment_engine.diagnostic
    restart: unless-stopped
```

k8s Deployment (외부 인프라 — 본 repo 두지 않음):
```yaml
apiVersion: apps/v1
kind: Deployment
metadata: {name: consumer}
spec:
  replicas: 3   # consumer 수평 확장 (broker queue prefetch 분산)
  template:
    spec:
      containers:
        - name: consumer
          image: ghcr.io/zconverter/assessment-engine:0.1.0
          args: ["assessment_engine.consumer"]   # CMD override
          envFrom:
            - secretRef: {name: assessment-engine-env}
          resources:
            requests: {cpu: 100m, memory: 256Mi}
            limits:   {cpu: 1000m, memory: 1Gi}
```

수평 확장 정합:
- web — replicas N 자유 (stateless HTTP)
- consumer — replicas N 자유 (broker prefetch_count=10 분산)
- diagnostic-worker — replicas N 자유 (job 단위 분산)

폐쇄망 (air-gapped) 운영: `docker save assessment-engine:1.2.3 -o image.tar` + scp → 운영 환경에서 `docker load -i image.tar` (ADR 0017 본문).

## 5. 운영 contract 한눈

| contract | 본 repo 위치 | 외부 인프라가 충족할 것 |
|---------|-----------|-------------------|
| 환경변수 | `docs/operations/env.md` | 모든 키를 어떤 채널이든 inject |
| Secret | `config.py` `_validate_prod_*` | `APP_ENV=prod`에서 weak default 거부 — strong random 주입 |
| Schema | wheel 안 `_alembic.ini` + `migrations/` | `alembic upgrade head` 사전 실행 |
| graceful shutdown | F11 (`docs/architecture/consumer.md`·`diagnostic.md`) | systemd `KillSignal=SIGTERM` + `TimeoutStopSec` 충분히 |
| 헬스 endpoint | `GET /health` (web) | systemd `Restart=always` + watchdog 외부 모니터 |
| 관측 | `LOG_FORMAT=json` (F7) 구조화 로그 | log aggregator collector |

## 6. 트러블슈팅 (자주 발견되는 사고)

| 증상 | 원인·조치 |
|------|----------|
| `Settings()` 진입 시점 `ValueError` | weak default 거부 — `APP_ENV=prod` 인데 password/user/ZDM 좌표가 dev default 그대로. secret 주입 채널 점검 (`docs/operations/env.md` 6 절) |
| `alembic upgrade head` 실패 — `extension "timescaledb" is not available` | PostgreSQL TimescaleDB extension 누락. 운영 DB 에서 `CREATE EXTENSION IF NOT EXISTS timescaledb` 사전 실행 (`docs/operations/alembic.md`) |
| consumer 가 broker 연결 실패 반복 | `RABBITMQ_HOST`/`RABBITMQ_VHOST`/auth 검토. `sudo rabbitmqctl list_permissions -p /assessment` 로 vhost 권한 확인 (`docs/architecture/rabbitmq.md` "vhost·권한 모델") |
| `/health` 는 200 인데 inventory 안 들어옴 | 에이전트가 별도 install·broker 연결 필요. agent 측 secret 채널 점검 (`docs/architecture/agent.md`) |

## 7. 인프라 레포 자동화 (권장 패턴)

본 가이드의 shell 명령은 한 번 직접 따라하기 용. 실제 prod 운영은 별도 인프라 레포 (Ansible·Salt·Chef·Pulumi 등) 에서 본 명령들을 declarative task 로 옮겨 관리.

본 repo 와 인프라 레포 사이 연결:
- git 차원 연결 0 — submodule·fork·clone 아님
- 인프라 레포는 본 repo 의 GitHub Release 에서 wheel artifact 만 다운로드 (`gh release download` 또는 `wget`)
- engine version 은 인프라 레포의 변수 (예: `group_vars/all/engine.yml` 의 `ENGINE_VERSION`) 로 관리. 새 release 배포 = 변수 한 줄 변경 + playbook replay
- secret (`POSTGRES_PASSWORD` 등) 은 ansible-vault·HashiCorp Vault·k8s Secret 등 외부 채널. 본 repo 는 채널 강제 0 — `APP_ENV=prod` 에서 weak default 거부만 검증

Ansible 매핑 예시 — 절 3.1 (wheel install) 일부를 task 로 옮긴 모습:

```yaml
- name: wheel artifact 다운로드 (GitHub Release)
  ansible.builtin.command:
    cmd: >
      gh release download {{ engine_version }}
      --repo z-converter-assessment/assessment-engine
      --pattern '*.whl'
      --pattern 'SHA256SUMS'
      --dir /tmp/release-{{ engine_version }}
    creates: /tmp/release-{{ engine_version }}/SHA256SUMS

- name: sha256 무결성 검증
  ansible.builtin.command:
    cmd: sha256sum -c SHA256SUMS
    chdir: /tmp/release-{{ engine_version }}

- name: venv 에 wheel install
  ansible.builtin.pip:
    name: "{{ lookup('fileglob', '/tmp/release-' + engine_version + '/assessment_engine-*.whl') }}"
    virtualenv: /opt/assessment-engine/venv
    virtualenv_python: python3.12
  become_user: assessment
```

본 가이드의 절 3 전 단계가 동일하게 1:1 매핑 가능. 정석 인프라 레포 구조:

```
infra-assessment/                     # 본 repo 와 별개 repo
  ansible/
    inventories/prod/hosts.ini        # 운영 VM 의 IP·hostname
    group_vars/
      all/shared.yml                  # POSTGRES_HOST 등 공통 (vault 또는 ansible-vault)
      engine/version.yml              # ENGINE_VERSION: v0.1.0
    roles/
      postgres/                       # PG + TimescaleDB install
      rabbitmq/                       # MQ install
      redis/                          # Redis install
      assessment_engine/              # engine 4 컴포넌트
        tasks/
          install.yml                 # wheel 다운로드 + venv install
          env.yml                     # shared.env + <component>.env 템플릿 렌더
          migration.yml               # web host 에서 alembic upgrade 1회
          systemd.yml                 # unit 생성 + systemctl enable --now
    site.yml
```

new engine release 배포 흐름:
1. 본 repo 에서 새 tag (예: v0.2.0) release 발사 (Release PR 머지)
2. 인프라 레포의 `group_vars/engine/version.yml` 에서 `ENGINE_VERSION: v0.1.0 -> v0.2.0` 한 줄 변경 + PR
3. 인프라 레포 PR 머지 → `ansible-playbook site.yml` 재실행 (수동 또는 인프라 레포의 CD)

## 8. 관련 문서

- release artifact: `docs/operations/release.md`
- 환경변수 카탈로그·prod 정책: `docs/operations/env.md`
- Alembic 운영: `docs/operations/alembic.md`
- 관측: `docs/operations/observability.md`
- 본 repo 범위 결정: CLAUDE.md #A0
- 의사결정 history: `docs/adr/` (특히 0005·0010·0011·0012)
