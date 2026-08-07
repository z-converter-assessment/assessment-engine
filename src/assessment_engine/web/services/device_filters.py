"""block_device·net_interface·fstype 계층 술어 단일 진실.

"물리 디스크인가" 같은 판정이 mapper 마다 흩어지면 같은 장치가 화면마다 다르게 세어진다. SQL 쪽
대응 필터는 `db/repositories/query/types.py` 가 갖고 둘은 같은 agent 태그를 본다.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject

"""측정 원칙 — 무엇을 어느 계층에서 재나 (Windows·Linux 통일, 모든 화면 귀속).

- 배정 용량 / 레이아웃 루트 / 디바이스 특성(rotational·sector_size·serial) = 물리 디스크.
- 파일시스템 용량 = 마운트된 데이터 볼륨. Linux part/LV / Windows volume.
- 사용량 = 파일시스템(마운트) 계층 2축 — bytes 와 inode. inode 고갈은 bytes 가 여유해도 쓰기가
  실패하므로 별도 축이다. fullness 는 파일시스템 속성이고 raw 디스크는 채우는 대상이 아니다.
- I/O (IOPS·처리량·await 포화) = 물리 디스크. LV/파티션 통과분 이중집계 회피.
- 확장 여력 = lvm_vgs.free_bytes(VG 미할당) + 물리 디스크 미파티션 갭(배정 - 파티션 합).

소비처가 계층을 바꿔 재는 것 금지 — 같은 질문은 같은 계층에서 답한다. 모든 소비처(용량·상세 표시·export·
토폴로지)가 본 술어를 공유하고 ad-hoc 재필터를 새로 쓰지 않는다 — 같은 raw 에서 소비처마다 다른 계층이 나온다.
"""

# 가상 파일시스템 목록의 출처는 df 관례(집계에서 빼는 것들). SQL 등가는 `types._VIRTUAL_FSTYPES` — 두 집합은
# 같이 움직여야 한다.
VIRTUAL_FSTYPES = frozenset(
    {
        "tmpfs",
        "devtmpfs",
        "overlay",
        "squashfs",
        "proc",
        "sysfs",
        "cgroup",
        "cgroup2",
        "mqueue",
        "debugfs",
        "tracefs",
        "securityfs",
        "pstore",
        "bpf",
        "configfs",
        "ramfs",
        "autofs",
        "hugetlbfs",
        "fusectl",
        "nsfs",
        "efivarfs",
        "binfmt_misc",
    }
)


def is_physical_disk(dtype: str | None) -> bool:
    """물리 블록 디바이스 — Linux sd/nvme/vd, Windows PhysicalDrive."""
    return dtype == "disk"


def is_lvm_disk(dtype: str | None) -> bool:
    """논리 볼륨 계층 — 물리 디스크가 없을 때 fallback 차원."""
    return dtype in ("lvm", "raid", "crypt", "mpath", "dynamic")


def is_partition(dtype: str | None) -> bool:
    return dtype == "part"


def is_swap(dtype: str | None) -> bool:
    return dtype == "swap"


def is_virtual_interface(kind: str | None) -> bool:
    """집계 대상이 아닌 인터페이스. kind 미상(None)도 제외한다 — allowlist 판정이라 통과가 아니다.

    bond_master 는 통과, bond_member 는 제외 (이중 집계 회피).
    """
    return kind not in ("physical", "bond_master")


def is_data_volume(fstype: str | None, mountpoint: str | None = None) -> bool:
    """실 데이터 파일시스템. `types._DATA_VOLUME_SQL_FILTER`(SQL) 와 동일 판정.

    fstype None(미상)은 데이터로 포함(안전, df 관례). /boot·/boot/efi 는 부팅 전용이라 제외.
    """
    if fstype is not None and fstype in VIRTUAL_FSTYPES:
        return False
    return not (mountpoint is not None and mountpoint.startswith("/boot"))


def disk_total_bytes(block_devices: list[JsonObject]) -> int:
    """물리 프로비저닝 디스크 총량(bytes).

    물리 디스크 합이 정석 — 마운트 안 된 공간 누락·이중계산 회피. Windows 도 PhysicalDrive 를
    type=disk 로 발행하므로 OS 분기가 없다.
    """
    return sum((d.get("size_bytes") or 0) for d in (block_devices or []) if is_physical_disk(d.get("type")))


def swap_total_bytes(block_devices: list[JsonObject]) -> int:
    """스왑 총량(bytes). swap 은 별도 축이 아니라 block_device 노드로 표현된다."""
    return sum((d.get("size_bytes") or 0) for d in (block_devices or []) if is_swap(d.get("type")))
