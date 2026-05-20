# 파이프라인 검증 (Lima)

본 문서는 dev 시연·파이프라인 검증 단일 진실. 운영자 절차·VM 매트릭스·OS 다양성·합성 부하·provisioning·누적 사고 패턴·운영 디버깅 모두 포함. macOS + Lima + Apple Virtualization Framework 한정 (CLAUDE.md #A0).

에이전트(C) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 실제 VM 환경에서 검증 + 시연용 분류·attention 분포 가시화.

```
HOST MACHINE
  Docker Compose (assessment-engine)
    FastAPI :8000  <----QUERY-----  PostgreSQL :5432
                                         ^
                                    PERSIST | (3)
                                         |
    RabbitMQ :5672 ---DISPATCH(2)---> Consumer
         ^
         | PUBLISH (1) Target: host.lima.internal
         |
  Lima VM x 7 (assessment-agent.service)
    web · offline · app · monitor · mq · cache · db
```

## 사전 요구

| 도구 | 설치 |
|------|------|
| Lima 1.0+ | `brew install lima` |
| Docker | Docker Desktop 또는 colima |

전제: agent 바이너리 (`dev/bin/assessment-agent`) 는 pipeline-up.sh 의 `ensure_agent_binary` 단계가 자동 확보. 두 분기:
- `AGENT_BINARY_URL` env set 시 — curl fetch (향후 agent CI release artifact 자동화).
- 미설정 시 — `dev/agent-build/build.sh` 호출, sibling repo (`AGENT_REPO_PATH`, default `../assessment-agent`) 를 buildx context 로 cross-build.

상세: `dev/README.md`.

## 실행

```bash
cp dev/.env.example dev/.env               # 엔진 환경변수 (dev compose 한정)
cp dev/agent.env.example dev/agent.env     # 에이전트 secret 채널 (분리됨, #B)
./dev/pipeline-up.sh                   # Docker → web 헬스체크 → Lima VM 4대
```

`dev/pipeline-up.sh` 4단계:
1. `docker compose up --build -d` — 엔진 기동 (COMPOSE_FILE=dev/docker-compose.yml export)
2. `migrate(alembic upgrade head)` 완료 대기 (cap 180s)
3. web 헬스체크 통과 대기 (cap 180s)
4. `start_or_resume_vm` wrapper로 4 VM 순차 (cloud image 다운로드 포함 최초 3~8분)

## 결과 확인

- http://localhost:8000/servers/ — 4 VM 등록 (offline 은 5m+ 후 offline 표시)
- 60초 주기 메트릭 갱신
- 분류 분포 시연은 `/servers/report?period_days=1` (대시보드는 `recommendation.WINDOW_DAYS=14` 고정, #F10)
- attention 카드 상단 요약: web `agent_unstable` + offline `gap_warnings`(5m+ 후) + db `capacity_warnings` (swap_used)

## 종료

```bash
./dev/pipeline-down.sh   # Lima VM 제거 → Docker 볼륨 삭제 (DB 초기화)
```

부분 종료:

| 시나리오 | 명령 |
|---------|------|
| Docker만 종료, VM 유지 | `docker compose down` (데이터 유지) / `docker compose down -v` (삭제) |
| 특정 VM만 종료 | `limactl delete -f web-server-01` |
| VM 일시 정지 | `limactl stop <vm>` (재기동 시 yaml provision 안 함) |

---

## 사용 맥락

Lima는 에이전트 E2E + 시연 분류 분포 가시화. 엔진(dev compose)은 호스트, Lima 4 VM이 실제 Linux 환경에서 에이전트 metrics를 RabbitMQ에 발행 → Consumer DB 저장 → web UI 확인.

```
[VM: web-server-01      ]   attention.agent_unstable (3분 주기 restart, 시간당 20회)
[VM: offline-server-01  ]   attention.gap_warnings (5m+ 끊김) + insufficient_data
[VM: cache-server-01    ]   over (light 부하)                            -> RabbitMQ -> consumer -> DB -> web UI
[VM: db-server-01       ]   attention.capacity_warnings (swap_used → under_provisioned)
```

4 VM 구성 의도 (호스트 macOS 영향 최소화 우선):
- OS 다양성: 패키지 매니저 분기(apt/dnf) + systemd + cloud-init 호환 검증. 4 distro (Debian 12 · Debian 13 · Rocky 9 · AlmaLinux 9).
- attention 카탈로그 발화: `AttentionSignals` 카테고리 중 3개 (agent_unstable · gap_warnings · capacity_warnings) 의도 발화.
- 합성 부하: 모든 VM light (sustained CPU 1~3s + mem 5~20MB) — 차트 변동만 가시화. CPU 임계 안 넘김. under_provisioned 만 swap_used 트리거로 분류 발화 (CPU 부담 0).

Lima + Apple Virtualization Framework / QEMU 채택 이유: Apple Silicon에서 부팅·메모리 가벼움, macOS 폐쇄망 라이선스 부담 없음(OSS), read-only mount + `/tmp/build` cp 패턴으로 host 빌드 산출물 보호.

---

## VM 매트릭스

`dev/lima/` 디렉토리에 4개 yaml. `pipeline-up.sh`의 `LIMA_VMS` 배열에서 단일 진실로 관리(`pipeline-down.sh`는 `source pipeline-up.sh`로 가져옴).

| 진행 순서 | VM | OS | family | 자원 | 서비스 | 뱃지 | 부하 | 분류 | attention 발화 |
|---|----|----|--------|------|--------|------|------|------|----------------|
| 1 | `web-server-01` | Debian 12 (bookworm) | apt | 1 CPU / 512 MiB / 5 GiB | nginx | web | light | over_provisioned | agent_unstable (1m boot + 3m 주기, 시간당 20회) |
| 2 | `offline-server-01` | Debian 13 (trixie) | apt | 1 CPU / 512 MiB / 5 GiB | (없음) | unknown | (offline-once) | insufficient_data | gap_warnings (5m+ 끊김) |
| 3 | `cache-server-01` | Rocky Linux 9 | dnf | 1 CPU / 1280 MiB / 10 GiB | redis | cache | light | over_provisioned | (분류 도넛에서만) |
| 4 | `db-server-01` | AlmaLinux 9 | dnf | 1 CPU / 1280 MiB / 10 GiB | postgresql-server | db | light + swap-trigger | under_provisioned | capacity_warnings (swap_used) |

진행 순서는 시연 가시화 우선:
- 1번 web — attention 가장 빠른 발화 (1m 후 첫 restart)
- 2번 offline — gap_warnings 5m+ 발화 위해 가장 빨리 stop
- 3번 cache — light 부하 (over_provisioned)
- 4번 db — swap-trigger + RPM postgresql-setup --initdb 자동 (capacity_warnings · under_provisioned)

OS · 뱃지 매트릭스:

| OS | VM | family |
|----|----|----|
| Debian 12 | web | apt |
| Debian 13 trixie | offline | apt |
| Rocky Linux 9 | cache | dnf |
| AlmaLinux 9 | db | dnf |

뱃지 분배 (`service_classifier.py` 카탈로그 부분 커버 — 4 VM 축소로 container · monitor · mq 카테고리 시연은 빠짐):

| 카테고리 | VM | 발화 키워드 |
|----------|----|----|
| web | web-server-01 | nginx |
| cache | cache-server-01 | redis |
| db | db-server-01 | postgresql |
| unknown | offline-server-01 | (서비스 없음) |

리소스 메모:
- 1 CPU 공통 — 최소 자원, cpu_p95 시연 1 코어 기준.
- apt family는 512 MiB / 5 GiB 기본.
- dnf family는 1280 MiB / 10 GiB — dnf install transaction 1 GiB OOM 확인 후 1.25 GiB 보수. disk는 RPM cloud image qcow2 raw 강제.
- db-server-01 의 swap-trigger 는 boot 1회 swapfile 512 MiB 활성 + 1000 MiB 메모리 압박 → swap_used > 0 영구 유지.

---

## 합성 부하 프로파일 (right-sizing 분류 발화)

`recommendation.py`의 USE Method 임계 — 호스트 macOS 영향 최소화 위해 모든 VM 을 light 로 통일. CPU 임계는 일부러 안 넘김 (CPU 부담 회피), swap_used 만 db 에서 트리거.

| 프로파일 | cpu burst | mem burst | 적용 VM | 목표 분류 |
|----------|-----------|-----------|---------|----------|
| light | 1~3s | 5~20MB | web, cache, db | over_provisioned (cpu_p95 ~5%, mem_p95 <50%) |
| swap-trigger (추가) | (boot 1회 1000MB 메모리 압박 + swapfile 512MB 활성) | swap_used > 0 영구 | db | under_provisioned (swap_used short-circuit) |
| (offline-once) | — | — | offline | insufficient_data (1회 발행 후 stop) |

원칙:
- 분류 임계는 `recommendation.py` 모듈 상단 명명 상수 (#E3). 부하 프로파일은 임계 충족 설계.
- `WINDOW_DAYS = 14` (#F10) — dev 시연에서 14일 못 채우면 분류 모두 `insufficient_data`. 보고서 라우터 `?period_days=1` 등 짧은 윈도우 시연 필수.
- swap-trigger 프로파일 — boot 직후 swapfile 512 MiB 활성 + `vm.swappiness=100` + 1000 MiB 메모리 압박. CPU 부담 0, 한 번 swap 에 page push 되면 SwapUsed > 0 영구 유지 → 매 measurement 에서 swap_used = True 안정 발화.
- light 부하는 차트 변동만 가시화. 분류 임계 안 넘김 (over_provisioned 유지).

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

## `start_or_resume_vm` wrapper

lima vz의 cloud-init 느린 distro(Oracle Linux 9 등) 우회용:

```
limactl start <vm> background (PID 보관)
loop (3s polling):
  - SSH ready check (limactl shell echo ok)
  - SSH ready 후 60s+ 경과해도 limactl PID 안 끝나면 → PID kill (final requirement stuck 우회)
  - 절대 cap 5분 — 초과 시 abort
limactl PID 종료(정상 또는 강제 kill) 후 SSH 재검증 → boot 성공 판정
```

`source pipeline-up.sh` 가드 (BASH_SOURCE):
```bash
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
```
`pipeline-down.sh`가 LIMA_VMS만 가져올 때 main 자동 실행 안 함. macOS bash 3.2 호환(`:-` empty default).

---

## 네트워크 구조

Lima default user-mode networking. VM → host는 Lima 자동 등록 DNS alias `host.lima.internal`로.

```
VM (assessment-agent)
  RABBITMQ_HOST=host.lima.internal  ->  host:5672 (docker-compose rabbitmq 포트 매핑)
```

에이전트 `.env`에 `RABBITMQ_HOST=host.lima.internal` 고정. 엔진 `.env`의 `RABBITMQ_HOST`(=`rabbitmq` 도커 서비스명)와 다르며, `pipeline-up.sh`가 VM별 `/etc/assessment-agent.env`를 생성할 때 명시.

VM 간 통신 사용 안 함 — 각 VM은 독립적, 모든 통신은 host RabbitMQ 경유.

---

## Mount 정책

기본 `mountType` 미명시 — Lima vz default(virtiofs) 사용. 4 VM 모두 virtiofs 정상 동작.

```yaml
mounts:
- location: "{{.Param.AgentBinDir}}"
  mountPoint: "/mnt/agent-bin"
  writable: false
```

`{{.Param.AgentBinDir}}` 절대 경로는 `pipeline-up.sh`가 `--set ".param.AgentBinDir = \"$AGENT_BIN_DIR\""`로 주입 (Lima yaml의 `{{.Dir}}`은 instance dir라 호스트 경로 추적 불가).

`writable: false` — read-only mount. `dev/bin/assessment-agent` (pipeline-up.sh 의 `ensure_agent_binary` 가 자동 산출) 만 VM 에서 cp — VM 안 build step·devel 패키지 install 0.

---

## Provisioning 단계

`limactl start --name=<vm> --tty=false --set ...`로 VM 생성. yaml `provision` 섹션 자동 실행 후 `pipeline-up.sh`의 `post_provision_vm`이 limactl shell로 후처리.

### 1. (yaml provision) 합성 부하 timer + (선택) swap 활성화

`/usr/local/bin/synthetic-load.sh` + `synthetic-load.service` + `synthetic-load.timer` yaml별 inline. `OnBootSec=2min`, `OnUnitActiveSec=1min`. offline-server-01만 timer 없음. 모든 VM light 부하 (sustained CPU 1~3s + mem 5~20MB) — 호스트 영향 최소화.

VM 특수 추가:
- `db-server-01` — `swap-trigger.service` (boot 1회 swapfile 512 MiB 활성 + `vm.swappiness=100` + 1000 MiB 메모리 압박 → SwapUsed > 0 영구). attention.capacity_warnings · under_provisioned 분류 발화. CPU 부담 0.
- `web-server-01` — `agent-restart-demo.service` + `agent-restart-demo.timer` (`OnBootSec=1min`, `OnUnitActiveSec=3min` — 시간당 20회 attention.agent_unstable 발화).

### 2. (pipeline-up.sh) `/etc/assessment-agent.env` 생성

`pipeline-up.sh`가 `dev/agent.env` source한 host env로 heredoc 치환:

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
RABBITMQ_WORKER_USER=...
RABBITMQ_WORKER_PASS=...
WORKER_TASK_EXCHANGE=assessment.tasks
WORKER_TASK_QUEUE_PREFIX=agent.tasks
WORKER_TASK_RESULT_KEY=task.result
WORKER_DOWNLOAD_ALLOWED_HOSTS=host.lima.internal
AGENT_HOSTNAME_OVERRIDE=<vm>     # 모든 VM에 vm 이름 그대로 (round 3에서 web-restart-demo override 제거)
AGENT_INTERVAL_SEC=60
AGENT_EXTERNAL_IP=203.0.113.10   # web-server-01만
```

`/etc/`에 두는 이유:
- mount된 `/mnt/agent-bin`은 host와 양방향 → VM별 값 분리 어려움.
- SELinux/AppArmor가 systemd가 사용자 홈 디렉토리 내부 파일을 `EnvironmentFile=`로 읽는 것 차단 가능.
- `/etc/`는 systemd 자유 read + VM 로컬 격리.

### 3. (pipeline-up.sh) OS detect + 서비스 패키지 설치

agent 바이너리는 `ensure_agent_binary` 단계가 `dev/bin/assessment-agent` 로 자동 확보. VM 안에서는 서비스 패키지만 install — devel·gcc·make 불필요.

`/etc/os-release`의 `ID` dispatch:

| family | 명령 |
|--------|------|
| Ubuntu/Debian (apt) | `apt-get install -y --no-install-recommends curl iputils-ping ${svc_pkg}` |
| Rocky 9 / AlmaLinux 9 (dnf) | `dnf install -y epel-release` → `dnf install -y curl iputils ${svc_pkg}` |

agent runtime dynamic dependency = OpenSSL/glibc/zlib만 — 본 매트릭스 distro 모두 base에 포함, 추가 install 불필요.

서비스 dispatch (4 VM):

| VM | service | apt 패키지 | dnf 패키지 | systemd 유닛 |
|----|---------|-----------|-----------|-------------|
| web-server-01 | `nginx` | `nginx` | — | `nginx` |
| offline-server-01 | `none` | (없음) | — | (없음) |
| cache-server-01 | `redis` | — | `redis` | `redis` |
| db-server-01 | `postgres` | — | `postgresql-server` (AlmaLinux 9 — `postgresql-setup --initdb` 자동) | `postgresql` |

dispatch 단일 진실은 `pipeline-up.sh`의 `case "${ID}:$service"` 블록. 새 service 도입 시 본 표 + pipeline-up.sh 동시 갱신 의무.

RPM family postgresql은 cluster init 수동 — `pipeline-up.sh`가 `postgresql-setup --initdb`로 자동 처리. apt 계열은 install 시 자동 init라 skip.

설치된 서비스는 `systemctl enable --now` 즉시 활성화. 에이전트가 `services[]`에 포함시켜 발행 → 엔진의 `service_classifier.classify()`가 카테고리 뱃지로 분류.

### 4. (pipeline-up.sh) 바이너리 cp + systemd unit

```
install -m 755 /mnt/agent-bin/assessment-agent /usr/local/bin/assessment-agent
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

`/usr/local/bin/`으로 복사 — Lima의 9p/virtiofs mount(`/mnt/agent-bin`)에서 systemd 직접 실행 시 SELinux/AppArmor 컨텍스트 충돌 가능. `/usr/local/bin/`은 표준 실행 경로로 통과.

`User=root`로 동작 — 에이전트는 `/proc/*` 전반 read만 필요. Lima default user는 host username으로 잡혀 VM 간 일관성을 위해 root 통일.

### 5. (pipeline-up.sh) finalize_vm (조건 분기)

`vm_mode` 함수 dispatch:
- `offline-server-01` → `offline-once`: inventory 1회 발행 대기 15s → `systemctl stop assessment-agent` + `systemctl disable assessment-agent` + `limactl stop`. 5분 후 attention.gap_warnings 발화 (시연 의도).
- 그 외 → `persistent`: agent restart로 publish 계속.

---

## 누적 사고 패턴 (반면교사 — 도입 검증 round에서 발견·해결)

| # | 문제 | 원인 | 해결 |
|---|------|------|------|
| 1 | cache-server-01 Rocky 8 aarch64 boot stuck (4분간 SSH 안 열림, serial.log 비어있음) | Rocky 8 aarch64 cloud image와 lima vz driver boot 호환 — Lima 공식 examples엔 Rocky 9만 검증 | Rocky 9 fallback (image URL `8` → `9`) |
| 2 | mq-server-01 openSUSE Leap 15 virtiofs mount silent fail (`/mnt/agent-src` 빈 directory) | guest virtiofs kernel module 미동작 (kernel 6.4+ 임에도) | yaml에 `mountType: "reverse-sshfs"` 명시 — sshfs binary 자동 install (lima ensureRequirement) |
| 3 | mq-server-01 zypper `libcjson-devel` not found | openSUSE 패키지명이 대문자 `cJSON-devel` (Debian/RHEL의 `libcjson-devel`과 다른 명명) | pipeline-up.sh zypper 분기에 `cJSON-devel` 명시 |
| 4 | monitor-server-01 dnf install exit 137 (SIGKILL OOM) | CentOS Stream 9 + EPEL 9 + zabbix-agent install transaction이 1280 MiB 초과 | yaml provision Step 1에 swap file 256 MiB 활성 + post-install `swapoff/swapon` reset (under 안 발화) |
| 5 | monitor-server-01 `golang-github-prometheus-node-exporter` EPEL 9 미존재 | EPEL 9에 prometheus exporter 패키지 자체 없음 (EPEL 8엔 있었음) | service `node_exporter` → `zabbix_agent` (pipeline-up.sh dispatch + service_classifier "zabbix" → monitor 매칭) |
| 6 | offline-server-01 Ubuntu 20.04 cloud image 다운로드 timeout (90s 안 안 끝남) | Ubuntu 20.04 cache miss + 네트워크 환경 | Oracle Linux 9 fallback 시도 |
| 7 | offline-server-01 OL9 EPEL GPG check FAILED | epel-release 설치 후에도 GPG key 자동 import 안 됨 | pipeline-up.sh ol:* 분기에 `rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-${os_major}` 명시 |
| 8 | offline-server-01 OL9 disk size error (16 GiB image vs 10 GiB yaml) | OL9 KVM image qcow2 raw 16 GiB 강제 | yaml disk 16 GiB로 늘림 |
| 9 | offline-server-01 OL9 cloud-init "boot scripts must finished" 5분+ stuck | OL9 cloud-init final 단계 lima vz와 호환 — SSH는 정상 ready | pipeline-up.sh `start_or_resume_vm` wrapper — SSH ready+60s 후 limactl PID kill하고 진행 |
| 10 | offline-server-01 OL9 자체 boot 시간 부담 + AWS 특화 OS 부적합 | OL9 자체가 시연 가치 약함 | Debian 13 (trixie) fallback — Lima 공식 검증, ~30~45s boot, apt 재사용 |
| 11 | pipeline-down.sh가 LIMA_VMS 3 VM hardcoded (cache/app/web) — 7 VM과 sync 안 됨 | 옛 hardcoded LIMA_VMS 잔재 | pipeline-down.sh를 `source pipeline-up.sh`로 LIMA_VMS 단일 진실로 변경 |
| 12 | pipeline-up.sh source 시 `BASH_SOURCE[0]: parameter not set` (zsh 환경) | zsh에 BASH_SOURCE array 없음 + pipeline-up.sh의 main 호출 가드 누락 | bash subshell 명시 + source guard `if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then main "$@"; fi` 추가 |
| 13 | monitor-server-01 (CentOS Stream 9) dnf install 시 `baseos` repomd.xml sha512 mismatch — "Downloading successful, but checksum doesn't match. All mirrors were tried" | mirrors.centos.org metalink 응답 expected hash가 mirror 풀의 실제 repomd.xml과 구조적으로 불일치 (2 stale 버전이 deterministic하게 반복 수신). `fastestmirror=False` + `dnf makecache --refresh` 3회 retry 모두 동일 실패 (mirror lag 일시 현상 아님) | distro 교체 — monitor-server-01을 Rocky 9으로 fallback. dl.rockylinux.org mirror 인프라가 별개라 metalink expected 일관성 OK. cache-server-01과 family 1회 중복은 OS 다양성 매트릭스 7→6+1중복으로 진술 정정 |
| 14 | mq-server-01 (openSUSE Leap 15) post-provision 진입 직후 zypper exit 7 — "System management is locked by the application with pid <N> (zypper). Close this application before trying again." | Lima cloud-init이 첫 boot 시 system 갱신을 위해 zypper를 점유 (zypp.lock). pipeline-up.sh가 즉시 zypper refresh/install 호출하면 lock fail | (1차) `zypper --lock-timeout`은 해당 옵션 자체 미존재(exit 2). (2차) heredoc 헤더에 `cloud-init status --wait` 추가 — VM에서 cloud-init이 PATH로 안 잡혀 silent skip, mq boot 78s 동안 lock 자연 해소되어 통과했으나 보장 없음 + stderr noise. (3차) pipeline-up.sh zypper 분기에 `pgrep -x zypper` lock retry loop(10회 x 10s) 추가 — distro-specific lock mechanism 직접 polling. heredoc 본문 line 335 주석의 백틱(`` ` ``)이 host bash command substitution을 발동시키는 부산 noise도 ASCII quote로 교체. (사고 #15에서 distro 자체가 교체되어 zypper 분기 자체가 dead code로 제거됨) |
| 15 | mq-server-01 (openSUSE Leap 15) zypper install 중 `Retrieving: glibc-devel-...rpm [.not found]` — 패키지 메타데이터엔 있지만 mirror 풀 일부가 .rpm 파일 sync 안 됨. zypper 자체 fallback으로 다른 mirror 시도 결국 통과했으나 매번 보장 없음 + 사고 #2 (virtiofs silent fail) + 사고 #3 (`cJSON-devel` 명명 차이) + 사고 #14 (cloud-init zypper hold) 누적 3중 불안정 | openSUSE Leap 15 + 일부 mirror의 update repo sync lag (CentOS Stream 9 사고 #13과 유사한 패턴). zypper retry/refresh 보강만으로는 deterministic 안 됨 | distro 교체 — mq-server-01을 Debian 12로 fallback. 사용자 결정: OS 다양성(zypper family 검증)보다 프로비저닝 안정성 우선 (apt + dpkg + cloud.debian.org mirror 가장 견고). 영향 반영: pipeline-up.sh zypper 분기 + opensuse 매칭 dead code 제거, mq yaml mountType reverse-sshfs 제거(virtiofs default), lima.md OS 다양성 매트릭스 7→5+(web·mq 중복, monitor·cache 중복), mq family memory note·dispatch 카탈로그 zypper 컬럼 정리, 사고 #2·#3 역사 기록 유지 |

15 사고 패턴 → 진행 검증 사이클 + 단계별 fix → 7 VM round 검증. 이후 호스트 macOS 영향 최소화 우선으로 4 VM 매트릭스 축소 (web · offline · cache · db). 사고 #2·#3·#4·#5·#13·#14·#15 의 mq · monitor 관련 distro/패키지 분기는 dead code 로 제거됨 — 본 사고 기록은 historical artifact.

---

## 운영 노트 / 트러블슈팅

### broker 재기동 시 에이전트 수동 재시작 (CRITICAL)

증상: docker compose RabbitMQ를 down/up 또는 `down -v` 후 재기동하면 VM 안 C 에이전트가 broker 재연결 silent 포기. systemd 상태는 `active(running)`이지만 publish 로그 끊김.

대응:
```bash
for vm in web-server-01 offline-server-01 cache-server-01 db-server-01; do
  limactl shell "$vm" sudo systemctl restart assessment-agent
done
```

원인: C 에이전트 publish 루프에 `connect_robust` 자동 재연결 없음. exit하지 않고 silent retry만 하므로 systemd `Restart=on-failure`도 트리거 안 됨.

### VM 시간 동기화

`collected_at`은 VM 로컬 시각. 호스트와 어긋나면 차트 시간축 안 맞음. Lima default 호스트 동기화이지만 장시간 절전·suspend 후 재개 시 어긋날 수 있음.

```bash
for vm in web-server-01 offline-server-01 cache-server-01 db-server-01; do
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
| `ensure_agent_binary` (sibling repo cross-build, 첫 실행) | 3–5분 |
| `docker compose up --build -d` (첫 빌드) | 60–120s |
| web 헬스체크 통과 | 5–10s |
| 4 VM cloud image 다운로드 (cache miss 가정) | 3–8분 |
| 4 VM cloud image (모두 캐시) | 0~10s |
| VM 당 boot + post-provision (cache hit 후) | 30~120s |
| 에이전트 첫 inventory 도달 | 즉시 |
| 첫 metrics 차트 그려짐 (delta 계산용 2회 readings) | 60–90초 |

cache hit 후 전체 [4/4] 단계 약 5분.

### 흔한 트러블

| 증상 | 원인 | 해결 |
|------|------|------|
| `limactl start` cloud image 다운로드 실패 | 네트워크 / mirror 일시 오류 | 재시도 |
| `ensure_agent_binary` 단계 OpenSSL configure 실패 (`-m64` unrecognized) | sibling agent repo Makefile 의 OpenSSL Configure 가 host arch 분기 누락 (linux-x86_64 hard-coded) | agent repo Makefile 의 Configure target 을 host arch 분기로 수정 |
| 에이전트 publish 실패 로그 (CONNREFUSED) | host docker rabbitmq 안 떠 있음 / host.lima.internal 해석 실패 | `docker compose ps rabbitmq` + `limactl shell <vm> getent hosts host.lima.internal` 확인 |
| consumer가 metrics 받지만 server_inventory 비어 있음 | inventory 메시지 유실 (broker 재기동 등) | VM 안 `systemctl restart assessment-agent` |
| OL9·기타 cloud-init 느린 distro 에서 limactl start 5분+ stuck | lima final requirement(`boot scripts must finished`) 가 distro 호환성 문제 | pipeline-up.sh `start_or_resume_vm` wrapper 가 SSH ready+60s 후 PID kill 로 자동 우회 |

### 개별 VM 조작

```bash
limactl start web-server-01                          # 단일 VM 기동 (yaml 등록된 상태)
limactl shell web-server-01                          # SSH 접속
limactl shell web-server-01 sudo <cmd>               # root 명령
limactl stop -f web-server-01                        # 강제 종료 (제거 X)
limactl delete -f web-server-01                      # 제거
limactl list                                         # VM 상태 표
```

단일 VM 시나리오는 `pipeline-up.sh`의 `LIMA_VMS` 배열 일부 항목 주석 처리.
