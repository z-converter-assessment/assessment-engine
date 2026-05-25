# INSTALL.md — ZConverter Cloud Assessment Engine 설치/운영 가이드

이 문서는 배포 인프라(VM + Linux + systemd)가 본 엔진을 설치/기동/운영하기 위한 단일 self-contained 가이드다. 이 문서 하나만 따라가면 동작한다 — 다른 문서를 참조할 필요가 없다.

대상 경로는 wheel + Python venv + systemd 단일 토폴로지다. 위에서 아래로 순서대로 따라가면 된다.

---

## 1. 이 문서의 범위

본 repo가 책임지는 것: 애플리케이션 코드 + 설치 artifact(wheel) + 환경변수 contract + DB 스키마 마이그레이션.

본 repo가 책임지지 않는 것(인프라 측 결정): VM provisioning, 네트워크/방화벽, PostgreSQL/RabbitMQ/Redis 자체 설치, secret 주입 채널, reverse proxy, 로그 수집 stack.

엔진은 위 세 가지 백킹 서비스(PostgreSQL/RabbitMQ/Redis)가 이미 떠 있고 도달 가능하다는 것을 전제로 동작한다.

---

## 2. 무엇을 배포하는가

엔진은 3개의 독립 프로세스(컴포넌트)로 구성된다. 같은 wheel 하나를 설치한 뒤, 실행할 모듈명만 바꿔서 띄운다.

```
                 inventory / metrics / error
   Agent (C) ----------------------------------> RabbitMQ ---> Consumer ---+
   (separate  <----------------------------------                          |
    repo)           task.install                  diagnostic.request       |
                                                         |                 |
                                                         v                 v
                                                 Diagnostic Worker     TimescaleDB
                                                         |            (PostgreSQL 16
                                                         v             + TimescaleDB
                                                    Ollama LLM          + pgvector)
                                                   (optional)              ^
                                                                           | read
   Operator <--- HTTP :8000 --- FastAPI Web <------------------------------+
                              ( + Redis cache / online TTL / pub-sub )
```

| 컴포넌트 | 실행 모듈 | 역할 | 외부 노출 |
|----------|-----------|------|-----------|
| web | `assessment_engine.web` | 운영자용 FastAPI UI + REST + SSE + `/metrics`. HTTP 포트 listen (기본 8000) | 운영자/리버스 프록시에만 |
| consumer | `assessment_engine.consumer` | RabbitMQ에서 inventory/metrics/error/task.result 메시지 소비 후 DB 저장 | 없음 (broker만 접속) |
| diagnostic-worker | `assessment_engine.diagnostic` | 진단 요청 소비 후 규칙 기반 + LLM 분석. web POST로만 트리거 | 없음 (broker + LLM만 접속) |

세 컴포넌트는 같은 host에 모두 띄워도 되고, 부하에 따라 다른 host로 분리해도 된다(6절). 모두 stateless라 수평 확장(N개 복제) 가능하다.

진입점은 전부 `python -m <모듈명>` 형태다.

---

## 3. 사전 준비

| 항목 | 요구 |
|------|------|
| Python | 3.12 이상 (설치 host에 `python3.12` 존재) |
| PostgreSQL | 16, TimescaleDB extension + pgvector extension 사용 가능 상태 |
| RabbitMQ | 3.13 이상 |
| Redis | 7 이상 |
| 네트워크 | 엔진 host에서 위 3개 서비스에 도달 가능. 에이전트가 엔진의 RabbitMQ broker에 도달 가능 |

### 3.1. PostgreSQL extension 준비

엔진의 시계열 저장은 TimescaleDB hypertable을, 진단 RAG 기능은 pgvector를 사용한다. 두 extension의 바이너리가 DB host에 설치돼 있어야 하고, 대상 DB에서 활성화돼 있어야 마이그레이션이 통과한다.

대상 DB에 접속해서 한 번 실행:

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
```

`extension "timescaledb" is not available` 오류가 나면 DB host에 TimescaleDB 패키지 자체가 설치돼 있지 않은 것이다 (OS 패키지로 별도 설치 필요).

pgvector(RAG)는 진단의 선택 기능이다. RAG를 쓰지 않더라도 마이그레이션이 `rag_documents` 테이블을 만들면서 vector 타입을 요구하므로, `vector` extension은 활성화해 두는 것을 권장한다.

### 3.2. RabbitMQ vhost / 계정 준비

엔진은 전용 vhost(`/assessment`)를 사용한다. exchange와 queue는 엔진이 기동 시 자동으로 선언하므로 직접 만들 필요는 없다. vhost와 접속 계정/권한만 준비한다.

```bash
# vhost 생성
sudo rabbitmqctl add_vhost /assessment

# 엔진 접속 계정 생성 (아래 값은 prod에서 강한 random으로)
sudo rabbitmqctl add_user engine_prod '<strong-random-password>'

# /assessment vhost에 대한 권한 부여 (configure / write / read)
sudo rabbitmqctl set_permissions -p /assessment engine_prod '.*' '.*' '.*'
```

에이전트(C 바이너리)는 엔진과 다른 별도 계정/secret 채널을 쓴다. 에이전트 자격은 엔진과 독립적으로 관리한다.

### 3.3. Redis

기본 설정으로 충분하다. 엔진은 모든 Redis 호출을 fail-open으로 처리하므로 Redis가 잠시 죽어도 엔진이 멈추지 않는다(중복 방지/온라인 표시 같은 부가 기능만 일시 저하). 메모리 cap과 eviction policy는 운영 정책에 맞춰 조정한다 (권장: `maxmemory` 설정 + `volatile-lru` — TTL 키 우선 eviction).

---

## 4. 설치 절차

아래 예시는 `/opt/assessment-engine`에 설치하고 `assessment`라는 system user로 실행한다고 가정한다. 경로/계정명은 자유롭게 바꿔도 된다.

### 4.1. 실행 계정/디렉토리 준비

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin assessment
sudo mkdir -p /opt/assessment-engine
sudo mkdir -p /etc/assessment-engine
```

### 4.2. wheel 다운로드 + 무결성 검증

release는 GitHub Release에 wheel + sdist + 체크섬 + SBOM + 서명으로 게시된다. wheel은 `py3-none-any`라 OS/아키텍처 무관이다.

```bash
# <org>/<repo>는 실제 GitHub 좌표로 교체. <X.Y.Z>는 배포할 버전.
gh release download v<X.Y.Z> --repo <org>/assessment-engine \
  --pattern '*.whl' --pattern 'SHA256SUMS' --dir /tmp/release

cd /tmp/release && sha256sum -c SHA256SUMS
# assessment_engine-<X.Y.Z>-py3-none-any.whl: OK
```

GitHub outbound가 막힌 폐쇄망이면 사내 mirror(Nexus/devpi/MinIO 등)에서 같은 파일을 받아도 된다.

추가 무결성 검증(선택): release에 동봉된 Sigstore 서명(`*.sigstore`)으로 `cosign verify-blob` 가능. SBOM(`sbom.cdx.json`, CycloneDX)으로 의존성 CVE 추적 가능.

### 4.3. venv 생성 + wheel 설치

```bash
sudo python3.12 -m venv /opt/assessment-engine/venv
sudo /opt/assessment-engine/venv/bin/pip install \
  /tmp/release/assessment_engine-<X.Y.Z>-py3-none-any.whl
sudo chown -R assessment:assessment /opt/assessment-engine
```

이 wheel 안에는 애플리케이션 코드뿐 아니라 Alembic 마이그레이션(`assessment_engine/_migrations/`)과 설정(`assessment_engine/_alembic.ini`)이 함께 들어 있다. 즉 wheel 하나만 설치하면 스키마 마이그레이션까지 바로 실행할 수 있다.

### 4.4. 환경변수 파일 작성

가장 단순한 패턴은 모든 컴포넌트가 공유하는 파일 하나다. secret이 들어가므로 권한을 0640으로 좁힌다.

```bash
sudo install -m 0640 -o root -g assessment /dev/stdin /etc/assessment-engine/engine.env <<'EOF'
# --- 환경 마커 ---
APP_ENV=prod
LOG_FORMAT=json

# --- PostgreSQL ---
POSTGRES_HOST=db.internal
POSTGRES_PORT=5432
POSTGRES_DB=assessment
POSTGRES_USER=engine_prod
POSTGRES_PASSWORD=<strong-random>

# --- RabbitMQ ---
RABBITMQ_HOST=mq.internal
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/assessment
RABBITMQ_USER=engine_prod
RABBITMQ_PASSWORD=<strong-random>

# --- Redis ---
REDIS_HOST=cache.internal
REDIS_PORT=6379

# --- Web ---
WEB_PORT=8000

# --- ZDM (원격 설치 task 대상 좌표 — 고객사 ZDM 서버) ---
ZDM_DEFAULT_IP=<zdm-host>:<port>
ZDM_DEFAULT_USER=<zdm-admin-account>

# --- Diagnostic worker LLM (Ollama) ---
OLLAMA_BASE_URL=http://<ollama-host>:11434
OLLAMA_MODEL=llama3.1:8b
EOF
```

각 키의 의미/기본값/필수 여부는 5절 카탈로그에 전부 정리돼 있다. `APP_ENV=prod`이면 약한 기본값(아래 5.3)을 거부하므로, password/user는 반드시 dev 기본값(`assessment`)이 아닌 값으로 채워야 한다.

secret을 평문 파일 대신 Vault/systemd `LoadCredential=`/외부 secret manager로 주입해도 된다 (5.2 우선순위 참고). 엔진은 환경변수든 `/run/secrets/<필드명>` 파일이든 모두 인식한다.

### 4.5. DB 스키마 마이그레이션 1회

wheel 안에 동봉된 alembic 설정 경로를 동적으로 찾아서 `upgrade head`를 한 번 실행한다. 환경변수 파일을 source해서 DB 접속 정보를 넘긴다.

```bash
set -a; . /etc/assessment-engine/engine.env; set +a

ALEMBIC_INI=$(/opt/assessment-engine/venv/bin/python -c \
  'from importlib.resources import files; print(files("assessment_engine") / "_alembic.ini")')

/opt/assessment-engine/venv/bin/python -m alembic -c "$ALEMBIC_INI" upgrade head
```

이 마이그레이션이 hypertable 변환과 `rag_documents`(pgvector) 테이블 생성까지 모두 수행한다. 그래서 3.1의 extension 준비가 선행돼야 한다.

큰 변경(DROP/대량 ALTER) 전에 실제 실행될 DDL을 먼저 보고 싶으면:

```bash
/opt/assessment-engine/venv/bin/python -m alembic -c "$ALEMBIC_INI" upgrade head --sql > /tmp/migration.sql
/opt/assessment-engine/venv/bin/python -m alembic -c "$ALEMBIC_INI" current   # 현재 적용 버전 확인
```

마이그레이션은 멱등하다 — 이미 최신이면 아무 것도 하지 않는다. 컴포넌트가 분리된 멀티노드 배포에서도 web host 한 곳에서만 1회 실행하면 된다.

### 4.6. systemd unit 등록

3개 컴포넌트 각각에 대해 unit 파일을 만든다. 실행 모듈(`ExecStart`의 마지막 인자)만 다르고 나머지는 동일하다.

web (`/etc/systemd/system/assessment-engine-web.service`):

```ini
[Unit]
Description=Assessment Engine Web
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=assessment
WorkingDirectory=/opt/assessment-engine
EnvironmentFile=/etc/assessment-engine/engine.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.web
Restart=always
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
```

consumer (`/etc/systemd/system/assessment-engine-consumer.service`)는 위에서 `Description`과 `ExecStart` 마지막만 교체:

```ini
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.consumer
```

diagnostic-worker (`/etc/systemd/system/assessment-engine-diagnostic.service`):

```ini
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.diagnostic
```

`KillSignal=SIGTERM` + 충분한 `TimeoutStopSec`이 중요하다. 엔진은 SIGTERM을 받으면 진행 중인 작업(HTTP 요청 완료, 처리 중 메시지 ack)을 끝내고 graceful하게 종료한다. SIGKILL로 강제 종료하면 in-flight 작업이 유실될 수 있다.

기동:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now assessment-engine-web
sudo systemctl enable --now assessment-engine-consumer
sudo systemctl enable --now assessment-engine-diagnostic
```

### 4.7. 헬스 확인

```bash
curl -fsS http://<engine-host>:8000/health
# {"status":"ok"} 응답이면 web 정상
```

consumer/diagnostic은 HTTP를 열지 않으므로 로그로 확인한다:

```bash
sudo journalctl -u assessment-engine-consumer -f
sudo journalctl -u assessment-engine-diagnostic -f
```

broker 연결 성공 로그가 보이면 정상이다. 에이전트가 메시지를 발행하기 시작하면 web UI(`http://<engine-host>:8000/servers/`)에 호스트가 나타난다.

---

## 5. 환경변수 레퍼런스

엔진의 모든 설정은 환경변수로 주입한다. 코드에 환경별 값을 박지 않는다 — 같은 wheel을 모든 환경에서 쓰고, 차이는 환경변수로만 둔다.

### 5.1. config vs secret

| 분류 | 정의 | 예시 | 보관 |
|------|------|------|------|
| config | 노출돼도 즉시 위태롭지 않은 운영 값 | `POSTGRES_HOST`/`RABBITMQ_VHOST`/`WEB_PORT`/`APP_ENV`/`LOG_FORMAT` | 평문 파일 가능 |
| secret | 노출 시 즉시 무단 접근 가능한 자격 | `POSTGRES_PASSWORD`/`RABBITMQ_PASSWORD` | secret 채널 권장, git 커밋 금지 |

헷갈리면 secret으로 취급한다. 잘못 분류해 secret을 평문 노출하는 비용이 그 반대보다 크다.

### 5.2. 주입 우선순위

엔진은 pydantic-settings로 설정을 읽으며, 같은 키가 여러 경로에 있으면 아래 우선순위로 결정된다(위가 약하고 아래가 강함).

```
[가장 약함]  코드 기본값
            cwd의 .env 파일
            <SECRETS_DIR>/<필드명> 파일   (SECRETS_DIR 기본값 /run/secrets, 디렉토리 존재 시에만)
[가장 강함]  OS 환경변수                   (systemd EnvironmentFile / export 포함)
```

따라서 어떤 secret 채널을 쓰든 동작한다:
- 환경변수 직접: `POSTGRES_PASSWORD=<값>` (systemd `EnvironmentFile`/`Environment`/shell export)
- 파일 채널: `/run/secrets/postgres_password` 파일에 값 — 파일명이 소문자 필드명과 정확히 일치해야 한다. 경로는 `SECRETS_DIR` 환경변수로 바꿀 수 있다.
- Vault/k8s Secret/SOPS 등: 위 두 형태 중 하나로 풀어서 주입하면 된다.

### 5.3. prod 시작 시 안전장치 (fail-fast)

`APP_ENV=prod`이면 엔진은 기동 시점에 약한 기본값을 거부하고 즉시 `ValueError`로 죽는다. 거부 대상은 `POSTGRES_PASSWORD`/`POSTGRES_USER`/`RABBITMQ_PASSWORD`/`RABBITMQ_USER`이며, 거부되는 약한 값 집합은:

```
"", "assessment", "password", "admin", "root", "changeme"
```

즉 prod에서 secret 주입을 빠뜨리거나 dev 기본값(`assessment`)을 그대로 두면 서비스가 시작되지 않는다. 이것은 의도된 동작이다 — "조용히 약한 자격으로 떠 있는" 상황을 막는다. 기동 직후 이 `ValueError`가 나면 secret 주입 채널을 점검하라는 신호다.

### 5.4. 전체 키 카탈로그

기본값은 dev 편의를 위한 값이며, prod에서는 host/계정/secret을 실제 값으로 채워야 한다.

공통 (web/consumer/diagnostic-worker 모두 필요):

| 키 | 기본값 | 설명 |
|----|--------|------|
| `APP_ENV` | `dev` | 환경 마커. `dev`/`staging`/`prod`. `prod`이면 약한 기본값 거부 |
| `LOG_FORMAT` | `text` | 로그 출력 형식. `text`(컬러/grep 친화) 또는 `json`(로그 수집기 indexing). prod는 `json` 권장 |
| `POSTGRES_HOST` | `postgres` | PostgreSQL 호스트. prod는 실제 host 명시 |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `assessment` | DB 이름 |
| `POSTGRES_USER` | `assessment` | prod에서 약한 기본값 거부 |
| `POSTGRES_PASSWORD` | `assessment` | secret. prod에서 약한 기본값 거부 |
| `REDIS_HOST` | `redis` | Redis 호스트. prod는 실제 host |
| `REDIS_PORT` | `6379` | |
| `RABBITMQ_HOST` | `rabbitmq` | broker 호스트. prod는 실제 host |
| `RABBITMQ_PORT` | `5672` | |
| `RABBITMQ_VHOST` | `/assessment` | 전용 vhost. AMQP URL의 `/`는 엔진이 자동으로 `%2F` 인코딩 |
| `RABBITMQ_USER` | `assessment` | prod에서 약한 기본값 거부 |
| `RABBITMQ_PASSWORD` | `assessment` | secret. prod에서 약한 기본값 거부 |
| `RABBITMQ_EXCHANGE` | `assessment` | inventory/metrics/error routing exchange. 에이전트와 동일 값이어야 함 |
| `RABBITMQ_ROUTING_KEY_INVENTORY` | `server.inventory` | 에이전트와 동일 값 |
| `RABBITMQ_ROUTING_KEY_METRICS` | `server.metrics` | 에이전트와 동일 값 |
| `RABBITMQ_ROUTING_KEY_ERROR` | `server.error` | 에이전트와 동일 값 |
| `SQLALCHEMY_ECHO` | `false` | SQL 로깅. 운영 환경은 `false` 유지 (로그 폭증/secret 노출 위험) |

web 컴포넌트에 필요:

| 키 | 기본값 | 설명 |
|----|--------|------|
| `WEB_PORT` | `8000` | web UI listen 포트 |
| `WEB_RELOAD` | `false` | uvicorn auto-reload. dev hot-reload 전용. prod는 미설정/false 유지 (코드 변경 감시 프로세스 불필요, wheel 배포에선 무의미) |
| `ZDM_DEFAULT_IP` | `host.docker.internal:8000` | 원격 설치 task 대상 ZDM 서버 좌표. install 모달 기본값. prod는 고객사 ZDM으로 교체 |
| `ZDM_DEFAULT_USER` | `admin@zconverter.com` | ZDM 관리자 계정 기본값 |
| `ZDM_PACKAGE_PATH` | `/download/ZConverter_CloudSource_Setup_Linux.tar.gz` | ZDM 본체 패키지 URL path (Linux) |
| `ZDM_PACKAGE_SCRIPT` | `zconverter_install_source/install.sh` | tar 추출 후 실행할 스크립트 경로 |
| `ZDM_META_CONNECT_TIMEOUT_SEC` | `5.0` | ZDM 메타 조회 connect timeout |
| `ZDM_META_TOTAL_TIMEOUT_SEC` | `120.0` | ZDM 메타 조회 total timeout |
| `INSTALL_TIMEOUT_SEC` | `600` | 원격 install.sh wall-clock timeout |
| `AGENT_RESTART_ALERT_THRESHOLD` | `3` | 1시간 슬라이딩 윈도우 내 에이전트 재시작 횟수 경고 임계 |

`ZDM_DEFAULT_IP`/`ZDM_DEFAULT_USER`는 기동을 막지 않는다(secret 아님). 미설정 상태로 두면 첫 install 발행이 런타임에서 실패할 뿐이다. 운영자가 web 모달에서 발행마다 덮어쓸 수 있다.

diagnostic-worker 컴포넌트에 필요:

| 키 | 기본값 | 설명 |
|----|--------|------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | LLM/embedding 공통 Ollama 서버 base URL |
| `OLLAMA_MODEL` | `llama3.1:8b` | LLM 모델명. 한국어 정합 우위 모델로 교체 가능 |
| `LLM_TIMEOUT_SECONDS` | `60` | LLM 호출 timeout |
| `WORKER_JOB_TIMEOUT_SECONDS` | `300` | 진단 1건 전체 처리 timeout (클라이언트 polling과 정렬) |
| `DIAGNOSTIC_ROUTING_KEY` | `diagnostic.request` | 진단 요청 routing key (web publish / worker consume 공통) |
| `DIAGNOSTIC_QUEUE_TTL_MS` | `86400000` | 진단 큐 메시지 TTL (24시간) |
| `DIAGNOSTIC_QUEUE_MAX_LEN` | `100000` | 진단 큐 최대 길이 |
| `RAG_ENABLED` | `false` | RAG 보강 활성 여부. `false`면 진단 시 RAG 검색 단계를 건너뛴다 |
| `EMBEDDING_PROVIDER` | `mock` | embedding 클라이언트. `mock`(결정적 더미) 또는 `ollama` |
| `EMBEDDING_MODEL` | `mxbai-embed-large` | Ollama embedding 모델명. RAG 사용 시 `ollama pull` 필요 |
| `EMBEDDING_DIMENSION` | `1024` | embedding 벡터 차원. 모델 변경 시 마이그레이션 1회 필요 |
| `EMBEDDING_TIMEOUT_SECONDS` | `30.0` | embedding 호출 timeout |
| `RAG_TOP_K` | `5` | RAG 검색 top-k |
| `RAG_MAX_CONTEXT_CHARS` | `4000` | LLM 프롬프트 내 RAG context 최대 길이 |

LLM(Ollama)은 외부 유료 API가 아니라 로컬에서 띄우는 LLM 런타임이다. diagnostic-worker가 도달 가능한 곳에 Ollama 서버를 띄우고 `OLLAMA_BASE_URL`을 가리키면 된다. RAG를 쓰려면 `RAG_ENABLED=true` + `EMBEDDING_PROVIDER=ollama` + Ollama에서 embedding 모델 pull이 필요하다.

카탈로그에 없는 키: Redis TTL/키 prefix 같은 내부 튜닝 값들은 합리적 기본값이 코드에 있고 환경변수로 노출하지 않았다. 필요하면 같은 이름의 환경변수로 덮어쓸 수 있다(예: `REDIS_TTL_ONLINE`).

### 5.5. 컴포넌트별 필요 키 매트릭스

3개 컴포넌트를 다른 host로 분리할 때, 어느 host에 어떤 키를 주입할지 기준이다. 한 host에 모두 띄우면 그냥 전체를 한 파일에 넣으면 된다.

| 키 그룹 | web | consumer | diagnostic-worker |
|--------|:---:|:--------:|:-----------------:|
| `APP_ENV`/`LOG_FORMAT` | 필요 | 필요 | 필요 |
| `POSTGRES_*` | 필요 | 필요 | 필요 |
| `REDIS_*` | 필요 | 필요 | 필요 |
| `RABBITMQ_*` (접속/exchange/routing key) | 필요 | 필요 | 필요 |
| `WEB_PORT`/`ZDM_*`/`INSTALL_TIMEOUT_SEC`/`AGENT_RESTART_ALERT_THRESHOLD` | 필요 | 불필요 | 불필요 |
| `OLLAMA_*`/`LLM_*` | 불필요 | 불필요 | 필요 |
| `RAG_*`/`EMBEDDING_*` | 불필요 | 불필요 | 필요 |
| `DIAGNOSTIC_ROUTING_KEY` | 필요 (publish) | 불필요 | 필요 (consume) |
| `DIAGNOSTIC_QUEUE_*`/`WORKER_JOB_TIMEOUT_SECONDS` | 불필요 | 불필요 | 필요 |

각 컴포넌트는 자기가 쓰는 키만 검증한다. 따라서 분리 배포 시 consumer host에 LLM 키가 없어도 정상이다.

---

## 6. 멀티노드 분리 배포 (선택)

부하가 커지면 3개 컴포넌트를 다른 host에 분산할 수 있다. 정석 패턴은 공통 키 파일 하나 + 컴포넌트별 키 파일 하나의 2단 계층이다. systemd는 `EnvironmentFile=`을 여러 줄 지원한다.

```
공통 (모든 노드 동일):       /etc/assessment-engine/shared.env
  APP_ENV / LOG_FORMAT / POSTGRES_* / REDIS_* / RABBITMQ_*

web 노드:                    /etc/assessment-engine/web.env
  WEB_PORT / ZDM_* / DIAGNOSTIC_ROUTING_KEY

diagnostic-worker 노드:      /etc/assessment-engine/diagnostic.env
  OLLAMA_* / RAG_* / EMBEDDING_* / DIAGNOSTIC_QUEUE_* / WORKER_JOB_TIMEOUT_SECONDS
```

각 노드의 unit:

```ini
[Service]
EnvironmentFile=/etc/assessment-engine/shared.env
EnvironmentFile=/etc/assessment-engine/<component>.env
ExecStart=/opt/assessment-engine/venv/bin/python -m assessment_engine.<module>
```

효과: 공통 키(DB/MQ)는 한 파일만 고치면 모든 노드가 따라간다(drift 차단). 노드가 침해돼도 그 노드의 키만 노출된다. 마이그레이션은 여전히 한 host에서 1회만 실행한다.

수평 확장: web/consumer/diagnostic-worker 모두 복제본 N개로 늘려도 안전하다. consumer는 broker prefetch로, diagnostic-worker는 job 단위로 부하가 분산된다.

---

## 7. 운영

### 7.1. graceful shutdown

SIGTERM을 받으면 web은 진행 중 HTTP 요청을 끝내고, consumer/diagnostic-worker는 처리 중 메시지를 ack한 뒤 종료한다. systemd unit의 `KillSignal=SIGTERM` + 넉넉한 `TimeoutStopSec`이 이를 보장한다. SIGKILL 강제 종료는 피한다.

### 7.2. 로그

prod는 `LOG_FORMAT=json`을 권장한다. 엔진은 stdout으로 JSON 라인을 출력하고, systemd journal이 이를 수집한다. 외부 로그 수집기(예: journal -> 수집기)로 indexing한다. 로그에는 secret이나 메시지 본문이 찍히지 않는다 — 식별자(host_id 등)와 카운트만 남는다.

```bash
sudo journalctl -u assessment-engine-web -f
sudo journalctl -u assessment-engine-consumer --since "1 hour ago"
```

### 7.3. 관측 (Prometheus)

web이 `/metrics` 경로로 Prometheus scrape 타깃을 노출한다. 주의: `/metrics`는 외부에 노출하면 안 된다. reverse proxy에서 internal-only로 막고, Prometheus만 내부망에서 scrape하도록 한다.

### 7.4. 업그레이드

새 버전 배포는 4.2~4.3을 새 버전으로 반복한 뒤(같은 venv에 새 wheel을 `pip install`하거나 새 venv 생성), 4.5 마이그레이션 1회, 그리고 서비스 재시작이다.

```bash
sudo /opt/assessment-engine/venv/bin/pip install --upgrade \
  /tmp/release/assessment_engine-<NEW>-py3-none-any.whl

# (위 4.5 마이그레이션 1회 — web host에서)

sudo systemctl restart assessment-engine-web assessment-engine-consumer assessment-engine-diagnostic
```

스키마 변경이 데이터 손실을 동반할 수 있는 큰 변경(컬럼 DROP/대량 ALTER)이면, 4.5의 `--sql` 옵션으로 실행될 DDL을 먼저 검토한 뒤 적용한다.

---

## 8. 트러블슈팅

| 증상 | 원인 / 조치 |
|------|-------------|
| 기동 즉시 `ValueError` (POSTGRES_PASSWORD/USER ... is unset or uses a dev default) | `APP_ENV=prod`인데 password/user가 약한 기본값. 5.3 참고 — 강한 secret 주입 |
| `alembic upgrade head` 실패: `extension "timescaledb" is not available` | DB host에 TimescaleDB 패키지 미설치. 3.1 참고 |
| `alembic upgrade head` 실패: vector 타입 오류 | 대상 DB에 `CREATE EXTENSION vector` 미실행. 3.1 참고 |
| consumer가 broker 연결을 반복 실패 | `RABBITMQ_HOST`/`RABBITMQ_VHOST`/계정/권한 점검. `sudo rabbitmqctl list_permissions -p /assessment` |
| `/health`는 200인데 서버 인벤토리가 안 들어옴 | 에이전트가 broker에 메시지를 발행하지 않는 상태. 에이전트 측 설치/broker 접속 자격 점검 (에이전트는 별도 repo/별도 secret 채널) |
| web UI는 뜨는데 진단이 안 돌아감 | diagnostic-worker가 떠 있는지 + `OLLAMA_BASE_URL` 도달성 확인. `journalctl -u assessment-engine-diagnostic` |
| `/metrics`가 외부에 노출됨 | reverse proxy에서 internal-only 라우트 차단 추가 |

---

## 9. 핵심 원칙 5가지

1. secret을 git에 커밋하지 않는다 — prod secret은 인프라의 secret 채널로.
2. dev 편의성을 prod 안전성과 거래하지 않는다 — 평문 `.env`는 dev에만, prod는 secret 채널 + `APP_ENV=prod` 검증.
3. 약한 기본값을 prod로 흘려보내지 않는다 — 기동 시 fail-fast.
4. 같은 wheel을 모든 환경에서 쓴다 — 차이는 환경변수로만.
5. 에이전트 자격을 엔진 자격과 분리한다 — 각자 독립 라이프사이클.
