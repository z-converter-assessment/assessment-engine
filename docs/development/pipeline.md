# 파이프라인 검증 (libvirt)

본 문서는 dev 시연·파이프라인 검증 단일 진실. 운영자 절차·VM 매트릭스·OS 다양성·합성 부하·provisioning·운영 디버깅 모두 포함. Linux 호스트(x86_64) + libvirt(KVM) 한정 (CLAUDE.md #A0).

VM 매트릭스 — Linux 5대 + Windows 1대 모두 libvirt(KVM). 본 문서는 Linux 단일 진실,
Windows 1대(Win Server 2022 autounattend 무인 설치, 기본 포함)는 `docs/development/windows-vm.md` 단일 진실.

에이전트(C) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 실제 VM 환경에서 검증 + 시연용 분류·attention 분포 가시화. 1 VM = 2 서비스로 `service_classifier` 6 카테고리 최대 커버.

```
HOST MACHINE (Ubuntu x86_64)
  Docker engine (assessment-engine) — docker0 bridge (172.17.0.0/16)
    FastAPI :8000  <----QUERY-----  PostgreSQL :5432
                                         ^
                                    PERSIST | (3)
                                         |
    RabbitMQ :5672 ---DISPATCH(2)---> Consumer
         ^   ^   (published 0.0.0.0 -> reachable via libvirt gateway 192.168.122.1)
         |   |
         |   | PUBLISH (1, Windows) Target: 192.168.122.1 (libvirt NAT gateway)
         |   +---  libvirt VM x 1: win-server-01 (Win Server 2022, default) -> windows-vm.md
         |
         | PUBLISH (1, Linux) Target: 192.168.122.1 (libvirt NAT gateway)
         |
  libvirt VM x 5 (assessment-agent.service) — virbr0 NAT (192.168.122.0/24)
    app/data/edge/offline-01/offline-02-server
```

libvirt(KVM)와 Docker 는 분리된 두 네트워크다. 컨테이너는 docker0
브리지, VM 은 virbr0 NAT(192.168.122.0/24)에 붙는다. VM 은 NAT 게이트웨이 IP(192.168.122.1, = host)로
host 의 docker 퍼블리시 포트(RabbitMQ 5672·web 8000)에 도달한다 — DNAT + libvirt 기본 forward 규칙(LIBVIRT_FWO).
컨테이너(web) ZDM mock resolver 는 자기 컨테이너 `localhost:8000`(`ZDM_RESOLVER_HOST_OVERRIDE`). Windows VM 도 동일 virbr0 라 게이트웨이 192.168.122.1 로 host 도달.

## 사전 요구

| 도구 | 설치 | 용도 |
|------|------|------|
| Docker | distro 패키지 | 엔진 compose 스택 |
| libvirt + KVM | `sudo apt install -y libvirt-daemon-system libvirt-clients qemu-kvm cloud-image-utils` | Linux VM 5대 (qemu-kvm + virsh + cloud-localds) |
| genisoimage·ovmf·mingw-w64·cmake | dev-up 자동 설치(sudo apt) | Windows VM(기본 포함) autounattend ISO·UEFI·agent 크로스빌드 (`windows-vm.md`) |

호스트 1회 설정:
- 본 유저를 `libvirt`·`kvm` 그룹에 추가 (`sudo usermod -aG libvirt,kvm $USER`) 후 재로그인 — qemu:///system 을 sudo 없이 사용 (재로그인 전 기존 셸은 그룹 미반영, `newgrp libvirt` 로 임시 적용 가능).
- `default` 네트워크·storage 풀은 dev-up.sh `ensure_libvirt_ready` 가 멱등 보장 (풀 디렉토리 mode 0711 포함 — qemu 가 디스크 traverse).
- VM -> host docker 퍼블리시 포트(RabbitMQ·web)는 libvirt 기본 forward 규칙(LIBVIRT_FWO) + docker DNAT 로 도달. 막힐 경우(방화벽 정책차) fallback: `sudo iptables -I DOCKER-USER -i virbr0 -j ACCEPT`.

전제: agent 바이너리 (`dev/bin/assessment-agent`) 는 dev-up.sh 의 `ensure_agent_binary` 단계가 자동 확보 (host arch = amd64, build.sh 가 자동 매핑). Windows agent .exe 는 별도 (`windows-vm.md`). 두 분기:
- `AGENT_BINARY_URL` env set 시 — curl fetch (향후 agent CI release artifact 자동화).
- 미설정 시 — `dev/agent-build/build.sh` 호출, sibling repo (`AGENT_REPO_PATH`, default `../assessment-agent`) 를 buildx context 로 cross-build.

상세: `dev/README.md`.

## 실행

```bash
./dev/dev-up.sh   # 인자 0 — 의존성 자동설치 + env 자동복사 + Docker + Linux 5 VM + Windows 모두 구성
```

env 파일(`dev/.env`·`dev/agent.env`)은 dev-up 이 example 에서 자동 복사하므로 사전 복붙 불요.
의존성(libvirt/qemu/genisoimage/ovmf/mingw-w64/cmake)도 부재 시 자동 설치(sudo apt).

`dev/dev-up.sh` 는 위 자동 준비 후 4단계 (Linux/libvirt), 이어서 Windows 블록(아래 71줄):
1. `docker compose up --build -d` — 엔진 기동 (COMPOSE_FILE=docker-compose.yml export)
2. `migrate(alembic upgrade head)` 완료 대기 (cap 180s)
3. web 헬스체크 통과 대기 (cap 180s)
4. `start_or_resume_vm` → `post_provision_vm` → `install_demo_loads` 로 5 VM 순차 (첫 실행 시 base cloud image 다운로드 + 풀 import, 이후 vol-clone 재사용)

Windows VM (win-server-01)은 `windows-vm.md` 단일 진실 — libvirt autounattend 무인 설치, 기본 포함(인자 없는 `./dev/dev-up.sh` 에 자동 실행, `WIN_ENABLE=0` opt-out). OS 무인설치(~20min)는 최초 1회만 설치 과정을 검증하고 완성본을 골든 이미지로 캐시 — 이후 dev-up 은 골든 clone(~수십초)으로 OS 설치 skip(dev-down 이 win-server-01.qcow2 만 지우고 골든 보존). agent 는 매번 deploy 갱신.

## 결과 확인

- http://localhost:8000/ — Linux 5 VM + Windows 1 (기본 6대) 등록 (환경 개요 홈)
- 60초 주기 메트릭 갱신
- 분류 분포 시연은 `/reports/servers?period_days=1` (대시보드는 `recommendation.WINDOW_DAYS=7` 고정, #F10)
- attention 카드 상단 요약: app `agent_unstable` + edge `gap_warnings` (3회 발행 후 down)
- 보고서 발행 (engineer/customer) — 정적 스냅샷 생성·`?job={id}` 조회 동작 관찰

## 종료

```bash
./dev/dev-down.sh   # 모든 VM(Linux+Windows) 삭제 → Docker 볼륨 삭제 (DB·메트릭 초기화). 캐시(base image/ISO/MSI/vendor) 보존
```

부분 종료:

| 시나리오 | 명령 |
|---------|------|
| Docker만 종료, VM 유지 | `docker compose down` (데이터 유지) / `docker compose down -v` (삭제) |
| 특정 Linux VM만 종료 | `virsh destroy app-server-01; virsh undefine app-server-01 --remove-all-storage` |
| Linux VM 일시 정지 | `virsh shutdown <vm>` 또는 `virsh destroy <vm>` (재기동 시 post-provision 재적용은 dev-up.sh 재실행) |
| Windows VM 일시정지 | `virsh shutdown win-server-01` (수동 보존 — dev-down 은 삭제, `windows-vm.md`) |

`dev-down.sh` 는 모든 VM(Linux + Windows, qcow2 삭제) + Docker 볼륨을 정리한다. 캐시(base cloud image·Windows ISO·redis MSI·windows-agent vendor static libs)는 보존 — 다음 dev-up 이 provisioning·설치를 처음부터 재수행해 설치 과정을 검증.

---

## 사용 맥락

VM 은 에이전트 E2E + 시연 분류 분포 가시화. 엔진(dev compose)은 host Docker, VM 들이 실제 OS 환경에서 에이전트 metrics 를 RabbitMQ 에 발행 → Consumer DB 저장 → web UI 확인.

```
[VM: app-server-01  Linux ]   nginx+rabbitmq  attention.agent_unstable (3m restart, 시간당 20회)
[VM: data-server-01 Linux ]   postgres+zabbix-agent  (db+monitor, under_provisioned: swap-demo)  -> MQ -> consumer -> DB -> web UI
[VM: edge-server-01 Linux ]   docker+memcached  attention.gap_warnings (3회 발행 후 down)
[VM: win-server-01  WinSrv ]   IIS+redis  (windows-vm.md, libvirt, default)
```

VM 구성 의도 (호스트 영향 최소화 우선):
- 1 VM = 2 서비스: `service_classifier` 6 카테고리(web/db/cache/mq/container/monitor) 전부 커버.
- OS 다양성: 패키지 매니저 분기(apt/dnf) + systemd + Windows SCM. distro (Debian 12, Rocky 9, Win Server 2022).
- attention 카탈로그 발화: `AttentionSignals` 카테고리 중 2개 (agent_unstable, gap_warnings) 의도 발화.
- 합성 부하: 모든 VM light (sustained CPU 1~3s + mem 5~20MB) — 차트 변동만 가시화. CPU 임계 안 넘김 → over_provisioned 분류 (CPU 부담 0).

libvirt(KVM) 채택 (Linux): cloud image qcow2 + cloud-init 으로 VM 부팅, virt-install(python3-gi) 대신 storage 볼륨 API(vol-clone)+`virsh define` 도메인 XML 직접 생성 — 의존 최소화. VM disk 는 type='file' 명시 경로(virt-aa-helper apparmor 프로파일 정합), cdrom 은 IDE(i440fx 네이티브, cloud-init NoCloud 인식). Windows(win-server-01)는 동일 libvirt 에 Win Server 2022 autounattend 무인 설치 — q35 + OVMF UEFI + SATA + e1000e, 기본 포함 (`windows-vm.md`).

---

## VM 매트릭스

Linux VM 정의(distro/service/ext_ip)는 `dev-up.sh` 의 dispatch 함수(`vm_distro`/`vm_service`/`vm_ext_ip`)가 단일 진실. `vm_service` 는 공백 구분 다중 서비스 반환 — `post_provision_vm` 이 for-loop 으로 각각 설치. `VMS` 배열 순서대로 기동(`dev-down.sh`는 `source dev-up.sh`로 가져옴). 별도 VM yaml 없음 — base cloud image vol-clone + cloud-init seed 로 `virsh define` 후 post-provision 이 패키지·합성 부하·systemd 를 모두 설치. distro key(`debian12`/`rocky9`) -> cloud image URL 은 `vm_image_url`. Windows(win-server-01)는 `windows-vm.md`.

| 순서 | VM | 가상화 | distro | family | 서비스 (2) | 카테고리 | 부하 | 분류 | attention 발화 |
|---|----|----|----|--------|--------|------|------|------|----------------|
| 1 | `app-server-01` | libvirt | `debian12` | apt | nginx, rabbitmq | web, mq | light | over | agent_unstable (1m boot + 3m, 시간당 20회) |
| 2 | `data-server-01` | libvirt | `rocky9` | dnf | postgresql, zabbix-agent | db, monitor | light + swap-demo | under | under_provisioned (swap-demo 1회 page-out → mem_saturation, 언더프로비저닝 상세 발화) |
| 3 | `edge-server-01` | libvirt | `debian12` | apt | docker, memcached | container, cache | light | over | gap_warnings (3회 발행 후 poweroff) |
| 4 | `offline-server-01` | libvirt | `debian12` | apt | (없음) | — | (offline-demo) | — | gap_warnings (목록 채우기·오프라인 표시) |
| 5 | `offline-server-02` | libvirt | `debian12` | apt | (없음) | — | (offline-demo) | — | gap_warnings (목록 채우기·오프라인 표시) |
| 6 | `win-server-01` | libvirt | Win Server 2022 | SCM | IIS, redis | web, cache | (기본 포함, windows-vm.md) | — | — |

진행 순서는 시연 가시화 우선:
- 1번 app — attention 가장 빠른 발화 (1m 후 첫 restart) + external IP (web-facing)
- 2번 data — postgresql(db) + zabbix-agent(monitor)
- 3번 edge — offline-demo (약 180s 후 poweroff → gap_warnings). docker(container) + memcached(cache)
- win-server-01 — libvirt 기본 포함 (`windows-vm.md`). IIS(native role)+redis(크로스플랫폼) 혼합

뱃지 분배 (`service_classifier.py` 6 카테고리 전부 커버):

| 카테고리 | VM | 발화 키워드 |
|----------|----|----|
| web | app-server-01, win-server-01 | nginx, IIS(w3svc) |
| mq | app-server-01 | rabbitmq |
| db | data-server-01 | postgresql |
| monitor | data-server-01 | zabbix-agent |
| container | edge-server-01 | docker |
| cache | edge-server-01, win-server-01 | memcached, redis |

리소스 메모: VM별 memory/vcpu 는 `vm_memory`/`vm_vcpu` dispatch (app/data 2048MB·2vcpu, edge 1536MB·2vcpu, offline 768MB·1vcpu). disk 는 base(약 3GiB virtual) vol-clone 후 20GiB resize — cloud-init growpart 가 부팅 시 확장. dnf family(Rocky)는 install transaction 이 무거워 `install_weak_deps=False` 로 절약.

---

## 합성 부하 프로파일 (right-sizing 분류 발화)

`recommendation.py`의 USE Method 임계 — 호스트 영향 최소화 위해 모든 VM 을 light 로 통일. CPU 임계는 일부러 안 넘김 (CPU 부담 회피) → 모든 VM over_provisioned 분류. 합성 부하 스크립트·timer 는 `dev-up.sh` 의 `install_synthetic_load`/`install_agent_restart_demo`/`install_offline_demo` 가 post-provision 으로 설치. (under_provisioned 시연은 폐기 — swap-trigger 미사용.)

| 프로파일 | cpu burst | mem burst | 적용 VM | 목표 분류 |
|----------|-----------|-----------|---------|----------|
| light | 1~3s | 5~20MB | app, data, edge | over_provisioned (cpu_p95 ~5%, mem_p95 <50%) |
| offline-demo (추가) | (약 180s 후 `systemctl poweroff`) | — | edge | gap_warnings (발행 중단) |

원칙:
- 분류 임계는 `recommendation.py` 모듈 상단 명명 상수 (#E3). 부하 프로파일은 임계 충족 설계.
- `WINDOW_DAYS = 7` (#F10) — dev 시연에서 7일 못 채우면 분류 모두 `insufficient_data`. 보고서 라우터 `?period_days=1` 등 짧은 윈도우 시연 필수.
- light 부하 스크립트는 libvirt 게이트웨이 IP(192.168.122.1):8000 으로 ping/curl (health·chart-utils) — 차트 변동만 가시화. 게이트웨이 IP 는 post-provision 이 placeholder(__HOST_TARGET__) sed 치환으로 주입. 분류 임계 안 넘김 (over_provisioned 유지).

운영신호(`AttentionSignals`) 카탈로그는 3개뿐 — gap_warnings / os_eol_warnings / agent_unstable (`query_service._assemble_attention`). disk·capacity·days_until_full 은 운영신호가 아니라 USE Method right-sizing 소속(중복 회피) — 운영신호 발화 매핑은 본 3개만:

| 운영신호 (3) | 발화 VM | 트리거 | OS 분기 |
|-------------|---------|--------|---------|
| gap_warnings | edge-server-01·offline-server-01/02 | offline-demo (약 180s 후 poweroff → 발행 중단, engine 미수신 시 발화) | 무관 (last_metric_at 타이밍) |
| agent_unstable | app-server-01 | agent-restart-demo timer (1h fixed 임계 3회 이상 — 3m 주기로 6배 마진) | 무관 (agent_started_at DISTINCT, 양 OS agent 발행) |
| os_eol_warnings | (없음) | EOL OS 자체가 cloud image 가용성 한계라 발화 안 함 | OS 분기 구현 (Linux os_version · Windows build) |

운영신호 3개는 모두 OS-중립이라 Linux/Windows 차이로 오발화하지 않는다 (OS 의미가 갈리는 swap vs pagefile 신호는 USE Method classify 소속이지 운영신호 아님). 참고: 옛 `disk_warnings`/`capacity_warnings`/`days_until_full_warnings` 운영신호 표기는 폐기 — capacity 는 환경 개요 under_provisioned, disk capacity/IO 는 classify, days_until_full 은 보고서 스토리지 컬럼.

---

## VM 생성 (`start_or_resume_vm`)

멱등 — 도메인이 있으면 실행 보장, 없으면 base image 확보 후 생성:

```
virsh dominfo <vm> 성공      → 실행 중 아니면 virsh start <vm>
없으면                       → ensure_base_image(distro) -> build_and_define_vm -> virsh start
이후 ssh dev@<VM_IP> echo ok 로 SSH 도달 검증 (cap 120s — 첫 부팅 cloud-init + DHCP lease 여유)
```

`build_and_define_vm`: base 볼륨 vol-clone + 20G resize -> cloud-init seed(유저 dev·NOPASSWD sudo·dev SSH 공개키·hostname) ISO 풀 import -> 도메인 XML(type=file disk, IDE cdrom, virtio net) `virsh define`. VM IP 는 `virsh domifaddr --source lease` 로 확인.

`source dev-up.sh` 가드 (BASH_SOURCE):
```bash
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
```
`dev-down.sh`가 `VMS` 만 가져올 때 main 자동 실행 안 함.

---

## 네트워크 구조

libvirt(virbr0 NAT) + Docker(docker0) 분리망. VM -> host 는 NAT 게이트웨이, host(dev-up 자동화) -> VM 은 dev SSH 키 접속.

```
VM (assessment-agent)  [virbr0 192.168.122.0/24]
  RABBITMQ_HOST=192.168.122.1  ->  host:5672 (docker publish -> DNAT -> rabbitmq container)

dev-up.sh (host)
  ssh dev@<VM IP>:22  ->  agent VM sshd (OpenSSH, post-provision)
```

- VM -> host: libvirt NAT 게이트웨이 IP(192.168.122.1 = host). docker 퍼블리시 포트(5672·8000)는 DNAT + libvirt 기본 forward 규칙(LIBVIRT_FWO)로 도달. 엔진 `.env` 의 `RABBITMQ_HOST`(=`rabbitmq` 도커 서비스명)와 다르며, `dev-up.sh`가 VM별 `/etc/assessment-agent.env` 생성 시 게이트웨이 IP 로 주입.
- 컨테이너 ZDM mock resolver: 자기 컨테이너 `localhost:8000`(`ZDM_RESOLVER_HOST_OVERRIDE`) — host 경유 안 함.
- host -> VM: dev-up.sh 가 VM IP(동적, DHCP lease)를 resolve 해 dev SSH 키로 접속, post-provision 수행. VM 22 는 post-provision `openssh-server` 라 listen.
- VM 간 통신 사용 안 함 — 각 VM 독립, 모든 통신은 host RabbitMQ 경유.

---

## 바이너리 전달

`dev-up.sh` 는 마운트 경로·권한 가정을 피하려 바이너리를 ssh stdin 으로 전송한다:

```bash
vm_ssh "$vm" 'cat > /tmp/assessment-agent' < dev/bin/assessment-agent
# post-provision SCRIPT 안에서 cmp 후 install -m 755 /tmp/assessment-agent /usr/local/bin/
```

`/usr/local/bin/` 으로 복사 — 표준 실행 경로. `cmp` 멱등 — 바이너리·env·unit 변경 없으면 restart 건너뜀 (attention.agent_unstable false positive 회피).

---

## Provisioning 단계

VM 생성(`build_and_define_vm`) 후, `dev-up.sh` 의 `post_provision_vm` + `install_demo_loads` 가 `vm_ssh <vm> sudo bash` 로 후처리.

### 1. `/etc/assessment-agent.env` 생성

`dev-up.sh`가 `dev/agent.env` source한 host env로 heredoc 치환 (RABBITMQ_HOST·WORKER_DOWNLOAD_ALLOWED_HOSTS 는 libvirt 게이트웨이 IP):

```
RABBITMQ_HOST=192.168.122.1
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
WORKER_DOWNLOAD_ALLOWED_HOSTS=192.168.122.1
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

`openssh-server` 는 host -> VM post-provision SSH 접속용 — cloud image 의 cloud-init SSH 와 별개로 표준 22 sshd unit 명시 enable 해 접속 일관성 보장. apt 는 `ssh`, dnf 는 `sshd` unit.

서비스별 패키지·유닛 dispatch (`case "${ID}:${svc}"` 단일 진실, `dev-up.sh` `post_provision_vm` for-loop):

| service | 카테고리 | apt (debian) pkg / unit | dnf (rocky) pkg / unit | 특이 |
|---------|----------|------------------------|------------------------|------|
| nginx | web | `nginx` / `nginx` | `nginx` / `nginx` | |
| rabbitmq | mq | `rabbitmq-server` / `rabbitmq-server` | `rabbitmq-server`(EPEL) / `rabbitmq-server` | |
| postgres | db | `postgresql` / `postgresql` | `postgresql-server` / `postgresql` | RPM 은 `postgresql-setup --initdb` 자동 |
| zabbix | monitor | `zabbix-agent` / `zabbix-agent` | `zabbix-agent`(EPEL) / `zabbix-agent` | EPEL 9 에 node_exporter 부재 → zabbix 채택. server 미설정이라 active 실패 가능(분류엔 무관) |
| redis | cache | `redis-server` / `redis-server` | `redis` / `redis` | |
| memcached | cache | `memcached` / `memcached` | `memcached` / `memcached` | |
| docker | container | `docker.io` / `docker` | (docker-ce repo 필요, 현재 미사용) | unix socket — listen port 없음, SCM unit 으로 분류 |

dispatch 단일 진실은 `dev-up.sh`의 `case "${ID}:${svc}"` 블록. 새 service 도입 시 본 표 + dev-up.sh + `service_classifier` 카탈로그(#E7) 동시 갱신 의무. docker 는 edge-server-01 을 debian 으로 배치(apt `docker.io` 단순)해 dnf docker-ce repo 경로 회피.

### 3. 바이너리 + systemd unit

ssh stdin 으로 받은 `/tmp/assessment-agent` 를 `cmp` 후 `/usr/local/bin/` 설치 + `assessment-agent.service` unit 작성. `User=root` (에이전트는 `/proc/*` read 만 필요, VM 간 일관성 위해 root 통일). binary·env·unit 변경 있을 때만 restart — `agent_started_at` 갱신 회피 (attention false positive 줄임).

### 4. 합성 부하·시연 트리거 (`install_demo_loads`)

모든 Linux VM 공통:
- `install_synthetic_load` — 공통. light 부하 timer (`OnBootSec=2min`, `OnUnitActiveSec=1min`). 모든 VM over_provisioned 분류.
- `app-server-01` — `install_agent_restart_demo` (`OnBootSec=1min`, `OnUnitActiveSec=3min` — 시간당 20회 attention.agent_unstable).
- `edge-server-01`·`offline-server-01`·`offline-server-02` — `install_offline_demo` (`OnBootSec=${OFFLINE_DOWN_AFTER_SEC:-180}` 후 `systemctl poweroff`). agent 약 3회 발행 후 VM 정지 → attention.gap_warnings. 도메인 XML `on_poweroff=destroy` 라 'shut off' 보존, 재기동은 `virsh start <vm>` (dev-up.sh 재실행 시 멱등 재적용).
- `data-server-01` — 추가 트리거 없음 (db+monitor, over_provisioned).

---

## 운영 노트 / 트러블슈팅

### broker 재기동 시 에이전트 수동 재시작 (CRITICAL)

증상: docker compose RabbitMQ를 down/up 또는 `down -v` 후 재기동하면 VM 안 C 에이전트가 broker 재연결 silent 포기. systemd 상태는 `active(running)`이지만 publish 로그 끊김.

대응 (VM IP 는 `virsh domifaddr <vm> --source lease`, 접속은 dev 키):
```bash
ssh_vm() { ip=$(virsh domifaddr "$1" --source lease | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1); \
  ssh -i dev/.ssh/id_dev -o StrictHostKeyChecking=no dev@"$ip" "${@:2}"; }
for vm in app-server-01 data-server-01 edge-server-01; do
  ssh_vm "$vm" sudo systemctl restart assessment-agent
done
```

원인: C 에이전트 publish 루프에 `connect_robust` 자동 재연결 없음. exit하지 않고 silent retry만 하므로 systemd `Restart=on-failure`도 트리거 안 됨.

### VM 시간 동기화

`collected_at`은 VM 로컬 시각. 호스트와 어긋나면 차트 시간축 안 맞음. 도메인 XML `<clock offset='utc'/>` 로 RTC=UTC 동기화이나 장시간 정지 후 재개 시 어긋날 수 있음.

```bash
for vm in app-server-01 data-server-01 edge-server-01; do
  ssh_vm "$vm" sudo bash -c 'systemctl restart systemd-timesyncd 2>/dev/null || systemctl restart chronyd'
done
```

### 에이전트 로그 확인

```bash
ssh_vm app-server-01 sudo journalctl -u assessment-agent --no-pager -n 50
```

이후 60초 주기 publish 로그가 추가돼야 정상. 멈춰 있으면 broker 재연결 실패 의심.

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| base cloud image 다운로드 실패 | 네트워크 / mirror 일시 오류 | 재시도 (vol-upload 까지 멱등) |
| VM start `Permission denied` (디스크 파일) | qemu(apparmor) 프로파일에 디스크 경로 미등록 또는 풀 디렉토리 0700 | 도메인 disk `type=file` 확인 + 풀 0711 (`ensure_libvirt_ready` 보장) |
| VM 부팅 OK 인데 hostname=localhost·SSH 안 됨 | cloud-init 이 seed(cidata) 미인식 — cdrom bus 문제 | cdrom `bus='ide'` 확인 (i440fx 네이티브) |
| 에이전트 publish 실패 로그 (CONNREFUSED) | host docker rabbitmq 안 떠 있음 / 게이트웨이 도달 실패 | `docker compose ps rabbitmq` + VM 안 `curl -m3 http://192.168.122.1:5672` (또는 fallback `sudo iptables -I DOCKER-USER -i virbr0 -j ACCEPT`) |
| consumer가 metrics 받지만 server_inventory 비어 있음 | inventory 메시지 유실 (broker 재기동 등) | VM 안 `systemctl restart assessment-agent` |
| post-provision SSH unreachable | agent VM 미기동 / IP 변경 | `virsh list --all` + `virsh domifaddr <vm> --source lease` 확인 |

### 개별 VM 조작

```bash
VMS_FILTER=app-server-01 ./dev/dev-up.sh             # 단일 VM 약식 검증
virsh list --all                                     # VM 상태
virsh domifaddr app-server-01 --source lease         # VM IP (DHCP lease)
virsh console app-server-01                          # 시리얼 콘솔 (부팅 디버깅)
virsh shutdown app-server-01                         # graceful 정지
virsh destroy app-server-01                          # 강제 정지 (제거 X, shut off)
virsh undefine app-server-01 --remove-all-storage    # 도메인+디스크 제거 (base 보존)
```
