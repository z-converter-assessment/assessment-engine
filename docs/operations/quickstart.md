# Quickstart — Multi-Node 운영자 가이드

본 가이드는 외부 인프라 운영자가 Debian 12 기반 7 VM 분산 환경에 본 엔진 release artifact를 받아 기동까지 가는 최단 경로. 명령어 복붙 위주.

원리·trade-off·hardening (TLS·reverse proxy·HA·observability)은 본 가이드 범위 밖 — `docs/operations/deployment.md` · `docs/operations/prod-contract.md` 참조.

## 본 가이드 사용 방식

청자: 외부 인프라 레포(별도 repo)를 관리하는 운영자. 본 repo의 release artifact를 받아 자기 환경에 install·운영함.

방식: GitHub Release에 게시된 wheel artifact를 받아 install. 본 repo를 git clone하거나 source에서 빌드하지 않음. wheel artifact 카탈로그·다운로드 채널·무결성 검증 contract는 `docs/operations/release.md`.

활용 패턴:
- 첫 셋업 검증·시연: 본 가이드의 명령을 7 VM에 SSH 들어가 순서대로 직접 입력
- 운영 자동화 (정석): 본 명령들을 Ansible role(또는 Salt·Chef·Pulumi)의 declarative task로 옮겨 인프라 레포에서 관리. 절 8 "본 quickstart를 자동화하기" 참조

본 가이드 끝점: 엔진 4 컴포넌트(web·consumer·diagnostic-worker·diagnostic-scheduler) 기동 + `/health` 200 응답.

## 0. 사전 준비

VM 7대 (Debian 12, 각각 SSH + sudo, 사이 네트워크 도달 가능):

| 역할 | 호스트명(가정) | 외부 노출 포트 |
|------|----------------|---------------|
| PostgreSQL 16 + TimescaleDB | db.internal | 5432 (engine-* VM 만) |
| RabbitMQ | mq.internal | 5672 (engine-* VM 만) |
| Redis 7 | cache.internal | 6379 (engine-* VM 만) |
| 엔진 web | engine-web | 8000 (외부) |
| 엔진 consumer | engine-consumer | 없음 |
| 엔진 diagnostic-worker | engine-worker | 없음 |
| 엔진 diagnostic-scheduler | engine-scheduler | 없음 |

본 가이드 placeholder (실제 값으로 치환):

| 토큰 | 의미 | 생성·결정 |
|------|------|----------|
| `<VERSION>` | release tag | GitHub Release 페이지 (예: `v0.1.0`) |
| `<DB_PASSWORD>` | PostgreSQL `engine_prod` 비밀번호 | `openssl rand -base64 32` |
| `<MQ_PASSWORD>` | RabbitMQ `engine_prod` 비밀번호 | `openssl rand -base64 32` |
| `<ENGINE_SUBNET>` | engine-* VM IP 대역 | 운영자 네트워크 결정 (예: `10.0.1.0/24`) |
| `<ZDM_IP>` | ZConverter Cloud Source Setup IP | 운영자 환경 |
| `<ZDM_USER>` | ZDM 관리자 계정 | 운영자 환경 |

가이드 범위 밖 (외부 인프라 결정): firewall rule·DNS·TLS·reverse proxy·HA 구성·log aggregator stack.

## 1. 외부 모듈 install (3 VM)

### 1.1. db.internal — PostgreSQL 16 + TimescaleDB

```bash
sudo apt update
sudo apt install -y curl gnupg lsb-release

# TimescaleDB 공식 apt repo (signed-by 패턴)
curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg
echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/timescaledb.list
sudo apt update

# PG 16 + TimescaleDB extension
sudo apt install -y postgresql-16 timescaledb-2-postgresql-16

# Timescale 튜닝 (shared_preload_libraries 자동 설정)
sudo timescaledb-tune --quiet --yes
sudo systemctl restart postgresql

# DB·role 생성 + extension
sudo -u postgres psql <<SQL
CREATE USER engine_prod WITH PASSWORD '<DB_PASSWORD>';
CREATE DATABASE assessment OWNER engine_prod;
\c assessment
CREATE EXTENSION IF NOT EXISTS timescaledb;
SQL

# 외부 접속 허용 (engine-* VM 만)
sudo sed -i "s/^#listen_addresses.*/listen_addresses = '*'/" /etc/postgresql/16/main/postgresql.conf
echo "host assessment engine_prod <ENGINE_SUBNET> scram-sha-256" \
  | sudo tee -a /etc/postgresql/16/main/pg_hba.conf
sudo systemctl restart postgresql
```

install 실패·URL 변경 시 공식 install 가이드: https://docs.timescale.com/self-hosted/latest/install/installation-linux/

### 1.2. mq.internal — RabbitMQ

```bash
sudo apt update
sudo apt install -y curl gnupg apt-transport-https

# RabbitMQ 공식 install (cloudsmith)
curl -fsSL https://github.com/rabbitmq/signing-keys/releases/download/3.0/rabbitmq-release-signing-key.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/rabbitmq-signing.gpg
sudo tee /etc/apt/sources.list.d/rabbitmq.list <<EOF
deb [signed-by=/usr/share/keyrings/rabbitmq-signing.gpg] https://ppa1.rabbitmq.com/rabbitmq/rabbitmq-erlang/deb/debian $(lsb_release -cs) main
deb [signed-by=/usr/share/keyrings/rabbitmq-signing.gpg] https://ppa1.rabbitmq.com/rabbitmq/rabbitmq-server/deb/debian $(lsb_release -cs) main
EOF
sudo apt update
sudo apt install -y rabbitmq-server

sudo systemctl enable --now rabbitmq-server

# vhost + user (engine 전용)
sudo rabbitmqctl add_vhost /assessment
sudo rabbitmqctl add_user engine_prod '<MQ_PASSWORD>'
sudo rabbitmqctl set_permissions -p /assessment engine_prod '.*' '.*' '.*'

# default guest 제거 (보안)
sudo rabbitmqctl delete_user guest
```

install 실패·URL 변경 시 공식: https://www.rabbitmq.com/docs/install-debian

### 1.3. cache.internal — Redis 7

```bash
sudo apt update
sudo apt install -y redis-server

# 외부 접속 허용 (firewall로 engine-* IP 만 도달 가정)
sudo sed -i 's/^bind 127.0.0.1.*/bind 0.0.0.0/' /etc/redis/redis.conf
sudo sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf

sudo systemctl restart redis-server
sudo systemctl enable redis-server
```

본 엔진은 `REDIS_PASSWORD` 키 미지원 — Redis 접근 통제는 network-level (firewall·SG)에서 engine-* IP 화이트리스트로 처리.

## 2. release artifact 다운로드 + wheel install (4 engine VM 공통)

engine-web · engine-consumer · engine-worker · engine-scheduler 4 VM 모두 동일 절차. 각 VM에서:

```bash
# 공통 패키지·시스템 사용자
sudo apt update
sudo apt install -y python3.12 python3.12-venv gh
sudo useradd -r -m -d /opt/assessment-engine -s /usr/sbin/nologin assessment

# venv 생성
sudo -u assessment python3.12 -m venv /opt/assessment-engine/venv

# wheel 다운로드 + 무결성 검증
gh release download <VERSION> --repo z-converter-assessment/assessment-engine \
  --pattern '*.whl' --pattern 'SHA256SUMS' --dir /tmp/release
cd /tmp/release && sha256sum -c SHA256SUMS

# install (wheel 안 alembic.ini·_migrations 같이 들어옴)
sudo -u assessment /opt/assessment-engine/venv/bin/pip install /tmp/release/*.whl
```

## 3. 환경변수 파일 (4 engine VM)

본 repo는 `shared.env` (공통) + `<component>.env` (컴포넌트별) 2-layer 패턴. systemd `EnvironmentFile=` 여러 줄로 로드.

### 3.1. shared.env (4 VM 동일)

각 engine VM에서:

```bash
sudo mkdir -p /etc/assessment-engine
sudo tee /etc/assessment-engine/shared.env <<EOF
APP_ENV=prod
LOG_FORMAT=json

POSTGRES_HOST=db.internal
POSTGRES_PORT=5432
POSTGRES_DB=assessment
POSTGRES_USER=engine_prod
POSTGRES_PASSWORD=<DB_PASSWORD>

RABBITMQ_HOST=mq.internal
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/assessment
RABBITMQ_USER=engine_prod
RABBITMQ_PASSWORD=<MQ_PASSWORD>

REDIS_HOST=cache.internal
REDIS_PORT=6379
EOF
sudo chmod 0640 /etc/assessment-engine/shared.env
sudo chown root:assessment /etc/assessment-engine/shared.env
```

### 3.2. 컴포넌트별 env

각 VM에서 자기 컴포넌트 파일만:

engine-web:
```bash
sudo tee /etc/assessment-engine/web.env <<EOF
WEB_PORT=8000
INSTALL_BUNDLE_URL=http://engine-web:8000/zconverter.tar.gz
ZDM_DEFAULT_IP=<ZDM_IP>
ZDM_DEFAULT_USER=<ZDM_USER>
EOF
sudo chmod 0640 /etc/assessment-engine/web.env
sudo chown root:assessment /etc/assessment-engine/web.env
```

engine-consumer:
```bash
sudo tee /etc/assessment-engine/consumer.env <<EOF
WORKER_TASK_EXCHANGE=assessment.tasks
WORKER_TASK_QUEUE_PREFIX=agent.tasks
EOF
sudo chmod 0640 /etc/assessment-engine/consumer.env
sudo chown root:assessment /etc/assessment-engine/consumer.env
```

engine-worker:
```bash
sudo tee /etc/assessment-engine/diagnostic-worker.env <<EOF
LLM_PROVIDER=mock
WORKER_JOB_TIMEOUT_SECONDS=300
DIAGNOSTIC_QUEUE_TTL_MS=86400000
DIAGNOSTIC_QUEUE_MAX_LEN=100000
EOF
sudo chmod 0640 /etc/assessment-engine/diagnostic-worker.env
sudo chown root:assessment /etc/assessment-engine/diagnostic-worker.env
```

engine-scheduler:
```bash
sudo tee /etc/assessment-engine/diagnostic-scheduler.env <<EOF
DIAGNOSTIC_SCHEDULE_CRON=0 3 * * *
DIAGNOSTIC_RETENTION_DAYS=90
DIAGNOSTIC_ACTIVE_SERVER_WINDOW_HOURS=24
EOF
sudo chmod 0640 /etc/assessment-engine/diagnostic-scheduler.env
sudo chown root:assessment /etc/assessment-engine/diagnostic-scheduler.env
```

전체 키 카탈로그·default 값·컴포넌트별 read 매트릭스: `docs/operations/env.md`.

## 4. DB schema migration — Alembic 1회 (engine-web VM)

본 엔진은 schema 관리에 Alembic 사용. wheel 안에 `_alembic.ini` + `_migrations/` 동봉돼 있어 추가 다운로드 없이 실행 가능.

runtime systemd unit과 분리된 단계 — 모든 엔진 컴포넌트 시작 전에 1회만 실행. 정석 마이크로서비스 패턴 (schema migration과 서비스 부팅 entangle 금지).

engine-web VM에서:

```bash
# wheel 안 동봉된 alembic.ini 경로 동적 해석
ALEMBIC_INI=$(sudo -u assessment /opt/assessment-engine/venv/bin/python -c \
  'from importlib.resources import files; print(files("assessment_engine") / "_alembic.ini")')

# shared.env load 후 alembic upgrade head 1회
set -a; source /etc/assessment-engine/shared.env; set +a
sudo -E -u assessment /opt/assessment-engine/venv/bin/python -m alembic -c "$ALEMBIC_INI" upgrade head
```

성공 시 출력에 `Running upgrade ... -> <revision>, ...` 라인 표시 후 종료. 다음 절(5절) systemd 시작 진행.

이후 release마다 (schema 변경 포함 시) 본 단계 1회 재실행. staging/prod에서는 deployment pipeline (Ansible task·CI job 등)으로 격상 권장. 자세한 운영 정책 (라운드트립 검증·autogenerate 미지원 카탈로그·downgrade 단계): `docs/operations/alembic.md`.

## 5. systemd unit 시작 (4 VM 각각)

unit 패턴 동일 — module 이름과 `EnvironmentFile=` 1줄만 다름.

engine-web:
```bash
sudo tee /etc/systemd/system/assessment-engine-web.service <<'EOF'
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
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now assessment-engine-web.service
```

engine-consumer:
```bash
sudo tee /etc/systemd/system/assessment-engine-consumer.service <<'EOF'
[Unit]
Description=Assessment Engine Consumer
After=network-online.target

[Service]
Type=simple
User=assessment
WorkingDirectory=/opt/assessment-engine
EnvironmentFile=/etc/assessment-engine/shared.env
EnvironmentFile=/etc/assessment-engine/consumer.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.consumer
Restart=always
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now assessment-engine-consumer.service
```

engine-worker:
```bash
sudo tee /etc/systemd/system/assessment-engine-diagnostic-worker.service <<'EOF'
[Unit]
Description=Assessment Engine Diagnostic Worker
After=network-online.target

[Service]
Type=simple
User=assessment
WorkingDirectory=/opt/assessment-engine
EnvironmentFile=/etc/assessment-engine/shared.env
EnvironmentFile=/etc/assessment-engine/diagnostic-worker.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.diagnostic
Restart=always
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now assessment-engine-diagnostic-worker.service
```

engine-scheduler:
```bash
sudo tee /etc/systemd/system/assessment-engine-diagnostic-scheduler.service <<'EOF'
[Unit]
Description=Assessment Engine Diagnostic Scheduler
After=network-online.target

[Service]
Type=simple
User=assessment
WorkingDirectory=/opt/assessment-engine
EnvironmentFile=/etc/assessment-engine/shared.env
EnvironmentFile=/etc/assessment-engine/diagnostic-scheduler.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.diagnostic.scheduler
Restart=always
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now assessment-engine-diagnostic-scheduler.service
```

## 6. 헬스 확인

engine-web 도달 가능한 곳에서:
```bash
curl -fsS http://engine-web:8000/health
# 기대: {"status":"ok"}
```

각 엔진 VM에서 unit 상태 + 로그:
```bash
sudo systemctl status assessment-engine-web.service
sudo systemctl status assessment-engine-consumer.service
sudo systemctl status assessment-engine-diagnostic-worker.service
sudo systemctl status assessment-engine-diagnostic-scheduler.service

sudo journalctl -u 'assessment-engine-*' -n 100 --no-pager
```

unit이 `active (running)`이고 `/health`가 200이면 본 quickstart 종료.

## 7. 자주 막히는 지점

| 증상 | 원인·조치 |
|------|----------|
| `Settings()` 진입 시점 `ValueError` | `APP_ENV=prod` + weak default 거부. shared.env의 password가 strong random인지 확인 |
| `alembic upgrade head` 실패 — `extension "timescaledb" is not available` | 1.1절 `CREATE EXTENSION` 누락. `assessment` DB에서 재실행 |
| consumer가 broker 연결 실패 반복 | `RABBITMQ_HOST`·`RABBITMQ_VHOST`·creds 검토. `sudo rabbitmqctl list_permissions -p /assessment` |
| `/health`는 200인데 inventory 안 들어옴 | 에이전트가 별도 install·연결 필요. `docs/architecture/agent.md` |

추가 운영 사고 패턴: `docs/operations/deployment.md` 6절.

## 8. 본 quickstart를 자동화하기 — 인프라 레포 작성

본 가이드의 shell 명령은 한 번 직접 따라하기 용. 실제 prod 운영은 별도 인프라 레포 (Ansible·Salt·Chef·Pulumi 등)에서 본 명령들을 declarative task로 옮겨 관리.

본 repo와 인프라 레포 사이 연결:
- git 차원 연결 0 — submodule·fork·clone 아님
- 인프라 레포는 본 repo의 GitHub Release에서 wheel artifact만 다운로드 (`gh release download` 또는 `wget`)
- engine version은 인프라 레포의 변수 (예: `group_vars/all/engine.yml`의 `ENGINE_VERSION`)로 관리. 새 release 배포 = 변수 한 줄 변경 + playbook replay
- secret(`POSTGRES_PASSWORD` 등)은 ansible-vault·HashiCorp Vault·k8s Secret 등 외부 채널. 본 repo는 채널을 강제하지 않음 — `APP_ENV=prod`에서 weak default 거부만 검증

Ansible 매핑 예시 — 절 2 (wheel install) 일부를 task로 옮긴 모습:

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

- name: venv에 wheel install
  ansible.builtin.pip:
    name: "{{ lookup('fileglob', '/tmp/release-' + engine_version + '/assessment_engine-*.whl') }}"
    virtualenv: /opt/assessment-engine/venv
    virtualenv_python: python3.12
  become_user: assessment
```

본 quickstart의 절 1~6 모든 shell 명령이 동일하게 1:1 매핑 가능. 정석 인프라 레포 구조:

```
infra-assessment/                     # 본 repo와 별개 repo
  ansible/
    inventories/prod/hosts.ini        # 7 VM의 IP·hostname
    group_vars/
      all/shared.yml                  # POSTGRES_HOST 등 공통 (vault 또는 ansible-vault)
      engine/version.yml              # ENGINE_VERSION: v0.1.0
    roles/
      postgres/                       # 절 1.1 PG+TimescaleDB install
      rabbitmq/                       # 절 1.2 MQ install
      redis/                          # 절 1.3 Redis install
      assessment_engine/              # 절 2~6 (engine 4 컴포넌트)
        tasks/
          install.yml                 # wheel 다운로드 + venv install
          env.yml                     # shared.env + <component>.env 템플릿 렌더
          migration.yml               # web host에서 alembic upgrade 1회
          systemd.yml                 # unit 생성 + systemctl enable --now
    site.yml
```

new engine release 배포 흐름:
1. 본 repo에서 새 tag (예: v0.2.0) release 발사 (Release PR 머지)
2. 인프라 레포의 `group_vars/engine/version.yml`에서 `ENGINE_VERSION: v0.1.0 -> v0.2.0` 한 줄 변경 + PR
3. 인프라 레포 PR 머지 → `ansible-playbook site.yml` 재실행 (수동 또는 인프라 레포의 CD)
4. Ansible이 engine VM 4개에 SSH → 새 wheel 다운로드 + 재설치 + systemd restart. 외부 모듈(DB·MQ·Redis) role은 변경 없으니 skip

도구 자유 — Salt·Chef·Pulumi·Helm(컨테이너 운영 결정 시) 모두 동일 원칙 (release artifact 받기 + declarative state 관리). 본 repo는 도구를 강제하지 않음.

## 9. 다음 단계

| 항목 | 위치 |
|------|------|
| 에이전트 install·연결 | `docs/architecture/agent.md` |
| 환경변수 전체 카탈로그 | `docs/operations/env.md` |
| prod hardening (secret 채널·TLS·reverse proxy·observability) | `docs/operations/prod-contract.md` · `docs/operations/observability.md` |
| Alembic 운영 (release마다 재실행·downgrade·라운드트립 검증) | `docs/operations/alembic.md` |
| 단일 host·다른 분리 패턴·systemd hardening | `docs/operations/deployment.md` 4·5절 |
| release artifact 생성·검증 채널 | `docs/operations/release.md` |
