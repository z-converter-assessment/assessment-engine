# 파이프라인 검증 가이드

## 파이프라인 검증 (Lima VM)

에이전트(C 바이너리) -> RabbitMQ -> Consumer -> DB -> Web UI 전체 파이프라인을 실제 VM 환경에서 검증한다 + 시연용 분류·attention 분포 가시화.

본 문서는 절차 요약. VM 매트릭스·OS 다양성·합성 부하 프로파일·dispatch 분기·누적 사고 패턴은 `docs/operations/lima.md` 단일 진실.

```
+------------------------------------------------------------------------------+
|                                 HOST MACHINE                                 |
|                                                                              |
|   +------------------------------------------------------------------+       |
|   |                 DOCKER COMPOSE (assessment-engine)                |      |
|   |        +-------------+              +-------------+               |      |
|   |        |   FastAPI   |              |   RabbitMQ  |               |      |
|   |        |   (:8000)   |              |   (:5672)   |               |      |
|   |        +-------------+              +-------------+               |      |
|   |              ^                            |                       |      |
|   |              | (4) QUERY                  | (2) DISPATCH          |      |
|   |              |                            v                       |      |
|   |        +-------------+              +-------------+               |      |
|   |        | PostgreSQL  | <----------- |   Consumer  |               |      |
|   |        |   (:5432)   |  (3) PERSIST +-------------+               |      |
|   |        +-------------+                                            |      |
|   +-----------------------^-------------------------------------------+      |
|                           |                                                  |
|                           | (1) PUBLISH (Target: host.lima.internal)         |
|     +--+--+--+--+--+--+--+                                                   |
|     |  |  |  |  |  |  |  |                                                   |
|  +--+--+--+--+--+--+--+--+ +-+-+-+-+-+-+-+                                   |
|  | web | offline | app   | | monitor | mq | cache | db |                    |
|  +-----+---------+-------+-+---------+----+-------+----+                    |
|  | Agent (C, systemd assessment-agent.service x 7 VM)  |                    |
|  +-----------------------------------------------------+                    |
+------------------------------------------------------------------------------+
```

- Lima user-mode 네트워크 — VM → host 접근 주소: `host.lima.internal`
- 7 VM 동시 동작 — 시연 분류 분포 over/optimal/under/insufficient_data + attention 신호 agent_unstable·gap_warnings 가시화
- `./dev-up.sh` 완료 시 각 VM 에이전트 자동 빌드 → systemd 서비스 등록 → 시작

### 사전 요구사항

| 소프트웨어 | 설치 방법 | 용도 |
|-----------|----------|------|
| Lima 1.0+ | `brew install lima` | macOS용 경량 Linux VM (Apple Virtualization Framework / QEMU 백엔드) |
| Docker | Docker Desktop 또는 colima | 엔진 docker-compose |

> Apple Silicon (ARM64) / Intel 모두 지원. cloud image를 host arch에 맞춰 자동 선택.

### 디렉토리 구조 전제

```
(작업 디렉토리)/
├── assessment-engine/   <- infra/lima/*.yaml 위치
└── assessment-agent/    <- 에이전트 소스 (git clone 필요)
```

### VM 구성 요약 (7 VM, 시연 가시화 진행 순서)

| 순서 | VM | OS | 서비스 | 분류 | attention | yaml |
|---|----|----|--------|------|-----------|------|
| 1 | `web-server-01` | Debian 12 | nginx | optimal | agent_unstable (3분 주기 restart) | `infra/lima/web-server-01.yaml` |
| 2 | `offline-server-01` | Debian 13 trixie | (없음) | insufficient_data | gap_warnings (5m+ 끊김) | `infra/lima/offline-server-01.yaml` |
| 3 | `app-server-01` | Ubuntu 24.04 | docker.io | under_provisioned | (도넛만) | `infra/lima/app-server-01.yaml` |
| 4 | `monitor-server-01` | CentOS Stream 9 | zabbix-agent | optimal | (도넛만) | `infra/lima/monitor-server-01.yaml` |
| 5 | `mq-server-01` | openSUSE Leap 15 | mosquitto | over_provisioned | (도넛만) | `infra/lima/mq-server-01.yaml` |
| 6 | `cache-server-01` | Rocky Linux 9 | redis | over_provisioned | (도넛만) | `infra/lima/cache-server-01.yaml` |
| 7 | `db-server-01` | AlmaLinux 9 | postgresql-server | over_provisioned | (도넛만) | `infra/lima/db-server-01.yaml` |

LIMA_VMS 단일 진실은 `dev-up.sh`. `dev-down.sh`도 `source dev-up.sh`로 가져옴 (BASH_SOURCE source guard).

### 실행

이하 모든 명령은 `assessment-engine/` 루트에서 실행한다.

```bash
# 환경변수 설정 (최초 1회)
cp .env.example .env                       # 엔진(web/consumer) 환경변수
cp infra/agent.env.example infra/agent.env # 에이전트(VM) 전용 secret 채널

# 전체 환경 기동 (Docker -> web 헬스체크 -> Lima VM 7대 순서)
./dev-up.sh
```

> 두 .env 파일 분리 이유: 엔진의 `.env`는 docker-compose로 web/consumer에 주입, `infra/agent.env`는 dev-up.sh가 source해 VM 안 `/etc/assessment-agent.env`로 옮긴다. 정책·근거: `docs/operations/dev-prod.md` #9 (에이전트 secret 채널 분리).

`dev-up.sh` 실행 순서:
1. `docker compose up --build -d`
2. migrate(alembic upgrade head) 완료 대기 (최대 180초)
3. web 헬스체크 통과 대기 (최대 180초)
4. `start_or_resume_vm` wrapper로 7 VM 순차 + 에이전트 빌드/설치/시작 (cloud image 다운로드 포함, 최초 1회는 5~15분)

각 VM별 `start_or_resume_vm` wrapper 동작:
- limactl start background 시작
- SSH ready check polling (3s 간격)
- SSH ready 후 60s+ 경과해도 lima final requirement(`boot scripts must finished`) 안 끝나면 PID kill 후 진행 (Oracle Linux 9 등 cloud-init 느린 distro 우회)
- 절대 cap 5분

각 VM 부팅 시 yaml provision이 자동 수행:
1. (선택) swap file 256 MiB 활성 (`app-server-01`: swap_trigger.service backing / `monitor-server-01`: dnf install OOM 회피 + swappiness=1)
2. (선택) 추가 서비스 timer (`web-server-01`: agent-restart-demo.timer 1m boot + 3m 주기, 시간당 20회)
3. 합성 부하 스크립트 + systemd timer (light/medium/swap_trigger — yaml별, offline은 없음)

이후 `dev-up.sh`가 limactl shell로 후처리:
4. `infra/agent.env` 값으로 `/etc/assessment-agent.env` 생성 (`RABBITMQ_HOST=host.lima.internal` + `AGENT_HOSTNAME_OVERRIDE=<vm>` + (web만) `AGENT_EXTERNAL_IP=203.0.113.10`)
5. OS dispatch (`apt`/`dnf`/`zypper`)로 빌드 의존성 + 서비스 패키지 설치
6. `/mnt/agent-src` (read-only mount) -> rsync -> `/tmp/build` -> `make`
7. `/usr/local/bin/assessment-agent` 설치 + systemd `assessment-agent.service` 등록·시작
8. `monitor-server-01` 전용 — `swapoff /swapfile && swapon /swapfile` (SwapUsed=0 reset, optimal 분류 보장)
9. `offline-server-01` 전용 — inventory 1회 발행 대기 15s → `systemctl stop` + `systemctl disable` + `limactl stop` (offline-once mode)

### 결과 확인

http://localhost:8000/servers/ 에서 서버 7대 온라인 확인.
60초 주기로 메트릭이 갱신되며 각 서버의 상세 페이지에서 CPU·메모리·디스크·네트워크 확인.

분류 분포 시연:
- 보고서 라우터 `/servers/report?period_days=1`로 짧은 윈도우 분류 확인 (대시보드는 `recommendation.WINDOW_DAYS=14` 고정 — `#F11`).
- attention 카드(상단 요약): web의 agent_unstable + offline의 gap_warnings (offline은 5m+ 후 발화).

### 환경 종료

`assessment-engine/` 루트에서 실행한다.

```bash
# 전체 환경 종료 (Lima VM 제거 -> Docker 볼륨 삭제, 데이터 초기화)
./dev-down.sh
```

`dev-down.sh` 실행 순서:
1. `source dev-up.sh`로 LIMA_VMS 가져옴 (BASH_SOURCE source guard로 main 자동 실행 안 함)
2. `limactl stop -f` + `limactl delete -f` (7 VM) — VM 제거 (각 VM의 `/etc/assessment-agent.env`·바이너리·systemd unit 모두 사라짐)
3. LIMA_VMS 외 명명 패턴 잔재(`(cache|app|web|db|legacy-mq|monitor|offline|container)-server-01`) 발견 시 알림만 (자동 삭제 안 함, 다른 프로젝트 인스턴스 보호)
4. `docker compose down -v` — 컨테이너 + `postgres_data` 볼륨 + 네트워크 제거 (DB 데이터 삭제)

#### 부분 종료 (선택)

| 시나리오 | 명령 |
|---------|------|
| Docker만 종료, VM은 유지 | `docker compose down` (데이터 유지) / `docker compose down -v` (데이터 삭제) |
| VM만 종료, Docker는 유지 | `for vm in $(./dev-down.sh와 동일 LIMA_VMS); do limactl delete -f $vm; done` 또는 일시 정지 `limactl stop <vm>` |
| 특정 VM만 종료 | `limactl delete -f web-server-01` |

VM `stop`(일시 정지) 후 `limactl start <vm>`은 yaml provision 다시 안 함 (재기동만). `delete` 후엔 다음 `./dev-up.sh`가 풀 provision (cloud image 캐시되어 있으면 1-3분).

### 트러블슈팅

상세는 `docs/operations/lima.md` "누적 사고 패턴" + "운영 노트 / 트러블슈팅" 절. 가장 흔한 케이스:

| 증상 | 해결 |
|------|------|
| broker 재기동 후 에이전트 publish 안 함 | 7 VM 모두 `limactl shell <vm> sudo systemctl restart assessment-agent` |
| OL9·기타 distro에서 limactl start 5분+ stuck | `start_or_resume_vm` wrapper가 자동 우회 (SSH ready+60s 후 PID kill) |
| `librabbitmq-devel` not found (RHEL family) | dev-up.sh dnf 분기가 EPEL + CRB/PowerTools 자동 활성화 — VM 삭제 후 재기동 |
