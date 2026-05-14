#!/usr/bin/env bash
# dev 풀 파이프라인 기동: Docker → migrate → web 헬스체크 → Lima VM 3대 + 에이전트 설치.
#
# 책임 분담:
#   - Docker compose는 prod-safe baseline + override.yml (#A2). migrate init-container가 alembic 자동 적용.
#   - Lima yaml은 boot + mount + 합성 부하 timer만 (yaml provision의 boot timeout 회피).
#   - 패키지 설치·에이전트 빌드·systemd unit은 본 스크립트의 post-provision step (단일 진실, 디스트로 분기 명시).
#
# 멱등성: 모든 단계 안전 재실행. VM 이미 Running이면 start 건너뜀, post-provision은 매번 재적용.

set -euo pipefail

# ────────────────────────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────────────────────────
readonly TIMEOUT=180                         # docker compose / migrate / web 헬스체크 공통 cap
# VM 진행 순서 — 시연 가시화 우선순위 (attention 발화 가장 빠른 VM 1번).
#   (1) web-server-01     — Debian 12 + nginx + agent-restart-demo timer (1m boot + 3m 주기, 시간당 20회)
#                           → attention.agent_unstable 가장 빠른 발화 (1m 후 첫 restart 즉시 가시화)
#   (2) offline-server-01 — Debian 13 trixie + finalize_vm offline-once mode (1회 발행 후 stop)
#                           → attention.gap_warnings 5m 후 발화 + insufficient_data 분류
#   (3) app-server-01     — Ubuntu 24.04 + docker.io + boot-time swap-trigger.service
#                           → under_provisioned 발화 (swap_used 트리거)
#   (4) monitor-server-01 — Rocky 9 + EPEL 9 + zabbix-agent + swap 256M (OOM 회피용)
#                           → optimal 분류 (medium 부하)
#   (5) mq-server-01      — Debian 12 + mosquitto (openSUSE Leap 15 zypper 누적 불안정으로 fallback, 사고 #15)
#                           → over_provisioned (light 부하, attention 카탈로그에 mq 매핑 없어 정상 분류)
#   (6) cache-server-01   — Rocky 9 + redis  → over_provisioned (light)
#   (7) db-server-01      — AlmaLinux 9 + postgresql-server  → over_provisioned (light, RPM initdb)
readonly LIMA_VMS=(web-server-01 offline-server-01 app-server-01 monitor-server-01 mq-server-01 cache-server-01 db-server-01)

# VM별 dispatch — services/ext_ip/mode는 dev-up.sh가 단일 진실로 갖는다.
# yaml에 박지 않는 이유: dev-up.sh가 패키지 설치·offline 분기까지 처리하므로 단일 진실.
# associative array 대신 함수 — macOS default bash 3.2 호환 (brew bash 의존 없음).
vm_service() {
  case "$1" in
    cache-server-01)     echo "redis" ;;
    app-server-01)       echo "docker" ;;        # docker.io (apt 표준) — service_classifier "container" 카테고리 + heavy++ 부하
    web-server-01)       echo "nginx" ;;
    db-server-01)        echo "postgres" ;;
    mq-server-01)        echo "mosquitto" ;;     # MQTT — 가장 가벼운 mq (EPEL 표준)
    monitor-server-01)   echo "zabbix_agent" ;;  # Zabbix agent — node_exporter는 EPEL 9에 미존재라 fallback (service_classifier "zabbix" → monitor 매칭)
    offline-server-01)   echo "none"  ;;
    *) echo "오류: 알 수 없는 VM: $1" >&2; return 1 ;;
  esac
}
vm_ext_ip() {
  case "$1" in
    web-server-01) echo "203.0.113.10" ;;
    *)             echo "" ;;
  esac
}
# offline-once: inventory 1회 발행 후 agent stop + VM stop. 5분 후 gap_warnings 발화 (시연용).
# persistent: 일반 — agent restart로 publish 계속.
vm_mode() {
  case "$1" in
    offline-server-01) echo "offline-once" ;;
    *)                 echo "persistent" ;;
  esac
}

# infra/agent.env 필수 키 (agent.env.example과 단일 진실).
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
check_prereqs() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "오류: docker가 PATH에 없다."
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "오류: docker daemon 미가동. Docker Desktop 기동 후 재시도."
    exit 1
  fi
  if ! command -v limactl >/dev/null 2>&1; then
    echo "오류: limactl이 PATH에 없다. 'brew install lima' 후 재시도."
    exit 1
  fi
  if [ ! -d "../assessment-agent" ]; then
    echo "오류: ../assessment-agent가 없다. 본 레포와 같은 부모 디렉토리에 git clone하라."
    exit 1
  fi
  if [ ! -f infra/agent.env ]; then
    echo "오류: infra/agent.env 없음. 'cp infra/agent.env.example infra/agent.env' 후 운영 값으로 수정하라."
    exit 1
  fi
}

# infra/agent.env를 host env로 export + 필수 키 검증.
load_agent_env() {
  set -a
  # shellcheck disable=SC1091
  source infra/agent.env
  set +a

  local k
  for k in "${REQUIRED_AGENT_KEYS[@]}"; do
    if [ -z "${!k:-}" ]; then
      echo "오류: infra/agent.env에 ${k} 누락. infra/agent.env.example 참조해 추가하라."
      exit 1
    fi
  done
}

# ────────────────────────────────────────────────────────────────────────────
# Step 2: Docker 스택
# ────────────────────────────────────────────────────────────────────────────
start_docker_stack() {
  echo "[1/4] Docker 서비스 기동 중 (build 포함, postgres healthy 후 migrate 실행)..."
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
# Step 3: Lima VM 기동 + 에이전트 설치
# ────────────────────────────────────────────────────────────────────────────

# limactl start 멱등 — 이미 Running이면 skip, Stopped면 resume, 미존재면 create.
# wrapper: SSH ready 후에도 lima final requirement("boot scripts must finished") 60s+ stuck시
# limactl PID kill 후 진행 (Oracle Linux 9 등 cloud-init 느린 distro 우회).
start_or_resume_vm() {
  local vm="$1"
  local agent_src="$2"

  if limactl list -q 2>/dev/null | grep -qx "$vm"; then
    local state
    state="$(limactl list --format '{{.Status}}' "$vm" 2>/dev/null || echo Unknown)"
    if [ "$state" = "Running" ]; then
      echo "  [$vm] 이미 Running — start 건너뜀"
      return 0
    fi
    echo "  [$vm] resume (state=$state)..."
    limactl start "$vm" > "/tmp/lima-start-$vm.log" 2>&1 &
  else
    echo "  [$vm] create + start (cloud image 다운로드 포함, 첫 실행 시 수분 소요)..."
    limactl start --name="$vm" --tty=false \
      --set ".param.AgentSrc = \"$agent_src\"" \
      "infra/lima/$vm.yaml" > "/tmp/lima-start-$vm.log" 2>&1 &
  fi

  local pid=$!
  local secs=0
  local ssh_ready_at=0
  while kill -0 $pid 2>/dev/null; do
    sleep 3; secs=$((secs+3))
    # SSH ready check — limactl shell echo ok 작동하면 ready
    if [ $ssh_ready_at -eq 0 ] && limactl shell --workdir / "$vm" -- echo ok 2>/dev/null | grep -q ok; then
      ssh_ready_at=$secs
      echo "  [$vm] SSH ready at ${secs}s"
    fi
    # SSH ready 후 추가 60s 경과해도 limactl 안 끝나면 final requirement stuck — 강제 진행
    if [ $ssh_ready_at -gt 0 ] && [ $((secs - ssh_ready_at)) -ge 60 ]; then
      echo "  [$vm] limactl final requirement 60s+ stuck — PID kill 후 진행 (SSH OK)"
      kill -KILL $pid 2>/dev/null || true
      break
    fi
    # 절대 cap 5분 — image 다운로드 + boot 합쳐 5분 초과면 abort
    if [ $secs -ge 300 ]; then
      echo "  [$vm] BOOT TIMEOUT 5min — abort"
      kill -KILL $pid 2>/dev/null || true
      tail -10 "/tmp/lima-start-$vm.log"
      return 1
    fi
  done
  wait $pid 2>/dev/null || true
  # 우리가 kill했든 limactl 정상 exit이든 SSH 작동 검증
  if ! limactl shell --workdir / "$vm" -- echo ok 2>/dev/null | grep -q ok; then
    echo "  [$vm] SSH 검증 실패 — boot 실패"
    tail -10 "/tmp/lima-start-$vm.log"
    return 1
  fi
  echo "  [$vm] boot OK (${secs}s)"
}

# VM 안에서 /etc/assessment-agent.env 생성 + OS detect + 패키지 설치 + 빌드 + systemd unit.
#
# heredoc 변수 치환 규약:
#   - outer `<<SCRIPT` (unquoted): host shell이 ${RABBITMQ_*}·$vm·$ext_ip·$service 치환 후 VM bash로 전달.
#   - VM 내부 변수($ID/$svc_pkg/$svc_unit)는 `\$` escape — host shell이 손 안 대고 VM bash가 처리.
#   - inner `<<'UNIT'` (quoted): VM bash가 그대로 받음 (systemd unit literal에 '$' 없음).
#   - inner `<<ENV` (unquoted): host expand된 값들을 inline (그 안에 '$' 없음).
post_provision_vm() {
  local vm="$1"
  local service ext_ip hostname_override
  service="$(vm_service "$vm")"
  ext_ip="$(vm_ext_ip "$vm")"
  # 모든 VM은 hostname=VM 이름 통일 (web-server-01도 agent-restart-demo.timer는 yaml에 그대로).
  hostname_override="$vm"

  echo "  [$vm] post-provision (env + 패키지 + 빌드 + systemd)..."
  limactl shell --workdir / "$vm" sudo bash -s <<SCRIPT
set -euo pipefail

# 1) /etc/assessment-agent.env — host의 infra/agent.env + host.lima.internal + VM별 hostname/ext_ip.
#    env_needs_restart=1이면 env 변경됨 → 뒤 단계에서 restart 트리거.
new_env=\$(cat <<ENV
RABBITMQ_HOST=host.lima.internal
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

# 2) OS detect + 패키지 설치. service($service)는 host shell이 inline한 literal.
. /etc/os-release
case "\${ID}:$service" in
  ubuntu:redis|debian:redis)                       svc_pkg="redis-server";       svc_unit="redis-server" ;;
  rocky:redis|rhel:redis|almalinux:redis)          svc_pkg="redis";              svc_unit="redis" ;;
  *:nginx)                                         svc_pkg="nginx";              svc_unit="nginx" ;;
  ubuntu:postgres|debian:postgres)                 svc_pkg="postgresql";         svc_unit="postgresql" ;;
  rocky:postgres|rhel:postgres|almalinux:postgres) svc_pkg="postgresql-server"; svc_unit="postgresql" ;;
  centos:postgres)                                 svc_pkg="postgresql-server";  svc_unit="postgresql" ;;
  *:mosquitto)                                     svc_pkg="mosquitto";          svc_unit="mosquitto" ;;
  centos:zabbix_agent|rocky:zabbix_agent|rhel:zabbix_agent|almalinux:zabbix_agent) \
                                                   svc_pkg="zabbix-agent";       svc_unit="zabbix-agent" ;;
  ubuntu:zabbix_agent|debian:zabbix_agent)         svc_pkg="zabbix-agent";       svc_unit="zabbix-agent" ;;
  ubuntu:docker|debian:docker)                     svc_pkg="docker.io";          svc_unit="docker" ;;
  *:none)                                          svc_pkg="";                   svc_unit="" ;;
  *) echo "지원 안 하는 OS/service: \${ID}/$service" >&2; exit 1 ;;
esac

# OS major 추출 — CentOS 7(EOL, yum)과 8+(dnf) 분기 위해.
os_major="\${VERSION_ID%%.*}"
case "\${ID}:\${os_major}" in
  ubuntu:*|debian:*)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends \\
      gcc make pkg-config libc6-dev librabbitmq-dev libcjson-dev \\
      libcurl4-openssl-dev libarchive-dev libssl-dev zlib1g-dev \\
      curl iputils-ping \${svc_pkg}
    ;;
  rocky:*|rhel:*|almalinux:*|centos:8|centos:9|centos:10)
    # install_weak_deps=False — Recommends 차단으로 transaction 메모리·디스크 절약 (1GiB OOM 회피).
    # tsflags=nodocs — man/info 등 문서 skip.
    # CRB enable: Rocky 9/AlmaLinux 9는 crb, Rocky 8/AlmaLinux 8은 powertools.
    # config-manager가 둘 다 시도 — 미존재 repo는 silent skip ("|| true" 안 해도 미발견 OK).
    dnf_opts="--setopt=install_weak_deps=False --setopt=tsflags=nodocs"
    dnf install -y \${dnf_opts} epel-release dnf-plugins-core
    dnf config-manager --set-enabled crb 2>/dev/null || dnf config-manager --set-enabled powertools 2>/dev/null || true
    dnf install -y \${dnf_opts} gcc make pkg-config librabbitmq-devel cjson-devel \\
      libcurl-devel libarchive-devel openssl-devel zlib-devel \\
      curl iputils \${svc_pkg}
    ;;
  centos:7)
    # CentOS 7 EOL 2024-06-30 — mirror.centos.org 사라짐. yum repo를 vault.centos.org로 redirect.
    # yum 사용 (dnf 미설치). EPEL7로 librabbitmq-devel·cjson-devel 가용.
    sed -i 's/^mirrorlist=/#mirrorlist=/' /etc/yum.repos.d/CentOS-*.repo
    sed -i 's|^#baseurl=http://mirror.centos.org|baseurl=https://vault.centos.org|' /etc/yum.repos.d/CentOS-*.repo
    yum install -y epel-release
    # EPEL 7도 동일 vault redirect (epel-release가 EOL 이후 metadata 못 받음).
    sed -i 's/^mirrorlist=/#mirrorlist=/' /etc/yum.repos.d/epel*.repo 2>/dev/null || true
    sed -i 's|^#\?baseurl=https\?://download.fedoraproject.org/pub|baseurl=https://archives.fedoraproject.org/pub/archive|' /etc/yum.repos.d/epel*.repo 2>/dev/null || true
    yum install -y gcc make pkgconfig librabbitmq-devel cjson-devel \\
      libcurl-devel libarchive-devel openssl-devel zlib-devel \\
      curl iputils \${svc_pkg}
    ;;
  *) echo "지원 안 하는 OS: \${ID}:\${os_major}" >&2; exit 1 ;;
esac

if [ -n "\${svc_unit}" ]; then
  # RPM family postgresql은 cluster init 수동 필요. apt 계열은 install 시 자동 init라 skip.
  if [ "$service" = "postgres" ]; then
    case "\${ID}" in
      rocky|rhel|almalinux|centos)
        # CentOS 7: postgresql-setup initdb (옛 syntax), CentOS 8+/Rocky: --initdb (긴 옵션).
        # 둘 다 시도 — 이미 init된 경우 silent skip.
        postgresql-setup --initdb 2>/dev/null || postgresql-setup initdb 2>/dev/null || true
        ;;
    esac
  fi
  systemctl enable --now "\${svc_unit}"
fi

# 3) 에이전트 빌드 — read-only mount(/mnt/agent-src) -> /tmp/build로 source만 sync 후 make.
#    rsync exclude로 host의 빌드 산출물(macOS Mach-O .o/binary)·.git 제외 — VM에서 fresh build.
#    cp -r 사용 시 host의 stale .o(다른 agent_version)가 들어가 make가 link만 하고 v3로 잘못 빌드되는 회귀 방지.
rm -rf /tmp/build
mkdir -p /tmp/build
rsync -a --delete \\
  --exclude='*.o' --exclude='*.a' --exclude='assessment-agent' --exclude='.git/' \\
  /mnt/agent-src/ /tmp/build/
cd /tmp/build
# pkg-config --libs libcurl이 -lcrypto·-lssl을 안 포함 — worker download.c의 EVP_DigestInit(sha256) 링크 실패 회피.
make LDFLAGS="-lcrypto -lssl -lz -lpthread"

# 4) 바이너리 설치 + systemd unit. 멱등 호출 시 binary·env·unit 변경 없으면 restart 건너뜀
#    (attention.agent_unstable false positive 회피 — 운영 환경 systemd Restart=on-failure만 트리거 정신).
needs_restart=0
if ! cmp -s assessment-agent /usr/local/bin/assessment-agent 2>/dev/null; then
  install -m 755 assessment-agent /usr/local/bin/assessment-agent
  needs_restart=1
fi

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

  # monitor-server-01 전용 — install 직후 swap reset (swap_used=0 보장).
  # yaml provision은 swap 256MB 활성 + swappiness=1로 dnf install OOM만 회피하나,
  # install 도중 OOM 직전 swap 사용된 page는 free되지 않아 SwapUsed > 0 영구 위험.
  # 명시적 swapoff/swapon으로 SwapUsed=0 reset → recommendation.classify에서
  # swap_used short-circuit 안 걸리고 monitor는 optimal 발화 (under_provisioned 아님).
  # swap_used 트리거는 app-server-01 한 곳만 — 분류 분배 일관성.
  if [ "$vm" = "monitor-server-01" ]; then
    # swapoff/swapon 분리 — `&&` 단락 평가 시 swapoff 실패 분기로 swapon이 skip돼 swap 영구 비활성 위험.
    # 둘 다 silent error tolerance (swapfile 없거나 이미 reset된 경우 OK).
    limactl shell --workdir / "$vm" sudo swapoff /swapfile 2>/dev/null || true
    limactl shell --workdir / "$vm" sudo swapon /swapfile 2>/dev/null || true
  fi
}

# offline-once mode: agent inventory 1회 발행 대기 후 agent stop + VM stop.
# 5분 후 gap_warnings 발화 (시연용 오프라인 서버 표시). host 자원 0.
finalize_vm() {
  local vm="$1"
  local mode
  mode="$(vm_mode "$vm")"
  if [ "$mode" = "offline-once" ]; then
    echo "  [$vm] inventory 1회 발행 대기 (15s) 후 offline 전환..."
    sleep 15
    limactl shell --workdir / "$vm" sudo systemctl stop assessment-agent 2>/dev/null || true
    limactl shell --workdir / "$vm" sudo systemctl disable assessment-agent 2>/dev/null || true
    limactl stop "$vm" 2>/dev/null || true
    echo "  [$vm] offline 완료 (agent disable + VM stop)"
  fi
}

start_lima_vms() {
  echo "[4/4] Lima VM 기동 + 에이전트 설치 중..."
  local agent_src
  agent_src="$(cd ../assessment-agent && pwd)"

  local vm
  for vm in "${LIMA_VMS[@]}"; do
    start_or_resume_vm "$vm" "$agent_src"
    post_provision_vm "$vm"
    finalize_vm "$vm"
  done
}

# ────────────────────────────────────────────────────────────────────────────
# Step 4: 결과 요약
# ────────────────────────────────────────────────────────────────────────────
print_summary() {
  echo ""
  echo "환경 준비 완료"
  echo "  Web UI  : http://localhost:${WEB_PORT:-8000}/servers/"
  echo "  RabbitMQ: http://localhost:${RABBITMQ_MANAGEMENT_PORT:-15672}"
  echo "  pgAdmin : 미가동 (필요 시 'docker compose --profile gui up -d pgadmin' — 약 250 MiB)"
}

main() {
  check_prereqs
  load_agent_env
  start_docker_stack
  wait_migrate_completed
  wait_web_healthy
  start_lima_vms
  print_summary
}

# 직접 실행 시만 main 호출. `source dev-up.sh`로 함수만 가져올 때는 자동 실행 안 함
# (단계별 디버깅·검증 시 유용 — VM별 start_or_resume_vm/post_provision_vm/finalize_vm 개별 호출 가능).
# set -u 환경에서 BASH_SOURCE 안전 access — macOS bash 3.2 호환.
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
