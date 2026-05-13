# Lima

## 사용 맥락

Lima는 에이전트 E2E 테스트 + 시연용 분류 분포 가시화 환경. 엔진(docker-compose)은 호스트에서 실행되고, Lima가 7 VM을 띄워 실제 Linux 환경에서 에이전트가 metrics를 RabbitMQ에 발행한다. Consumer가 소비해 DB에 저장하고 web UI에서 결과를 확인하는 전체 파이프라인을 검증한다.

```
[VM: web-server-01      ]   attention.agent_unstable (3분 주기 restart, 시간당 20회)
[VM: offline-server-01  ]   attention.gap_warnings (5m+ 끊김) + insufficient_data
[VM: app-server-01      ]   under_provisioned (swap_used 트리거)         -> RabbitMQ -> consumer -> DB -> web UI
[VM: monitor-server-01  ]   optimal (medium 부하 + swap reset)
[VM: mq-server-01       ]   over (light 부하 + zypper family + reverse-sshfs mount)
[VM: cache-server-01    ]   over (light 부하)
[VM: db-server-01       ]   over (light 부하 + RPM postgresql-setup --initdb)
```

7 VM이 서로 다른 OS + 서로 다른 서비스 뱃지 + 의도적 분류 분포를 가지는 이유:
- OS 다양성: 패키지 매니저 분기(apt/dnf/zypper) + systemd + GLIBC major + cloud-init 호환 동시 검증.
- 서비스 뱃지 다양성: `service_classifier.py` 7 카테고리(web·db·cache·mq·container·monitor·unknown) 100% 커버.
- 분류 분포: right-sizing 분류(over/optimal/under/insufficient_data)가 한쪽에 쏠리지 않게 합성 부하 프로파일 4단계로 분기. attention 카탈로그(`AttentionSignals`) 6 카테고리 중 2개(agent_unstable·gap_warnings) 의도 발화.

Lima + Apple Virtualization Framework / QEMU 채택 이유: Apple Silicon에서 부팅·메모리 가벼움, macOS 폐쇄망 라이선스 부담 없음(OSS), read-only mount + `/tmp/build` cp 패턴으로 host 빌드 산출물 보호.

---

## VM 매트릭스

`infra/lima/` 디렉토리에 7개 yaml. `dev-up.sh`의 `LIMA_VMS` 배열에서 단일 진실로 관리(`dev-down.sh`는 `source dev-up.sh`로 가져옴).

| 진행 순서 | VM | OS | family | 자원 | 서비스 | 뱃지 | 부하 | 분류 | attention 발화 |
|---|----|----|--------|------|--------|------|------|------|----------------|
| 1 | `web-server-01` | Debian 12 (bookworm) | apt | 1 CPU / 512 MiB / 5 GiB | nginx | web | medium | optimal | agent_unstable (1m boot + 3m 주기, 시간당 20회) |
| 2 | `offline-server-01` | Debian 13 (trixie) | apt | 1 CPU / 512 MiB / 5 GiB | (없음) | unknown | (offline-once) | insufficient_data | gap_warnings (5m+ 끊김) |
| 3 | `app-server-01` | Ubuntu 24.04 LTS (noble) | apt | 1 CPU / 1280 MiB / 5 GiB | docker.io | container | swap_trigger | under_provisioned | (분류 도넛에서만) |
| 4 | `monitor-server-01` | CentOS Stream 9 | dnf | 1 CPU / 1280 MiB / 10 GiB | zabbix-agent | monitor | medium | optimal | (분류 도넛에서만) |
| 5 | `mq-server-01` | openSUSE Leap 15 | zypper | 1 CPU / 1280 MiB / 10 GiB | mosquitto | mq | light | over_provisioned | (분류 도넛에서만) |
| 6 | `cache-server-01` | Rocky Linux 9 | dnf | 1 CPU / 1280 MiB / 10 GiB | redis | cache | light | over_provisioned | (분류 도넛에서만) |
| 7 | `db-server-01` | AlmaLinux 9 | dnf | 1 CPU / 1280 MiB / 10 GiB | postgresql-server | db | light | over_provisioned | (분류 도넛에서만) |

진행 순서는 시연 가시화 우선:
- 1번 web — attention 가장 빠른 발화 (1m 후 첫 restart)
- 2번 offline — gap_warnings 5m+ 발화 위해 가장 빨리 stop
- 3번 app — under_provisioned (swap_trigger) 보장 — 호스트 부담 우려로 다음 단계
- 4번 monitor — swap install 부담 (OOM 회피 swap reset 적용)
- 5~7번 mq/cache/db — 정상 분류 (over/over/over) — 후순위

OS 다양성 매트릭스 (7 distro 모두 다름, 0 중복):

| OS | VM | family |
|----|----|----|
| Debian 12 | web | apt |
| Debian 13 trixie | offline | apt |
| Ubuntu 24.04 LTS | app | apt |
| CentOS Stream 9 | monitor | dnf |
| openSUSE Leap 15 | mq | zypper |
| Rocky Linux 9 | cache | dnf |
| AlmaLinux 9 | db | dnf |

뱃지 분배 (`service_classifier.py` 7 카탈로그 100% 커버):

| 카테고리 | VM | 발화 키워드 |
|----------|----|----|
| web | web-server-01 | nginx |
| db | db-server-01 | postgresql |
| cache | cache-server-01 | redis |
| mq | mq-server-01 | mosquitto |
| container | app-server-01 | docker |
| monitor | monitor-server-01 | zabbix-agent |
| unknown | offline-server-01 | (서비스 없음) |

리소스 메모:
- 1 CPU 공통 — 최소 자원, cpu_p95 시연 1 코어 기준.
- apt family는 512 MiB / 5 GiB 기본. app-server는 docker 데몬 ~100 MiB + swap-trigger 1100 MB burst라 1280 MiB로 보수.
- dnf family는 1280 MiB / 10 GiB — dnf install transaction 1 GiB OOM 확인 후 1.25 GiB 보수. disk는 RPM cloud image qcow2 raw 강제.
- zypper family도 1280 MiB / 10 GiB.

---

## 합성 부하 프로파일 (right-sizing 분류 발화)

`recommendation.py`의 USE Method 임계에 분류가 골고루 떨어지도록 4 단계 + (offline-once).

| 프로파일 | cpu burst | mem burst | mem 점유 | 적용 VM | 목표 분류 |
|----------|-----------|-----------|---------|---------|----------|
| light | 1~3s | 5~20MB | 즉시 sync rm | mq, cache, db | over (cpu_p95 ~5%, mem_p95 <50%) |
| medium | 20~28s sustained | 240~300MB (web) / 700~850MB (monitor) | 25s sleep | web, monitor | optimal (cpu_p95 40~60%, mem_p95 50~70%) |
| swap_trigger | (boot 직후 1회 1100MB burst) + 이후 light | 1100MB 1회 → swap에 push → SwapUsed > 0 영구 | 10s | app | under_provisioned (swap_used short-circuit) |
| (offline-once) | — | — | — | offline | insufficient_data (1회 발행 후 stop) |

원칙:
- 분류 임계는 `recommendation.py` 모듈 상단 명명 상수 (#E3). 부하 프로파일은 임계 충족 설계.
- `WINDOW_DAYS = 14` (#F11) — dev 시연에서 14일 못 채우면 분류 모두 `insufficient_data`. 보고서 라우터 `?period_days=1` 등 짧은 윈도우 시연 필수.
- swap_trigger 프로파일이 host CPU 부담 최소(heavy++ sustained CPU 50s 대신 boot 1회 mem burst). 한 번 swap에 page push되면 SwapUsed > 0 영구 유지 → 매 measurement에서 swap_used = True 안정 발화.
- monitor swap은 OOM 회피 전용. yaml provision의 `vm.swappiness=1` + `dev-up.sh` post_provision 끝 `swapoff /swapfile && swapon /swapfile`로 SwapUsed=0 reset (swap_used 트리거 X, optimal 분류).

attention 카탈로그 발화 매핑:

| attention 카테고리 | 발화 VM | 트리거 |
|-------------------|---------|--------|
| disk_warnings | (없음) | 디스크 사용률 85%+ — 시연 위해 발화 안 시킴 |
| gap_warnings | offline-server-01 | offline-once mode (5m+ 끊김) |
| capacity_warnings | app-server-01 | under_provisioned (swap_used 트리거) |
| days_until_full_warnings | (없음) | 디스크 fill_rate 추정 30일 — 시연 안 함 |
| os_eol_warnings | (없음) | EOL OS 자체가 cloud image 가용성 한계라 발화 안 함 |
| agent_unstable | web-server-01 | agent-restart-demo timer (1h 슬라이딩 임계 3회 이상 — 3m 주기로 6배 마진) |

---

## dev-up.sh 흐름 + start_or_resume_vm wrapper

`./dev-up.sh` 실행:
```
[1/4] docker compose up -d --build           # 엔진 기동
[2/4] until migrate 완료 (최대 180s)          # alembic upgrade head init container
[3/4] until web 헬스체크 (최대 180s)          # 스키마 준비 완료 대기
[4/4] limactl start + agent install (7 VM)   # VM별 sync 진행
```

`start_or_resume_vm` wrapper 동작 (lima vz의 cloud-init 느린 distro 우회):

```
limactl start <vm> background 시작 (PID 보관)
loop (3s polling):
  - SSH ready check (limactl shell echo ok)
  - SSH ready 시점 기록
  - SSH ready 후 60s+ 경과해도 limactl PID 안 끝나면 → 강제 PID kill (lima final requirement
    "boot scripts must finished" stuck 우회 — Oracle Linux 9 등에서 5분+ stuck 확인)
  - 절대 cap 5분 — 초과 시 abort
limactl PID 종료 (정상 또는 우리 kill) 후 limactl shell echo ok 재검증 → boot 성공 판정
```

이 wrapper로 lima의 final requirement check가 distro 호환성 문제로 stuck돼도 SSH 작동하면 post-provision 진행. 1차 시도 round 2에서 OL9 5분 30초 stuck 후 process kill 시 SSH OK 확인 → wrapper 도입 결정.

`source dev-up.sh` 가드 (BASH_SOURCE):
```bash
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
```
직접 실행 시만 main 호출. `dev-down.sh`가 `source dev-up.sh`로 LIMA_VMS만 가져올 때는 main 자동 실행 안 함. set -u 환경에서 BASH_SOURCE 안전 access (`:-` empty default) — macOS bash 3.2 호환.

---

## 네트워크 구조

Lima default user-mode networking. VM → host는 Lima 자동 등록 DNS alias `host.lima.internal`로.

```
VM (assessment-agent)
  RABBITMQ_HOST=host.lima.internal  ->  host:5672 (docker-compose rabbitmq 포트 매핑)
```

에이전트 `.env`에 `RABBITMQ_HOST=host.lima.internal` 고정. 엔진 `.env`의 `RABBITMQ_HOST`(=`rabbitmq` 도커 서비스명)와 다르며, `dev-up.sh`가 VM별 `/etc/assessment-agent.env`를 생성할 때 명시.

VM 간 통신 사용 안 함 — 각 VM은 독립적, 모든 통신은 host RabbitMQ 경유.

---

## Mount 정책

기본 `mountType` 미명시 — Lima vz default(virtiofs) 사용. 단 `mq-server-01`(openSUSE Leap 15)은 yaml에 `mountType: "reverse-sshfs"` 명시 — virtiofs guest mount가 silent fail (kernel 6.4+ 임에도 mount table에 안 잡힘) 확인 후 fallback. reverse-sshfs는 host에서 sshfs로 push, guest distro 무관 + sshfs binary 자동 install (lima ensureRequirement).

```yaml
mounts:
- location: "{{.Param.AgentSrc}}"
  mountPoint: "/mnt/agent-src"
  writable: false
```

`{{.Param.AgentSrc}}` 절대 경로는 `dev-up.sh`가 `--set ".param.AgentSrc = \"$AGENT_SRC\""`로 주입 (Lima yaml의 `{{.Dir}}`은 instance dir라 호스트 경로 추적 불가).

`writable: false` — read-only mount. VM 안 빌드는 `rsync /mnt/agent-src/ /tmp/build/`로 cp 후 make. 호스트 빌드 산출물(macOS Mach-O) 보호.

---

## Provisioning 단계

`limactl start --name=<vm> --tty=false --set ...`로 VM 생성. yaml `provision` 섹션 자동 실행 후 `dev-up.sh`의 `post_provision_vm`이 limactl shell로 후처리.

### 1. (yaml provision) 합성 부하 timer + (선택) swap 활성화

`/usr/local/bin/synthetic-load.sh` + `synthetic-load.service` + `synthetic-load.timer` yaml별 inline. `OnBootSec=2min`, `OnUnitActiveSec=1min`. offline-server-01만 timer 없음.

VM 특수 추가:
- `app-server-01` — `swap-trigger.service` (boot 1회 1100MB mem burst → swap 발화 → SwapUsed > 0 영구). yaml provision Step 1에 swap file 256MB 활성 + Step 2 oneshot service.
- `monitor-server-01` — swap file 256MB 활성 + `vm.swappiness=1` sysctl 영구 (dnf install OOM 회피만, swap 사용 거의 안 함).
- `web-server-01` — `agent-restart-demo.service` + `agent-restart-demo.timer` (`OnBootSec=1min`, `OnUnitActiveSec=3min` — 시간당 20회 attention.agent_unstable 발화).

### 2. (dev-up.sh) `/etc/assessment-agent.env` 생성

`dev-up.sh`가 `infra/agent.env` source한 host env로 heredoc 치환:

```
RABBITMQ_HOST=host.lima.internal
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/assessment
RABBITMQ_USER=...
RABBITMQ_PASS=...
RABBITMQ_EXCHANGE=...
RABBITMQ_ROUTING_KEY_INVENTORY=server.inventory
RABBITMQ_ROUTING_KEY_METRICS=server.metrics
RABBITMQ_ROUTING_KEY_ERROR=server.error
RABBITMQ_ROUTING_KEY_TASK_RESULT=task.result
AGENT_HOSTNAME_OVERRIDE=<vm>     # 모든 VM에 vm 이름 그대로 (round 3에서 web-restart-demo override 제거)
AGENT_INTERVAL_SEC=60
AGENT_EXTERNAL_IP=203.0.113.10   # web-server-01만
```

`/etc/`에 두는 이유:
- mount된 `/mnt/agent-src`는 host와 양방향 → VM별 값 분리 어려움.
- SELinux/AppArmor가 systemd가 사용자 홈 디렉토리 내부 파일을 `EnvironmentFile=`로 읽는 것 차단 가능.
- `/etc/`는 systemd 자유 read + VM 로컬 격리.

### 3. (dev-up.sh) OS detect + 패키지 설치

`/etc/os-release`의 `ID:VERSION_ID` dispatch:

| family | 명령 |
|--------|------|
| Ubuntu/Debian (apt) | `apt-get install -y --no-install-recommends gcc make pkg-config libc6-dev librabbitmq-dev libcjson-dev curl iputils-ping ${svc_pkg}` |
| Rocky 8/9 / AlmaLinux 8/9 / CentOS Stream 9 (dnf) | `dnf install -y epel-release dnf-plugins-core` → `dnf config-manager --set-enabled crb \|\| --set-enabled powertools` → `dnf install -y gcc make pkg-config librabbitmq-devel cjson-devel curl iputils ${svc_pkg}` |
| openSUSE Leap 15 (zypper) | `zypper --gpg-auto-import-keys --non-interactive refresh` → `zypper --non-interactive install -y gcc make pkg-config librabbitmq-devel cJSON-devel curl iputils ${svc_pkg}` |
| CentOS 7 (yum, EOL — vault.centos.org redirect) | (코드 잔존, 현 활성 VM 없음) |

RPM family는 `librabbitmq-devel`이 EPEL + CRB(RHEL 9) 또는 PowerTools(RHEL 8) 저장소에 있어 활성화 의무. zypper는 cJSON-devel(대문자 J — Debian/RHEL의 `libcjson-devel`과 다름).

서비스 dispatch:

| VM | service | apt 패키지 | dnf 패키지 | zypper 패키지 | systemd 유닛 |
|----|---------|-----------|-----------|--------------|-------------|
| cache-server-01 | `redis` | `redis-server` | `redis` (rocky/rhel/almalinux) | — | `redis` (현 cache는 Rocky 9) |
| app-server-01 | `docker` | `docker.io` | (미지원 — podman default + docker-ce 외부 repo 필요) | — | `docker` |
| web-server-01 | `nginx` | `nginx` | `nginx` | `nginx` | `nginx` |
| db-server-01 | `postgres` | `postgresql` | `postgresql-server` (현 db는 AlmaLinux 9 — RPM 분기 + `postgresql-setup --initdb` 자동) | — | `postgresql` |
| mq-server-01 | `mosquitto` | `mosquitto` | `mosquitto` | `mosquitto` | `mosquitto` |
| monitor-server-01 | `zabbix_agent` | `zabbix-agent` | `zabbix-agent` (centos/rocky/rhel/almalinux) | — | `zabbix-agent` |
| offline-server-01 | `none` | (없음) | (없음) | (없음) | (없음) |

dispatch 단일 진실은 `dev-up.sh`의 `case "${ID}:$service"` 블록. 새 service 도입 시 본 표 + dev-up.sh 동시 갱신 의무.

RPM family postgresql은 cluster init 수동 — `dev-up.sh`가 `postgresql-setup --initdb`로 자동 처리. apt 계열은 install 시 자동 init라 skip.

설치된 서비스는 `systemctl enable --now` 즉시 활성화. 에이전트가 `services[]`에 포함시켜 발행 → 엔진의 `service_classifier.classify()`가 카테고리 뱃지(cache/web/db/mq/monitor/container)로 분류.

### 4. (dev-up.sh) 에이전트 빌드

```bash
rm -rf /tmp/build
mkdir -p /tmp/build
rsync -a --delete \
  --exclude='*.o' --exclude='*.a' --exclude='assessment-agent' --exclude='.git/' \
  /mnt/agent-src/ /tmp/build/
cd /tmp/build
make
```

`/mnt/agent-src`는 read-only mount이므로 rsync로 source만 sync 후 빌드. exclude로 host 빌드 산출물(macOS Mach-O .o/binary)·.git 제외 → fresh build (cp -r 시 stale .o로 잘못 link되는 회귀 방지).

### 5. (dev-up.sh) 바이너리 설치 + systemd unit

```
install -m 755 /tmp/build/assessment-agent /usr/local/bin/assessment-agent
cat > /etc/systemd/system/assessment-agent.service <<EOF
[Unit]
Description=Assessment Agent
After=network.target
[Service]
User=root
EnvironmentFile=/etc/assessment-agent.env
ExecStart=/usr/local/bin/assessment-agent
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable assessment-agent
# binary·env·unit 변경 있을 때만 restart — agent_started_at 갱신 회피 (attention false positive 줄임)
```

`/usr/local/bin/`으로 복사 — Lima의 9p/virtiofs mount(`/mnt/agent-src`)에서 systemd 직접 실행 시 SELinux/AppArmor 컨텍스트 충돌 가능. `/usr/local/bin/`은 표준 실행 경로로 통과.

`User=root`로 동작 — 에이전트는 `/proc/*` 전반 read만 필요. Lima default user는 host username으로 잡혀 VM 간 일관성을 위해 root 통일.

### 6. (dev-up.sh) monitor swap reset (조건 분기)

post_provision_vm 끝에 `if [ "$vm" = "monitor-server-01" ]` → `swapoff /swapfile && swapon /swapfile`. dnf install 도중 swap에 push된 page를 reset해 SwapUsed=0 보장 (under_provisioned 분류 안 발화, optimal 유지).

### 7. (dev-up.sh) finalize_vm (조건 분기)

`vm_mode` 함수 dispatch:
- `offline-server-01` → `offline-once`: inventory 1회 발행 대기 15s → `systemctl stop assessment-agent` + `systemctl disable assessment-agent` + `limactl stop`. 5분 후 attention.gap_warnings 발화 (시연 의도).
- 그 외 → `persistent`: agent restart로 publish 계속.

---

## dev-up.sh / dev-down.sh

### dev-up.sh
```
[1/4] docker compose up -d --build           # 엔진 기동
[2/4] until migrate 완료 (최대 180s)
[3/4] until web 헬스체크 (최대 180s)
[4/4] limactl start + agent install (7 VM, 시연 가시화 순서)
```

`limactl start`가 web 헬스체크 통과 후 호출 — 에이전트 첫 inventory 발행 시 RabbitMQ + consumer ready 의무.

### dev-down.sh
```
[1/2] limactl stop -f + delete -f (LIMA_VMS 7 VM, source dev-up.sh)
[2/2] docker compose down -v
```

`source "$(dirname "$0")/dev-up.sh"`로 LIMA_VMS 단일 진실 가져옴. BASH_SOURCE source guard로 main 자동 실행 안 함.

LIMA_VMS 외 본 프로젝트 명명 패턴(`(cache|app|web|db|legacy-mq|monitor|offline|container)-server-01`) 잔재 발견 시 알림만 (자동 삭제 안 함, 다른 프로젝트 인스턴스 보호).

순서 — VM을 먼저 죽인 뒤 broker 종료 (broker 먼저 죽으면 에이전트가 silent publish 실패 로그 누적).

---

## 누적 사고 패턴 (반면교사 — 도입 검증 round에서 발견·해결)

| # | 문제 | 원인 | 해결 |
|---|------|------|------|
| 1 | cache-server-01 Rocky 8 aarch64 boot stuck (4분간 SSH 안 열림, serial.log 비어있음) | Rocky 8 aarch64 cloud image와 lima vz driver boot 호환 — Lima 공식 examples엔 Rocky 9만 검증 | Rocky 9 fallback (image URL `8` → `9`) |
| 2 | mq-server-01 openSUSE Leap 15 virtiofs mount silent fail (`/mnt/agent-src` 빈 directory) | guest virtiofs kernel module 미동작 (kernel 6.4+ 임에도) | yaml에 `mountType: "reverse-sshfs"` 명시 — sshfs binary 자동 install (lima ensureRequirement) |
| 3 | mq-server-01 zypper `libcjson-devel` not found | openSUSE 패키지명이 대문자 `cJSON-devel` (Debian/RHEL의 `libcjson-devel`과 다른 명명) | dev-up.sh zypper 분기에 `cJSON-devel` 명시 |
| 4 | monitor-server-01 dnf install exit 137 (SIGKILL OOM) | CentOS Stream 9 + EPEL 9 + zabbix-agent install transaction이 1280 MiB 초과 | yaml provision Step 1에 swap file 256 MiB 활성 + post-install `swapoff/swapon` reset (under 안 발화) |
| 5 | monitor-server-01 `golang-github-prometheus-node-exporter` EPEL 9 미존재 | EPEL 9에 prometheus exporter 패키지 자체 없음 (EPEL 8엔 있었음) | service `node_exporter` → `zabbix_agent` (dev-up.sh dispatch + service_classifier "zabbix" → monitor 매칭) |
| 6 | offline-server-01 Ubuntu 20.04 cloud image 다운로드 timeout (90s 안 안 끝남) | Ubuntu 20.04 cache miss + 네트워크 환경 | Oracle Linux 9 fallback 시도 |
| 7 | offline-server-01 OL9 EPEL GPG check FAILED | epel-release 설치 후에도 GPG key 자동 import 안 됨 | dev-up.sh ol:* 분기에 `rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-${os_major}` 명시 |
| 8 | offline-server-01 OL9 disk size error (16 GiB image vs 10 GiB yaml) | OL9 KVM image qcow2 raw 16 GiB 강제 | yaml disk 16 GiB로 늘림 |
| 9 | offline-server-01 OL9 cloud-init "boot scripts must finished" 5분+ stuck | OL9 cloud-init final 단계 lima vz와 호환 — SSH는 정상 ready | dev-up.sh `start_or_resume_vm` wrapper — SSH ready+60s 후 limactl PID kill하고 진행 |
| 10 | offline-server-01 OL9 자체 boot 시간 부담 + AWS 특화 OS 부적합 | OL9 자체가 시연 가치 약함 | Debian 13 (trixie) fallback — Lima 공식 검증, ~30~45s boot, apt 재사용 |
| 11 | dev-down.sh가 LIMA_VMS 3 VM hardcoded (cache/app/web) — 7 VM과 sync 안 됨 | 옛 hardcoded LIMA_VMS 잔재 | dev-down.sh를 `source dev-up.sh`로 LIMA_VMS 단일 진실로 변경 |
| 12 | dev-up.sh source 시 `BASH_SOURCE[0]: parameter not set` (zsh 환경) | zsh에 BASH_SOURCE array 없음 + dev-up.sh의 main 호출 가드 누락 | bash subshell 명시 + source guard `if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then main "$@"; fi` 추가 |

12 사고 패턴 → 진행 검증 사이클 + 단계별 fix → 최종 7 VM 모두 boot OK + post-provision exit 0 + agent message 발행.

---

## 운영 노트 / 트러블슈팅

### broker 재기동 시 에이전트 수동 재시작 (CRITICAL)

증상: docker compose RabbitMQ를 down/up 또는 `down -v` 후 재기동하면 VM 안 C 에이전트가 broker 재연결 silent 포기. systemd 상태는 `active(running)`이지만 publish 로그 끊김.

대응:
```bash
for vm in web-server-01 offline-server-01 app-server-01 monitor-server-01 \
          mq-server-01 cache-server-01 db-server-01; do
  limactl shell "$vm" sudo systemctl restart assessment-agent
done
```

원인: C 에이전트 publish 루프에 `connect_robust` 자동 재연결 없음. exit하지 않고 silent retry만 하므로 systemd `Restart=on-failure`도 트리거 안 됨.

### VM 시간 동기화

`collected_at`은 VM 로컬 시각. 호스트와 어긋나면 차트 시간축 안 맞음. Lima default 호스트 동기화이지만 장시간 절전·suspend 후 재개 시 어긋날 수 있음.

```bash
for vm in web-server-01 offline-server-01 app-server-01 monitor-server-01 \
          mq-server-01 cache-server-01 db-server-01; do
  limactl shell "$vm" sudo bash -c 'systemctl restart systemd-timesyncd 2>/dev/null || systemctl restart chronyd'
done
```

### 에이전트 로그 확인

```bash
limactl shell web-server-01 sudo journalctl -u assessment-agent --no-pager -n 50
```

기대 로그 (정상):
```
[agent] cmd lsblk         available
[agent] cmd curl          available
[agent] cmd dbus-uuidgen  available
[agent] machine_id=<32 hex>
[agent] published inventory
[agent] loop mode: interval=60s (Ctrl+C to exit)
```

이후 60초 주기 publish 로그가 추가돼야 정상. 멈춰 있으면 broker 재연결 실패 의심.

### 첫 기동 시간

| 단계 | 예상 시간 |
|------|-----------|
| `docker compose up --build -d` (첫 빌드) | 60–120s |
| web 헬스체크 통과 | 5–10s |
| 7 VM cloud image 다운로드 (cache miss 가정) | 5–15분 |
| 7 VM cloud image (모두 캐시) | 0~10s |
| VM당 boot + post-provision (cache hit 후) | 30~120s |
| 에이전트 첫 inventory 도달 | 즉시 |
| 첫 metrics 차트 그려짐 (delta 계산용 2회 readings) | 60–90초 |

라운드 3 (모두 cache hit + Apple Silicon 환경) — 전체 [4/4] 단계 약 8분.

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| `limactl start` cloud image 다운로드 실패 | 네트워크 / mirror 일시 오류 | 재시도 |
| `librabbitmq-devel` not found (RHEL family) | EPEL/CRB·PowerTools 미활성화 | dev-up.sh dnf 분기가 자동 처리 — VM 삭제 후 재기동 |
| 에이전트 publish 실패 로그 (CONNREFUSED) | host docker rabbitmq 안 떠 있음 / host.lima.internal 해석 실패 | `docker compose ps rabbitmq` + `limactl shell <vm> getent hosts host.lima.internal` 확인 |
| consumer가 metrics 받지만 server_inventory 비어 있음 | inventory 메시지 유실 (broker 재기동 등) | VM 안 `systemctl restart assessment-agent` |
| `make` 실패 (`/mnt/agent-src` write 권한 없음) | mount writable=false인데 빌드 산출물 쓰려 함 | dev-up.sh가 `/tmp/build`로 rsync — 정상. 직접 build 시도 X |
| OL9·기타 cloud-init 느린 distro에서 limactl start 5분+ stuck | lima final requirement(`boot scripts must finished`)가 distro 호환성 문제 | dev-up.sh `start_or_resume_vm` wrapper가 SSH ready+60s 후 PID kill로 자동 우회 |

### 개별 VM 조작

```bash
limactl start web-server-01                          # 단일 VM 기동 (yaml 등록된 상태)
limactl shell web-server-01                          # SSH 접속
limactl shell web-server-01 sudo <cmd>               # root 명령
limactl stop -f web-server-01                        # 강제 종료 (제거 X)
limactl delete -f web-server-01                      # 제거
limactl list                                         # VM 상태 표
```

단일 VM 시나리오는 `dev-up.sh`의 `LIMA_VMS` 배열 일부 항목 주석 처리.
