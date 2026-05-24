# 파이프라인 검증 (OrbStack)

본 문서는 dev 시연·파이프라인 검증 단일 진실. 운영자 절차·VM 매트릭스·OS 다양성·합성 부하·provisioning·운영 디버깅 모두 포함. macOS + OrbStack 한정 (CLAUDE.md #A0).

에이전트(C) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 실제 VM 환경에서 검증 + 시연용 분류·attention 분포 가시화.

```
HOST MACHINE (OrbStack)
  Docker engine (assessment-engine)
    FastAPI :8000  <----QUERY-----  PostgreSQL :5432
                                         ^
                                    PERSIST | (3)
                                         |
    RabbitMQ :5672 ---DISPATCH(2)---> Consumer
         ^
         | PUBLISH (1) Target: host.docker.internal
         |
  OrbStack VM x 4 (assessment-agent.service)
    web-server-01 · offline-server-01 · cache-server-01 · db-server-01
```

OrbStack 은 Docker 엔진과 Linux machines(VM) 를 한 런타임에서 통합 네트워크로 묶는다. 컨테이너·VM 모두 `host.docker.internal` 로 host 에 도달하고, VM 은 `<name>.orb.local` 도메인으로 host·컨테이너 어디서나 도달한다 (Lima user-mode 격리·localPort 포워딩 불필요).

## 사전 요구

| 도구 | 설치 |
|------|------|
| OrbStack | https://orbstack.dev (Docker 엔진 + Linux machines 통합 제공) |

전제: agent 바이너리 (`dev/bin/assessment-agent`) 는 pipeline-up.sh 의 `ensure_agent_binary` 단계가 자동 확보. 두 분기:
- `AGENT_BINARY_URL` env set 시 — curl fetch (향후 agent CI release artifact 자동화).
- 미설정 시 — `dev/agent-build/build.sh` 호출, sibling repo (`AGENT_REPO_PATH`, default `../assessment-agent`) 를 buildx context 로 cross-build.

상세: `dev/README.md`.

## 실행

```bash
cp dev/.env.example dev/.env               # 엔진 환경변수 (dev compose 한정)
cp dev/agent.env.example dev/agent.env     # 에이전트 secret 채널 (분리됨, #B)
./dev/pipeline-up.sh                   # Docker → web 헬스체크 → OrbStack VM 4대
```

`dev/pipeline-up.sh` 4단계:
1. `docker compose up --build -d` — 엔진 기동 (COMPOSE_FILE=dev/docker-compose.yml export)
2. `migrate(alembic upgrade head)` 완료 대기 (cap 180s)
3. web 헬스체크 통과 대기 (cap 180s)
4. `start_or_resume_vm` → `post_provision_vm` → `install_demo_loads` 로 4 VM 순차 (`orb create` 첫 실행 시 cloud image pull 포함)

## 결과 확인

- http://localhost:8000/servers/ — 4 VM 등록 (offline 은 5m+ 후 offline 표시)
- 60초 주기 메트릭 갱신
- 분류 분포 시연은 `/servers/report?period_days=1` (대시보드는 `recommendation.WINDOW_DAYS=14` 고정, #F10)
- attention 카드 상단 요약: web `agent_unstable` + offline `gap_warnings`(5m+ 후) + db `capacity_warnings` (swap_used)
- 서버 발견 모달 probe — `print_summary` 가 안내한 VM IP 를 모달에 직접 입력 (`.orb.local` 은 컨테이너 미해석 + VM IP 동적이라 자동 기본값 없음). VM 은 post-provision `openssh-server` 설치라 `SSH-2.0-OpenSSH` banner 로 도달

## 종료

```bash
./dev/pipeline-down.sh   # OrbStack VM 제거 → Docker 볼륨 삭제 (DB 초기화)
```

부분 종료:

| 시나리오 | 명령 |
|---------|------|
| Docker만 종료, VM 유지 | `docker compose down` (데이터 유지) / `docker compose down -v` (삭제) |
| 특정 VM만 종료 | `orb delete -f web-server-01` |
| VM 일시 정지 | `orb stop <vm>` (재기동 시 post-provision 재적용은 pipeline-up.sh 재실행) |

---

## 사용 맥락

OrbStack VM 은 에이전트 E2E + 시연 분류 분포 가시화. 엔진(dev compose)은 host Docker, VM 4대가 실제 Linux 환경에서 에이전트 metrics 를 RabbitMQ 에 발행 → Consumer DB 저장 → web UI 확인.

```
[VM: web-server-01      ]   attention.agent_unstable (3분 주기 restart, 시간당 20회)
[VM: offline-server-01  ]   attention.gap_warnings (5m+ 끊김) + insufficient_data
[VM: cache-server-01    ]   over (light 부하)                            -> RabbitMQ -> consumer -> DB -> web UI
[VM: db-server-01       ]   attention.capacity_warnings (swap_used → under_provisioned)
```

4 VM 구성 의도 (호스트 macOS 영향 최소화 우선):
- OS 다양성: 패키지 매니저 분기(apt/dnf) + systemd 호환 검증. 4 distro (Debian 12 · Debian 13 · Rocky 9 · AlmaLinux 9).
- attention 카탈로그 발화: `AttentionSignals` 카테고리 중 3개 (agent_unstable · gap_warnings · capacity_warnings) 의도 발화.
- 합성 부하: 모든 VM light (sustained CPU 1~3s + mem 5~20MB) — 차트 변동만 가시화. CPU 임계 안 넘김. under_provisioned 만 swap_used 트리거로 분류 발화 (CPU 부담 0).

OrbStack 채택 이유: Apple Silicon 네이티브 가상화로 부팅·메모리 가벼움, cloud-init 없이 `orb create` 즉시 ready (Lima 의 boot stuck 우회 로직 불필요), Docker 엔진·VM 통합 네트워크로 컨테이너·VM 양방향 직접 도달 (probe 포워딩 불필요).

---

## VM 매트릭스

VM 정의(distro/service/ext_ip/mode)는 `pipeline-up.sh` 의 dispatch 함수(`vm_distro`/`vm_service`/`vm_ext_ip`/`vm_mode`)가 단일 진실. `ORB_VMS` 배열 순서대로 기동(`pipeline-down.sh`는 `source pipeline-up.sh`로 가져옴). 별도 VM yaml 없음 — `orb create <distro> <name>` 후 post-provision 이 패키지·합성 부하·systemd 를 모두 설치.

| 진행 순서 | VM | distro (orb create) | family | 서비스 | 뱃지 | 부하 | 분류 | attention 발화 |
|---|----|----|--------|--------|------|------|------|----------------|
| 1 | `web-server-01` | `debian:12` | apt | nginx | web | light | over_provisioned | agent_unstable (1m boot + 3m 주기, 시간당 20회) |
| 2 | `offline-server-01` | `debian:13` | apt | (없음) | unknown | (offline-once) | insufficient_data | gap_warnings (5m+ 끊김) |
| 3 | `cache-server-01` | `rocky:9` | dnf | redis | cache | light | over_provisioned | (분류 도넛에서만) |
| 4 | `db-server-01` | `alma:9` | dnf | postgresql-server | db | light + swap-trigger | under_provisioned | capacity_warnings (swap_used) |

진행 순서는 시연 가시화 우선:
- 1번 web — attention 가장 빠른 발화 (1m 후 첫 restart)
- 2번 offline — gap_warnings 5m+ 발화 위해 가장 빨리 stop
- 3번 cache — light 부하 (over_provisioned)
- 4번 db — swap-trigger + RPM postgresql-setup --initdb 자동 (capacity_warnings · under_provisioned)

뱃지 분배 (`service_classifier.py` 카탈로그 부분 커버 — 4 VM 축소로 container · monitor · mq 카테고리 시연은 빠짐):

| 카테고리 | VM | 발화 키워드 |
|----------|----|----|
| web | web-server-01 | nginx |
| cache | cache-server-01 | redis |
| db | db-server-01 | postgresql |
| unknown | offline-server-01 | (서비스 없음) |

리소스 메모: OrbStack VM 은 host CPU·메모리를 공유(Lima 처럼 VM별 cpu/mem/disk 고정 할당 없음) — distro 만 지정. dnf family(Rocky·Alma)는 install transaction 이 apt 보다 무거우나 OrbStack 의 동적 메모리로 OOM 회피. db-server-01 의 swap-trigger 는 boot 1회 swapfile 512 MiB 활성 + 1000 MiB 메모리 압박 → swap_used > 0 영구 유지.

---

## 합성 부하 프로파일 (right-sizing 분류 발화)

`recommendation.py`의 USE Method 임계 — 호스트 macOS 영향 최소화 위해 모든 VM 을 light 로 통일. CPU 임계는 일부러 안 넘김 (CPU 부담 회피), swap_used 만 db 에서 트리거. 합성 부하 스크립트·timer 는 `pipeline-up.sh` 의 `install_synthetic_load`/`install_swap_trigger`/`install_agent_restart_demo` 가 post-provision 으로 설치 (옛 Lima yaml provision 흡수).

| 프로파일 | cpu burst | mem burst | 적용 VM | 목표 분류 |
|----------|-----------|-----------|---------|----------|
| light | 1~3s | 5~20MB | web, cache, db | over_provisioned (cpu_p95 ~5%, mem_p95 <50%) |
| swap-trigger (추가) | (boot 1회 1000MB 메모리 압박 + swapfile 512MB 활성) | swap_used > 0 영구 | db | under_provisioned (swap_used short-circuit) |
| (offline-once) | — | — | offline | insufficient_data (1회 발행 후 stop) |

원칙:
- 분류 임계는 `recommendation.py` 모듈 상단 명명 상수 (#E3). 부하 프로파일은 임계 충족 설계.
- `WINDOW_DAYS = 14` (#F10) — dev 시연에서 14일 못 채우면 분류 모두 `insufficient_data`. 보고서 라우터 `?period_days=1` 등 짧은 윈도우 시연 필수.
- swap-trigger 프로파일 — boot 직후 swapfile 512 MiB 활성 + `vm.swappiness=100` + 1000 MiB 메모리 압박. CPU 부담 0, 한 번 swap 에 page push 되면 SwapUsed > 0 영구 유지 → 매 measurement 에서 swap_used = True 안정 발화.
- light 부하 스크립트는 `host.docker.internal:8000` 으로 ping/curl (health·chart-utils) — 차트 변동만 가시화. 분류 임계 안 넘김 (over_provisioned 유지).

attention 카탈로그 발화 매핑:

| attention 카테고리 | 발화 VM | 트리거 |
|-------------------|---------|--------|
| disk_warnings | (없음) | 디스크 사용률 85%+ — 시연 안 함 |
| gap_warnings | offline-server-01 | offline-once mode (5m+ 끊김) |
| capacity_warnings | db-server-01 | under_provisioned (swap_used 트리거) |
| days_until_full_warnings | (없음) | 디스크 fill_rate 추정 30일 — 시연 안 함 |
| os_eol_warnings | (없음) | EOL OS 자체가 cloud image 가용성 한계라 발화 안 함 |
| agent_unstable | web-server-01 | agent-restart-demo timer (1h 슬라이딩 임계 3회 이상 — 3m 주기로 6배 마진) |

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

`orb create <distro> <name>` 로 VM 생성 후, `pipeline-up.sh` 의 `post_provision_vm` + `install_demo_loads` 가 `ssh <name>@orb sudo bash` 로 후처리. 옛 Lima yaml `provision` 섹션(합성 부하·swap·restart-demo)을 모두 흡수.

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
AGENT_EXTERNAL_IP=203.0.113.10   # web-server-01만
```

`/etc/`에 두는 이유: systemd 자유 read + VM 로컬 격리. SELinux/AppArmor가 사용자 홈 내부 파일을 `EnvironmentFile=`로 읽는 것 차단 가능.

### 2. OS detect + 서비스 패키지 설치

agent 바이너리는 `ensure_agent_binary` 단계가 `dev/bin/assessment-agent` 로 자동 확보. VM 안에서는 서비스 패키지만 install — devel·gcc·make 불필요 (runtime OpenSSL/glibc/zlib 만 동적 의존이고 base distro 기본 포함).

`/etc/os-release`의 `ID` dispatch:

| family | 명령 |
|--------|------|
| Debian (apt) | `apt-get install -y --no-install-recommends curl iputils-ping openssh-server ${svc_pkg}` + `systemctl enable --now ssh` |
| Rocky 9 / AlmaLinux 9 (dnf) | `dnf install -y epel-release` → `dnf install -y curl iputils openssh-server ${svc_pkg}` + `systemctl enable --now sshd` |

`openssh-server` 는 서버 발견 probe(web 컨테이너 -> VM IP:22) 시연용 — OrbStack VM 은 표준 22 sshd 미탑재(OrbStack SSH 는 host-network proxy)라 명시 설치. apt 는 `ssh`, dnf 는 `sshd` unit.

서비스 dispatch (4 VM):

| VM | service | apt 패키지 | dnf 패키지 | systemd 유닛 |
|----|---------|-----------|-----------|-------------|
| web-server-01 | `nginx` | `nginx` | — | `nginx` |
| offline-server-01 | `none` | (없음) | — | (없음) |
| cache-server-01 | `redis` | — | `redis` | `redis` |
| db-server-01 | `postgres` | — | `postgresql-server` (AlmaLinux 9 — `postgresql-setup --initdb` 자동) | `postgresql` |

dispatch 단일 진실은 `pipeline-up.sh`의 `case "${ID}:$service"` 블록. 새 service 도입 시 본 표 + pipeline-up.sh 동시 갱신 의무. RPM family postgresql 은 cluster init 수동 — `postgresql-setup --initdb` 자동 처리. apt 계열은 install 시 자동 init 라 skip.

### 3. 바이너리 + systemd unit

ssh stdin 으로 받은 `/tmp/assessment-agent` 를 `cmp` 후 `/usr/local/bin/` 설치 + `assessment-agent.service` unit 작성. `User=root` (에이전트는 `/proc/*` read 만 필요, VM 간 일관성 위해 root 통일). binary·env·unit 변경 있을 때만 restart — `agent_started_at` 갱신 회피 (attention false positive 줄임).

### 4. 합성 부하·시연 트리거 (`install_demo_loads`)

`vm_mode` 가 `offline-once` 면 skip (publish 안 하므로 부하 무의미). persistent VM:
- `install_synthetic_load` — 공통 (web/cache/db). light 부하 timer (`OnBootSec=2min`, `OnUnitActiveSec=1min`).
- `db-server-01` — `install_swap_trigger` (boot 1회 swapfile 512 MiB + `vm.swappiness=100` + 1000 MiB 메모리 압박). attention.capacity_warnings · under_provisioned.
- `web-server-01` — `install_agent_restart_demo` (`OnBootSec=1min`, `OnUnitActiveSec=3min` — 시간당 20회 attention.agent_unstable).

### 5. finalize_vm (조건 분기)

`vm_mode` dispatch:
- `offline-server-01` → `offline-once`: inventory 1회 발행 대기 15s → `systemctl stop/disable assessment-agent` + `orb stop`. 5분 후 attention.gap_warnings 발화 (시연 의도).
- 그 외 → `persistent`: agent restart로 publish 계속.

---

## 운영 노트 / 트러블슈팅

### broker 재기동 시 에이전트 수동 재시작 (CRITICAL)

증상: docker compose RabbitMQ를 down/up 또는 `down -v` 후 재기동하면 VM 안 C 에이전트가 broker 재연결 silent 포기. systemd 상태는 `active(running)`이지만 publish 로그 끊김.

대응:
```bash
for vm in web-server-01 offline-server-01 cache-server-01 db-server-01; do
  ssh "$vm@orb" sudo systemctl restart assessment-agent
done
```

원인: C 에이전트 publish 루프에 `connect_robust` 자동 재연결 없음. exit하지 않고 silent retry만 하므로 systemd `Restart=on-failure`도 트리거 안 됨.

### VM 시간 동기화

`collected_at`은 VM 로컬 시각. 호스트와 어긋나면 차트 시간축 안 맞음. OrbStack VM 은 host 시각 동기화이지만 장시간 절전·suspend 후 재개 시 어긋날 수 있음.

```bash
for vm in web-server-01 offline-server-01 cache-server-01 db-server-01; do
  ssh "$vm@orb" sudo bash -c 'systemctl restart systemd-timesyncd 2>/dev/null || systemctl restart chronyd'
done
```

### 에이전트 로그 확인

```bash
ssh web-server-01@orb sudo journalctl -u assessment-agent --no-pager -n 50
```

이후 60초 주기 publish 로그가 추가돼야 정상. 멈춰 있으면 broker 재연결 실패 의심.

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| `orb create` cloud image pull 실패 | 네트워크 / mirror 일시 오류 | 재시도 |
| `ensure_agent_binary` OpenSSL configure 실패 (`-m64` unrecognized) | sibling agent repo Makefile 의 OpenSSL Configure 가 host arch 분기 누락 | agent repo Makefile 의 Configure target 을 host arch 분기로 수정 |
| 에이전트 publish 실패 로그 (CONNREFUSED) | host docker rabbitmq 안 떠 있음 / host.docker.internal 해석 실패 | `docker compose ps rabbitmq` + `ssh <vm>@orb getent hosts host.docker.internal` 확인 |
| consumer가 metrics 받지만 server_inventory 비어 있음 | inventory 메시지 유실 (broker 재기동 등) | VM 안 `systemctl restart assessment-agent` |
| 서버 발견 probe unreachable | agent VM 미기동 / `<name>.orb.local` 해석 실패 | `orb list` + `ssh <vm>@orb echo ok` 확인 |

### 개별 VM 조작

```bash
orb create debian:12 web-server-01                  # 단일 VM 생성
ssh web-server-01@orb                               # SSH 접속
ssh web-server-01@orb sudo <cmd>                    # root 명령
orb stop web-server-01                              # 정지 (제거 X)
orb delete -f web-server-01                         # 제거
orb list                                            # VM 상태 표
```

단일 VM 시나리오는 `ORB_VMS_FILTER=web-server-01 ./dev/pipeline-up.sh` 로 약식 검증.
