#!/usr/bin/env bash
# dev Windows agent 부분 자동화 — UTM Win11 ARM VM(win-server-01) start + agent.env 갱신 + 서비스 restart.
#
# 책임 분담 (docs/development/windows-vm.md 단일 진실):
#   [1회 수동] UTM VM 생성·Windows 설치·OpenSSH+SSH키·build-windows.ps1(.exe)·install.ps1(서비스 등록)·IIS/redis
#   [본 스크립트 자동] utmctl start -> VM IP·host IP 확인 -> agent.env 생성(host IP 주입) -> scp -> 서비스 restart
#
# 왜 host IP 주입: OrbStack 의 host.docker.internal 은 UTM VM 미해석. Windows agent 의 RABBITMQ_HOST 는
# host 의 실제 IP 여야 host docker compose 의 rabbitmq(5672 포트 매핑)에 도달. host IP 는 네트워크 환경
# (Wi-Fi/Ethernet·DHCP)마다 바뀌므로 매 실행 재확인 후 agent.env 덮어쓰기 + restart (Linux /etc/ heredoc 대응).
#
# 멱등성: 안전 재실행. VM 이미 started 면 start 무해, agent.env 매번 갱신, 서비스 restart.

set -euo pipefail

# 호출 위치 무관 — dev/.. = 프로젝트 루트로 cwd 고정.
cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.."

# pipeline-up.sh 의 load_agent_env·REQUIRED_AGENT_KEYS 재사용 (source guard 로 main 자동 실행 안 함).
# shellcheck disable=SC1091
source dev/pipeline-up.sh

# ────────────────────────────────────────────────────────────────────────────
# 설정 (env override)
# ────────────────────────────────────────────────────────────────────────────
WIN_VM_NAME="${WIN_VM_NAME:-win-server-01}"
WIN_SSH_USER="${WIN_SSH_USER:-test}"      # dev VM 계정 (다른 환경은 env override)
WIN_HOST_IFACE="${WIN_HOST_IFACE:-en0}"   # 3순위 fallback (bridged network 등 비-NAT 환경)
UTMCTL="${UTMCTL:-utmctl}"
# WIN_VM_IP — utmctl ip-address 는 QEMU 게스트 에이전트(SPICE 도구) 설치 시에만 동작.
# 미설치면 host ARP 또는 VM 안 ipconfig 로 확인한 IP 를 env 로 직접 지정.
WIN_VM_IP="${WIN_VM_IP:-}"
# WIN_HOST_IP — VM 에서 host docker rabbitmq 도달 IP. 미지정 시 resolve_host_ip 가 자동 추론
# (UTM shared network gateway = host). bridged network 등에서만 명시 필요.
WIN_HOST_IP="${WIN_HOST_IP:-}"
# Windows agent env 파일 위치 (deploy/install.ps1 단일 진실).
readonly WIN_ENV_PATH='C:/ProgramData/assessment-agent/agent.env'

# ────────────────────────────────────────────────────────────────────────────
# 사전 점검
# ────────────────────────────────────────────────────────────────────────────
check_win_prereqs() {
  if ! command -v "$UTMCTL" >/dev/null 2>&1; then
    echo "오류: utmctl 없음. brew install --cask utm 후 symlink:" >&2
    echo "  ln -s /Applications/UTM.app/Contents/MacOS/utmctl /opt/homebrew/bin/utmctl" >&2
    exit 1
  fi
  # UTM 앱 기동 필요 (headless 라도 앱 daemon 은 떠 있어야 utmctl 동작).
  if ! pgrep -x UTM >/dev/null 2>&1; then
    echo "  UTM 앱 미기동 — open -a UTM"
    open -a UTM
    sleep 3
  fi
  # VM 존재 확인 — 없으면 수동 생성 안내 후 exit (UTM VM 생성은 GUI 라 자동화 불가).
  if ! "$UTMCTL" list 2>/dev/null | grep -qw "$WIN_VM_NAME"; then
    cat >&2 <<MSG
오류: UTM VM '$WIN_VM_NAME' 없음.
docs/development/windows-vm.md 수동 절차로 1회 생성 필요:
  1. UTM 으로 Windows 11 ARM VM 생성·설치 (ISO·TPM·Secure Boot — GUI)
  2. OpenSSH Server 활성 + SSH 공개키 등록 (비대화형 ssh 위해)
  3. windows-agent/scripts/build-windows.ps1 로 assessment-agent.exe 빌드
  4. windows-agent/deploy/install.ps1 로 서비스 등록 (agent.env 최초 seed)
  5. IIS(웹) + redis(Memurai 등) 설치
이후 본 스크립트가 start·env 갱신·restart 자동화.
MSG
    exit 1
  fi
}

# ────────────────────────────────────────────────────────────────────────────
# VM 기동 + IP 확인
# ────────────────────────────────────────────────────────────────────────────
start_win_vm() {
  echo "[1/3] UTM VM '$WIN_VM_NAME' 기동..."
  # started 면 무해 (utmctl start 멱등). 상태 출력만.
  "$UTMCTL" start "$WIN_VM_NAME" >/dev/null 2>&1 || true
}

# guest IP — WIN_VM_IP env 우선, 없으면 utmctl(게스트 에이전트), 그래도 없으면 host ARP.
win_vm_ip() {
  # 0순위: env 직접 지정 (게스트 에이전트 미설치 시 — VM 안 ipconfig 로 확인한 IP).
  if [ -n "$WIN_VM_IP" ]; then echo "$WIN_VM_IP"; return 0; fi

  local secs=0 ip=""
  while [ "$secs" -lt 60 ]; do
    # 1순위: utmctl ip-address (QEMU 게스트 에이전트=SPICE 도구 설치 시).
    ip="$("$UTMCTL" ip-address "$WIN_VM_NAME" 2>/dev/null \
          | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' \
          | grep -vE '^(169\.254|127\.)' \
          | head -1 || true)"
    [ -n "$ip" ] && { echo "$ip"; return 0; }
    # 2순위: host ARP (UTM bridge 에 VM MAC-IP 매핑). 게스트 에이전트 없어도 동작.
    ip="$(arp -an 2>/dev/null | grep -oE '192\.168\.6[0-9]\.[0-9]+' | grep -v '\.1$' | head -1 || true)"
    [ -n "$ip" ] && { echo "$ip"; return 0; }
    sleep 3; secs=$((secs+3))
  done
  echo "오류: $WIN_VM_NAME IP 미확인. WIN_VM_IP env 로 직접 지정 (VM 안 ipconfig 의 IPv4)." >&2
  return 1
}

# host IP — Windows agent 의 RABBITMQ_HOST 대상 (VM->host docker rabbitmq 도달).
# UTM shared network 에서 host 는 VM subnet 의 gateway (예: 192.168.64.1) — en0(Wi-Fi LAN)은
# NAT 밖이라 VM 미도달. VM IP 의 /24 에 속한 host bridge inet 을 우선 채택.
resolve_host_ip() {
  local ip=""
  # 1순위: WIN_HOST_IP env (운영자 명시 — bridged network 등 비표준).
  if [ -n "$WIN_HOST_IP" ]; then echo "$WIN_HOST_IP"; return 0; fi
  # 2순위: VM subnet 의 host bridge inet (UTM shared network gateway = host).
  #         main 이 win_vm_ip 결과를 WIN_VM_IP 에 채운 뒤 호출하므로 그 subnet 사용.
  if [ -n "$WIN_VM_IP" ]; then
    local subnet="${WIN_VM_IP%.*}."
    ip="$(ifconfig 2>/dev/null | grep "inet ${subnet}" | awk '{print $2}' | head -1)"
  fi
  # 3순위: WIN_HOST_IFACE LAN IP (bridged network 등 비-NAT 환경에서만 유효).
  if [ -z "$ip" ]; then ip="$(ipconfig getifaddr "$WIN_HOST_IFACE" 2>/dev/null || true)"; fi
  if [ -z "$ip" ]; then
    echo "오류: host IP 미확인. WIN_HOST_IP env 로 명시 (VM 의 default gateway = host)." >&2
    return 1
  fi
  echo "$ip"
}

# ────────────────────────────────────────────────────────────────────────────
# agent.env 갱신 + 서비스 restart (OpenSSH)
# ────────────────────────────────────────────────────────────────────────────
win_ssh() {
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 \
      "${WIN_SSH_USER}@${WIN_VM_IP}" "$@"
}

deploy_win_agent() {
  echo "[2/3] agent.env 생성·전송 (host IP 주입)..."
  local host_ip tmp_env
  host_ip="$(resolve_host_ip)"
  echo "  host IP : $host_ip (RABBITMQ_HOST·WORKER_DOWNLOAD_ALLOWED_HOSTS)"
  echo "  VM  IP  : $WIN_VM_IP"

  # 평문 secret 이 들어가므로 host /tmp 에 짧게만 두고 전송 직후 삭제 (#F8).
  # trap RETURN 은 set -u + local 스코프에서 RETURN 시점 unbound 평가 이슈 → 함수 끝 명시 rm.
  tmp_env="$(mktemp)"
  cat > "$tmp_env" <<ENV
RABBITMQ_HOST=$host_ip
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
WORKER_DOWNLOAD_ALLOWED_HOSTS=$host_ip
AGENT_HOSTNAME_OVERRIDE=$WIN_VM_NAME
AGENT_INTERVAL_SEC=60
ENV

  # Windows OpenSSH scp — C:/ 경로. 덮어쓰기 (host 가 master, Linux /etc/ heredoc 대응).
  scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "$tmp_env" "${WIN_SSH_USER}@${WIN_VM_IP}:${WIN_ENV_PATH}"

  echo "[3/3] 서비스 restart..."
  win_ssh 'powershell -NoProfile -Command "Restart-Service assessment-agent"'

  rm -f "$tmp_env"   # 평문 secret 임시파일 정리
}

# ────────────────────────────────────────────────────────────────────────────
print_win_summary() {
  echo ""
  echo "Windows agent 부분 자동화 완료"
  echo "  VM       : $WIN_VM_NAME ($WIN_VM_IP)"
  echo "  agent.env: $WIN_ENV_PATH (RABBITMQ_HOST 주입 완료)"
  echo "  확인:"
  echo "    RabbitMQ 콘솔  : http://localhost:${RABBITMQ_MANAGEMENT_PORT:-15672} ($WIN_VM_NAME 메시지 적재)"
  echo "    Web UI         : http://localhost:${WEB_PORT:-8000}/servers/ ($WIN_VM_NAME 등록, os_family=windows)"
  echo "    서비스 상태    : ssh ${WIN_SSH_USER}@${WIN_VM_IP} 'powershell -Command \"Get-Service assessment-agent\"'"
}

main() {
  check_win_prereqs
  load_agent_env          # pipeline-up.sh 정의 재사용 (dev/agent.env source + 필수 키 검증)
  start_win_vm
  WIN_VM_IP="$(win_vm_ip)"
  deploy_win_agent
  print_win_summary
}

# 직접 실행 시만 main (source 로 함수만 가져올 때 자동 실행 안 함).
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
