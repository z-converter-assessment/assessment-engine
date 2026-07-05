"""device_filters.py — 디바이스 분류·data-volume 필터·major/minor 조인."""

import pytest

from assessment_engine.web.services.device_filters import (
    disk_total_bytes,
    find_parent_disk,
    is_data_volume,
    is_lvm_disk,
    is_partition,
    is_physical_disk,
    is_virtual_interface,
)


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("physical", False),      # 물리 NIC — 집계 대상
        ("bond_master", False),   # 본딩 집계 단위 — 집계 대상(가상 아님)
        ("bond_member", True),    # 물리 leg — bond_master 가 합산분이라 이중집계 회피 제외
        ("bridge", True),
        ("veth", True),
        ("vlan", True),
        ("tunnel", True),
        ("loopback", True),
        (None, True),             # placeholder — 제외
    ],
)
def test_is_virtual_interface(kind, expected):
    assert is_virtual_interface(kind) is expected

# ─── is_physical_disk ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind, expected",
    [
        # kind=="physical" 만 물리 디스크
        ("physical", True),
        # 파티션·LVM·RAID·가상은 physical 아님
        ("partition", False),
        ("lvm", False),
        ("raid", False),
        ("virtual", False),
        # data volume kind 도 physical 아님
        ("data", False),
        # placeholder(None)·빈 kind 제외
        (None, False),
        ("", False),
    ],
)
def test_is_physical_disk(kind, expected):
    assert is_physical_disk(kind) is expected


@pytest.mark.parametrize(
    "kind, expected",
    [
        # kind in ("lvm","raid") 가 논리 볼륨
        ("lvm", True),
        ("raid", True),
        ("physical", False),
        ("partition", False),
        ("virtual", False),
        (None, False),
    ],
)
def test_is_lvm_disk(kind, expected):
    assert is_lvm_disk(kind) is expected


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("partition", True),
        ("physical", False),
        ("lvm", False),
        ("raid", False),
        ("virtual", False),
        (None, False),
    ],
)
def test_is_partition(kind, expected):
    assert is_partition(kind) is expected


# ─── is_data_volume ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind, expected",
    [
        # kind=="data" 만 데이터 볼륨 (agent 가 boot/image/가상 fs 를 kind 로 사전 분류)
        ("data", True),
        # 부트/펌웨어·이미지·가상은 데이터 아님
        ("boot", False),
        ("image", False),
        ("virtual", False),
        # placeholder(None)·빈 kind 제외
        (None, False),
        ("", False),
    ],
)
def test_is_data_volume(kind, expected):
    assert is_data_volume(kind) is expected


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
        {"name": "sda", "kind": "physical", "size_bytes": 100 * 10**9},
        {"name": "sda1", "kind": "partition", "size_bytes": 99 * 10**9},  # 파티션 — 제외
        {"name": "loop0", "kind": "virtual", "size_bytes": 5 * 10**9},  # 가상 — 제외
    ]
    mounts = [{"mount": "/", "kind": "data", "total_bytes": 80 * 10**9}]
    assert disk_total_bytes(disks, mounts) == 100 * 10**9


def test_disk_total_bytes_falls_back_to_data_volume_mounts():
    """물리 합 0(Windows agent 물리 disks 미발행)이면 data volume mounts 합 — 가상 fs 제외."""
    disks = [{"name": "loop0", "kind": "virtual", "size_bytes": 5 * 10**9}]  # 가상만 — 물리 합 0
    mounts = [
        {"mount": "C:\\", "kind": "data", "total_bytes": 120 * 10**9},
        {"mount": "D:\\", "kind": "data", "total_bytes": 200 * 10**9},
        {"mount": "/proc", "kind": "virtual", "total_bytes": 1},  # 가상 — 제외
    ]
    assert disk_total_bytes(disks, mounts) == 320 * 10**9


def test_disk_total_bytes_zero_when_no_sources():
    assert disk_total_bytes([], []) == 0
