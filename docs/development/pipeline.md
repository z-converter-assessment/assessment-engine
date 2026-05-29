# 파이프라인 검증 (OrbStack + UTM)

본 문서는 dev 시연·파이프라인 검증 단일 진실. 운영자 절차·VM 매트릭스·OS 다양성·합성 부하·provisioning·운영 디버깅 모두 포함. macOS + Apple Silicon 한정 (CLAUDE.md #A0).

4 VM 매트릭스 — Linux 3대는 OrbStack, Windows 1대는 UTM (OrbStack Windows 미지원). 본 문서는
Linux 3대(OrbStack) 단일 진실, Windows 1대(UTM)는 `docs/development/windows-vm.md` 단일 진실.

에이전트(C) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 실제 VM 환경에서 검증 + 시연용 분류·attention 분포 가시화. 1 VM = 2 서비스로 `service_classifier` 6 카테고리 최대 커버.

```
HOST MACHINE (macOS, Apple Silicon)
  Docker engine (assessment-engine) — via OrbStack
    FastAPI :8000  <----QUERY-----  PostgreSQL :5432
                                         ^
                                    PERSIST | (3)
                                         |
    RabbitMQ :5672 ---DISPATCH(2)---> Consumer
         ^   ^
         |   | PUBLISH (1, Windows) Target: <host IP> (UTM)
         |   |
         |   +---  UTM VM x 1: win-server-01 (Win11 ARM) -> windows-vm.md
         |
         | PUBLISH (1, Linux) Target: host.docker.internal
         |
  OrbStack VM x 3 (assessment-agent.service)
    app-server-01, data-server-01, edge-server-01
```

OrbStack 은 Docker 엔진과 Linux machines(VM) 를 한 런타임에서 통합 네트워크로 묶는다. 컨테이너·VM 모두 `host.docker.internal` 로 host 에 도달하고, VM 은 `<name>.orb.local` 도메인으로 host·컨테이너 어디서나 도달한다. UTM Windows VM 은 이 통합 네트워크 밖이라 host 의 실제 IP 로 도달 (`windows-vm.md` 네트워크 절).

LLM 서버(ollama)는 본 파이프라인에서 제거됨 — AI 진단(engineer 보고서 narrative) 발행 시 LLM 호출
실패 시나리오를 의도적으로 재현 (`dev/docker-compose.yml` diagnostic-worker 주석). 진단 워커 로직 무수정.

## 사전 요구

| 도구 | 설치 | 용도 |
|------|------|------|
| OrbStack | https://orbstack.dev | Docker 엔진 + Linux VM 3대 |
| UTM | `brew install --cask utm` | Windows VM 1대 (`windows-vm.md`) |

전제: agent 바이너리 (`dev/bin/assessment-agent`) 는 pipeline-up.sh 의 `ensure_agent_binary` 단계가 자동 확보 (Linux arm64). Windows agent .exe 는 별도 (`windows-vm.md`). 두 분기:
- `AGENT_BINARY_URL` env set 시 — curl fetch (향후 agent CI release artifact 자동화).
- 미설정 시 — `dev/agent-build/build.sh` 호출, sibling repo (`AGENT_REPO_PATH`, default `../assessment-agent`) 를 buildx context 로 cross-build.

상세: `dev/README.md`.

## 실행

```bash
cp dev/.env.example dev/.env               # 엔진 환경변수 (dev compose 한정)
cp dev/agent.env.example dev/agent.env     # 에이전트 secret 채널 (분리됨, #B)
./dev/pipeline-up.sh                   # Docker → web 헬스체크 → OrbStack Linux VM 3대
```

`dev/pipeline-up.sh` 4단계 (Linux/OrbStack):
1. `docker compose up --build -d` — 엔진 기동 (COMPOSE_FILE=dev/docker-compose.yml export)
2. `migrate(alembic upgrade head)` 완료 대기 (cap 180s)
3. web 헬스체크 통과 대기 (cap 180s)
4. `start_or_resume_vm` → `post_provision_vm` → `install_demo_loads` 로 3 VM 순차 (`orb create` 첫 실행 시 cloud image pull 포함)

Windows VM (win-server-01)은 `windows-vm.md` 절차로 별도 — UTM VM 생성·Windows 설치가 GUI 수동이라 스크립트 통합 안 함.

## 결과 확인

- http://localhost:8000/servers/ — Linux 3 VM (+ Windows 설정 시 4) 등록
- 60초 주기 메트릭 갱신
- 분류 분포 시연은 `/servers/report?period_days=1` (대시보드는 `recommendation.WINDOW_DAYS=14` 고정, #F10)
- attention 카드 상단 요약: app `agent_unstable` + edge `gap_warnings` (3회 발행 후 down)
- AI 진단 발행 (engineer 보고서) — LLM 호출 실패 (ollama 제거) 동작 관찰
- 서버 발견 모달 probe — `print_summary` 가 안내한 VM IP 를 모달에 직접 입력 (`.orb.local` 은 컨테이너 미해석 + VM IP 동적이라 자동 기본값 없음). VM 은 post-provision `openssh-server` 설치라 `SSH-2.0-OpenSSH` banner 로 도달

## 종료

```bash
./dev/pipeline-down.sh   # OrbStack VM 제거 → Docker 볼륨 삭제 (DB 초기화)
```

부분 종료:

| 시나리오 | 명령 |
|---------|------|
| Docker만 종료, VM 유지 | `docker compose down` (데이터 유지) / `docker compose down -v` (삭제) |
| 특정 Linux VM만 종료 | `orb delete -f app-server-01` |
| Linux VM 일시 정지 | `orb stop <vm>` (재기동 시 post-provision 재적용은 pipeline-up.sh 재실행) |
| Windows VM 종료 | `utmctl stop win-server-01` (`windows-vm.md`) |

`pipeline-down.sh` 는 OrbStack Linux 3대 + Docker 볼륨만 정리 — Windows(UTM)는 `utmctl stop/delete` 수동.

---

## 사용 맥락

VM 은 에이전트 E2E + 시연 분류 분포 가시화. 엔진(dev compose)은 host Docker, VM 들이 실제 OS 환경에서 에이전트 metrics 를 RabbitMQ 에 발행 → Consumer DB 저장 → web UI 확인.

```
[VM: app-server-01  Linux ]   nginx+rabbitmq  attention.agent_unstable (3m restart, 시간당 20회)
[VM: data-server-01 Linux ]   postgres+zabbix-agent  (db+monitor, over_provisioned)  -> MQ -> consumer -> DB -> web UI
[VM: edge-server-01 Linux ]   docker+memcached  attention.gap_warnings (3회 발행 후 down)
[VM: win-server-01  Win11 ]   IIS+redis  (windows-vm.md, UTM)
```

4 VM 구성 의도 (호스트 macOS 영향 최소화 우선):
- 1 VM = 2 서비스: `service_classifier` 6 카테고리(web/db/cache/mq/container/monitor) 전부 커버.
- OS 다양성: 패키지 매니저 분기(apt/dnf) + systemd + Windows SCM. distro (Debian 12, Rocky 9, Win11 ARM).
- attention 카탈로그 발화: `AttentionSignals` 카테고리 중 2개 (agent_unstable, gap_warnings) 의도 발화.
- LLM 실패: ollama 제거 → AI 진단 발행 시 호출 실패 (진단 워커 로직 무수정).
- 합성 부하: 모든 VM light (sustained CPU 1~3s + mem 5~20MB) — 차트 변동만 가시화. CPU 임계 안 넘김 → over_provisioned 분류 (CPU 부담 0).

OrbStack 채택 이유 (Linux): Apple Silicon 네이티브 가상화로 부팅·메모리 가벼움, cloud-init 없이 `orb create` 즉시 ready (Lima 의 boot stuck 우회 로직 불필요), Docker 엔진·VM 통합 네트워크로 컨테이너·VM 양방향 직접 도달 (probe 포워딩 불필요). Windows 는 OrbStack 미지원이라 UTM (`windows-vm.md`).

---

## VM 매트릭스

Linux VM 정의(distro/service/ext_ip)는 `pipeline-up.sh` 의 dispatch 함수(`vm_distro`/`vm_service`/`vm_ext_ip`)가 단일 진실. `vm_service` 는 공백 구분 다중 서비스 반환 — `post_provision_vm` 이 for-loop 으로 각각 설치. `ORB_VMS` 배열 순서대로 기동(`pipeline-down.sh`는 `source pipeline-up.sh`로 가져옴). 별도 VM yaml 없음 — `orb create <distro> <name>` 후 post-provision 이 패키지·합성 부하·systemd 를 모두 설치. Windows(win-server-01)는 `windows-vm.md`.

| 순서 | VM | 가상화 | distro | family | 서비스 (2) | 카테고리 | 부하 | 분류 | attention 발화 |
|---|----|----|----|--------|--------|------|------|------|----------------|
| 1 | `app-server-01` | OrbStack | `debian:12` | apt | nginx, rabbitmq | web, mq | light | over | agent_unstable (1m boot + 3m, 시간당 20회) |
| 2 | `data-server-01` | OrbStack | `rocky:9` | dnf | postgresql, zabbix-agent | db, monitor | light | over | (분류만, 운영신호 없음) |
| 3 | `edge-server-01` | OrbStack | `debian:12` | apt | docker, memcached | container, cache | light | over | gap_warnings (3회 발행 후 poweroff) |
| 4 | `win-server-01` | UTM | Win11 ARM | SCM | IIS, redis | web, cache | (windows-vm.md) | — | — |

진행 순서는 시연 가시화 우선:
- 1번 app — attention 가장 빠른 발화 (1m 후 첫 restart) + external IP (web-facing)
- 2번 data — postgresql(db) + zabbix-agent(monitor)
- 3번 edge — offline-demo (약 180s 후 poweroff → gap_warnings). docker(container) + memcached(cache)
- 4번 win — UTM 별도 (`windows-vm.md`). IIS(native)+redis(크로스플랫폼) 혼합

뱃지 분배 (`service_classifier.py` 6 카테고리 전부 커버):

| 카테고리 | VM | 발화 키워드 |
|----------|----|----|
| web | app-server-01, win-server-01 | nginx, IIS(w3svc) |
| mq | app-server-01 | rabbitmq |
| db | data-server-01 | postgresql |
| monitor | data-server-01 | zabbix-agent |
| container | edge-server-01 | docker |
| cache | edge-server-01, win-server-01 | memcached, redis |

리소스 메모: OrbStack VM 은 host CPU·메모리를 공유(Lima 처럼 VM별 cpu/mem/disk 고정 할당 없음) — distro 만 지정. dnf family(Rocky)는 install transaction 이 apt 보다 무거우나 OrbStack 의 동적 메모리로 OOM 회피 (`install_weak_deps=False` 로 추가 절약).

---

## 합성 부하 프로파일 (right-sizing 분류 발화)

`recommendation.py`의 USE Method 임계 — 호스트 macOS 영향 최소화 위해 모든 VM 을 light 로 통일. CPU 임계는 일부러 안 넘김 (CPU 부담 회피) → 모든 VM over_provisioned 분류. 합성 부하 스크립트·timer 는 `dev-up.sh` 의 `install_synthetic_load`/`install_agent_restart_demo`/`install_offline_demo` 가 post-provision 으로 설치 (옛 Lima yaml provision 흡수). (under_provisioned 시연은 폐기 — swap-trigger 가 OrbStack 동적 메모리에서 swap-out 을 안정적으로 못 일으켜 제거.)

| 프로파일 | cpu burst | mem burst | 적용 VM | 목표 분류 |
|----------|-----------|-----------|---------|----------|
| light | 1~3s | 5~20MB | app, data, edge | over_provisioned (cpu_p95 ~5%, mem_p95 <50%) |
| offline-demo (추가) | (약 180s 후 `systemctl poweroff`) | — | edge | gap_warnings (발행 중단) |

원칙:
- 분류 임계는 `recommendation.py` 모듈 상단 명명 상수 (#E3). 부하 프로파일은 임계 충족 설계.
- `WINDOW_DAYS = 14` (#F10) — dev 시연에서 14일 못 채우면 분류 모두 `insufficient_data`. 보고서 라우터 `?period_days=1` 등 짧은 윈도우 시연 필수.
- light 부하 스크립트는 `host.docker.internal:8000` 으로 ping/curl (health·chart-utils) — 차트 변동만 가시화. 분류 임계 안 넘김 (over_provisioned 유지).

attention 카탈로그 발화 매핑:

| attention 카테고리 | 발화 VM | 트리거 |
|-------------------|---------|--------|
| disk_warnings | (없음) | 디스크 사용률 85%+ — 시연 안 함 |
| gap_warnings | edge-server-01 | offline-demo (약 180s 후 poweroff → 발행 중단, engine 이 일정 시간 미수신 시 발화) |
| capacity_warnings | (없음) | under_provisioned 시연 폐기 (swap-trigger 제거) |
| days_until_full_warnings | (없음) | 디스크 fill_rate 추정 30일 — 시연 안 함 |
| os_eol_warnings | (없음) | EOL OS 자체가 cloud image 가용성 한계라 발화 안 함 |
| agent_unstable | app-server-01 | agent-restart-demo timer (1h 슬라이딩 임계 3회 이상 — 3m 주기로 6배 마진) |

---

## VM 생성 (`start_or_resume_vm`)

`orb create <distro> <name>` 는 동기 — 완료 후 반환하며 cloud-init 단계가 없어 즉시 SSH ready (Lima 의 "boot scripts must finished" stuck 우회 PID kill 로직 불필요).

```
orb list 에 있으면      → orb start <vm> (멱등)
없으면                 → orb create <distro> <vm>
이후 ssh <vm>@orb echo ok 로 SSH 도달 검증 (cap 60s)
```

`source pipeline-up.sh` 가드 (BASH_SOURCE):
```bash
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
```
`pipeline-down.sh`가 `ORB_VMS` 만 가져올 때 main 자동 실행 안 함. macOS bash 3.2 호환(`:-` empty default).

---

## 네트워크 구조

OrbStack 통합 네트워크 — 컨테이너·VM 모두 동일 도메인 평면.

```
VM (assessment-agent)
  RABBITMQ_HOST=host.docker.internal  ->  host:5672 (docker-compose rabbitmq 포트 매핑)

web container (discovery probe)
  <VM IP>:22  (operator-entered)      ->  agent VM sshd (OpenSSH)
```

- VM·컨테이너 → host: `host.docker.internal` (OrbStack 이 양쪽에서 자동 해석 — Lima 의 user-mode `host.lima.internal` 대체). 엔진 `.env` 의 `RABBITMQ_HOST`(=`rabbitmq` 도커 서비스명)와 다르며, `pipeline-up.sh`가 VM별 `/etc/assessment-agent.env` 를 생성할 때 명시.
- host → VM: `<name>.orb.local` (host DNS 등록). 단 docker 컨테이너 안에선 `.orb.local` 미해석 — web 컨테이너 probe 는 VM IP(동적, `print_summary` 안내)를 운영자가 모달 입력. VM 22 는 post-provision `openssh-server` 라 listen (OrbStack 기본 sshd 미탑재). Lima SSH localPort 포워딩은 불필요.
- VM 간 통신 사용 안 함 — 각 VM 독립, 모든 통신은 host RabbitMQ 경유.

---

## 바이너리 전달

OrbStack 은 host 파일시스템을 VM 에 자동 마운트하지만, `pipeline-up.sh` 는 마운트 경로·권한 가정을 피하려 바이너리를 ssh stdin 으로 전송한다:

```bash
orb_ssh "$vm" 'cat > /tmp/assessment-agent' < dev/bin/assessment-agent
# post-provision SCRIPT 안에서 cmp 후 install -m 755 /tmp/assessment-agent /usr/local/bin/
```

`/usr/local/bin/` 으로 복사 — 표준 실행 경로. `cmp` 멱등 — 바이너리·env·unit 변경 없으면 restart 건너뜀 (attention.agent_unstable false positive 회피).

---

## Provisioning 단계

`orb create <distro> <name>` 로 VM 생성 후, `dev-up.sh` 의 `post_provision_vm` + `install_demo_loads` 가 `ssh <name>@orb sudo bash` 로 후처리. 옛 Lima yaml `provision` 섹션(합성 부하·restart-demo)을 모두 흡수.

### 1. `/etc/assessment-agent.env` 생성

`pipeline-up.sh`가 `dev/agent.env` source한 host env로 heredoc 치환:

```
RABBITMQ_HOST=host.docker.internal
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/assessment
RABBITMQ_USER=...
RABBITMQ_PASS=...
RABBITMQ_EXCHANGE=...
RABBITMQ_ROUTING_KEY_INVENTORY=server.inventory
RABBITMQ_ROUTING_KEY_METRICS=server.metrics
RABBITMQ_ROUTING_KEY_ERROR=server.error
RABBITMQ_WORKER_USER=...
RABBITMQ_WORKER_PASS=...
WORKER_TASK_EXCHANGE=assessment.tasks
WORKER_TASK_QUEUE_PREFIX=agent.tasks
WORKER_TASK_RESULT_KEY=task.result
WORKER_DOWNLOAD_ALLOWED_HOSTS=host.docker.internal
AGENT_HOSTNAME_OVERRIDE=<vm>     # 모든 VM에 vm 이름 그대로
AGENT_INTERVAL_SEC=60
AGENT_EXTERNAL_IP=203.0.113.10   # app-server-01만
```

`/etc/`에 두는 이유: systemd 자유 read + VM 로컬 격리. SELinux/AppArmor가 사용자 홈 내부 파일을 `EnvironmentFile=`로 읽는 것 차단 가능.

### 2. OS detect + 서비스 패키지 설치 (다중 서비스)

agent 바이너리는 `ensure_agent_binary` 단계가 `dev/bin/assessment-agent` 로 자동 확보. VM 안에서는 서비스 패키지만 install — devel·gcc·make 불필요 (runtime OpenSSL/glibc/zlib 만 동적 의존이고 base distro 기본 포함).

`/etc/os-release`의 `ID` 로 base 패키지 (openssh-server + curl) 설치 후, `vm_service` 가 반환한 공백 구분 다중 서비스를 for-loop 으로 각각 설치:

| family | base 패키지 명령 |
|--------|------|
| Debian (apt) | `apt-get install -y --no-install-recommends curl iputils-ping openssh-server` + `systemctl enable --now ssh` |
| Rocky 9 (dnf) | `dnf install -y --setopt=install_weak_deps=False epel-release` → `dnf install -y ... curl iputils openssh-server` + `systemctl enable --now sshd` |

`openssh-server` 는 서버 발견 probe(web 컨테이너 -> VM IP:22) 시연용 — OrbStack VM 은 표준 22 sshd 미탑재(OrbStack SSH 는 host-network proxy)라 명시 설치. apt 는 `ssh`, dnf 는 `sshd` unit.

서비스별 패키지·유닛 dispatch (`case "${ID}:${svc}"` 단일 진실, `pipeline-up.sh` `post_provision_vm` for-loop):

| service | 카테고리 | apt (debian) pkg / unit | dnf (rocky) pkg / unit | 특이 |
|---------|----------|------------------------|------------------------|------|
| nginx | web | `nginx` / `nginx` | `nginx` / `nginx` | |
| rabbitmq | mq | `rabbitmq-server` / `rabbitmq-server` | `rabbitmq-server`(EPEL) / `rabbitmq-server` | |
| postgres | db | `postgresql` / `postgresql` | `postgresql-server` / `postgresql` | RPM 은 `postgresql-setup --initdb` 자동 |
| zabbix | monitor | `zabbix-agent` / `zabbix-agent` | `zabbix-agent`(EPEL) / `zabbix-agent` | EPEL 9 에 node_exporter 부재 → zabbix 채택. server 미설정이라 active 실패 가능(분류엔 무관) |
| redis | cache | `redis-server` / `redis-server` | `redis` / `redis` | |
| memcached | cache | `memcached` / `memcached` | `memcached` / `memcached` | |
| docker | container | `docker.io` / `docker` | (docker-ce repo 필요, 현재 미사용) | unix socket — listen port 없음, SCM unit 으로 분류 |

dispatch 단일 진실은 `pipeline-up.sh`의 `case "${ID}:${svc}"` 블록. 새 service 도입 시 본 표 + pipeline-up.sh + `service_classifier._PATTERNS`(#E7) 동시 갱신 의무. docker 는 edge-server-01 을 debian 으로 배치(apt `docker.io` 단순)해 dnf docker-ce repo 경로 회피.

### 3. 바이너리 + systemd unit

ssh stdin 으로 받은 `/tmp/assessment-agent` 를 `cmp` 후 `/usr/local/bin/` 설치 + `assessment-agent.service` unit 작성. `User=root` (에이전트는 `/proc/*` read 만 필요, VM 간 일관성 위해 root 통일). binary·env·unit 변경 있을 때만 restart — `agent_started_at` 갱신 회피 (attention false positive 줄임).

### 4. 합성 부하·시연 트리거 (`install_demo_loads`)

모든 Linux VM(app/data/edge) 공통:
- `install_synthetic_load` — 공통. light 부하 timer (`OnBootSec=2min`, `OnUnitActiveSec=1min`). 모든 VM over_provisioned 분류.
- `app-server-01` — `install_agent_restart_demo` (`OnBootSec=1min`, `OnUnitActiveSec=3min` — 시간당 20회 attention.agent_unstable).
- `edge-server-01` — `install_offline_demo` (`OnBootSec=${OFFLINE_DOWN_AFTER_SEC:-180}` 후 `systemctl poweroff`). agent 약 3회 발행 후 VM 정지 → attention.gap_warnings. 재기동은 `orb start edge-server-01` (dev-up.sh 재실행 시 멱등 재적용).
- `data-server-01` — 추가 트리거 없음 (db+monitor, over_provisioned). under_provisioned 시연 폐기.

---

## 운영 노트 / 트러블슈팅

### broker 재기동 시 에이전트 수동 재시작 (CRITICAL)

증상: docker compose RabbitMQ를 down/up 또는 `down -v` 후 재기동하면 VM 안 C 에이전트가 broker 재연결 silent 포기. systemd 상태는 `active(running)`이지만 publish 로그 끊김.

대응:
```bash
for vm in app-server-01 data-server-01 edge-server-01; do
  ssh "$vm@orb" sudo systemctl restart assessment-agent
done
```

원인: C 에이전트 publish 루프에 `connect_robust` 자동 재연결 없음. exit하지 않고 silent retry만 하므로 systemd `Restart=on-failure`도 트리거 안 됨.

### VM 시간 동기화

`collected_at`은 VM 로컬 시각. 호스트와 어긋나면 차트 시간축 안 맞음. OrbStack VM 은 host 시각 동기화이지만 장시간 절전·suspend 후 재개 시 어긋날 수 있음.

```bash
for vm in app-server-01 data-server-01 edge-server-01; do
  ssh "$vm@orb" sudo bash -c 'systemctl restart systemd-timesyncd 2>/dev/null || systemctl restart chronyd'
done
```

### 에이전트 로그 확인

```bash
ssh app-server-01@orb sudo journalctl -u assessment-agent --no-pager -n 50
```

이후 60초 주기 publish 로그가 추가돼야 정상. 멈춰 있으면 broker 재연결 실패 의심.

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| `orb create` cloud image pull 실패 | 네트워크 / mirror 일시 오류 | 재시도 |
| `ensure_agent_binary` OpenSSL configure 실패 (`-m64` unrecognized) | agent repo Makefile 의 OpenSSL Configure 가 arch 하드코딩 (해결됨 — `./config` arch 자동 감지로 linux-aarch64 선택) | agent repo 최신 Makefile (openssl `./config`) 사용 |
| 에이전트 publish 실패 로그 (CONNREFUSED) | host docker rabbitmq 안 떠 있음 / host.docker.internal 해석 실패 | `docker compose ps rabbitmq` + `ssh <vm>@orb getent hosts host.docker.internal` 확인 |
| consumer가 metrics 받지만 server_inventory 비어 있음 | inventory 메시지 유실 (broker 재기동 등) | VM 안 `systemctl restart assessment-agent` |
| 서버 발견 probe unreachable | agent VM 미기동 / `<name>.orb.local` 해석 실패 | `orb list` + `ssh <vm>@orb echo ok` 확인 |

### 개별 VM 조작

```bash
orb create debian:12 app-server-01                  # 단일 VM 생성
ssh app-server-01@orb                               # SSH 접속
ssh app-server-01@orb sudo <cmd>                    # root 명령
orb stop app-server-01                              # 정지 (제거 X)
orb delete -f app-server-01                         # 제거
orb list                                            # VM 상태 표
```

단일 VM 시나리오는 `ORB_VMS_FILTER=app-server-01 ./dev/pipeline-up.sh` 로 약식 검증.
