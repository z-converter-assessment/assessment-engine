"""device_filters.py — 디바이스 분류·data-volume 필터·major/minor 조인."""

import pytest

from assessment_engine.web.services.device_filters import (
    disk_total_bytes,
    find_parent_disk,
    is_data_volume,
    is_lvm_disk,
    is_partition,
    is_physical_disk,
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


# ─── is_data_volume ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mount, major, fstype, expected",
    [
        # major 신호 — Linux 블록 디바이스(major>0) = 데이터 볼륨
        ("/", 254, "ext4", True),
        ("/data", 8, "xfs", True),
        # major==0 = 블록 디바이스 없는 가상 fs (fstype 블랙리스트 대체)
        ("/sys/fs/selinux", 0, "selinuxfs", False),
        ("/proc", 0, "proc", False),
        ("/run", 0, "tmpfs", False),  # tmpfs major==0 — 옛 블랙리스트 누락도 major 로 잡힘
        # Windows drive — major 0 이나 drive letter 로 인정
        ("C:\\", 0, "ntfs", True),
        ("D:\\", 0, "ntfs", True),
        # 부트/펌웨어 — 실블록(major>0)이나 비데이터
        ("/boot", 254, "xfs", False),
        ("/boot/efi", 254, "vfat", False),
        # 이미지 fstype (loop 기반 read-only)
        ("/snap/core/123", 7, "squashfs", False),
        # major 미전파(None) fallback — path 블랙리스트
        (None, None, None, False),  # 빈 mount
        ("/proc", None, None, False),
        ("/sys/fs/cgroup", None, None, False),
        ("/snap", None, None, False),
        ("/", None, "ext4", True),
        ("/data", None, None, True),
        ("/snapper", None, None, True),  # /snap prefix 아님
    ],
)
def test_is_data_volume(mount, major, fstype, expected):
    assert is_data_volume(mount or "", major, fstype) is expected


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


# ─── disk_total_bytes — 보고서·export 디스크 총량 단일 산식 ──────────────


def test_disk_total_bytes_prefers_physical_disks():
    """물리 disks 가 있으면 물리 합만 — 파티션·가상 disk·mounts 무시."""
    disks = [
        {"name": "sda", "size_bytes": 100 * 10**9},
        {"name": "sda1", "size_bytes": 99 * 10**9},  # 파티션 — 제외
        {"name": "loop0", "size_bytes": 5 * 10**9},  # 가상 — 제외
    ]
    mounts = [{"mount": "/", "total_bytes": 80 * 10**9, "fstype": "ext4"}]
    assert disk_total_bytes(disks, mounts) == 100 * 10**9


def test_disk_total_bytes_falls_back_to_data_volume_mounts():
    """물리 합 0(Windows agent 물리 disks 미발행)이면 data volume mounts 합 — 가상 fs 제외."""
    disks = [{"name": "loop0", "size_bytes": 5 * 10**9}]  # 가상만 — 물리 합 0
    mounts = [
        {"mount": "C:\\", "total_bytes": 120 * 10**9, "fstype": "NTFS"},
        {"mount": "D:\\", "total_bytes": 200 * 10**9, "fstype": "NTFS"},
        {"mount": "/proc", "total_bytes": 1, "fstype": None},  # 가상 prefix — 제외
    ]
    assert disk_total_bytes(disks, mounts) == 320 * 10**9


def test_disk_total_bytes_zero_when_no_sources():
    assert disk_total_bytes([], []) == 0
