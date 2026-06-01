#!/usr/bin/env bash
# dev-down.sh: 전체 dev 환경 정리 — 모든 VM(Linux + Windows) 삭제 + docker 볼륨 제거.
#
# 무보존 원칙: 다음 dev-up 이 provisioning(OS 무인설치·agent/redis/postgres 설치)을 매번 다시
# 수행해 "설치 과정" 자체를 검증하도록, VM(qcow2)·DB·메트릭을 전부 삭제한다. Windows 도 비보존
# (이전 stop/start 보존 폐기 — 설치 검증 위해 매번 재설치, 시간이 걸려도 의도된 검증 항목).
#
# 캐시 보존(로직 무관 다운로드물만): base cloud image / Windows Server ISO / redis MSI /
# windows-agent vendor static libs. 앞 셋은 libvirt pool·dev/run 에 남기고, vendor libs 는
# windows-agent repo 안(본 dev repo 밖)이라 본 스크립트가 애초에 건드리지 않는다.
#
# 함수·VMS·WIN_VM_NAME·LIBVIRT_POOL·WIN_* 등은 dev-up.sh 단일 진실 — source guard 로 함수만
# 가져옴(main 자동 실행 안 함). dev-up.sh 가 LIBVIRT_DEFAULT_URI=qemu:///system 를 export.
set -euo pipefail

# 호출 위치 무관 정합 — SCRIPT_DIR 절대 경로 확정 후 cwd 고정 + source 절대화.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# shellcheck disable=SC1091
source "$SCRIPT_DIR/dev-up.sh"

echo "[1/2] libvirt VM 제거 중 (Linux ${#VMS[@]} + Windows)..."
if command -v virsh >/dev/null 2>&1 && virsh version >/dev/null 2>&1; then
  # Linux VM — clone qcow2 + seed iso 삭제(--remove-all-storage). base cloud image 는 도메인
  # 비연결(clone 원본)이라 보존. nvram 옵션은 Linux(BIOS)엔 무해.
  for vm in "${VMS[@]}"; do
    if virsh dominfo "$vm" >/dev/null 2>&1; then
      echo "  $vm destroy + undefine (clone 디스크·seed 삭제, base 이미지 보존)..."
      virsh destroy "$vm" >/dev/null 2>&1 || true   # 실행 중이면 강제 정지 (shut off 면 무해)
      virsh undefine "$vm" --remove-all-storage --nvram >/dev/null 2>&1 \
        || virsh undefine "$vm" --remove-all-storage >/dev/null 2>&1 || true
    else
      echo "  $vm 도메인 없음 — 건너뜀"
    fi
  done

  # orphan 볼륨 청소 — dev-up 이 define 전(vol-clone~define 사이)에 SIGINT 로 죽으면 도메인 없이
  # clone disk/seed 볼륨이 남는다. 도메인 기반 undefine 이 못 잡으므로 명시적 vol-delete.
  # base cloud image(vm_base_vol, distro 이름)·Windows ISO 는 이름이 달라 미해당 — 캐시 보존.
  # 이미 undefine --remove-all-storage 로 지워졌으면 무해(|| true).
  for vm in "${VMS[@]}"; do
    virsh vol-delete "${vm}.qcow2" --pool "$LIBVIRT_POOL" >/dev/null 2>&1 || true
    virsh vol-delete "${vm}-seed.iso" --pool "$LIBVIRT_POOL" >/dev/null 2>&1 || true
  done

  # Windows VM — destroy + undefine(--nvram). 스토리지는 --remove-all-storage 미사용:
  # cdrom 에 base Server ISO($WIN_ISO_VOL, ~4.7GB 캐시)가 연결돼 함께 지워지면 재다운로드 발생.
  # 설치 산출물(qcow2)·VM 별 unattend ISO 만 명시적 vol-delete. base Server ISO 와 골든 이미지
  # (${WIN_VM_NAME}-golden.qcow2, OS 설치 완성본)는 캐시라 보존 — 다음 dev-up 이 골든 clone 으로
  # OS 무인설치(~20min)를 skip. 골든 재생성(예: 대대적 갱신)이 필요하면 수동으로:
  #   virsh vol-delete ${WIN_VM_NAME}-golden.qcow2 --pool $LIBVIRT_POOL
  if virsh dominfo "$WIN_VM_NAME" >/dev/null 2>&1; then
    echo "  $WIN_VM_NAME destroy + undefine (qcow2·unattend ISO 삭제, base Server ISO 보존)..."
    virsh destroy "$WIN_VM_NAME" >/dev/null 2>&1 || true
    virsh undefine "$WIN_VM_NAME" --nvram >/dev/null 2>&1 \
      || virsh undefine "$WIN_VM_NAME" >/dev/null 2>&1 || true
    virsh vol-delete "${WIN_VM_NAME}.qcow2" --pool "$LIBVIRT_POOL" >/dev/null 2>&1 || true
    virsh vol-delete "$WIN_UNATTEND_VOL" --pool "$LIBVIRT_POOL" >/dev/null 2>&1 || true
  else
    echo "  $WIN_VM_NAME 도메인 없음 — 건너뜀"
  fi

  # VMS 외 본 프로젝트 명명 패턴 잔재 — 알림만 (자동 삭제 안 함, 다른 워크로드 보호).
  remaining=$(virsh list --all --name 2>/dev/null | grep -E "(cache|app|web|db|data|edge|legacy-mq|monitor|mq|offline|container)-server-01" | grep -vF -f <(printf '%s\n' "${VMS[@]}") || true)
  if [ -n "$remaining" ]; then
    echo "  주의: VMS 외 프로젝트 명명 패턴 잔재 발견 — 수동 정리 검토:"
    echo "$remaining"
  fi
else
  echo "  virsh/libvirt 접근 불가 — 건너뜀"
fi

echo "[2/2] Docker 서비스 및 볼륨 제거 중 (DB·메트릭·redis 데이터 전부 삭제)..."
docker compose down -v

echo ""
echo "환경 종료 완료 — 모든 VM(Linux ${#VMS[@]} + Windows) 삭제 + Docker 컨테이너·볼륨 제거."
echo "캐시 보존: base cloud image / Windows Server ISO + 골든(OS 설치본) / redis MSI / vendor libs."
echo "다음 dev-up 은 provisioning·설치를 처음부터 다시 수행 (설치 과정 검증)."
