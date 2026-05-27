"""에이전트가 push 하는 raw 데이터에서 가상·커널 항목 제거 필터 — engine 단일 진실.

`_VIRTUAL_FSTYPES` 22 / `_VIRTUAL_MOUNT_PREFIXES` 8 / 정규식 3 (phys / lvm / part) catalog.
새 fstype 출현 시 본 catalog 만 갱신.
"""

import re

_PHYS_DISK_RE = re.compile(r"^(sd[a-z]+|vd[a-z]+|hd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+|PhysicalDrive\d+)$")
_LVM_DISK_RE = re.compile(r"^(dm-\d+|md\d+)$")
_PART_DISK_RE = re.compile(r"^(sd[a-z]+\d+|vd[a-z]+\d+|hd[a-z]+\d+|xvd[a-z]+\d+|nvme\d+n\d+p\d+|mmcblk\d+p\d+)$")

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


def is_physical_disk(name: str) -> bool:
    return bool(_PHYS_DISK_RE.match(name))


def is_lvm_disk(name: str) -> bool:
    return bool(_LVM_DISK_RE.match(name))


def is_partition(name: str) -> bool:
    return bool(_PART_DISK_RE.match(name))


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
