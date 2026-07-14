"""스토리지·네트워크 device 분류 — block_device `type` · fstype · net_interface `kind` 단일 진실.

인벤토리 모델 (lsblk/df/vgs 정석 매핑):
- block_devices[] = 정적 토폴로지 (lsblk). 평면 DAG 노드 {name,type,size_bytes,fstype,mountpoint,parent,id,id_type}.
  type = disk/part/lvm/crypt/raid/mpath/dynamic/volume/swap. 물리 디스크 = type=="disk", 스왑 = type=="swap".
- server_filesystem 시계열 = 동적 사용량 (df). 마운트별 used/free/inode + fstype + device_id.
- lvm_vgs[] = 확장 여력 (vgs). VG 미할당 공간 {name,size_bytes,free_bytes,..}.

정적 토폴로지(무엇이 존재)와 동적 사용량(얼마나 찼나)을 분리 — 계층 가시성 단일 정책. 모든 소비처(용량·상세
표시·export·토폴로지)가 본 술어를 공유하고 ad-hoc 재필터 금지 — 같은 raw 가 소비처마다 다른 계층을 뽑는
불일치를 차단. device 부모-자식 조인은 노드 `parent`(부모 id)로 — major/minor 폐기.

측정 원칙 (무엇을 어느 계층에서 재나 — Windows·Linux 통일 단일 규칙, 모든 화면 귀속. 기존 쿼리 노출분이
아니라 DB 원본 필드 전체 기준):
- 배정 용량 / 레이아웃 루트 = 물리 디스크 (block_devices type=="disk"). Linux vda / Windows PhysicalDrive0.
  디바이스 특성(rotational=HDD/SSD·sector_size·serial)도 이 계층 속성.
- 파일시스템 용량 = 마운트된 데이터 볼륨 (mountpoint 有 + is_data_volume). Linux part/LV / Windows volume.
- 사용량 = 파일시스템(마운트) 계층 (server_filesystem, df/Get-Volume) — 2축: bytes(used/free) +
  inode(inodes_used/free). inode 고갈은 bytes 여유해도 쓰기 실패라 별도 full 축. fullness 는 파일시스템
  속성(raw 디스크는 채우는 대상이 아님) — 배정·확장·I/O·특성이 물리 디스크/VG 축을 맡는다.
- I/O (IOPS·처리량·await 포화) = 물리 디스크 (server_disk_io type=="disk"). LV/파티션 통과분 이중집계 회피.
- 확장 여력 = (a) lvm_vgs.free_bytes = VG 미할당(LV 확장 정밀치) + (b) 물리 디스크 미파티션 갭(배정 − 파티션 합).
논리 계층 구조(lvm_vg·lvm_segtype·lvm_stripes·raid_level·crypt_type·partition_table·mount_options)는 block_devices
노드 속성으로 레이아웃에 표현 — 무엇을 보든 원본에 있으면 계층에 귀속. 어느 축이든 계층 고정: 사용량=파일시스템,
활동/용량/확장/특성=물리 디스크·VG. 소비처가 계층을 바꿔 재는 것 금지(같은 질문=같은 계층). 복잡 스택
(disk->raid->lvm->crypt->fs)은 parent 체인, 다중 부모(RAID span·striped VG)는 디스크별 그룹으로 노출.
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
