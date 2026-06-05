"""에이전트가 push 하는 raw 데이터에서 가상·커널 항목 제거 필터 — engine 단일 진실.

`_VIRTUAL_FSTYPES` 22 / `_VIRTUAL_MOUNT_PREFIXES` 8 / 정규식 catalog (virtual-disk / lvm / part / virtual-iface).
디스크·인터페이스는 블랙리스트 정책 (가상·시스템만 제외, 나머지 통과 — 관측성 놓침 방지).
새 fstype 출현 시 본 catalog 만 갱신.
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

_VIRTUAL_FSTYPES: frozenset[str] = frozenset(
    {
        "proc",
        "sysfs",
        "devtmpfs",
        "devpts",
        "hugetlbfs",
        "debugfs",
        "tracefs",
        "squashfs",  # snap 패키지 read-only 마운트
        "nsfs",  # Linux namespace 마운트 (snapd ns, lxd.mnt 등)
        "overlay",  # Docker 컨테이너 레이어
        "cgroup",
        "cgroup2",
        "pstore",
        "bpf",
        "fusectl",
        "efivarfs",
        "configfs",
        "securityfs",
        "mqueue",
        "ramfs",
        "iso9660",  # ISO 이미지 read-only 마운트 (cloud-init ISO·CD-ROM 등) — 본질적으로 100% used
        "udf",  # DVD/Blu-ray UDF 파일시스템 — 동일 사유
    }
)

# trailing slash 없이 통일 — startswith(p + '/') 에서 이중 슬래시 방지
_VIRTUAL_MOUNT_PREFIXES: tuple[str, ...] = (
    "/proc",
    "/sys",
    "/dev/pts",
    "/snap",
    "/run/snapd",
    "/sys/fs",
    "/sys/kernel",
)


def is_virtual_disk(name: str) -> bool:
    """디스크가 가상·시스템(loopback·RAM·압축RAM·플로피·광학·network block)인지 — 블랙리스트."""
    return bool(_VIRTUAL_DISK_RE.match(name))


def is_physical_disk(name: str) -> bool:
    """물리 디스크 — 블랙리스트(가상·논리(LVM/RAID)·파티션 제외, 나머지 통과).

    화이트리스트(알려진 sd/vd/nvme 패턴만)와 달리 특이 물리 컨트롤러(mpath·cciss 등) 놓침 방지 (관측성 정석).
    """
    if not name:
        return False
    return not (is_virtual_disk(name) or is_lvm_disk(name) or is_partition(name))


def is_lvm_disk(name: str) -> bool:
    return bool(_LVM_DISK_RE.match(name))


def is_partition(name: str) -> bool:
    return bool(_PART_DISK_RE.match(name))


def is_virtual_interface(name: str) -> bool:
    """네트워크 인터페이스가 가상·시스템 레이어(루프백·터널·veth·dummy·Windows NDIS 필터)인지.

    물리 트래픽 관측 대상이 아닌 것만 보수적으로 제외 (docker/br/bond/vlan 회색지대는 통과).
    표시 경계(차트·스냅샷)에서만 적용 — 저장은 모두 유지.
    """
    if not name:
        return False
    return bool(_VIRTUAL_IFACE_RE.match(name) or _WIN_IFACE_FILTER_RE.match(name))


def is_virtual_mount(fstype: str | None, mount: str) -> bool:
    if fstype and fstype in _VIRTUAL_FSTYPES:
        return True
    return any(mount == p or mount.startswith(p + "/") for p in _VIRTUAL_MOUNT_PREFIXES)


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
    """mount의 (major, minor)와 매칭되는 disk의 name 반환. 없으면 None.

    매칭 규칙:
    - mount.major == disk.major AND mount.minor == disk.minor → 디스크 자체에 마운트
    - mount.major == disk.major AND mount.minor 가 disk.minor 의 파티션 영역 → 그 디스크의 파티션
      (SCSI/virtio: disk.minor + 1..15)
    - 가상 파일시스템(major=0, tmpfs 등)은 None.
    """
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
