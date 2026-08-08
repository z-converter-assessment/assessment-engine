import pytest

from assessment_engine.web.services.device_filters import (
    disk_total_bytes,
    is_data_volume,
    is_lvm_disk,
    is_partition,
    is_physical_disk,
    is_swap,
    is_virtual_interface,
    swap_total_bytes,
)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("physical", False),
        ("bond_master", False),
        ("bond_member", True),
        ("bridge", True),
        ("veth", True),
        ("vlan", True),
        ("tunnel", True),
        ("loopback", True),
        (None, True),
    ],
)
def test_is_virtual_interface(kind: str | None, expected: bool):
    assert is_virtual_interface(kind) is expected


@pytest.mark.parametrize(
    ("dtype", "expected"),
    [
        ("disk", True),
        ("part", False),
        ("lvm", False),
        ("raid", False),
        ("crypt", False),
        ("swap", False),
        (None, False),
        ("", False),
    ],
)
def test_is_physical_disk(dtype: str | None, expected: bool):
    assert is_physical_disk(dtype) is expected


@pytest.mark.parametrize(
    ("dtype", "expected"),
    [
        ("lvm", True),
        ("raid", True),
        ("crypt", True),
        ("mpath", True),
        ("dynamic", True),
        ("disk", False),
        ("part", False),
        ("swap", False),
        (None, False),
    ],
)
def test_is_lvm_disk(dtype: str | None, expected: bool):
    assert is_lvm_disk(dtype) is expected


@pytest.mark.parametrize(
    ("dtype", "expected"),
    [
        ("part", True),
        ("disk", False),
        ("lvm", False),
        ("raid", False),
        ("swap", False),
        (None, False),
    ],
)
def test_is_partition(dtype: str | None, expected: bool):
    assert is_partition(dtype) is expected


@pytest.mark.parametrize(
    ("dtype", "expected"),
    [
        ("swap", True),
        ("disk", False),
        ("part", False),
        ("lvm", False),
        (None, False),
    ],
)
def test_is_swap(dtype: str | None, expected: bool):
    assert is_swap(dtype) is expected


@pytest.mark.parametrize(
    ("fstype", "mountpoint", "expected"),
    [
        ("ext4", "/", True),
        ("xfs", "/data", True),
        ("ntfs", "C:\\", True),
        ("tmpfs", "/run", False),
        ("proc", "/proc", False),
        ("overlay", "/var/lib/docker/overlay2", False),
        ("cgroup2", "/sys/fs/cgroup", False),
        ("ext4", "/boot", False),
        ("vfat", "/boot/efi", False),
        (None, "/mnt", True),
        (None, None, True),
    ],
)
def test_is_data_volume(fstype: str | None, mountpoint: str | None, expected: bool):
    assert is_data_volume(fstype, mountpoint) is expected


def test_disk_total_bytes_sums_physical_disks_only():
    block_devices = [
        {"name": "sda", "type": "disk", "size_bytes": 100 * 10**9},
        {"name": "sda1", "type": "part", "size_bytes": 99 * 10**9},
        {"name": "vg-root", "type": "lvm", "size_bytes": 40 * 10**9},
        {"name": "swap0", "type": "swap", "size_bytes": 8 * 10**9},
    ]
    assert disk_total_bytes(block_devices) == 100 * 10**9


def test_disk_total_bytes_sums_multiple_disks_both_os():
    block_devices = [
        {"name": "PhysicalDrive0", "type": "disk", "size_bytes": 120 * 10**9},
        {"name": "PhysicalDrive1", "type": "disk", "size_bytes": 200 * 10**9},
        {"name": "part0", "type": "part", "size_bytes": 300 * 10**9},
    ]
    assert disk_total_bytes(block_devices) == 320 * 10**9


def test_disk_total_bytes_skips_missing_size():
    block_devices = [
        {"name": "sda", "type": "disk"},
        {"name": "sdb", "type": "disk", "size_bytes": 50 * 10**9},
    ]
    assert disk_total_bytes(block_devices) == 50 * 10**9


def test_disk_total_bytes_zero_when_empty():
    assert disk_total_bytes([]) == 0


def test_swap_total_bytes_sums_swap_nodes_only():
    block_devices = [
        {"name": "sda", "type": "disk", "size_bytes": 100 * 10**9},
        {"name": "swap0", "type": "swap", "size_bytes": 8 * 10**9},
        {"name": "swap1", "type": "swap", "size_bytes": 4 * 10**9},
    ]
    assert swap_total_bytes(block_devices) == 12 * 10**9
    assert disk_total_bytes(block_devices) == 100 * 10**9


def test_swap_total_bytes_zero_when_no_swap():
    assert swap_total_bytes([{"name": "sda", "type": "disk", "size_bytes": 100 * 10**9}]) == 0
