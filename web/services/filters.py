"""
에이전트가 보내는 raw 데이터에서 가상·커널 항목을 제거하는 임시 필터.
근본 해결은 에이전트 측 수집 단계에서의 필터링이며, 다음 agent_version 계약에 반영 예정.
(DESIGN_DECISIONS.md #14 참고)
"""
import re

_PHYS_DISK_RE = re.compile(r'^(sd[a-z]+|vd[a-z]+|hd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$')

_VIRTUAL_FSTYPES: frozenset[str] = frozenset({
    'proc', 'sysfs', 'devtmpfs', 'devpts',
    'hugetlbfs', 'debugfs', 'tracefs',
    'squashfs',   # snap 패키지 read-only 마운트
    'nsfs',       # Linux namespace 마운트 (snapd ns, lxd.mnt 등)
    'overlay',    # Docker 컨테이너 레이어
    'cgroup', 'cgroup2',
    'pstore', 'bpf', 'fusectl', 'efivarfs',
    'configfs', 'securityfs', 'mqueue', 'ramfs',
})

# trailing slash 없이 통일 — startswith(p + '/') 에서 이중 슬래시 방지
_VIRTUAL_MOUNT_PREFIXES: tuple[str, ...] = (
    '/proc', '/sys', '/dev/pts', '/snap',
    '/run/snapd', '/sys/fs', '/sys/kernel',
)


def is_physical_disk(name: str) -> bool:
    return bool(_PHYS_DISK_RE.match(name))


def is_virtual_mount(fstype: str | None, mount: str) -> bool:
    if fstype and fstype in _VIRTUAL_FSTYPES:
        return True
    return any(mount == p or mount.startswith(p + '/') for p in _VIRTUAL_MOUNT_PREFIXES)