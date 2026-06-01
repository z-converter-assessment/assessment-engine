"""device_filters.py — 디바이스 분류·가상 마운트 필터·major/minor 조인."""

import pytest

from assessment_engine.web.services.device_filters import (
    find_parent_disk,
    is_lvm_disk,
    is_partition,
    is_physical_disk,
    is_virtual_mount,
)

# ─── is_physical_disk ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name, expected",
    [
        ("sda", True),
        ("sdb", True),
        ("sdaa", True),
        ("vda", True),
        ("hda", True),
        ("xvda", True),
        ("nvme0n1", True),
        ("mmcblk0", True),
        # 파티션은 physical 아님
        ("sda1", False),
        ("nvme0n1p1", False),
        # LVM은 physical 아님
        ("dm-0", False),
        ("md0", False),
        # 가상·빈 이름 제외
        ("loop0", False),
        ("", False),
        # 블랙리스트 정책 — 미지·특이 컨트롤러도 통과 (화이트리스트면 놓칠 mpath/cciss 등 관측성 보존)
        ("foo", True),
        ("mpatha", True),
        ("cciss", True),
    ],
)
def test_is_physical_disk(name, expected):
    assert is_physical_disk(name) is expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("dm-0", True),
        ("dm-12", True),
        ("md0", True),
        ("md127", True),
        ("sda", False),
        ("nvme0n1", False),
    ],
)
def test_is_lvm_disk(name, expected):
    assert is_lvm_disk(name) is expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("sda1", True),
        ("vda12", True),
        ("nvme0n1p1", True),
        ("mmcblk0p1", True),
        ("sda", False),
        ("nvme0n1", False),
        ("dm-0", False),
    ],
)
def test_is_partition(name, expected):
    assert is_partition(name) is expected


# ─── is_virtual_mount ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fstype, mount, expected",
    [
        ("proc", "/proc", True),
        ("sysfs", "/sys", True),
        ("tmpfs", "/run", False),  # tmpfs는 가상 fstype 목록에 없음 (지킬 수도 있는 마운트)
        ("ext4", "/", False),
        ("ext4", "/data", False),
        # mount prefix 기반
        (None, "/proc/sys", True),
        (None, "/sys/fs/cgroup", True),
        (None, "/snap/core/12345", True),
        (None, "/snap", True),  # 정확 일치
        (None, "/snapper", False),  # /snap 으로 시작하지만 /snap/ 이 아님
        (None, "/", False),
        (None, "/var", False),
    ],
)
def test_is_virtual_mount(fstype, mount, expected):
    assert is_virtual_mount(fstype, mount) is expected


# ─── find_parent_disk ─────────────────────────────────────────────────────

_DISKS = [
    {"name": "sda", "major": 8, "minor": 0},
    {"name": "sdb", "major": 8, "minor": 16},
    {"name": "nvme0n1", "major": 259, "minor": 0},
]


@pytest.mark.parametrize(
    "mount_major, mount_minor, expected_name",
    [
        (8, 0, "sda"),  # 디스크 자체
        (8, 1, "sda"),  # sda의 파티션
        (8, 15, "sda"),  # sda의 마지막 파티션 (minor 차이 15까지)
        (8, 16, "sdb"),  # sdb 자체 (디스크 minor)
        (8, 17, "sdb"),  # sdb의 파티션
        (259, 0, "nvme0n1"),
        (259, 5, "nvme0n1"),
        # 매칭 없음
        (8, 32, None),  # major 8이지만 minor 차이 16 = sdb 자체나 그 너머 — 위 disks엔 sdb까지만 → None
        (252, 0, None),  # 다른 major
        (None, 0, None),  # major None
        (8, None, None),  # minor None
        (0, 0, None),  # major=0 (가상 fs)
    ],
)
def test_find_parent_disk(mount_major, mount_minor, expected_name):
    assert find_parent_disk(mount_major, mount_minor, _DISKS) == expected_name


def test_find_parent_disk_skips_disk_without_major_minor():
    """disk dict에 major/minor 없으면 그 disk는 skip — 옛 에이전트 호환."""
    disks = [
        {"name": "old-disk"},  # major/minor 없음
        {"name": "sda", "major": 8, "minor": 0},
    ]
    assert find_parent_disk(8, 1, disks) == "sda"
