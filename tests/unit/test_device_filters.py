"""device_filters.py — block_device `type`·fstype·net_interface `kind` 기반 device 분류.

device 계층은 lsblk 평면 DAG 노드의 `type`
(disk/part/lvm/crypt/raid/mpath/dynamic/swap) 과 fstype/mountpoint 로 계층을 가른다.
부모-자식 조인은 노드 `parent`(부모 id) 다.
"""

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

# --- is_virtual_interface (net_interface kind) ---------------------------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("physical", False),  # 물리 NIC — 집계 대상
        ("bond_master", False),  # 본딩 집계 단위 — 집계 대상(가상 아님)
        ("bond_member", True),  # 물리 leg — bond_master 가 합산분이라 이중집계 회피 제외
        ("bridge", True),
        ("veth", True),
        ("vlan", True),
        ("tunnel", True),
        ("loopback", True),
        (None, True),  # placeholder — 제외
    ],
)
def test_is_virtual_interface(kind: str | None, expected: bool):
    assert is_virtual_interface(kind) is expected


# --- is_physical_disk (block_device type) --------------------------------


@pytest.mark.parametrize(
    ("dtype", "expected"),
    [
        # type=="disk" 만 물리 디스크 (sd/nvme/vd/PhysicalDrive)
        ("disk", True),
        # 파티션·LVM·RAID·crypt·swap 은 physical 아님
        ("part", False),
        ("lvm", False),
        ("raid", False),
        ("crypt", False),
        ("swap", False),
        # placeholder(None)·빈 type 제외
        (None, False),
        ("", False),
    ],
)
def test_is_physical_disk(dtype: str | None, expected: bool):
    assert is_physical_disk(dtype) is expected


@pytest.mark.parametrize(
    ("dtype", "expected"),
    [
        # type in (lvm,raid,crypt,mpath,dynamic) 가 논리 볼륨 계층
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


# --- is_data_volume (fstype + mountpoint) --------------------------------


@pytest.mark.parametrize(
    ("fstype", "mountpoint", "expected"),
    [
        # 실 데이터 파일시스템 — 데이터 볼륨
        ("ext4", "/", True),
        ("xfs", "/data", True),
        ("ntfs", "C:\\", True),
        # 가상 fs — 데이터 아님 (VIRTUAL_FSTYPES)
        ("tmpfs", "/run", False),
        ("proc", "/proc", False),
        ("overlay", "/var/lib/docker/overlay2", False),
        ("cgroup2", "/sys/fs/cgroup", False),
        # /boot·/boot/efi 는 부팅 전용 — 데이터 용량서 제외
        ("ext4", "/boot", False),
        ("vfat", "/boot/efi", False),
        # fstype None(미상)은 데이터로 포함 (안전, df 관례)
        (None, "/mnt", True),
        (None, None, True),
    ],
)
def test_is_data_volume(fstype: str | None, mountpoint: str | None, expected: bool):
    assert is_data_volume(fstype, mountpoint) is expected


# --- disk_total_bytes — 물리 프로비저닝 총량(type=disk 합, 양 OS 단일 산식) ---


def test_disk_total_bytes_sums_physical_disks_only():
    """물리 디스크(type=disk) size_bytes 합만 — 파티션·LVM·swap 제외 (이중계산 회피)."""
    block_devices = [
        {"name": "sda", "type": "disk", "size_bytes": 100 * 10**9},
        {"name": "sda1", "type": "part", "size_bytes": 99 * 10**9},  # 파티션 — 제외
        {"name": "vg-root", "type": "lvm", "size_bytes": 40 * 10**9},  # 논리 — 제외
        {"name": "swap0", "type": "swap", "size_bytes": 8 * 10**9},  # 스왑 — 제외
    ]
    assert disk_total_bytes(block_devices) == 100 * 10**9


def test_disk_total_bytes_sums_multiple_disks_both_os():
    """다중 물리 디스크 합 — Windows PhysicalDrive 도 type=disk 발행이라 fallback 불요, 양 OS 동일 산식."""
    block_devices = [
        {"name": "PhysicalDrive0", "type": "disk", "size_bytes": 120 * 10**9},
        {"name": "PhysicalDrive1", "type": "disk", "size_bytes": 200 * 10**9},
        {"name": "part0", "type": "part", "size_bytes": 300 * 10**9},  # 제외
    ]
    assert disk_total_bytes(block_devices) == 320 * 10**9


def test_disk_total_bytes_skips_missing_size():
    """size_bytes 누락 노드는 0 기여 — `(size_bytes or 0)` 가드."""
    block_devices = [
        {"name": "sda", "type": "disk"},  # size_bytes 없음
        {"name": "sdb", "type": "disk", "size_bytes": 50 * 10**9},
    ]
    assert disk_total_bytes(block_devices) == 50 * 10**9


def test_disk_total_bytes_zero_when_empty():
    assert disk_total_bytes([]) == 0


# --- swap_total_bytes — 스왑 총량(type=swap 합. swap 은 block_device 노드로 표현된다) ---


def test_swap_total_bytes_sums_swap_nodes_only():
    block_devices = [
        {"name": "sda", "type": "disk", "size_bytes": 100 * 10**9},  # 물리 — 제외
        {"name": "swap0", "type": "swap", "size_bytes": 8 * 10**9},
        {"name": "swap1", "type": "swap", "size_bytes": 4 * 10**9},
    ]
    assert swap_total_bytes(block_devices) == 12 * 10**9
    # disk_total_bytes 와 상호 배타 — swap 은 물리 총량서 제외
    assert disk_total_bytes(block_devices) == 100 * 10**9


def test_swap_total_bytes_zero_when_no_swap():
    assert swap_total_bytes([{"name": "sda", "type": "disk", "size_bytes": 100 * 10**9}]) == 0
