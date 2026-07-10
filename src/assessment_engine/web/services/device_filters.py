"""스토리지·네트워크 device 분류 — block_device `type` · fstype · net_interface `kind` 단일 진실.

인벤토리 모델 (lsblk/df/vgs 정석 매핑):
- block_devices[] = 정적 토폴로지 (lsblk). 평면 DAG 노드 {name,type,size_bytes,fstype,mountpoint,parent,id,id_type}.
  type = disk/part/lvm/crypt/raid/mpath/dynamic/volume/swap. 물리 디스크 = type=="disk", 스왑 = type=="swap".
- server_filesystem 시계열 = 동적 사용량 (df). 마운트별 used/free/inode + fstype + device_id.
- lvm_vgs[] = 확장 여력 (vgs). VG 미할당 공간 {name,size_bytes,free_bytes,..}.

정적 토폴로지(무엇이 존재)와 동적 사용량(얼마나 찼나)을 분리 — 계층 가시성 단일 정책. 모든 소비처(용량·상세
표시·export·토폴로지)가 본 술어를 공유하고 ad-hoc 재필터 금지 — 같은 raw 가 소비처마다 다른 계층을 뽑는
불일치를 차단. device 부모-자식 조인은 노드 `parent`(부모 id)로 — major/minor 폐기.
"""

# 가상 파일시스템 — 데이터 볼륨 아님(용량 집계·상세 표시 제외). df 관례. types._VIRTUAL_FSTYPES(SQL) 와 동일 집합.
VIRTUAL_FSTYPES = frozenset(
    {
        "tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs", "cgroup", "cgroup2", "mqueue",
        "debugfs", "tracefs", "securityfs", "pstore", "bpf", "configfs", "ramfs", "autofs",
        "hugetlbfs", "fusectl", "nsfs", "efivarfs", "binfmt_misc",
    }
)


def is_physical_disk(dtype: str | None) -> bool:
    """물리 블록 디바이스 — type=="disk" (PhysicalDrive/sd/nvme/vd). partition/lvm/raid/swap 제외."""
    return dtype == "disk"


def is_lvm_disk(dtype: str | None) -> bool:
    """논리 볼륨 계층 — LVM/RAID/crypt/multipath/dynamic. 물리 부재 시 fallback 차원."""
    return dtype in ("lvm", "raid", "crypt", "mpath", "dynamic")


def is_partition(dtype: str | None) -> bool:
    return dtype == "part"


def is_swap(dtype: str | None) -> bool:
    return dtype == "swap"


def is_virtual_interface(kind: str | None) -> bool:
    """집계 대상 아닌 인터페이스 — loopback/bridge/veth/bond_member/vlan/tunnel/virtual. kind None 도 제외.

    `not is_virtual_interface(kind)` == 집계 대상(physical/bond_master)만 통과. bond_master 포함, bond_member 제외
    (이중 집계 회피). net_interfaces 는 kind 를 유지(block_device 와 달리).
    """
    return kind not in ("physical", "bond_master")


def is_data_volume(fstype: str | None, mountpoint: str | None = None) -> bool:
    """실 데이터 파일시스템 — 가상 fs 와 /boot 제외. types._DATA_VOLUME_SQL_FILTER(SQL) 와 동일 판정.

    fstype None(미상)은 데이터로 포함(안전, df 관례). /boot·/boot/efi 는 부팅 전용이라 데이터 용량서 제외.
    """
    if fstype is not None and fstype in VIRTUAL_FSTYPES:
        return False
    if mountpoint is not None and mountpoint.startswith("/boot"):
        return False
    return True


def disk_total_bytes(block_devices: list[dict]) -> int:
    """물리 프로비저닝 디스크 총량(bytes) = sum block_device(type==disk) size_bytes.

    물리 디스크 합이 정석(마운트 안 된 공간 누락·이중계산 회피). Windows 도 PhysicalDrive 를 type=disk 로
    발행하므로 fallback 불요 — 양 OS 단일 산식. 환경·개별·목록 보고서 공용.
    """
    return sum((d.get("size_bytes") or 0) for d in (block_devices or []) if is_physical_disk(d.get("type")))


def swap_total_bytes(block_devices: list[dict]) -> int:
    """스왑 총량(bytes) = sum block_device(type==swap) size_bytes. v2 는 swap 을 block_device 노드로 표현."""
    return sum((d.get("size_bytes") or 0) for d in (block_devices or []) if is_swap(d.get("type")))
