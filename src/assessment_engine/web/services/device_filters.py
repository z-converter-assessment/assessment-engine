"""에이전트가 push 하는 raw 데이터에서 비데이터/가상 항목 제거 필터 — engine 단일 진실.

마운트(데이터 볼륨): `is_data_volume(mount, major, fstype)` 단일 진실 — major==0(블록 디바이스 없는 가상 fs)
+ Windows drive + 부트 path(/boot) + 이미지 fstype(`_IMAGE_FSTYPES`)을 제외. major 가 핵심 신호(파편화된
fstype 블랙리스트를 대체). major 미전파 outbound 경로는 `_VIRTUAL_MOUNT_PREFIXES` path fallback.
디스크·인터페이스: 정규식 catalog (virtual-disk / lvm / part / virtual-iface) 블랙리스트
(가상·시스템만 제외, 나머지 통과 — 관측성 놓침 방지).
"""

import re

# 가상·시스템 디스크 (블랙리스트 — loopback·RAM·압축RAM·플로피·광학·network block). 물리 관측 대상 아님.
_VIRTUAL_DISK_RE = re.compile(r"^(loop\d*|ram\d*|zram\d*|fd\d*|sr\d*|nbd\d*)$")
_LVM_DISK_RE = re.compile(r"^(dm-\d+|md\d+)$")
_PART_DISK_RE = re.compile(r"^(sd[a-z]+\d+|vd[a-z]+\d+|hd[a-z]+\d+|xvd[a-z]+\d+|nvme\d+n\d+p\d+|mmcblk\d+p\d+)$")
# 가상·시스템 네트워크 인터페이스 (제외 — 물리 트래픽 관측 대상 아님).
# bond/team master·br/docker/virbr bridge·vlan(eth0.100) sub-interface 도 제외 — master(집계기)와
# member(물리 NIC) 양쪽이 같은 물리 트래픽을 카운트하는 이중 집계 회피(환경 합산 정합).
# 물리 member(eth/ens/eno 등)만 남긴다.
_VIRTUAL_IFACE_RE = re.compile(
    r"^(lo|veth.*|sit\d*|tunl\d*|ip6tnl\d*|gre\d*|gretap\d*|erspan\d*|dummy\d*|ifb\d*|nlmon\d*"
    r"|bond\d+|team\d+|br\d+|br-.+|docker\d+|virbr\d+(-nic)?|.+\.\d+)$"
)
# Windows NDIS 필터 드라이버 인스턴스 — "<adapter>-<filter>-NNNN" (하이픈 + 4자리 인덱스 suffix).
_WIN_IFACE_FILTER_RE = re.compile(r".+-\d{4}$")

# trailing slash 없이 통일 — startswith(p + '/') 에서 이중 슬래시 방지
_VIRTUAL_MOUNT_PREFIXES: tuple[str, ...] = (
    "/proc",
    "/sys",
    "/dev/pts",
    "/snap",
    "/run/snapd",
    "/sys/fs",
    "/sys/kernel",
    # 부트 파티션(/boot, /boot/efi — 커널·initramfs·EFI 펌웨어). 가상 fs 는 아니나 실데이터 저장 볼륨이 아니라
    # 파일시스템 용량(volume_total_gb = 실데이터 저장 총량) 의미에서 제외 — 가상 마운트와 동일 "비데이터" 정책.
    "/boot",
)


def is_virtual_disk(name: str) -> bool:
    return bool(_VIRTUAL_DISK_RE.match(name))


def is_physical_disk(name: str) -> bool:
    """물리 디스크 — 블랙리스트(가상·논리(LVM/RAID)·파티션 제외, 나머지 통과).

    화이트리스트(sd/vd/nvme 패턴만)와 달리 특이 물리 컨트롤러(mpath·cciss 등) 놓침 방지.
    """
    if not name:
        return False
    return not (is_virtual_disk(name) or is_lvm_disk(name) or is_partition(name))


def is_lvm_disk(name: str) -> bool:
    return bool(_LVM_DISK_RE.match(name))


def is_partition(name: str) -> bool:
    return bool(_PART_DISK_RE.match(name))


def is_virtual_interface(name: str) -> bool:
    """가상·시스템 레이어 인터페이스 — 물리 트래픽 관측 대상이 아닌 것만 보수적으로 제외.

    표시 경계(차트·스냅샷)에서만 적용 — 저장은 모두 유지.
    """
    if not name:
        return False
    return bool(_VIRTUAL_IFACE_RE.match(name) or _WIN_IFACE_FILTER_RE.match(name))


# data-volume 판단 (마운트 표시 필터 단일 진실) — major 주축 + Windows drive + 부트 path + 이미지 fstype.
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")
# loop 기반 read-only 이미지 — major>0(loop)이라 major 로 못 잡음. inventory defense(메트릭엔 fstype 없음).
_IMAGE_FSTYPES: frozenset[str] = frozenset({"squashfs", "iso9660", "udf"})


def is_data_volume(mount: str, major: int | None = None, fstype: str | None = None) -> bool:
    """영속 물리 블록 디바이스 위 데이터 파일시스템인지 — 마운트 표시 필터 단일 진실.

    판단(강 -> 약):
    - Windows drive(C:\\) -> 데이터 (Windows 는 major 가 항상 0이라 major 신호 무의미, drive letter 로 인정).
    - major == 0 -> 가상 fs(블록 디바이스 없음: proc/sys/tmpfs/cgroup/overlay/selinuxfs ...) -> 비데이터.
    - 부트/펌웨어 mount(/boot, /boot/efi) -> 비데이터 (실블록이나 부트 용도).
    - 이미지 fstype(squashfs/iso9660/udf) -> 비데이터 (inventory defense; 메트릭엔 fstype 없어 차트엔 미적용).
    - else -> 데이터.

    major 는 server_mount_usage(차트)·inventory 공통 신호. fstype 은 inventory 전용 보조.
    """
    if not mount:
        return False
    if _WIN_DRIVE_RE.match(mount):
        return True
    if major == 0:
        return False
    if mount == "/boot" or mount.startswith("/boot/"):
        return False
    if fstype and fstype in _IMAGE_FSTYPES:
        return False
    # major 미전파 경로(server_mount_usage outbound DTO 일부)용 fallback — 가상 커널 fs path.
    # major 가 있으면 위 major==0 이 이미 처리하므로 이 분기는 major None 일 때만.
    if major is None and any(mount == p or mount.startswith(p + "/") for p in _VIRTUAL_MOUNT_PREFIXES):
        return False
    return True


def disk_total_bytes(disks: list[dict], inventory_mounts: list[dict]) -> int:
    """디스크 총 용량(bytes) — 물리 disks 우선, 비면(Windows 등 물리 미발행) data volume 파일시스템 합.

    물리 디스크가 정석 용량이나 Windows agent 는 물리 disks 미발행(파일시스템만)이라 inventory mounts 로
    fallback. 환경·개별·세부 목록 보고서 단일 산식 (Windows 포함 일관).
    """
    physical = sum((d.get("size_bytes") or 0) for d in disks if is_physical_disk(d.get("name", "")))
    if physical > 0:
        return physical
    return sum(
        (m.get("total_bytes") or 0)
        for m in inventory_mounts
        if is_data_volume(m.get("mount", ""), None, m.get("fstype"))
    )


# ── major/minor 기반 조인 헬퍼 ──
# Linux 디바이스 식별 표준 (POSIX). 정규식 휴리스틱보다 정확.
# inventory.disks[]의 (major, minor)와 inventory.mounts[]의 (major, minor) 비교로
# "이 마운트가 어느 디스크 위인가"를 알 수 있음.
#
# 같은 major + (minor가 디스크 minor이거나 그 디스크의 파티션 minor) 이면 같은 디스크.
# SCSI/virtio 관례: minor 0,16,32,... 가 디스크 자체, 1~15 / 17~31 / ... 이 파티션.


def find_parent_disk(
    mount_major: int | None,
    mount_minor: int | None,
    disks: list[dict],
) -> str | None:
    """mount의 (major, minor)와 매칭되는 disk의 name 반환. 없으면 None."""
    if mount_major is None or mount_minor is None or mount_major == 0:
        return None
    for d in disks:
        d_major = d.get("major")
        d_minor = d.get("minor")
        if d_major is None or d_minor is None:
            continue
        if d_major != mount_major:
            continue
        # 디스크 자체 또는 파티션 (minor 차이 1~15)
        if d_minor == mount_minor or 0 < (mount_minor - d_minor) < 16:
            return d.get("name")
    return None
