from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assessment_engine.db.models.base import Base


class ServerInventory(Base):
    """등록 호스트 인벤토리 (wire inventory).

    식별 단일 키 = `agent_id` (UUID, UNIQUE, #C1) — 부팅 무관 불변. `composite_id`/`machine_id` 는
    감사·표시 전용, `hostname` 은 display field (UNIQUE X), `public_id` 는 URL 노출용.

    정규화 스토리지/네트워크 그래프는 JSONB pass-through — block_devices(스토리지 트리, swap 노드 포함)·
    net_interfaces(주소·게이트웨이)·lvm_vgs(Linux VG). mem_total_bytes 는 By 가 canonical.
    """

    __tablename__ = "server_inventory"
    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_server_inventory_agent_id"),
        # 서비스 카테고리 필터(category 멤버십 @>/&&) GIN.
        Index("ix_server_inventory_service_categories", "service_categories", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        server_default=text("gen_random_uuid()"),
        unique=True,
        nullable=False,
    )
    # 호스트 식별 단일 키 (#C1) — agent 매칭·MQ 라우팅. UUID v4, 부팅 무관 불변 (MAC/machine_id 재발급 무관).
    agent_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    # composite_id — SHA-256(machine_id+MAC). 감사·표시용 강등 (clone collision 진단). 식별·라우팅 미사용, nullable.
    composite_id: Mapped[str | None] = mapped_column(String(64))
    # raw machine-id (Linux /etc/machine-id, Windows MachineGuid). 표시 전용 — 식별 미사용.
    machine_id: Mapped[str | None] = mapped_column(String(64))
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version: Mapped[str | None] = mapped_column(String(32))

    # OS family (linux / windows) — task.install OS 별 dispatch 단일 진실.
    os_family: Mapped[str | None] = mapped_column(String(16))
    os_id: Mapped[str | None] = mapped_column(String(64))
    os_version: Mapped[str | None] = mapped_column(String(64))
    os_codename: Mapped[str | None] = mapped_column(String(64))
    kernel_version: Mapped[str | None] = mapped_column(String(64))

    # OS 재현 서술자 (flat) — 이미지/부트 기반 정확 재현용. agent 발행(uname·efivars·/etc/timezone 등).
    arch: Mapped[str | None] = mapped_column(String(32))  # x86_64|aarch64|...
    bits: Mapped[int | None] = mapped_column(Integer)  # 32|64
    boot_firmware: Mapped[str | None] = mapped_column(String(8))  # uefi|bios
    secure_boot: Mapped[bool | None] = mapped_column(Boolean)
    edition: Mapped[str | None] = mapped_column(String(64))  # Windows EditionID (Linux null)
    # CurrentVersion ProductName 원문(Windows only, Linux null) — os_display 짧은 라벨 파싱 소스.
    product_name: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str | None] = mapped_column(String(64))  # IANA
    rtc_utc: Mapped[bool | None] = mapped_column(Boolean)

    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    cpu_model: Mapped[str | None] = mapped_column(String(255))
    mem_total_bytes: Mapped[int | None] = mapped_column(BigInteger)  # 단위 By (swap 은 block_devices type=swap)

    boot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 스토리지 트리 — [{name,type,size_bytes,fstype,mountpoint,parent,id,id_type}] (type=swap 포함).
    block_devices: Mapped[list[Any] | None] = mapped_column(JSONB)
    # 네트워크 그래프 — [{name,id,id_type,kind,speed_mbps,addresses:[{address,prefix,family}],gateway}].
    net_interfaces: Mapped[list[Any] | None] = mapped_column(JSONB)
    # LVM VG — [{name,size_bytes,free_bytes,data_percent,metadata_percent}] (Linux 전용).
    lvm_vgs: Mapped[list[Any] | None] = mapped_column(JSONB)
    # boot 재현 서술자 — {kernel_cmdline,root_ref_type,grub_install_target} (Linux; Windows null).
    boot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 비블록 마운트 — [{source,target,fstype,options,fs_freq,fs_passno}] (tmpfs/nfs/cifs/bind 등, Linux).
    nonblock_mounts: Mapped[list[Any] | None] = mapped_column(JSONB)
    ip_external: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    services: Mapped[list[Any] | None] = mapped_column(JSONB)  # [{unit,sub,pid,exe}]
    listen_ports: Mapped[list[Any] | None] = mapped_column(JSONB)  # [{proto,addr,port,uid,pid,comm}]
    # 서비스 카테고리 집합 (ingest 사전계산, service_classifier.compute_service_categories 단일 진실).
    # 이름·comm·포트 어느 신호로 식별되든 동일 — 모든 read 경로가 본 저장값 소비(목록·상세·리포트·필터 뱃지 일치).
    service_categories: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
