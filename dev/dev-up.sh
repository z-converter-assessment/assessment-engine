#!/usr/bin/env bash
# dev-up.sh: 전체 dev 환경 기동 단일 진입점 — 의존성 자동설치 + Docker + libvirt(KVM) Linux VM 5대
#            + Windows VM. 인자 없이 실행하면 모든 환경 구성, 정리는 dev-down.sh.
#
# LLM 서버(ollama)는 두지 않는다 — AI 진단 발행 시 LLM 호출 실패 시나리오를 의도적으로 재현
# (docker-compose.yml diagnostic-worker 주석 참조).
#
# 책임 분담:
#   - agent 바이너리는 dev/bin/assessment-agent 로 산출 — ensure_agent_binary 가 확보.
#     AGENT_BINARY_URL set 시 curl fetch, 미설정 시 dev/agent-build/build.sh (sibling repo cross-build).
#   - Docker compose 는 루트 docker-compose.yml (dev·퀵스타트 단일 파일, ADR 0033). migrate init-container 가 alembic 자동 적용.
#   - Linux VM 은 cloud image qcow2 vol-clone + cloud-init seed + virsh define 도메인 XML 로 생성
#     (virt-install 의 python3-gi 의존 회피). VM 안 패키지·바이너리·systemd·합성 부하는 post-provision.
#   - Windows VM 은 autounattend 무인 설치 후 골든 이미지 캐시 (docs/development/windows-vm.md).
#
# 네트워크 모델: Docker(docker0 bridge) 와 libvirt(virbr0 NAT, 192.168.122.0/24) 는 분리된 두 망.
#   - 연결 URI: qemu:///system (LIBVIRT_DEFAULT_URI export). ubuntu 유저는 libvirt 그룹 멤버라 sudo 불요.
#   - VM 명령: cloud-init 이 유저 dev(NOPASSWD sudo) + dev SSH 공개키 주입 -> ssh dev@<VM_IP>.
#     VM IP 는 libvirt DHCP lease 에서 동적 확인 (virsh domifaddr --source lease).
#   - VM -> host 도달: libvirt NAT 게이트웨이 IP(LIBVIRT_GW, 기본 192.168.122.1). agent RABBITMQ_HOST 가
#     이 IP 를 가리킨다. WORKER_DOWNLOAD_ALLOWED_HOSTS 는 agent.env 값(엔진 ZDM_DEFAULT_IP host 와 일치).
#   - 컨테이너 -> host: docker-compose web extra_hosts host.docker.internal:host-gateway.
#   - 컨테이너(web) -> VM(서버 발견 probe :22): 운영자가 VM IP 직접 입력 (컨테이너에서 VM hostname DNS 미해석).
#
# 멱등성: 모든 단계 안전 재실행. VM 이미 있으면 define 건너뜀, post-provision 은 매번 재적용.

set -euo pipefail

# 모든 virsh/볼륨 호출이 system libvirtd 를 향하게 고정 — 명령마다 `-c qemu:///system` 반복 회피.
export LIBVIRT_DEFAULT_URI="${LIBVIRT_DEFAULT_URI:-qemu:///system}"
# IDE 터미널(PyCharm 등)은 SSH_ASKPASS/GIT_ASKPASS 를 주입해 ssh/git 이 인증 입력 시 IDE GUI 팝업을
# 띄운다(독립 터미널엔 없음). 자동화 스크립트라 GUI 가 필요 없으므로 해제 — 어느 터미널이든 동일 동작.
unset SSH_ASKPASS SSH_ASKPASS_REQUIRE GIT_ASKPASS 2>/dev/null || true

# 호출 위치 무관 정합 — scripts/.. = 프로젝트 루트로 cwd 고정.
# BASH_SOURCE는 source된 스크립트 자체 경로 (직접 실행 시 $0과 동일) — source 시 부모의 $0으로 잘못 cd하지 않게.
cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.."

# 루트 docker-compose.yml 단일 compose (dev·퀵스타트 겸용, ADR 0033) — `docker compose` 호출이 자동 인식.
export COMPOSE_FILE=docker-compose.yml
# 프로젝트명 고정 — compose 파일이 루트라 기본 프로젝트명이 루트 디렉토리명이 되지만, dev 컨테이너/볼륨
# 네임스페이스를 `dev` 로 안정화(퀵스타트 bare `docker compose up` 과 분리, dev-down 연속성 보장).
export COMPOSE_PROJECT_NAME=dev
# dev 파이프라인은 dev/.env(dev 카탈로그)를 compose env_file 로 인식 — compose 의 ${ENV_FILE:-.env} 분기.
# (퀵스타트 bare `docker compose up` 은 ENV_FILE 미설정 -> 루트 .env.)
export ENV_FILE=dev/.env

# ────────────────────────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────────────────────────
readonly TIMEOUT=180                         # docker compose / migrate / web 헬스체크 공통 cap

# libvirt 토폴로지 단일 진실.
readonly LIBVIRT_NET="${LIBVIRT_NET:-default}"     # NAT 네트워크 (virbr0)
readonly LIBVIRT_POOL="${LIBVIRT_POOL:-default}"   # storage 풀 (/var/lib/libvirt/images)
# 풀 target 경로 — ensure_libvirt_ready 가 채움. 도메인 XML 이 disk 를 type='file' + 명시 경로로 참조해야
# virt-aa-helper(apparmor)가 디스크 경로를 프로파일에 등록 (type='volume' 은 미해석 -> 빈 프로파일 -> qemu 접근 거부).
POOL_PATH=""
# VM -> host 도달 게이트웨이 — default net 의 <ip address>. resolve_libvirt_gw 가 동적 확인, 실패 시 fallback.
LIBVIRT_GW=""
# base cloud image qcow2 캐시는 libvirt 풀 안 `<distro>-base.qcow2` 볼륨 (qemu:///system 정석 — qemu-user
# 가 홈/임시 경로를 traverse 못 하는 문제 회피, 모든 디스크를 풀에서 관리).
# 본 repo dev 전용 SSH keypair — cloud-init 으로 VM 에 공개키 주입, vm_ssh 가 개인키로 접속. (.gitignore)
readonly DEV_SSH_KEY="dev/.ssh/id_dev"
readonly VM_SSH_USER="${VM_SSH_USER:-dev}"
# cloud-init seed / 디스크 작업용 host 임시 디렉토리 (풀 업로드 전 staging).
readonly VM_WORK_DIR="dev/run"

# VM 매트릭스 — 1 VM = 2 서비스 (service_classifier 6 카테고리 최대 커버).
# Windows(win-server-01: IIS+redis)는 별도 흐름 (docs/development/windows-vm.md). 본 배열은 Linux 5대.
#
#   (1) app-server-01   — Debian 12 + nginx + rabbitmq (web + mq)
#                         agent-restart-demo timer (1m boot + 3m 주기) → attention.agent_unstable
#                         external IP 부여 (web-facing) — IP 분류 시연
#   (2) data-server-01  — Rocky 9  + postgresql + zabbix-agent (db + monitor)
#   (3) edge-server-01  — Debian 12 + docker + memcached (container + cache)
#                         offline-demo → N회(기본 3) 발행 후 VM poweroff → attention.gap_warnings
#   (4) offline-server-01 — Debian 12, 서비스 없음. offline-demo (최초 메트릭 발행 후 poweroff)
#   (5) offline-server-02 — Debian 12, 서비스 없음. offline-demo
#                         (4)(5) 는 오프라인 표시 + 서버목록 행 채우기(총 6대 → "더보기" 발현) 용도.
# VMS_FILTER env로 약식 검증 가능 (콤마 구분, 예: `VMS_FILTER=app-server-01`).
# 호스트 영향 최소화 위해 모든 VM 의 합성 부하는 light (sustained CPU 1~3s + mem 5~20MB).
if [ -n "${VMS_FILTER:-}" ]; then
  IFS=',' read -ra VMS <<< "$VMS_FILTER"
else
  VMS=(app-server-01 data-server-01 edge-server-01 offline-server-01 offline-server-02)
fi
readonly VMS

# VM별 dispatch — services/ext_ip/distro 는 본 스크립트(아래 vm_* 함수)가 단일 진실.
# vm_service 는 공백 구분 다중 서비스 — post_provision_vm 이 for-loop 으로 각각 설치.
vm_service() {
  case "$1" in
    app-server-01)   echo "nginx rabbitmq" ;;
    data-server-01)  echo "postgres zabbix" ;;
    edge-server-01)  echo "docker memcached" ;;
    offline-server-01|offline-server-02) echo "" ;;  # 서비스 없음 — agent 만 (오프라인 시연·행 채우기)
    *) echo "오류: 알 수 없는 VM: $1" >&2; return 1 ;;
  esac
}
vm_ext_ip() {
  case "$1" in
    app-server-01) echo "203.0.113.10" ;;
    *)             echo "" ;;
  esac
}
# distro key — base cloud image 선택. 서비스 설치 난이도 기준 배치: docker.io·rabbitmq-server 는
# apt(debian) 가 단순, postgresql-server·zabbix-agent(EPEL)는 dnf(rocky). apt/dnf 양 family 검증 유지.
vm_distro() {
  case "$1" in
    app-server-01)   echo "debian12" ;;
    data-server-01)  echo "rocky9" ;;
    edge-server-01)  echo "debian12" ;;
    offline-server-01|offline-server-02) echo "debian12" ;;  # 가벼운 base — 서비스 없이 agent 만
    *) echo "오류: 알 수 없는 VM: $1" >&2; return 1 ;;
  esac
}
# distro key -> amd64 cloud image (qcow2, cloud-init NoCloud 지원). genericcloud = 최소 패키지 + cloud-init.
vm_image_url() {
  case "$1" in
    debian12) echo "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2" ;;
    rocky9)   echo "https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2" ;;
    *) echo "오류: 알 수 없는 distro: $1" >&2; return 1 ;;
  esac
}
# distro key -> 풀 안 base 볼륨 이름 (1회 import 후 overlay backing 으로 공유).
vm_base_vol() { echo "$1-base.qcow2"; }

# probe 도달 시연 — web 컨테이너(docker0) -> VM(virbr0) 는 host 라우팅 경유. VM IP 가 동적이고
# 컨테이너에서 VM hostname DNS 해석이 없어 운영자가 VM IP 를 모달에 직접 입력 (print_summary 안내).
# DISCOVERY_DEFAULT_TARGET 는 docker-compose default 빈값 유지.

# dev/agent.env 필수 키 (agent.env.example과 단일 진실).
readonly REQUIRED_AGENT_KEYS=(
  RABBITMQ_USER
  RABBITMQ_PASSWORD
  RABBITMQ_EXCHANGE
  RABBITMQ_ROUTING_KEY_INVENTORY
  RABBITMQ_ROUTING_KEY_METRICS
  RABBITMQ_ROUTING_KEY_ERROR
  RABBITMQ_WORKER_USER
  RABBITMQ_WORKER_PASSWORD
  WORKER_TASK_EXCHANGE
  WORKER_TASK_QUEUE_PREFIX
  WORKER_TASK_RESULT_KEY
  WORKER_DOWNLOAD_ALLOWED_HOSTS
)

# ────────────────────────────────────────────────────────────────────────────
# Docker compose helpers (docker inspect 기반 정확 상태 read)
# ────────────────────────────────────────────────────────────────────────────
service_state() {
  local cid
  cid="$(docker compose ps -aq "$1" 2>/dev/null | head -1)"
  [ -z "$cid" ] && { echo "missing"; return; }
  docker inspect --format='{{.State.Status}}' "$cid" 2>/dev/null || echo "missing"
}
service_health() {
  local cid
  cid="$(docker compose ps -aq "$1" 2>/dev/null | head -1)"
  [ -z "$cid" ] && { echo "none"; return; }
  docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo "none"
}
service_exit_code() {
  local cid
  cid="$(docker compose ps -aq "$1" 2>/dev/null | head -1)"
  [ -z "$cid" ] && { echo "-1"; return; }
  docker inspect --format='{{.State.ExitCode}}' "$cid" 2>/dev/null || echo "-1"
}

dump_logs_and_exit() {
  echo "--- migrate 로그 (마지막 30줄) ---"
  docker compose logs migrate --tail=30 || true
  echo "--- web 로그 (마지막 30줄) ---"
  docker compose logs web --tail=30 || true
  exit 1
}

# ────────────────────────────────────────────────────────────────────────────
# Step 1: 사전 점검
# ────────────────────────────────────────────────────────────────────────────
# 의존성 자동 설치 — "실행만 하면 구성" 원칙(sudo NOPASSWD 전제). "cmd:pkg1,pkg2" 목록을 받아
# cmd 부재 시 해당 패키지를 모아 1회 apt install. cmd 존재 시 skip(이미 설치 = 캐시).
ensure_apt_packages() {
  local spec cmd pkgs need=()
  for spec in "$@"; do
    cmd="${spec%%:*}"; pkgs="${spec#*:}"
    command -v "$cmd" >/dev/null 2>&1 || need+=(${pkgs//,/ })
  done
  if [ ${#need[@]} -gt 0 ]; then
    echo "  의존성 자동 설치 (sudo apt): ${need[*]}"
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${need[@]}"
  fi
}

check_prereqs() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "오류: docker가 PATH에 없다."
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "오류: docker daemon 미가동. Docker 기동 후 재시도."
    exit 1
  fi
  # libvirt 툴체인 자동 설치 — virsh(도메인·볼륨 API) + cloud-localds(NoCloud seed) +
  # curl(cloud image fetch) + qemu(KVM). 부재 시 sudo apt 로 자동 설치.
  ensure_apt_packages virsh:libvirt-clients,libvirt-daemon-system,qemu-system-x86 \
                       cloud-localds:cloud-image-utils curl:curl
  # qemu:///system 접근 검증 — 실패 시 대개 libvirt 그룹 미반영(재로그인 필요) 또는 libvirtd 미가동.
  if ! virsh version >/dev/null 2>&1; then
    echo "오류: qemu:///system 접근 실패. (1) libvirtd 가동: systemctl status libvirtd" >&2
    echo "      (2) 본 유저가 libvirt 그룹? id -nG. 방금 추가했다면 재로그인 또는 'newgrp libvirt' 후 재시도." >&2
    exit 1
  fi
  ensure_libvirt_ready
  resolve_libvirt_gw
  ensure_dev_ssh_key
  # x86_64 전제 (homeserver). agent 빌드(build.sh)는 host arch 자동 매핑이라 별도 처리 불요.
  if [ "$(uname -m)" != "x86_64" ]; then
    echo "경고: 본 dev 파이프라인은 x86_64 + amd64 cloud image 가정. 다른 host arch 는 image URL·바이너리 검토 필요."
  fi
  # env 파일 자동 cp — dev 작업파일 dev/.env(dev 카탈로그 복사본)을 dev/ 안에 둔다.
  # compose 는 ENV_FILE=dev/.env 로 본 파일을 env_file 인식 (퀵스타트는 루트 .env, ADR 0033).
  if [ ! -f dev/.env ]; then
    echo "  dev/.env 없음 — dev/.env.example 복사"
    cp dev/.env.example dev/.env
  fi
  if [ ! -f dev/agent.env ]; then
    echo "  dev/agent.env 없음 — dev/agent.env.example 복사"
    cp dev/agent.env.example dev/agent.env
  fi
}

# default 네트워크·storage 풀 active 보장 (autostart 미설정·정지 상태 멱등 복구).
ensure_libvirt_ready() {
  if ! virsh net-info "$LIBVIRT_NET" >/dev/null 2>&1; then
    echo "오류: libvirt 네트워크 '$LIBVIRT_NET' 없음. 'virsh net-define' 또는 libvirt 기본 설치 확인." >&2
    exit 1
  fi
  virsh net-list --name 2>/dev/null | grep -qx "$LIBVIRT_NET" || virsh net-start "$LIBVIRT_NET" >/dev/null
  virsh net-autostart "$LIBVIRT_NET" >/dev/null 2>&1 || true
  # storage 풀 — 없으면 정의(기본 경로 /var/lib/libvirt/images). qemu:///system 정석.
  # target permissions 0711 명시 — qemu(libvirt-qemu)가 풀 디렉토리를 traverse 해 디스크 파일 접근.
  # (Ubuntu 일부 버전은 디렉토리 기본 0700 -> traverse 불가 -> qemu Permission denied.)
  if ! virsh pool-info "$LIBVIRT_POOL" >/dev/null 2>&1; then
    echo "  storage 풀 '$LIBVIRT_POOL' 없음 — define (/var/lib/libvirt/images, mode 0711)"
    local pool_xml="${TMPDIR:-/tmp}/_assessment_pool_$$.xml"
    cat > "$pool_xml" <<POOLXML
<pool type='dir'>
  <name>${LIBVIRT_POOL}</name>
  <target>
    <path>/var/lib/libvirt/images</path>
    <permissions><mode>0711</mode><owner>0</owner><group>0</group></permissions>
  </target>
</pool>
POOLXML
    virsh pool-define "$pool_xml" >/dev/null
    virsh pool-build "$LIBVIRT_POOL" >/dev/null 2>&1 || true
    rm -f "$pool_xml"
  fi
  virsh pool-list --name 2>/dev/null | grep -qx "$LIBVIRT_POOL" || virsh pool-start "$LIBVIRT_POOL" >/dev/null
  virsh pool-autostart "$LIBVIRT_POOL" >/dev/null 2>&1 || true
  # 풀 target 경로 확보 — 도메인 XML disk source file 경로에 사용.
  POOL_PATH="$(virsh pool-dumpxml "$LIBVIRT_POOL" 2>/dev/null \
    | grep -oE '<path>[^<]+</path>' | head -1 | sed 's/<[^>]*>//g')"
  POOL_PATH="${POOL_PATH:-/var/lib/libvirt/images}"
}

# VM -> host 게이트웨이 IP — default net 의 <ip address>. agent 가 RABBITMQ_HOST 로 이 IP 를 가리킨다.
resolve_libvirt_gw() {
  LIBVIRT_GW="$(virsh net-dumpxml "$LIBVIRT_NET" 2>/dev/null \
    | grep -oE "ip address='[0-9.]+'" | head -1 | grep -oE '[0-9.]+' || true)"
  LIBVIRT_GW="${LIBVIRT_GW:-192.168.122.1}"
  echo "  libvirt 게이트웨이(VM->host): $LIBVIRT_GW"
}

# dev 전용 SSH keypair — 없으면 생성 (passphrase 없음, dev 한정). cloud-init 이 공개키를 VM 에 주입.
ensure_dev_ssh_key() {
  if [ ! -f "$DEV_SSH_KEY" ]; then
    echo "  dev SSH key 없음 — 생성 ($DEV_SSH_KEY)"
    mkdir -p "$(dirname "$DEV_SSH_KEY")"
    ssh-keygen -t ed25519 -N "" -C "assessment-dev" -f "$DEV_SSH_KEY" >/dev/null
  fi
}

# agent 바이너리 확보 — AGENT_BINARY_URL 우선, 미설정 시 sibling repo cross-build.
# 본 분기는 확장 전제: agent CI release artifact 자동화 후 운영자가 AGENT_BINARY_URL 만 set
# 하면 fetch 흐름으로 즉시 전환. 현재는 build 흐름이 default.
ensure_agent_binary() {
  local out_bin="dev/bin/assessment-agent"
  if [ -n "${AGENT_BINARY_URL:-}" ]; then
    echo "  AGENT_BINARY_URL set — fetch (cache 없음, 매 호출마다 검증·갱신)..."
    mkdir -p dev/bin
    if ! curl -fsSL -o "$out_bin" "$AGENT_BINARY_URL"; then
      echo "오류: agent 바이너리 fetch 실패 ($AGENT_BINARY_URL)" >&2
      exit 1
    fi
    chmod 755 "$out_bin"
  else
    echo "  AGENT_BINARY_URL 미설정 — dev/agent-build/build.sh 호출 (sibling repo cross-build)..."
    ./dev/agent-build/build.sh
  fi
  if [ ! -f "$out_bin" ]; then
    echo "오류: $out_bin 확보 실패. AGENT_BINARY_URL 또는 AGENT_REPO_PATH 확인하라." >&2
    exit 1
  fi
}

# dev/agent.env를 host env로 export + 필수 키 검증.
load_agent_env() {
  set -a
  # shellcheck disable=SC1091
  source dev/agent.env
  set +a

  local k
  for k in "${REQUIRED_AGENT_KEYS[@]}"; do
    if [ -z "${!k:-}" ]; then
      echo "오류: dev/agent.env에 ${k} 누락. dev/agent.env.example 참조해 추가하라."
      exit 1
    fi
  done
}

# ────────────────────────────────────────────────────────────────────────────
# Step 2: Docker 스택
# ────────────────────────────────────────────────────────────────────────────
start_docker_stack() {
  echo "[1/4] Docker 서비스 기동 중 (build 포함, postgres healthy 후 migrate 실행)..."
  # hatch-vcs(ADR 0030) 버전 주입 — .git 은 .dockerignore 로 build context 에서 제외되므로
  # host git 으로 derive 한 PEP440 버전을 build arg(APP_VERSION → SETUPTOOLS_SCM_PRETEND_VERSION)로 전달.
  # 예: v0.1.2-3-gf1164d1 → 0.1.2.dev3. tag 없거나 git 아니면 0.0.0 (build 통과 보장).
  APP_VERSION="$(git describe --tags 2>/dev/null | sed 's/^v//; s/-\([0-9]*\)-g.*/.dev\1/' || true)"
  APP_VERSION="${APP_VERSION:-0.0.0}"
  export APP_VERSION
  echo "  APP_VERSION=${APP_VERSION} (hatch-vcs 주입)"
  # discovery probe 좌표는 운영자가 VM IP 직접 입력 (print_summary 안내) — docker-compose default 빈값.
  docker compose up -d --build
}

wait_migrate_completed() {
  echo "[2/4] migrate(alembic upgrade head) 완료 대기 중..."
  SECONDS=0
  while :; do
    local state code
    state="$(service_state migrate)"
    if [ "$state" = "exited" ]; then
      code="$(service_exit_code migrate)"
      if [ "$code" = "0" ]; then
        echo "  migrate 완료 (exit 0)"
        return
      fi
      echo "오류: migrate 실패 (exit ${code})."
      dump_logs_and_exit
    fi
    if [ "$SECONDS" -ge "$TIMEOUT" ]; then
      echo "오류: migrate ${TIMEOUT}초 초과 (현재 state=${state})."
      dump_logs_and_exit
    fi
    sleep 2
    echo "  대기 중... (${SECONDS}s / ${TIMEOUT}s, state=${state})"
  done
}

wait_web_healthy() {
  echo "[3/4] web 헬스체크 대기 중..."
  SECONDS=0
  while [ "$(service_health web)" != "healthy" ]; do
    local health state
    health="$(service_health web)"
    state="$(service_state web)"
    if [ "$state" = "exited" ]; then
      echo "오류: web 컨테이너가 종료됨 (exit $(service_exit_code web))."
      dump_logs_and_exit
    fi
    if [ "$SECONDS" -ge "$TIMEOUT" ]; then
      echo "오류: web 헬스체크 ${TIMEOUT}초 초과 (health=${health})."
      dump_logs_and_exit
    fi
    sleep 3
    echo "  대기 중... (${SECONDS}s / ${TIMEOUT}s, health=${health})"
  done
  echo "  web healthy"
}

# ────────────────────────────────────────────────────────────────────────────
# Step 4: libvirt VM 기동 + 에이전트 설치
# ────────────────────────────────────────────────────────────────────────────

# VM 리소스 — light 부하라 작게. offline-* 는 agent 만 떠서 최소.
vm_memory() {
  case "$1" in
    app-server-01|data-server-01) echo 2048 ;;   # rabbitmq/postgres 여유
    edge-server-01)               echo 1536 ;;   # docker
    *)                            echo 768  ;;    # offline-* (서비스 없음)
  esac
}
vm_vcpu() {
  case "$1" in
    offline-server-01|offline-server-02) echo 1 ;;
    *)                                   echo 2 ;;
  esac
}

# VM IP — libvirt DHCP lease 에서 동적 확인. lease 미발급(부팅 직후)이면 비어 nonzero return.
vm_ip() {
  local vm="$1" ip
  ip="$(virsh domifaddr "$vm" --source lease 2>/dev/null \
    | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1 || true)"
  [ -n "$ip" ] && { echo "$ip"; return 0; }
  return 1
}

# ssh 헬퍼 — DHCP lease 로 IP resolve 후 dev SSH 개인키로 접속. dev 재생성 빈번이라 host key 검증 끔.
vm_ssh() {
  local vm="$1"; shift
  local ip
  ip="$(vm_ip "$vm")" || return 1
  ssh -i "$DEV_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 "${VM_SSH_USER}@${ip}" "$@"
}

# base cloud image 를 풀로 1회 import (vol-upload). 이미 import 됐으면 skip — overlay backing 으로 공유.
ensure_base_image() {
  local distro="$1" vol url tmp sz
  vol="$(vm_base_vol "$distro")"
  if virsh vol-info "$vol" --pool "$LIBVIRT_POOL" >/dev/null 2>&1; then
    return 0
  fi
  url="$(vm_image_url "$distro")"
  echo "  [$distro] base cloud image 다운로드 (1회)..."
  mkdir -p "$VM_WORK_DIR"
  tmp="$VM_WORK_DIR/${vol}.download"
  curl -fSL --retry 3 -o "$tmp" "$url"
  # 볼륨 생성 capacity 는 파일 바이트 — vol-upload 후 libvirt 가 qcow2 헤더의 진짜 virtual size 로 재인식.
  sz="$(stat -c%s "$tmp")"
  echo "  [$distro] 풀로 import (vol-upload, ${sz}B)..."
  virsh vol-create-as "$LIBVIRT_POOL" "$vol" "$sz" --format qcow2 >/dev/null
  # vol-upload 중 SIGINT/실패 시 부분 볼륨 삭제 — 손상된 base 가 캐시로 남아 다음 vol-clone 을
  # 깨뜨리는 것 방지(부분 base 는 dev-down 도 캐시로 보존하므로 여기서 원자성 보장).
  trap 'virsh vol-delete "$vol" --pool "$LIBVIRT_POOL" >/dev/null 2>&1; exit 130' INT TERM ERR
  virsh vol-upload --pool "$LIBVIRT_POOL" --vol "$vol" --file "$tmp"
  trap - INT TERM ERR
  rm -f "$tmp"
}

# overlay 디스크 + cloud-init seed ISO + 도메인 XML 작성 후 virsh define. 모든 디스크는 풀 안 관리.
build_and_define_vm() {
  local vm="$1" distro="$2"
  local base disk seed_iso pubkey memmib vcpu
  base="$(vm_base_vol "$distro")"
  disk="${vm}.qcow2"
  seed_iso="${vm}-seed.iso"
  pubkey="$(cat "${DEV_SSH_KEY}.pub")"
  memmib="$(vm_memory "$vm")"
  vcpu="$(vm_vcpu "$vm")"
  mkdir -p "$VM_WORK_DIR"

  # 1) VM 디스크 — base 볼륨 전체 복사(vol-clone) 후 20G 확장. backing 체인 미사용:
  #    qemu(libvirt-qemu)가 backing 파일을 relabel 못 해 Permission denied 나는 문제 회피 + VM 독립성.
  #    cloud-init growpart 가 부팅 시 파티션을 20G 로 확장. 없을 때만 생성 (멱등).
  if ! virsh vol-info "$disk" --pool "$LIBVIRT_POOL" >/dev/null 2>&1; then
    virsh vol-clone --pool "$LIBVIRT_POOL" "$base" "$disk" >/dev/null
    virsh vol-resize --pool "$LIBVIRT_POOL" "$disk" 20G >/dev/null
  fi

  # 2) cloud-init seed — 유저 dev(NOPASSWD sudo) + dev 공개키 + hostname. NoCloud(cidata) ISO.
  local ud="$VM_WORK_DIR/${vm}-user-data" md="$VM_WORK_DIR/${vm}-meta-data" iso="$VM_WORK_DIR/${seed_iso}"
  cat > "$ud" <<CLOUDCFG
#cloud-config
hostname: ${vm}
fqdn: ${vm}
preserve_hostname: false
users:
  - name: ${VM_SSH_USER}
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - ${pubkey}
ssh_pwauth: false
CLOUDCFG
  printf 'instance-id: %s\nlocal-hostname: %s\n' "$vm" "$vm" > "$md"
  cloud-localds "$iso" "$ud" "$md"
  # 풀로 import (멱등 — 기존 seed 교체해 키/hostname 갱신 반영).
  virsh vol-info "$seed_iso" --pool "$LIBVIRT_POOL" >/dev/null 2>&1 \
    && virsh vol-delete "$seed_iso" --pool "$LIBVIRT_POOL" >/dev/null
  local isz; isz="$(stat -c%s "$iso")"
  virsh vol-create-as "$LIBVIRT_POOL" "$seed_iso" "$isz" --format raw >/dev/null
  virsh vol-upload --pool "$LIBVIRT_POOL" --vol "$seed_iso" --file "$iso"
  rm -f "$ud" "$md" "$iso"

  # 3) 도메인 XML + define. machine type 미지정 — libvirt 기본. virtio disk/net, BIOS 부팅(cloud genericcloud).
  #    disk 는 type='file' + 명시 경로 — virt-aa-helper(apparmor) 가 경로를 qemu 프로파일에 등록 (type='volume' 미해석 회피).
  local xml="$VM_WORK_DIR/${vm}.xml"
  cat > "$xml" <<DOMXML
<domain type='kvm'>
  <name>${vm}</name>
  <memory unit='MiB'>${memmib}</memory>
  <vcpu>${vcpu}</vcpu>
  <os><type arch='x86_64'>hvm</type><boot dev='hd'/></os>
  <features><acpi/><apic/></features>
  <cpu mode='host-passthrough' check='none'/>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='${POOL_PATH}/${disk}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='${POOL_PATH}/${seed_iso}'/>
      <target dev='hda' bus='ide'/>
      <readonly/>
    </disk>
    <interface type='network'>
      <source network='${LIBVIRT_NET}'/>
      <model type='virtio'/>
    </interface>
    <serial type='pty'><target port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
    <graphics type='vnc' port='-1' listen='127.0.0.1'/>
    <video><model type='virtio'/></video>
    <memballoon model='virtio'/>
    <rng model='virtio'><backend model='random'>/dev/urandom</backend></rng>
  </devices>
</domain>
DOMXML
  virsh define "$xml" >/dev/null
  rm -f "$xml"
}

# 멱등 — 도메인 있으면 실행 보장, 없으면 base image 확보 + overlay/seed/도메인 생성 후 start.
start_or_resume_vm() {
  local vm="$1"
  local distro
  distro="$(vm_distro "$vm")"

  if virsh dominfo "$vm" >/dev/null 2>&1; then
    echo "  [$vm] 도메인 존재 — 실행 보장 (멱등)"
    virsh domstate "$vm" 2>/dev/null | grep -q running || virsh start "$vm" >/dev/null
  else
    ensure_base_image "$distro"
    echo "  [$vm] 생성 (overlay + cloud-init seed + define + start)..."
    build_and_define_vm "$vm" "$distro"
    virsh start "$vm" >/dev/null
  fi

  # SSH 도달 검증 — 첫 부팅 cloud-init(유저·키 주입) + DHCP lease 완료까지 여유 cap.
  local secs=0
  until vm_ssh "$vm" echo ok 2>/dev/null | grep -q ok; do
    sleep 3; secs=$((secs+3))
    if [ "$secs" -ge 120 ]; then
      echo "  [$vm] SSH 120s 초과 — boot/cloud-init 실패 ('virsh console $vm' 로 확인)"
      return 1
    fi
  done
  echo "  [$vm] boot OK"
}

# VM 안에서 /etc/assessment-agent.env 생성 + OS detect + 서비스 패키지 설치 + 바이너리 설치 + systemd unit.
#
# 바이너리 전달: host dev/bin/assessment-agent 를 ssh stdin(tee) 으로 VM /tmp 에 옮긴 뒤 SCRIPT 가 cp.
# heredoc 변수 치환 규약:
#   - outer `<<SCRIPT` (unquoted): host shell이 ${RABBITMQ_*}·$vm·$ext_ip·$service 치환 후 VM bash로 전달.
#   - VM 내부 변수($ID/$svc_pkg/$svc_unit)는 `\$` escape — host shell이 손 안 대고 VM bash가 처리.
#   - inner `<<'UNIT'` (quoted): VM bash가 그대로 받음 (systemd unit literal에 '$' 없음).
#   - inner `<<ENV` (unquoted): host expand된 값들을 inline (그 안에 '$' 없음).
post_provision_vm() {
  local vm="$1"
  local services ext_ip hostname_override
  services="$(vm_service "$vm")"   # 공백 구분 다중 서비스 (예: "nginx rabbitmq")
  ext_ip="$(vm_ext_ip "$vm")"
  # 모든 VM은 hostname=VM 이름 통일.
  hostname_override="$vm"

  echo "  [$vm] 바이너리 전송 (ssh stdin)..."
  vm_ssh "$vm" 'cat > /tmp/assessment-agent' < dev/bin/assessment-agent

  echo "  [$vm] post-provision (env + 패키지 + 바이너리 + systemd)..."
  vm_ssh "$vm" sudo bash -s <<SCRIPT
set -euo pipefail

# 1) /etc/assessment-agent.env — host의 dev/agent.env + libvirt 게이트웨이 IP + VM별 hostname/ext_ip.
#    RABBITMQ_HOST 는 VM->host 도달 IP(LIBVIRT_GW), WORKER_DOWNLOAD_ALLOWED_HOSTS 는 agent.env 값
#    (엔진 ZDM_DEFAULT_IP host 와 일치). host shell 이 치환.
#    env_needs_restart=1이면 env 변경됨 → 뒤 단계에서 restart 트리거.
new_env=\$(cat <<ENV
RABBITMQ_HOST=${LIBVIRT_GW}
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/assessment
RABBITMQ_USER=${RABBITMQ_USER}
RABBITMQ_PASS=${RABBITMQ_PASSWORD}
RABBITMQ_EXCHANGE=${RABBITMQ_EXCHANGE}
RABBITMQ_ROUTING_KEY_INVENTORY=${RABBITMQ_ROUTING_KEY_INVENTORY}
RABBITMQ_ROUTING_KEY_METRICS=${RABBITMQ_ROUTING_KEY_METRICS}
RABBITMQ_ROUTING_KEY_ERROR=${RABBITMQ_ROUTING_KEY_ERROR}
RABBITMQ_WORKER_USER=${RABBITMQ_WORKER_USER}
RABBITMQ_WORKER_PASS=${RABBITMQ_WORKER_PASSWORD}
WORKER_TASK_EXCHANGE=${WORKER_TASK_EXCHANGE}
WORKER_TASK_QUEUE_PREFIX=${WORKER_TASK_QUEUE_PREFIX}
WORKER_TASK_RESULT_KEY=${WORKER_TASK_RESULT_KEY}
WORKER_DOWNLOAD_ALLOWED_HOSTS=${WORKER_DOWNLOAD_ALLOWED_HOSTS}
AGENT_HOSTNAME_OVERRIDE=$hostname_override
AGENT_INTERVAL_SEC=60
${ext_ip:+AGENT_EXTERNAL_IP=$ext_ip}
ENV
)
env_needs_restart=0
if [ "\$new_env" != "\$(cat /etc/assessment-agent.env 2>/dev/null)" ]; then
  echo "\$new_env" > /etc/assessment-agent.env
  chmod 644 /etc/assessment-agent.env
  env_needs_restart=1
fi

# 2) base 패키지 + 다중 서비스 설치. services("$services")는 host shell이 inline한 공백 구분 literal.
#    agent 바이너리는 ensure_agent_binary 가 host arch(amd64) 로 빌드/확보 (dev/bin/assessment-agent).
#    devel 패키지·gcc·make 불필요 — runtime OpenSSL/glibc/zlib만 동적 의존이고 모두 base distro 기본 포함.
. /etc/os-release

# dnf_opts — case 밖 정의 (set -u 안전, debian 경로는 미참조). install_weak_deps=False 로
# Recommends 차단(transaction 메모리·디스크 절약, 1GiB OOM 회피), tsflags=nodocs 로 man/info skip.
dnf_opts="--setopt=install_weak_deps=False --setopt=tsflags=nodocs"

# base 패키지 — openssh-server(서버 발견 probe 시연: web 컨테이너 -> VM IP:22) + curl + ping.
# cloud image 는 cloud-init 으로 SSH 가능하나, probe 시연용 표준 22 sshd unit 을 명시 enable.
case "\${ID}" in
  ubuntu|debian)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends curl iputils-ping openssh-server
    systemctl enable --now ssh
    ;;
  rocky|rhel|almalinux)
    dnf install -y \${dnf_opts} epel-release
    dnf install -y \${dnf_opts} curl iputils openssh-server
    systemctl enable --now sshd
    ;;
  *) echo "지원 안 하는 OS: \${ID}" >&2; exit 1 ;;
esac

# 서비스별 패키지 설치 + 특수 init + systemd unit enable. (distro:service) 매핑 단일 진실.
# service_classifier 카테고리 발화: nginx=web, rabbitmq=mq, postgres=db, zabbix=monitor,
# docker=container, memcached=cache, redis=cache.
for svc in $services; do
  pkg=""; unit=""
  case "\${ID}:\${svc}" in
    *:nginx)                                       pkg="nginx";                                   unit="nginx" ;;
    ubuntu:rabbitmq|debian:rabbitmq)               pkg="rabbitmq-server";                         unit="rabbitmq-server" ;;
    rocky:rabbitmq|rhel:rabbitmq|almalinux:rabbitmq) pkg="rabbitmq-server";                       unit="rabbitmq-server" ;;
    ubuntu:postgres|debian:postgres)               pkg="postgresql";                              unit="postgresql" ;;
    rocky:postgres|rhel:postgres|almalinux:postgres) pkg="postgresql-server";                     unit="postgresql" ;;
    ubuntu:redis|debian:redis)                     pkg="redis-server";                            unit="redis-server" ;;
    rocky:redis|rhel:redis|almalinux:redis)        pkg="redis";                                   unit="redis" ;;
    ubuntu:memcached|debian:memcached)             pkg="memcached";                               unit="memcached" ;;
    rocky:memcached|rhel:memcached|almalinux:memcached) pkg="memcached";                          unit="memcached" ;;
    ubuntu:docker|debian:docker)                   pkg="docker.io";                               unit="docker" ;;
    # monitor — EPEL 9 에 node_exporter 부재(EPEL 8 에서 제거). zabbix-agent 채택
    # (service_classifier 의 monitor 패턴 'zabbix' 매칭). zabbix server 미설정이라 active 실패 가능하나
    # systemd unit·listen 10050 으로 분류엔 충분 (enable 실패는 post_provision 의 || true 흡수).
    ubuntu:zabbix|debian:zabbix)                   pkg="zabbix-agent";                            unit="zabbix-agent" ;;
    rocky:zabbix|rhel:zabbix|almalinux:zabbix)     pkg="zabbix-agent";                            unit="zabbix-agent" ;;
    *) echo "지원 안 하는 OS/service: \${ID}/\${svc}" >&2; exit 1 ;;
  esac

  # rocky/RHEL postgresql: cloud image 의 appstream postgresql 모듈 메타데이터가 깨져("broken modules"
  # postgresql:16/server 해결 불가) modular 경로가 실패한다. PGDG repo 의 비모듈 패키지로 우회 —
  # 모듈 disable 후 postgresql16-server 설치. unit=postgresql-16, initdb 는 pgsql-16-setup.
  # (service_classifier 'postgres' 부분일치 분류 유지. apt 계열은 그대로 modular 무관.)
  if [ "\${svc}" = "postgres" ]; then
    case "\${ID}" in
      rocky|rhel|almalinux)
        dnf -y install https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/pgdg-redhat-repo-latest.noarch.rpm >/dev/null 2>&1 || true
        dnf -qy module disable postgresql >/dev/null 2>&1 || true
        pkg="postgresql16-server"; unit="postgresql-16"
        ;;
    esac
  fi

  # 패키지 설치 — 실패해도 전체 파이프라인 중단 금지(set -e 회피). 실패 시 해당 서비스만 건너뜀
  # (한 distro 의 한 패키지 문제로 나머지 VM·Windows 까지 막히지 않게).
  svc_ok=1
  case "\${ID}" in
    ubuntu|debian)        apt-get install -y --no-install-recommends "\${pkg}" || svc_ok=0 ;;
    rocky|rhel|almalinux) dnf install -y \${dnf_opts} "\${pkg}" || svc_ok=0 ;;
  esac
  if [ "\$svc_ok" != "1" ]; then
    echo "경고: \${pkg} 설치 실패 — \${svc} 건너뜀 (분류 누락 가능, 파이프라인 계속)" >&2
    continue
  fi

  # postgres cluster init. apt 계열은 install 시 자동 init. PGDG(rocky)는 전용 setup 바이너리.
  if [ "\${svc}" = "postgres" ]; then
    case "\${ID}" in
      rocky|rhel|almalinux) /usr/pgsql-16/bin/postgresql-16-setup initdb 2>/dev/null || true ;;
    esac
  fi

  [ -n "\${unit}" ] && systemctl enable --now "\${unit}" 2>/dev/null || true
done

# 3) 바이너리 설치 — /tmp/assessment-agent (ssh stdin 전송) → /usr/local/bin/.
#    멱등 — 바이너리·env·unit 변경 없으면 restart 건너뜀 (attention.agent_unstable false positive 회피).
needs_restart=0
if ! cmp -s /tmp/assessment-agent /usr/local/bin/assessment-agent 2>/dev/null; then
  install -m 755 /tmp/assessment-agent /usr/local/bin/assessment-agent
  needs_restart=1
fi
rm -f /tmp/assessment-agent

unit_content=\$(cat <<'UNIT'
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
UNIT
)
if [ ! -f /etc/systemd/system/assessment-agent.service ] || \\
   [ "\$unit_content" != "\$(cat /etc/systemd/system/assessment-agent.service)" ]; then
  echo "\$unit_content" > /etc/systemd/system/assessment-agent.service
  systemctl daemon-reload
  needs_restart=1
fi
systemctl enable assessment-agent
if ! systemctl is-active --quiet assessment-agent; then
  # 비활성 상태(첫 install 또는 stop된 경우)면 start
  systemctl start assessment-agent
elif [ "\$needs_restart" = "1" ] || [ "\$env_needs_restart" = "1" ]; then
  # binary·unit·env 변경 있을 때만 restart — agent_started_at 갱신 회피 (attention false positive 줄임)
  systemctl restart assessment-agent
fi
SCRIPT
}

# 합성 부하 timer 설치 — persistent VM(web/cache/db) 공통.
# light (sustained CPU 1~3s + mem 5~20MB) — 차트 변동만 가시화. host(libvirt 게이트웨이)로 ping/curl.
# host 좌표는 quoted heredoc 안 placeholder(__HOST_TARGET__)로 두고 remote 에서 sed 치환 (RANDOM 등 보존).
install_synthetic_load() {
  local vm="$1"
  vm_ssh "$vm" sudo bash -s "$LIBVIRT_GW" <<'SCRIPT'
set -euo pipefail
gw="$1"
cat > /usr/local/bin/synthetic-load.sh <<'EOF'
#!/bin/bash
sleep $(( RANDOM % 30 ))
duration=$(( (RANDOM % 3) + 1 ))
timeout ${duration}s sha256sum /dev/zero > /dev/null 2>&1 || true
size=$(( (RANDOM % 16) + 5 ))
head -c ${size}M /dev/urandom > /tmp/synthetic-load 2>/dev/null || true
sync
rm -f /tmp/synthetic-load
count=$(( (RANDOM % 100) + 50 ))
dd if=/dev/zero of=/tmp/synthetic-io bs=4k count=${count} 2>/dev/null || true
sync
rm -f /tmp/synthetic-io
ping -c $(( (RANDOM % 5) + 3 )) -q __HOST_TARGET__ > /dev/null 2>&1 || true
curl -s -m 2 http://__HOST_TARGET__:8000/health > /dev/null 2>&1 || true
if [ $((RANDOM % 2)) -eq 0 ]; then
  curl -s -m 2 http://__HOST_TARGET__:8000/static/js/chart-utils.js > /dev/null 2>&1 || true
fi
EOF
sed -i "s/__HOST_TARGET__/${gw}/g" /usr/local/bin/synthetic-load.sh
chmod 755 /usr/local/bin/synthetic-load.sh
cat > /etc/systemd/system/synthetic-load.service <<'EOF'
[Unit]
Description=Synthetic load (metrics visualization aid)
[Service]
Type=oneshot
ExecStart=/usr/local/bin/synthetic-load.sh
EOF
cat > /etc/systemd/system/synthetic-load.timer <<'EOF'
[Unit]
Description=Run synthetic-load every minute
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable synthetic-load.timer
systemctl start synthetic-load.timer
SCRIPT
}

# agent-restart-demo 설치 (app-server-01 전용) — agent 주기 restart 로 agent_unstable 항상 발화.
# 1시간 슬라이딩 윈도우 임계 3회를 큰 마진으로 초과 (OnBootSec=1min + OnUnitActiveSec=3min → 시간당 약 20회).
install_agent_restart_demo() {
  local vm="$1"
  vm_ssh "$vm" sudo bash -s <<'SCRIPT'
set -euo pipefail
cat > /etc/systemd/system/agent-restart-demo.service <<'EOF'
[Unit]
Description=Agent restart demo (attention.agent_unstable 시연용)
[Service]
Type=oneshot
ExecStart=/bin/systemctl restart assessment-agent
EOF
cat > /etc/systemd/system/agent-restart-demo.timer <<'EOF'
[Unit]
Description=Restart assessment-agent every 3 minutes (demo, 시간당 20회 — 임계 3회의 6배 마진)
[Timer]
OnBootSec=1min
OnUnitActiveSec=3min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable agent-restart-demo.timer
systemctl start agent-restart-demo.timer
SCRIPT
}

# offline-demo 설치 (edge-server-01 전용) — agent 가 약 N회 발행 후 VM poweroff → attention.gap_warnings.
# 발행 횟수는 시간 기반 근사: AGENT_INTERVAL_SEC=60 기준 boot 후 0s/60s/120s 발행 → 약 3회.
# OFFLINE_DOWN_AFTER_SEC(기본 180=3min) 후 systemctl poweroff. VM 자체 정지라 SSH 끊김 — 디버깅 시엔
# `virsh start edge-server-01` 재기동(dev-up.sh 재실행 시 멱등 재적용, 다시 3회 후 down 반복).
# 도메인 XML on_poweroff=destroy 라 poweroff 시 도메인은 'shut off' 상태로 보존 (재기동 가능).
# agent stop 대신 poweroff 채택 — 사용자 요구("VM 이 내려감") 의도 충실. gap_warnings 발화는 둘 다 동일.
install_offline_demo() {
  local vm="$1"
  local down_after="${OFFLINE_DOWN_AFTER_SEC:-180}"
  vm_ssh "$vm" sudo bash -s <<SCRIPT
set -euo pipefail
cat > /etc/systemd/system/offline-demo.service <<'EOF'
[Unit]
Description=Offline demo (attention.gap_warnings 시연 — N회 발행 후 VM poweroff)
[Service]
Type=oneshot
ExecStart=/bin/systemctl poweroff
EOF
# timer heredoc 은 unquoted — host shell 이 ${down_after} 치환 (VM bash 는 숫자라 무해).
cat > /etc/systemd/system/offline-demo.timer <<EOF
[Unit]
Description=Poweroff after agent publishes ~3 times (gap_warnings demo)
[Timer]
OnBootSec=${down_after}
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable offline-demo.timer
systemctl start offline-demo.timer
SCRIPT
}

# VM별 합성 부하·시연 트리거 설치 — 옛 Lima yaml provision 분기 흡수.
install_demo_loads() {
  local vm="$1"
  install_synthetic_load "$vm"
  case "$vm" in
    app-server-01)  install_agent_restart_demo "$vm" ;;
    edge-server-01) install_offline_demo "$vm" ;;
    # 오프라인 시연 VM — 서비스 없이 최초 메트릭만 발행 후 poweroff → 목록에 오프라인 2대(총 6대).
    offline-server-01|offline-server-02) install_offline_demo "$vm" ;;
  esac
}

start_vms() {
  echo "[4/4] libvirt VM 기동 + 에이전트 설치 중 (병렬)..."
  mkdir -p "$VM_WORK_DIR"
  # 같은 distro 를 공유하는 VM 들(app·edge=debian12)이 동시에 base image 를 vol-create 하면 race —
  # 병렬 전에 distro 별 1회 선 import (멱등, 이미 있으면 skip). 이후 VM 별 build 는 base vol-info 가
  # 있으니 clone 만 한다.
  local vm d seen=" "
  for vm in "${VMS[@]}"; do
    d="$(vm_distro "$vm")"
    case "$seen" in *" $d "*) ;; *) ensure_base_image "$d"; seen="$seen$d ";; esac
  done
  # VM 별 병렬 — 부팅·SSH 대기·provision·합성부하가 독립이라 전체 시간이 "합"에서 "가장 느린 1대"로
  # 줄어든다(약 N배 단축). 출력 섞임 방지로 per-vm 로그 파일에 리다이렉트, wait 로 결과 수집.
  local pids=() names=() rc=0
  for vm in "${VMS[@]}"; do
    ( start_or_resume_vm "$vm" && post_provision_vm "$vm" && install_demo_loads "$vm" ) \
      > "$VM_WORK_DIR/${vm}.up.log" 2>&1 &
    pids+=("$!"); names+=("$vm")
  done
  echo "  ${#VMS[@]} VM 병렬 provisioning 중 (진행 로그: $VM_WORK_DIR/<vm>.up.log)..."
  local i
  for i in "${!pids[@]}"; do
    if wait "${pids[$i]}"; then
      echo "  OK: ${names[$i]}"
    else
      echo "  실패: ${names[$i]} — 로그 끝부분:" >&2
      tail -8 "$VM_WORK_DIR/${names[$i]}.up.log" >&2
      rc=1
    fi
  done
  return "$rc"
}

# ────────────────────────────────────────────────────────────────────────────
# Step 5: 결과 요약
# ────────────────────────────────────────────────────────────────────────────
print_summary() {
  echo ""
  echo "환경 준비 완료"
  echo "  Web UI  : http://localhost:${WEB_PORT:-8000}/servers/"
  echo "  RabbitMQ: http://localhost:${RABBITMQ_MANAGEMENT_PORT:-15672}"
  # 서버 발견 probe 시연 — web 컨테이너에서 VM hostname DNS 미해석이라 운영자가 VM IP 를 모달에 직접 입력.
  echo "  서버 발견 probe 대상 VM IP (모달에 입력 -> SSH 도달성 확인):"
  local vm ip
  for vm in "${VMS[@]}"; do
    ip="$(vm_ip "$vm" 2>/dev/null || true)"
    # offline-demo 로 poweroff 된 VM 은 IP 가 비어 echo skip. if 문으로 — `&& echo` 는 마지막 반복이
    # false 일 때 함수 exit 1 을 반환해 set -e 가 뒤 Windows 블록을 건너뛴다(회귀 방지).
    if [ -n "$ip" ]; then echo "    $vm : $ip"; fi
  done
}

# ----------------------------------------------------------------------------
# Windows (libvirt win-server-01) — Windows Server 2022 Eval x64 + autounattend 무인 설치.
# Linux VM 과 동일 libvirt(qemu:///system)·virbr0(게이트웨이 LIBVIRT_GW). agent.exe 는 mingw
# 크로스빌드(PE32+ x86-64)라 x86 Server 에서 네이티브 실행. dev-down.sh 가 source 재사용.
# 무거워도(ISO ~4.7GB + 설치 ~20min) "모든 환경" 기본 포함 — main 에서 항상 실행. WIN_ENABLE=0 opt-out(Linux 전용).
# Windows Server 채택 — TPM/SecureBoot/MS계정 OOBE 불요라 autounattend·domain XML 이 단순.
# ----------------------------------------------------------------------------

# ────────────────────────────────────────────────────────────────────────────
# 설정 (env override)
# ────────────────────────────────────────────────────────────────────────────
WIN_VM_NAME="${WIN_VM_NAME:-win-server-01}"
WIN_SSH_USER="${WIN_SSH_USER:-Administrator}"        # Server 내장 관리자 (autounattend 가 SSH키 등록)
WIN_ADMIN_PASS="${WIN_ADMIN_PASS:-Assess!Dev2026}"   # dev built-in Administrator (복잡도 충족)
WIN_MEM_MIB="${WIN_MEM_MIB:-4096}"
WIN_VCPU="${WIN_VCPU:-4}"
WIN_DISK_GB="${WIN_DISK_GB:-64}"
# Server 2022 Standard Eval x64 fwlink (제품키 불요·180일). 풀 안 ISO 볼륨명.
WIN_ISO_URL="${WIN_ISO_URL:-https://go.microsoft.com/fwlink/p/?LinkID=2195280&clcid=0x409&culture=en-us&country=US}"
readonly WIN_ISO_VOL="win-server-2022-eval.iso"
readonly WIN_UNATTEND_VOL="${WIN_VM_NAME}-unattend.iso"
# redis (tporadowski, 서비스명 redis -> service_classifier cache 매칭). host 에서 받아 scp -> msiexec.
WIN_REDIS_MSI_URL="${WIN_REDIS_MSI_URL:-https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.msi}"
# OVMF UEFI 펌웨어 (Server 라 secboot/TPM 불요 — non-secboot OVMF).
WIN_OVMF_CODE="${WIN_OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.fd}"
WIN_OVMF_VARS="${WIN_OVMF_VARS:-/usr/share/OVMF/OVMF_VARS_4M.fd}"
# Windows agent 경로 (deploy/install.ps1 단일 진실 — 공백 포함).
readonly WIN_ENV_PATH='C:/ProgramData/assessment-agent/agent.env'
readonly WIN_EXE_PATH='C:/Program Files/assessment-agent/assessment-agent.exe'
readonly WIN_EXE_STAGE='C:/ProgramData/assessment-agent/agent.exe.new'
# windows-agent sibling repo — mingw 크로스빌드 대상 (Linux agent build.sh 와 동일한 ../assessment-agent 규약).
WIN_AGENT_REPO="${WIN_AGENT_REPO:-../assessment-agent/windows-agent}"
# 빌드 산출 .exe (build_win_agent 가 채움 — 빌드 skip 시 빈 문자열 → exe 교체 생략, env 만 갱신).
WIN_AGENT_EXE=""
# win_vm_ip(=vm_ip) 가 DHCP lease 로 채움.
WIN_VM_IP=""

# ────────────────────────────────────────────────────────────────────────────
# 사전 점검
# ────────────────────────────────────────────────────────────────────────────
check_win_prereqs() {
  # Windows 전용 의존성 자동 설치(sudo) — genisoimage(unattend ISO) + mingw-w64/cmake
  # (agent·vendor static lib 크로스빌드). libvirt 는 check_prereqs 가 이미 보장.
  ensure_apt_packages genisoimage:genisoimage x86_64-w64-mingw32-gcc:mingw-w64 cmake:cmake
  # OVMF 는 명령이 아닌 UEFI 펌웨어 파일이라 ensure_apt_packages(cmd 기반)와 별개 — 부재 시 설치.
  if [ ! -f "$WIN_OVMF_CODE" ]; then
    echo "  의존성 자동 설치 (sudo apt): ovmf"
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ovmf
  fi
  if [ ! -f dev/win/autounattend.xml.tmpl ]; then
    echo "오류: dev/win/autounattend.xml.tmpl 없음 (repo 산출물 — 누락 시 git 상태 확인)." >&2
    exit 1
  fi
}

# ────────────────────────────────────────────────────────────────────────────
# Windows VM 생성·기동 (autounattend 무인 설치)
# ────────────────────────────────────────────────────────────────────────────

# Server ISO 를 풀로 1회 import (vol-upload). dev/run 에 사전 다운로드돼 있으면 재사용.
ensure_win_iso() {
  if virsh vol-info "$WIN_ISO_VOL" --pool "$LIBVIRT_POOL" >/dev/null 2>&1; then return 0; fi
  mkdir -p "$VM_WORK_DIR"
  local iso="$VM_WORK_DIR/$WIN_ISO_VOL"
  if [ ! -f "$iso" ]; then
    echo "  Windows Server ISO 다운로드 (~4.7GB, 1회)..."
    # .part 원자적 받기 — curl 중단 시 부분 파일이 다음 실행에서 완성본으로 오인되지 않게.
    curl -fSL --retry 3 -o "$iso.part" "$WIN_ISO_URL"
    mv "$iso.part" "$iso"
  fi
  local sz; sz="$(stat -c%s "$iso")"
  echo "  ISO 풀로 import (vol-upload, ${sz}B)..."
  virsh vol-create-as "$LIBVIRT_POOL" "$WIN_ISO_VOL" "$sz" --format raw >/dev/null
  # vol-upload 중 SIGINT/실패 시 부분 ISO 볼륨 삭제 (base image 와 동일 원자성).
  trap 'virsh vol-delete "$WIN_ISO_VOL" --pool "$LIBVIRT_POOL" >/dev/null 2>&1; exit 130' INT TERM ERR
  virsh vol-upload --pool "$LIBVIRT_POOL" --vol "$WIN_ISO_VOL" --file "$iso"
  trap - INT TERM ERR
}

# autounattend.xml(템플릿 치환) + provision.ps1 -> ISO(autounattend.xml·provision.ps1 루트) -> 풀 import.
# provision(OpenSSH·방화벽·SSH키·DefaultShell·IIS)은 별도 파일 — FirstLogonCommands CommandLine 에 거대 base64 를
# 넣으면 maxLength 초과로 oobeSystem "Value is invalid"(diag 확인). 템플릿 CommandLine 이 CD 스캔 후 provision.ps1 실행.
build_win_autounattend() {
  local pubkey iso_abs
  pubkey="$(cat "${DEV_SSH_KEY}.pub")"
  mkdir -p "$VM_WORK_DIR/unattend.d"
  # provision.ps1 — 실제 파일이라 길이·escape 제약 없음. ISO 에 동봉돼 FirstLogon 시 CD 에서 실행.
  cat > "$VM_WORK_DIR/unattend.d/provision.ps1" <<PS
\$ErrorActionPreference='Continue'
# RTC 를 UTC 로 해석 — libvirt 도메인이 <clock offset='utc'> 라 RTC 에 UTC 를 주는데, Windows 기본은
# RTC 를 local TZ 로 읽어 collected_at 이 UTC 오프셋만큼 미래로 튄다. RealTimeIsUniversal=1 로 RTC=UTC
# 해석을 강제해 부팅 직후부터 시각 정합 (골든 이미지에 영구 반영, w32tm resync 의 NTP 의존 이전 단계).
New-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation' -Name RealTimeIsUniversal -Value 1 -PropertyType DWord -Force | Out-Null
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service sshd -StartupType Automatic
Start-Service sshd
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled \$false
\$d='C:\ProgramData\ssh'
New-Item -ItemType Directory -Force -Path \$d | Out-Null
\$ak=Join-Path \$d 'administrators_authorized_keys'
Set-Content -Path \$ak -Value '$pubkey' -Encoding ascii
icacls \$ak /inheritance:r /grant 'Administrators:F' 'SYSTEM:F'
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -PropertyType String -Force
Install-WindowsFeature -Name Web-Server
PS
  sed -e "s|@@HOSTNAME@@|${WIN_VM_NAME}|g" \
      -e "s|@@ADMINPASS@@|${WIN_ADMIN_PASS}|g" \
      dev/win/autounattend.xml.tmpl > "$VM_WORK_DIR/unattend.d/autounattend.xml"
  iso_abs="$(pwd)/$VM_WORK_DIR/$WIN_UNATTEND_VOL"
  ( cd "$VM_WORK_DIR/unattend.d" && genisoimage -quiet -o "$iso_abs" -J -r -V AUTOUNATTEND . )
  virsh vol-info "$WIN_UNATTEND_VOL" --pool "$LIBVIRT_POOL" >/dev/null 2>&1 \
    && virsh vol-delete "$WIN_UNATTEND_VOL" --pool "$LIBVIRT_POOL" >/dev/null
  local sz; sz="$(stat -c%s "$iso_abs")"
  virsh vol-create-as "$LIBVIRT_POOL" "$WIN_UNATTEND_VOL" "$sz" --format raw >/dev/null
  virsh vol-upload --pool "$LIBVIRT_POOL" --vol "$WIN_UNATTEND_VOL" --file "$iso_abs"
  rm -rf "$VM_WORK_DIR/unattend.d" "$iso_abs"
}

# 도메인 XML(q35 + OVMF UEFI + SATA disk/cdrom + e1000e NIC) + define.
# disk type=file 명시 경로(apparmor 정합). cdrom 2개: Server ISO(boot) + autounattend(루트 검색).
define_win_domain() {
  local disk="${WIN_VM_NAME}.qcow2"
  if ! virsh vol-info "$disk" --pool "$LIBVIRT_POOL" >/dev/null 2>&1; then
    virsh vol-create-as "$LIBVIRT_POOL" "$disk" "${WIN_DISK_GB}G" --format qcow2 >/dev/null
  fi
  local xml="$VM_WORK_DIR/${WIN_VM_NAME}.xml"
  cat > "$xml" <<DOMXML
<domain type='kvm'>
  <name>${WIN_VM_NAME}</name>
  <memory unit='MiB'>${WIN_MEM_MIB}</memory>
  <vcpu>${WIN_VCPU}</vcpu>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader readonly='yes' type='pflash'>${WIN_OVMF_CODE}</loader>
    <nvram template='${WIN_OVMF_VARS}'>/var/lib/libvirt/qemu/nvram/${WIN_VM_NAME}_VARS.fd</nvram>
    <!-- hd 우선: 첫 부팅은 빈 디스크라 cdrom 으로 자동 폴백(send-key 로 'press any key' 통과),
         설치 후 재부팅은 hd 의 Windows Boot Manager 로 직행 -> specialize/oobe 진입.
         cdrom 1순위면 재부팅 때도 CD 'press any key' 로 가서 폴백 못 하고 멈춤(검증됨). -->
    <boot dev='hd'/>
    <boot dev='cdrom'/>
    <bootmenu enable='no'/>
  </os>
  <features><acpi/><apic/></features>
  <cpu mode='host-passthrough' check='none'/>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='hpet' present='no'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='${POOL_PATH}/${disk}'/>
      <target dev='sda' bus='sata'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='${POOL_PATH}/${WIN_ISO_VOL}'/>
      <target dev='sdb' bus='sata'/>
      <readonly/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='${POOL_PATH}/${WIN_UNATTEND_VOL}'/>
      <target dev='sdc' bus='sata'/>
      <readonly/>
    </disk>
    <interface type='network'>
      <source network='${LIBVIRT_NET}'/>
      <model type='e1000e'/>
    </interface>
    <controller type='usb' model='qemu-xhci'/>
    <input type='tablet' bus='usb'/>
    <graphics type='vnc' port='-1' listen='127.0.0.1'/>
    <video><model type='vga'/></video>
    <memballoon model='none'/>
  </devices>
</domain>
DOMXML
  virsh define "$xml" >/dev/null
  rm -f "$xml"
}

# 멱등 — 도메인 있으면 실행 보장, 없으면 ISO·autounattend·도메인 생성 후 무인 설치. WIN_VM_IP 채움.
start_win_vm() {
  local golden="${WIN_VM_NAME}-golden.qcow2" disk="${WIN_VM_NAME}.qcow2"
  if virsh dominfo "$WIN_VM_NAME" >/dev/null 2>&1; then
    echo "  [$WIN_VM_NAME] 도메인 존재 — 실행 보장 (멱등)"
    virsh domstate "$WIN_VM_NAME" 2>/dev/null | grep -q running || virsh start "$WIN_VM_NAME" >/dev/null
  elif virsh vol-info "$golden" --pool "$LIBVIRT_POOL" >/dev/null 2>&1; then
    # 골든 이미지(OS+OpenSSH+IIS+redis 완성본) clone — OS 무인설치 ~20min skip. agent 는 이후 deploy
    # 가 멱등 갱신(.exe 교체)하므로 최신 반영. send-key 불요(설치 완료본이라 hd 직행).
    echo "  [$WIN_VM_NAME] 골든 이미지 clone (OS 무인설치 skip, ~수십초)..."
    virsh vol-info "$disk" --pool "$LIBVIRT_POOL" >/dev/null 2>&1 \
      && virsh vol-delete "$disk" --pool "$LIBVIRT_POOL" >/dev/null
    virsh vol-clone --pool "$LIBVIRT_POOL" "$golden" "$disk" >/dev/null
    build_win_autounattend   # unattend ISO 재생성 — define_win_domain cdrom 참조용(골든 hd 부팅엔 미사용)
    define_win_domain
    virsh start "$WIN_VM_NAME" >/dev/null
  else
    ensure_win_iso
    build_win_autounattend
    echo "  [$WIN_VM_NAME] 생성 + autounattend 무인 설치 시작 (설치 ~20min, 최초 1회 — 이후 골든 clone)..."
    define_win_domain
    virsh start "$WIN_VM_NAME" >/dev/null
    # "Press any key to boot from CD" 통과 — Enter(키코드 28) 연타. 새 빈 디스크 첫 부팅은 펌웨어 POST +
    # 빈 HD 부팅 타임아웃 후에야 CD 프롬프트가 떠서 시점이 가변적(20s 윈도우는 놓침) — 2s 간격 90s 로 넓게 커버.
    local i
    for i in $(seq 1 45); do virsh send-key "$WIN_VM_NAME" --codeset linux 28 >/dev/null 2>&1 || true; sleep 2; done
  fi
  echo "  [$WIN_VM_NAME] SSH 대기 (신규 설치 시 매우 김)..."
  local secs=0 cap="${WIN_SSH_CAP:-2400}"
  WIN_VM_IP="$(vm_ip "$WIN_VM_NAME" 2>/dev/null || true)"
  until [ -n "$WIN_VM_IP" ] && win_ssh "echo ok" 2>/dev/null | grep -q ok; do
    sleep 15; secs=$((secs+15))
    WIN_VM_IP="$(vm_ip "$WIN_VM_NAME" 2>/dev/null || true)"
    if [ "$secs" -ge "$cap" ]; then
      echo "  [$WIN_VM_NAME] SSH ${cap}s 초과 — 설치 진행 확인 ('virsh screenshot $WIN_VM_NAME /tmp/w.ppm')"
      return 1
    fi
  done
  echo "  [$WIN_VM_NAME] SSH OK ($WIN_VM_IP)"
  # 시계 UTC 동기화 — agent 배포(첫 발행) 이전에 수행해야 한다. 골든 clone 부팅 직후 Windows RTC 가
  # local TZ(예: PST)로 떠 있으면, resync 전 창에 agent 가 발행한 collected_at 이 미래로 튀어(UTC 오프셋만큼)
  # "가짜 최신 행"으로 영구 잔존 -> 대시보드 CPU delta(최신 2행) 깨짐. 동기화를 먼저 하면 첫 발행부터 정상.
  win_ps 'Set-Service w32time -StartupType Automatic -ErrorAction SilentlyContinue; Start-Service w32time -ErrorAction SilentlyContinue; w32tm /resync /force' >/dev/null 2>&1 || true
}

# redis(tporadowski) 설치 — host 에서 MSI 받아 scp -> msiexec. 서비스 redis 있으면 skip (멱등).
install_win_redis() {
  if win_ps 'if (Get-Service redis -ErrorAction SilentlyContinue) { "yes" } else { "no" }' 2>/dev/null | grep -q yes; then
    return 0
  fi
  echo "  [$WIN_VM_NAME] redis(tporadowski) 설치..."
  local msi="$VM_WORK_DIR/Redis-x64.msi"
  [ -f "$msi" ] || curl -fSL --retry 3 -o "$msi" "$WIN_REDIS_MSI_URL"
  scp -i "$DEV_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "$msi" "${WIN_SSH_USER}@${WIN_VM_IP}:C:/Windows/Temp/Redis-x64.msi"
  win_ps 'Start-Process msiexec.exe -ArgumentList "/i","C:\Windows\Temp\Redis-x64.msi","/qn","/norestart" -Wait'
}

# ────────────────────────────────────────────────────────────────────────────
# agent.env 갱신 + 서비스 등록·restart (OpenSSH)
# ────────────────────────────────────────────────────────────────────────────
win_ssh() {
  ssh -i "$DEV_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 "${WIN_SSH_USER}@${WIN_VM_IP}" "$@"
}

# 원격 powershell 실행 — 경로 공백("C:\Program Files\...")·중첩 따옴표가 OpenSSH escape 에서
# 깨지는 문제를 -EncodedCommand(UTF-16LE base64) 로 근본 회피. 인자는 raw powershell 스크립트.
win_ps() {
  local b64
  b64="$(printf '%s' "$1" | iconv -t UTF-16LE | base64 | tr -d '\n')"
  win_ssh "powershell -NoProfile -EncodedCommand $b64"
}

# ────────────────────────────────────────────────────────────────────────────
# Windows agent mingw 크로스빌드 (host) — collect.c 등 변경분 반영. vendor static libs 재사용.
# vendor 미빌드 시 자동 크로스빌드(ensure_win_vendor)·toolchain 부재·repo 부재 시 skip — Linux 전용 dev 도 정상.
# ────────────────────────────────────────────────────────────────────────────

# vendor static libs(openssl/zlib/cjson/rabbitmq/curl) 자동 크로스빌드 — 최초 1회. 산출물 존재 시
# skip(캐시: 외부 라이브러리라 로직 무관 -> dev-down 도 보존, 재빌드 불요). cross 정합 플래그
# (SYSTEM_NAME=Windows·RC·find-root·openssl 직접지정)는 windows-agent Makefile(MSYS2 가정)에 주입.
# Makefile 의 cmake build 줄은 plain(`cmake --build`)이라 본 override 가 build 단계 -j 를 안 깬다.
ensure_win_vendor() {
  local od="$WIN_AGENT_REPO/vendor/openssl/install"
  if [ -f "$od/lib/libssl.a" ] \
     && [ -f "$WIN_AGENT_REPO/vendor/curl/build/lib/libcurl.a" ] \
     && [ -f "$WIN_AGENT_REPO/vendor/rabbitmq-c/build/librabbitmq/librabbitmq.a" ]; then
    return 0
  fi
  echo "  [vendor] static libs 크로스빌드 — 최초 1회(~10min, git clone 5개 + mingw 컴파일)..."
  make -C "$WIN_AGENT_REPO" vendor-build \
    CC=x86_64-w64-mingw32-gcc AR=x86_64-w64-mingw32-ar \
    OPENSSL_CROSS=--cross-compile-prefix=x86_64-w64-mingw32- \
    CMAKE="cmake -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres -DCMAKE_FIND_ROOT_PATH=/usr/x86_64-w64-mingw32 -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH -DOPENSSL_INCLUDE_DIR=$od/include -DOPENSSL_CRYPTO_LIBRARY=$od/lib/libcrypto.a -DOPENSSL_SSL_LIBRARY=$od/lib/libssl.a"
}

build_win_agent() {
  echo "  [$WIN_VM_NAME] Windows agent mingw 크로스빌드..."
  if [ ! -d "$WIN_AGENT_REPO" ]; then
    echo "  windows-agent repo 없음 ($WIN_AGENT_REPO) — 빌드 skip, VM 기존 .exe 유지"
    return 0
  fi
  if ! command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    echo "  mingw 크로스 툴체인 없음 (sudo apt install -y mingw-w64) — 빌드 skip, VM 기존 .exe 유지"
    return 0
  fi
  # vendor static libs 없으면 자동 크로스빌드(최초 1회 캐시). 빌드 실패 시 기존 .exe 유지.
  ensure_win_vendor || true
  if [ ! -f "$WIN_AGENT_REPO/vendor/openssl/install/lib/libssl.a" ]; then
    echo "  vendor static libs 빌드 실패 — agent 빌드 skip, VM 기존 .exe 유지" >&2
    return 0
  fi
  # Makefile incremental — collect.c 등 미변경 시 link 만, 변경분만 재컴파일.
  if ! make -C "$WIN_AGENT_REPO" release \
       CC=x86_64-w64-mingw32-gcc AR=x86_64-w64-mingw32-ar >/dev/null 2>&1; then
    echo "  경고: windows-agent 빌드 실패 — VM 기존 .exe 유지" >&2
    return 0
  fi
  WIN_AGENT_EXE="$WIN_AGENT_REPO/dist/assessment-agent.exe"
  echo "  빌드 완료: $WIN_AGENT_EXE"
}

deploy_win_agent() {
  echo "  [$WIN_VM_NAME] agent.env 생성·전송 (게이트웨이 IP 주입)..."
  local tmp_env
  # 평문 secret 이 들어가므로 host /tmp 에 짧게만 두고 전송 직후 삭제 (#F8).
  tmp_env="$(mktemp)"
  cat > "$tmp_env" <<ENV
RABBITMQ_HOST=${LIBVIRT_GW}
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/assessment
RABBITMQ_USER=${RABBITMQ_USER}
RABBITMQ_PASS=${RABBITMQ_PASSWORD}
RABBITMQ_EXCHANGE=${RABBITMQ_EXCHANGE}
RABBITMQ_ROUTING_KEY_INVENTORY=${RABBITMQ_ROUTING_KEY_INVENTORY}
RABBITMQ_ROUTING_KEY_METRICS=${RABBITMQ_ROUTING_KEY_METRICS}
RABBITMQ_ROUTING_KEY_ERROR=${RABBITMQ_ROUTING_KEY_ERROR}
RABBITMQ_WORKER_USER=${RABBITMQ_WORKER_USER}
RABBITMQ_WORKER_PASS=${RABBITMQ_WORKER_PASSWORD}
WORKER_TASK_EXCHANGE=${WORKER_TASK_EXCHANGE}
WORKER_TASK_QUEUE_PREFIX=${WORKER_TASK_QUEUE_PREFIX}
WORKER_TASK_RESULT_KEY=${WORKER_TASK_RESULT_KEY}
WORKER_DOWNLOAD_ALLOWED_HOSTS=${WORKER_DOWNLOAD_ALLOWED_HOSTS}
AGENT_HOSTNAME_OVERRIDE=$WIN_VM_NAME
AGENT_INTERVAL_SEC=60
ENV
  win_ps 'New-Item -ItemType Directory -Force -Path "C:\ProgramData\assessment-agent" | Out-Null'
  scp -i "$DEV_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "$tmp_env" "${WIN_SSH_USER}@${WIN_VM_IP}:${WIN_ENV_PATH}"
  rm -f "$tmp_env"   # 평문 secret 임시파일 정리

  # agent.exe 전송·설치 (build_win_agent 산출 있으면). 서비스 실행 중엔 .exe 잠금 →
  # 공백 없는 staging 경로로 scp 후 정지·Copy-Item. 서비스 미등록이면 New-Service 로 등록.
  if [ -n "$WIN_AGENT_EXE" ] && [ -f "$WIN_AGENT_EXE" ]; then
    echo "  [$WIN_VM_NAME] agent.exe 전송·설치..."
    win_ps 'New-Item -ItemType Directory -Force -Path "C:\Program Files\assessment-agent" | Out-Null'
    scp -i "$DEV_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        "$WIN_AGENT_EXE" "${WIN_SSH_USER}@${WIN_VM_IP}:${WIN_EXE_STAGE}"
    win_ps 'Stop-Service assessment-agent -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2; Copy-Item -Path "C:\ProgramData\assessment-agent\agent.exe.new" -Destination "C:\Program Files\assessment-agent\assessment-agent.exe" -Force; Remove-Item "C:\ProgramData\assessment-agent\agent.exe.new" -Force -ErrorAction SilentlyContinue'
    win_ps 'if (-not (Get-Service assessment-agent -ErrorAction SilentlyContinue)) { New-Service -Name assessment-agent -BinaryPathName '\''"C:\Program Files\assessment-agent\assessment-agent.exe"'\'' -DisplayName "Assessment Agent" -StartupType Automatic | Out-Null; sc.exe failure assessment-agent reset= 86400 actions= restart/10000/restart/10000/restart/10000 | Out-Null }'
  fi

  # 시계 UTC 동기화는 start_win_vm(agent 배포 이전)에서 이미 수행 — 여기선 IIS·redis·agent 기동만.
  echo "  [$WIN_VM_NAME] IIS·redis 기동 보장 + agent (re)start..."
  win_ps 'Set-Service W3SVC -StartupType Automatic -ErrorAction SilentlyContinue; Set-Service redis -StartupType Automatic -ErrorAction SilentlyContinue; Start-Service W3SVC,redis -ErrorAction SilentlyContinue; if (Get-Service assessment-agent -ErrorAction SilentlyContinue) { Restart-Service assessment-agent -ErrorAction SilentlyContinue }'
}

# 최초 설치+배포 완료 후 골든 이미지 생성 — 다음 dev-up 부터 OS 무인설치(~20min)를 clone(~수십초)으로
# 대체. 골든 = OS+OpenSSH+IIS+redis+agent 완성본. 디스크 일관성 위해 VM 정지 후 vol-clone, 재시작.
# 이미 골든 있으면(= 이번 기동이 clone 이었던 경우) skip. dev-down 은 golden 을 이름이 달라 보존(캐시).
make_win_golden_if_absent() {
  local golden="${WIN_VM_NAME}-golden.qcow2" disk="${WIN_VM_NAME}.qcow2"
  virsh vol-info "$golden" --pool "$LIBVIRT_POOL" >/dev/null 2>&1 && return 0
  echo "  [$WIN_VM_NAME] 골든 이미지 생성 중 (다음 dev-up 부터 OS 설치 skip)..."
  virsh shutdown "$WIN_VM_NAME" >/dev/null 2>&1 || true
  local w=0
  until virsh domstate "$WIN_VM_NAME" 2>/dev/null | grep -q "shut off"; do
    sleep 3; w=$((w+3))
    if [ "$w" -ge 180 ]; then virsh destroy "$WIN_VM_NAME" >/dev/null 2>&1 || true; sleep 2; break; fi
  done
  virsh vol-clone --pool "$LIBVIRT_POOL" "$disk" "$golden" >/dev/null
  virsh start "$WIN_VM_NAME" >/dev/null
  echo "  [$WIN_VM_NAME] 골든 생성 완료 — VM 재시작(메트릭 재개)."
}

# ────────────────────────────────────────────────────────────────────────────
print_win_summary() {
  echo ""
  echo "Windows agent 자동화 완료 (libvirt)"
  echo "  VM       : $WIN_VM_NAME ($WIN_VM_IP)"
  echo "  agent.env: $WIN_ENV_PATH (RABBITMQ_HOST=${LIBVIRT_GW} 주입 완료)"
  echo "  확인:"
  echo "    RabbitMQ 콘솔  : http://localhost:${RABBITMQ_MANAGEMENT_PORT:-15672} ($WIN_VM_NAME 메시지 적재)"
  echo "    Web UI         : http://localhost:${WEB_PORT:-8000}/servers/ ($WIN_VM_NAME 등록, os_family=windows)"
  echo "    서비스 상태    : ssh -i $DEV_SSH_KEY ${WIN_SSH_USER}@${WIN_VM_IP} 'Get-Service assessment-agent,W3SVC,redis'"
}

main() {
  # ── Linux (libvirt + compose) ── WIN_ONLY=1 이면 Linux provision 스킵 — Windows VM 만 반복
  #    빌드·디버깅용. check_prereqs 가 docker/libvirt/gw/ssh-key/env 공통 의존을 보장하므로 그것만
  #    호출하고 Linux VM provision(ssh 헬스체크 포함)은 건너뛴다. ──
  if [ "${WIN_ONLY:-0}" = "1" ]; then
    check_prereqs
    load_agent_env   # deploy_win_agent 의 agent.env 생성이 RABBITMQ_USER 등 참조 — Linux 스킵해도 필요
  else
    check_prereqs
    ensure_agent_binary
    load_agent_env
    start_docker_stack
    wait_migrate_completed
    wait_web_healthy
    start_vms
    print_summary
  fi
  # ── Windows (libvirt) — 기본 포함("모든 환경" 구성). 무거워도(ISO ~4.7GB + 설치 ~20min) 매 dev-up
  #    이 무인설치를 수행해 설치 과정을 검증한다(dev-down 이 Windows 도 삭제하므로). Linux 전용 dev
  #    는 WIN_ENABLE=0 으로 opt-out. WIN_ONLY=1 / 도메인 기존재 시에도 실행. ──
  if [ "${WIN_ENABLE:-1}" != "0" ] || [ "${WIN_ONLY:-0}" = "1" ] || virsh dominfo "$WIN_VM_NAME" >/dev/null 2>&1; then
    echo ""
    echo "=== Windows 파이프라인 (libvirt $WIN_VM_NAME) ==="
    check_win_prereqs
    start_win_vm
    build_win_agent
    install_win_redis
    deploy_win_agent
    make_win_golden_if_absent
    print_win_summary
  fi
}

# 직접 실행 시만 main 호출. `source dev-up.sh`(dev-down.sh)로 함수만 가져올 때는 자동 실행 안 함
# (단계별 디버깅·검증 시 유용 — VM별 start_or_resume_vm/post_provision_vm/install_demo_loads 개별 호출 가능).
# set -u 환경에서 BASH_SOURCE 안전 access — source(dev-down.sh) 시 부모 $0 오인 방지.
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
