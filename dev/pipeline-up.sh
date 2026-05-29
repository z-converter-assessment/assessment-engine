#!/usr/bin/env bash
# dev 풀 파이프라인 기동: Docker → migrate → web 헬스체크 → OrbStack VM + 에이전트 설치.
#
# LLM 서버(ollama) 제거 — 파이프라인 검증 한정 (dev pipeline 시연). AI 진단 발행 시 LLM 호출
# 실패 시나리오를 의도적으로 재현 (docker-compose.yml diagnostic-worker 주석 참조). ollama 모델
# pull 단계 없음.
#
# 책임 분담:
#   - agent 바이너리는 `dev/bin/assessment-agent` 로 산출 — 본 스크립트 `ensure_agent_binary` 단계가 확보.
#     AGENT_BINARY_URL set 시 curl fetch (향후 agent CI release artifact 자동화 분기),
#     미설정 시 `dev/agent-build/build.sh` 호출 (sibling repo cross-build, default ../assessment-agent).
#   - Docker compose는 dev/docker-compose.yml (dev 한정 #A0, ADR 0012). migrate init-container가 alembic 자동 적용.
#   - OrbStack VM 은 `orb create <distro> <name>` 로 즉시 생성 — cloud-init 이 없어 Lima 의 boot stuck 우회 로직 불필요.
#   - VM 안 작업(패키지·바이너리·systemd·합성 부하 timer)은 모두 본 스크립트 post-provision — 옛 Lima yaml provision 을 흡수.
#
# OrbStack 전환 가정 (Lima → OrbStack):
#   - VM 명령: `ssh <name>@orb` (OrbStack 이 ~/.ssh/config 에 <name>@orb 자동 등록). sudo 는 passwordless.
#   - host 파일 전달: 바이너리는 ssh stdin(tee) 으로 — host 디렉토리 자동 마운트 가정·권한 문제 회피.
#   - VM 도달: web 컨테이너 → `<name>.orb.local:22` 직접 (Lima user-mode localPort 포워딩 폐기).
#   - host 도달: VM·컨테이너 → `host.docker.internal` (OrbStack 이 양쪽에서 host 로 해석).
#
# 멱등성: 모든 단계 안전 재실행. VM 이미 있으면 create 건너뜀, post-provision은 매번 재적용.

set -euo pipefail

# 호출 위치 무관 정합 — scripts/.. = 프로젝트 루트로 cwd 고정.
# BASH_SOURCE는 source된 스크립트 자체 경로 (직접 실행 시 $0과 동일) — source 시 부모의 $0으로 잘못 cd하지 않게.
cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.."

# dev compose 단일 진실 — `docker compose` 호출이 본 파일을 자동 인식.
export COMPOSE_FILE=dev/docker-compose.yml

# ────────────────────────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────────────────────────
readonly TIMEOUT=180                         # docker compose / migrate / web 헬스체크 공통 cap
# VM 매트릭스 — 1 VM = 2 서비스 (service_classifier 6 카테고리 최대 커버).
# Windows(win-server-01: IIS+redis)는 OrbStack 미지원 → UTM 별도 (docs/development/windows-vm.md).
# 본 OrbStack 배열은 Linux 3대만.
#
#   (1) app-server-01   — Debian 12 + nginx + rabbitmq (web + mq)
#                         agent-restart-demo timer (1m boot + 3m 주기) → attention.agent_unstable
#                         external IP 부여 (web-facing) — IP 분류 시연
#   (2) data-server-01  — Rocky 9  + postgresql + zabbix-agent (db + monitor)
#                         swap-trigger → attention.capacity_warnings (under_provisioned)
#   (3) edge-server-01  — Debian 12 + docker + memcached (container + cache)
#                         offline-demo → N회(기본 3) 발행 후 agent stop → attention.gap_warnings
# ORB_VMS_FILTER env로 약식 검증 가능 (콤마 구분, 예: `ORB_VMS_FILTER=app-server-01`).
# 호스트 영향 최소화 위해 모든 VM 의 합성 부하는 light (sustained CPU 1~3s + mem 5~20MB).
if [ -n "${ORB_VMS_FILTER:-}" ]; then
  IFS=',' read -ra ORB_VMS <<< "$ORB_VMS_FILTER"
else
  ORB_VMS=(app-server-01 data-server-01 edge-server-01)
fi
readonly ORB_VMS

# VM별 dispatch — services/ext_ip/distro는 pipeline-up.sh가 단일 진실로 갖는다.
# associative array 대신 함수 — macOS default bash 3.2 호환 (brew bash 의존 없음).
# vm_service 는 공백 구분 다중 서비스 — post_provision_vm 이 for-loop 으로 각각 설치.
vm_service() {
  case "$1" in
    app-server-01)   echo "nginx rabbitmq" ;;
    data-server-01)  echo "postgres zabbix" ;;
    edge-server-01)  echo "docker memcached" ;;
    *) echo "오류: 알 수 없는 VM: $1" >&2; return 1 ;;
  esac
}
vm_ext_ip() {
  case "$1" in
    app-server-01) echo "203.0.113.10" ;;
    *)             echo "" ;;
  esac
}
# orb create 이미지 태그 — OrbStack 이 arch(Apple Silicon = arm64)·cloud image pull 자동 처리.
# 서비스 설치 난이도 기준 distro 배치: docker.io·rabbitmq-server 는 apt(debian) 가 단순,
# postgresql-server·zabbix-agent(EPEL)는 dnf(rocky). apt/dnf 양 family 검증 유지.
vm_distro() {
  case "$1" in
    app-server-01)   echo "debian:12" ;;
    data-server-01)  echo "rocky:9" ;;
    edge-server-01)  echo "debian:12" ;;
    *) echo "오류: 알 수 없는 VM: $1" >&2; return 1 ;;
  esac
}

# probe 도달 시연 — OrbStack VM 은 `<name>.orb.local:22` 로 web 컨테이너가 직접 도달
# (Lima user-mode localPort 포워딩 불필요). probe 대상(app-server-01.orb.local 등)은 docker-compose
# DISCOVERY_DEFAULT_TARGET default 가 단일 진실.

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
check_prereqs() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "오류: docker가 PATH에 없다."
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "오류: docker daemon 미가동. OrbStack 기동 후 재시도."
    exit 1
  fi
  if ! command -v orb >/dev/null 2>&1; then
    echo "오류: orb가 PATH에 없다. OrbStack 설치 후 재시도 (https://orbstack.dev)."
    exit 1
  fi
  # env 파일 자동 cp — 없으면 example 복사 (dev 한정, example default 그대로 충분).
  # 루트 .env.example 은 prod 운영자 카탈로그라 본 dev 파이프라인은 dev/.env.example 사용.
  if [ ! -f dev/.env ]; then
    echo "  dev/.env 없음 — dev/.env.example 복사"
    cp dev/.env.example dev/.env
  fi
  if [ ! -f dev/agent.env ]; then
    echo "  dev/agent.env 없음 — dev/agent.env.example 복사"
    cp dev/agent.env.example dev/agent.env
  fi
  # 본 dev 파이프라인은 Apple Silicon + arm64 VM 가정. host arch 검증.
  if [ "$(uname -m)" != "arm64" ]; then
    echo "경고: dev 파이프라인은 Apple Silicon(arm64) 가정. 다른 host arch에선 바이너리 호환 검토 필요."
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
  # discovery probe 는 web 컨테이너 → app-server-01.orb.local:22 직접 (docker-compose default).
  # Lima 처럼 port 를 export 할 필요 없음 — OrbStack VM 은 표준 22 SSH 직접 도달.
  docker compose --profile gui up -d --build
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
# Step 4: OrbStack VM 기동 + 에이전트 설치
# ────────────────────────────────────────────────────────────────────────────

# ssh 헬퍼 — OrbStack 이 등록한 `<name>@orb` 호스트로 명령. StrictHostKeyChecking 끔 (dev 재생성 빈번).
orb_ssh() {
  local vm="$1"; shift
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 "${vm}@orb" "$@"
}

# orb create 멱등 — 이미 있으면 start, 없으면 create (동기, cloud-init 없어 즉시 ready).
start_or_resume_vm() {
  local vm="$1"
  local distro
  distro="$(vm_distro "$vm")"

  if orb list 2>/dev/null | grep -qw "$vm"; then
    echo "  [$vm] 이미 존재 — start (멱등)"
    orb start "$vm" >/dev/null 2>&1 || true
  else
    echo "  [$vm] create ($distro, 첫 실행 시 cloud image pull)..."
    orb create "$distro" "$vm"
  fi

  # SSH 도달 검증 — orb create 완료 = ready 라 짧은 cap 으로 충분 (Lima 의 boot stuck 우회 로직 불필요).
  local secs=0
  until orb_ssh "$vm" echo ok 2>/dev/null | grep -q ok; do
    sleep 2; secs=$((secs+2))
    if [ "$secs" -ge 60 ]; then
      echo "  [$vm] SSH 60s 초과 — boot 실패"
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
  orb_ssh "$vm" 'cat > /tmp/assessment-agent' < dev/bin/assessment-agent

  echo "  [$vm] post-provision (env + 패키지 + 바이너리 + systemd)..."
  orb_ssh "$vm" sudo bash -s <<SCRIPT
set -euo pipefail

# 1) /etc/assessment-agent.env — host의 dev/agent.env + host.docker.internal + VM별 hostname/ext_ip.
#    env_needs_restart=1이면 env 변경됨 → 뒤 단계에서 restart 트리거.
new_env=\$(cat <<ENV
RABBITMQ_HOST=host.docker.internal
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
#    agent 바이너리는 본 repo dev/bin/에 사전 commit (Apple Silicon arm64 dev 한정).
#    devel 패키지·gcc·make 불필요 — runtime OpenSSL/glibc/zlib만 동적 의존이고 모두 base distro 기본 포함.
. /etc/os-release

# dnf_opts — case 밖 정의 (set -u 안전, debian 경로는 미참조). install_weak_deps=False 로
# Recommends 차단(transaction 메모리·디스크 절약, 1GiB OOM 회피), tsflags=nodocs 로 man/info skip.
dnf_opts="--setopt=install_weak_deps=False --setopt=tsflags=nodocs"

# base 패키지 — openssh-server(서버 발견 probe 시연: web 컨테이너 -> VM IP:22) + curl + ping.
# OrbStack VM 은 표준 22 sshd 미탑재(OrbStack SSH 는 host-network proxy)라 명시 설치.
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

  case "\${ID}" in
    ubuntu|debian)        apt-get install -y --no-install-recommends "\${pkg}" ;;
    rocky|rhel|almalinux) dnf install -y \${dnf_opts} "\${pkg}" ;;
  esac

  # postgres RPM family 는 cluster init 수동 필요. apt 계열은 install 시 자동 init라 skip.
  if [ "\${svc}" = "postgres" ]; then
    case "\${ID}" in
      rocky|rhel|almalinux) postgresql-setup --initdb 2>/dev/null || true ;;
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

# 합성 부하 timer 설치 — persistent VM(web/cache/db) 공통. 옛 Lima yaml provision 흡수.
# light (sustained CPU 1~3s + mem 5~20MB) — 차트 변동만 가시화. host.docker.internal 로 ping/curl.
install_synthetic_load() {
  local vm="$1"
  orb_ssh "$vm" sudo bash -s <<'SCRIPT'
set -euo pipefail
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
ping -c $(( (RANDOM % 5) + 3 )) -q host.docker.internal > /dev/null 2>&1 || true
curl -s -m 2 http://host.docker.internal:8000/health > /dev/null 2>&1 || true
if [ $((RANDOM % 2)) -eq 0 ]; then
  curl -s -m 2 http://host.docker.internal:8000/static/js/chart-utils.js > /dev/null 2>&1 || true
fi
EOF
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

# swap-trigger 설치 (data-server-01 전용) — boot 1회 swap 활성 + 메모리 압박 → swap_used 임계 초과.
# attention.capacity_warnings(under_provisioned) 발화 시연용. CPU 부하 추가 없음 (호스트 영향 회피).
install_swap_trigger() {
  local vm="$1"
  orb_ssh "$vm" sudo bash -s <<'SCRIPT'
set -euo pipefail
cat > /usr/local/bin/swap-trigger.sh <<'EOF'
#!/bin/bash
# 1) swap 파일 생성·활성 (cloud image default 에는 swap 없음).
if ! swapon --show | grep -q '^/swapfile'; then
  fallocate -l 512M /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=512
  chmod 600 /swapfile
  mkswap /swapfile > /dev/null
  swapon /swapfile
fi
# 2) swappiness 높여 kernel 이 swap 적극 사용하게.
sysctl -w vm.swappiness=100 > /dev/null
# 3) 메모리 압박 — 1000MiB 점유 + lock (RAM 부족 → swap out).
nohup bash -c 'head -c 1000M /dev/urandom > /tmp/swap-pressure 2>/dev/null; sleep 86400' &
EOF
chmod 755 /usr/local/bin/swap-trigger.sh
cat > /etc/systemd/system/swap-trigger.service <<'EOF'
[Unit]
Description=Swap trigger (attention.under_provisioned 시연용)
After=multi-user.target
[Service]
Type=oneshot
RemainAfterExit=true
ExecStart=/usr/local/bin/swap-trigger.sh
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable swap-trigger.service
systemctl start swap-trigger.service
SCRIPT
}

# agent-restart-demo 설치 (app-server-01 전용) — agent 주기 restart 로 agent_unstable 항상 발화.
# 1시간 슬라이딩 윈도우 임계 3회를 큰 마진으로 초과 (OnBootSec=1min + OnUnitActiveSec=3min → 시간당 약 20회).
install_agent_restart_demo() {
  local vm="$1"
  orb_ssh "$vm" sudo bash -s <<'SCRIPT'
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
# `orb start edge-server-01` 재기동(pipeline-up.sh 재실행 시 멱등 재적용, 다시 3회 후 down 반복).
# agent stop 대신 poweroff 채택 — 사용자 요구("VM 이 내려감") 의도 충실. gap_warnings 발화는 둘 다 동일.
install_offline_demo() {
  local vm="$1"
  local down_after="${OFFLINE_DOWN_AFTER_SEC:-180}"
  orb_ssh "$vm" sudo bash -s <<SCRIPT
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
    data-server-01) install_swap_trigger "$vm" ;;
    app-server-01)  install_agent_restart_demo "$vm" ;;
    edge-server-01) install_offline_demo "$vm" ;;
  esac
}

start_orb_vms() {
  echo "[4/4] OrbStack VM 기동 + 에이전트 설치 중..."
  local vm
  for vm in "${ORB_VMS[@]}"; do
    start_or_resume_vm "$vm"
    post_provision_vm "$vm"
    install_demo_loads "$vm"
  done
}

# ────────────────────────────────────────────────────────────────────────────
# Step 5: 결과 요약
# ────────────────────────────────────────────────────────────────────────────
print_summary() {
  echo ""
  echo "환경 준비 완료"
  echo "  Web UI  : http://localhost:${WEB_PORT:-8000}/servers/"
  echo "  RabbitMQ: http://localhost:${RABBITMQ_MANAGEMENT_PORT:-15672}"
  # 서버 발견 probe 시연 — OrbStack .orb.local 은 컨테이너 미해석이라 운영자가 VM IP 를 모달에 직접 입력.
  echo "  서버 발견 probe 대상 VM IP (모달에 입력 -> SSH 도달성 확인):"
  local vm ip
  for vm in "${ORB_VMS[@]}"; do
    ip="$(orb_ssh "$vm" hostname -I 2>/dev/null | awk '{print $1}')"
    [ -n "$ip" ] && echo "    $vm : $ip"
  done
}

main() {
  check_prereqs
  ensure_agent_binary
  load_agent_env
  start_docker_stack
  wait_migrate_completed
  wait_web_healthy
  start_orb_vms
  print_summary
}

# 직접 실행 시만 main 호출. `source pipeline-up.sh`로 함수만 가져올 때는 자동 실행 안 함
# (단계별 디버깅·검증 시 유용 — VM별 start_or_resume_vm/post_provision_vm/install_demo_loads 개별 호출 가능).
# set -u 환경에서 BASH_SOURCE 안전 access — macOS bash 3.2 호환.
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
