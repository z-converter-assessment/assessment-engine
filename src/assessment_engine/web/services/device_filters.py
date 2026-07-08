"""agent device `kind` 태그 기반 물리/데이터 필터 — engine 단일 진실.

agent 가 disk/net/mount 각 항목에 `kind` 를 발행한다 (Linux/Windows 공용 분류기). 엔진은 정규식·major
추론 없이 kind 로만 판정한다 (화면·집계·용량 단일 기준, Windows major=0 문제 해소):
- 물리 디스크 = kind=="physical" (partition/lvm/raid/virtual 제외).
- 집계 iface = kind in {physical, bond_master} — bond_member/loopback/bridge/veth/vlan/tunnel/virtual 제외.
  bond_master(본딩 집계 단위)는 포함, bond_member(물리 leg)는 제외 — master/member 이중 집계 회피.
- 데이터 볼륨 = kind=="data" (boot/image 제외; 가상 fs 는 agent 가 pre-drop 해 애초에 없음).

계층 가시성 단일 정책 — 모든 소비처(cagg 집계·용량·상세 표시·JSON export·토폴로지)가 본 술어를 사용한다. 소비처별
ad-hoc 재필터(정규식·kind 직접 비교) 금지 — 같은 raw 가 소비처마다 다른 계층을 뽑는 불일치를 차단.

major/minor 는 물리 판정에서 빠지고 mount-disk 조인(find_parent_disk) 전용으로만 잔존.
"""


def is_physical_disk(kind: str | None) -> bool:
    return kind == "physical"


def is_lvm_disk(kind: str | None) -> bool:
    """논리 볼륨 — LVM(dm) + software RAID(md). 물리 부재 시 disk 차트 fallback 차원."""
    return kind in ("lvm", "raid")


def is_partition(kind: str | None) -> bool:
    return kind == "partition"


def is_virtual_interface(kind: str | None) -> bool:
    """집계 대상 인터페이스가 아닌 것 — loopback/bridge/veth/bond_member/vlan/tunnel/virtual. 표시·집계 제외 신호.

    `not is_virtual_interface(kind)` == 집계 대상(kind in ("physical", "bond_master"))만 통과. bond_master 는 본딩
    집계 단위라 포함, bond_member 는 제외(master/member 이중 집계 회피). kind None(placeholder)도 제외.
    """
    return kind not in ("physical", "bond_master")


def is_data_volume(kind: str | None) -> bool:
    """영속 물리 블록 디바이스 위 데이터 파일시스템 — kind=="data" (boot/image/가상 fs 제외)."""
    return kind == "data"


def disk_total_bytes(disks: list[dict], inventory_mounts: list[dict]) -> int:
    """디스크 총 용량(bytes) — 물리 disks 우선, 비면(Windows 등 물리 미발행) data volume 파일시스템 합.

    물리 디스크가 정석 용량이나 Windows agent 는 물리 disks 미발행(파일시스템만)이라 inventory mounts 로
    fallback. 환경·개별·세부 목록 보고서 단일 산식 (Windows 포함 일관).
    """
    physical = sum((d.get("size_bytes") or 0) for d in disks if is_physical_disk(d.get("kind")))
    if physical > 0:
        return physical
    return sum((m.get("total_bytes") or 0) for m in inventory_mounts if is_data_volume(m.get("kind")))


# ── major/minor 기반 조인 헬퍼 ──
# Linux 디바이스 식별 표준 (POSIX). inventory.disks[]의 (major, minor)와 inventory.mounts[]의 (major, minor)
# 비교로 "이 마운트가 어느 디스크 위인가"를 알 수 있음.
# 같은 major + (minor가 디스크 minor이거나 그 디스크의 파티션 minor) 이면 같은 디스크.
# SCSI/virtio 관례: minor 0,16,32,... 가 디스크 자체, 1~15 / 17~31 / ... 이 파티션.


def find_parent_disk(
    mount_major: int | None,
    mount_minor: int | None,
    disks: list[dict],
) -> str | None:
    """mount의 (major, minor)와 매칭되는 disk의 name 반환. 없으면 None."""
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
