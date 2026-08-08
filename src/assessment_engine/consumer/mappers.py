"""wire 파싱 — datapoint-array(system.*) + inventory 배열 -> 저장 DTO.

null 은 미측정 그대로 보존한다 — 0 으로 날조하면 미수집과 실측 0 을 구분할 수 없다.
"""

from typing import TYPE_CHECKING

from assessment_engine.db.dtos.inbound import (
    CpuCoreEntry,
    DiskErrorEntry,
    DiskIoEntry,
    FilesystemEntry,
    NetIoEntry,
    PressureEntry,
    ServerInventoryCreate,
    ServerMetricCreate,
)
from assessment_engine.domain.service_classifier import compute_service_categories

if TYPE_CHECKING:
    from assessment_engine.consumer.schemas import (
        BlockDeviceInfo,
        Datapoint,
        InventoryInput,
        LvmVgInfo,
        MetricsInput,
        Namespace,
        NetInterfaceInfo,
    )
    from assessment_engine.json_types import JsonObject


def _points(ns: Namespace | None, metric: str) -> list[Datapoint]:
    m = ns.get(metric) if ns else None
    return m.points if m else []


def _attr_key(v: object) -> str | None:
    """attr 값 비교 키 — 계약이 문자열과 숫자를 모두 허용하므로 문자열로 맞춘다. None 은 키 부재를 뜻해 보존."""
    return None if v is None else str(v)


def _match(pts: list[Datapoint], **attrs: object) -> float | None:
    want = {k: _attr_key(v) for k, v in attrs.items()}
    for p in pts:
        if all(_attr_key(p.attr.get(k)) == v for k, v in want.items()):
            return p.value
    return None


def _scalar(ns: Namespace | None, metric: str) -> float | None:
    pts = _points(ns, metric)
    return pts[0].value if pts else None


def _int(v: float | None) -> int | None:
    return int(v) if v is not None else None


def _state_sum(pts: list[Datapoint], state: str) -> float | None:
    vals = [p.value for p in pts if _attr_key(p.attr.get("state")) == state and p.value is not None]
    return sum(vals) if vals else None


def _attr_of(pts_lists: list[list[Datapoint]], key: str, **attrs: object) -> str | None:
    """attr 전부 일치하는 point 중 key 가 채워진 첫 값. 같은 축이라도 point 마다 attr 은 sparse(계약 G5)."""
    want = {k: _attr_key(v) for k, v in attrs.items()}
    for pts in pts_lists:
        for p in pts:
            if all(_attr_key(p.attr.get(k)) == v for k, v in want.items()):
                found = _attr_key(p.attr.get(key))
                if found is not None:
                    return found
    return None


def _distinct(pts_lists: list[list[Datapoint]], key: str) -> list[str]:
    seen: dict[str, None] = {}
    for pts in pts_lists:
        for p in pts:
            v = _attr_key(p.attr.get(key))
            if v is not None:
                seen.setdefault(v, None)
    return list(seen)


def to_metric_create(data: MetricsInput) -> ServerMetricCreate:
    """검증된 metrics 메시지를 시계열 저장 DTO로 변환한다.

    없는 측정값은 0으로 바꾸지 않고 None으로 보존한다.
    """
    cpu_ns, mem_ns, disk_ns, net_ns = data.system_cpu, data.system_memory, data.system_disk, data.system_network
    pag_ns, fs_ns, pr_ns = data.system_paging, data.system_filesystem, data.system_pressure

    cpu_time = _points(cpu_ns, "cpu.time")
    mem_usage = _points(mem_ns, "memory.usage")
    paging = _points(pag_ns, "paging.operations")

    return ServerMetricCreate(
        collected_at=data.collected_at,
        boot_time=data.boot_time,
        agent_started_at=data.agent_started_at,
        cpu_user_s=_state_sum(cpu_time, "user"),
        cpu_nice_s=_state_sum(cpu_time, "nice"),
        cpu_system_s=_state_sum(cpu_time, "system"),
        cpu_idle_s=_state_sum(cpu_time, "idle"),
        cpu_iowait_s=_state_sum(cpu_time, "iowait"),
        cpu_irq_s=_state_sum(cpu_time, "irq"),
        cpu_softirq_s=_state_sum(cpu_time, "softirq"),
        cpu_steal_s=_state_sum(cpu_time, "steal"),
        cpu_logical_count=_int(_scalar(cpu_ns, "cpu.logical.count")),
        cpu_run_queue=_scalar(cpu_ns, "cpu.run_queue"),
        cpu_blocked=_scalar(cpu_ns, "cpu.blocked"),
        cpu_mce=_int(_scalar(cpu_ns, "cpu.mce")),
        mem_free_bytes=_int(_match(mem_usage, state="free")),
        mem_cached_bytes=_int(_match(mem_usage, state="cached")),
        mem_buffered_bytes=_int(_match(mem_usage, state="buffered")),
        mem_available_bytes=_int(_match(mem_usage, state="available")),
        mem_used_bytes=_int(_match(mem_usage, state="used")),
        mem_limit_bytes=_int(_scalar(mem_ns, "memory.limit")),
        mem_commit_usage_bytes=_int(_scalar(mem_ns, "memory.commit.usage")),
        mem_commit_limit_bytes=_int(_scalar(mem_ns, "memory.commit.limit")),
        mem_hardware_corrupted_bytes=_int(_scalar(mem_ns, "memory.hardware_corrupted")),
        mem_oom_kill=_int(_scalar(mem_ns, "memory.oom_kill")),
        paging_in=_int(_match(paging, direction="in", type=None)),
        paging_out=_int(_match(paging, direction="out", type=None)),
        paging_major=_int(_match(paging, direction="in", type="major")),
        net_tcp_retransmits=_int(_scalar(net_ns, "network.tcp.retransmits")),
        net_conntrack_usage=_int(_scalar(net_ns, "network.conntrack.usage")),
        net_conntrack_limit=_int(_scalar(net_ns, "network.conntrack.limit")),
        disk_io=_build_disk_io(disk_ns),
        net_io=_build_net_io(net_ns),
        filesystems=_build_filesystems(fs_ns),
        cpu_per_core=_build_cpu_cores(cpu_time),
        pressure=_build_pressure(pr_ns),
        disk_errors=_build_disk_errors(disk_ns),
    )


def _build_disk_io(disk_ns: Namespace | None) -> list[DiskIoEntry]:
    io, ops = _points(disk_ns, "disk.io"), _points(disk_ns, "disk.operations")
    io_time, op_time = _points(disk_ns, "disk.io_time"), _points(disk_ns, "disk.operation_time")
    pending = _points(disk_ns, "disk.pending_operations")
    devs = _distinct([io, ops, io_time, op_time, pending], "device")
    return [
        DiskIoEntry(
            device_id=dev,
            io_read_bytes=_int(_match(io, device=dev, direction="read")),
            io_write_bytes=_int(_match(io, device=dev, direction="write")),
            ops_read=_int(_match(ops, device=dev, direction="read")),
            ops_write=_int(_match(ops, device=dev, direction="write")),
            io_time_s=_match(io_time, device=dev),
            op_read_time_s=_match(op_time, device=dev, direction="read"),
            op_write_time_s=_match(op_time, device=dev, direction="write"),
            pending_ops=_match(pending, device=dev),
        )
        for dev in devs
    ]


def _build_disk_errors(disk_ns: Namespace | None) -> list[DiskErrorEntry]:
    out: list[DiskErrorEntry] = []
    for p in _points(disk_ns, "disk.errors"):
        kind = _attr_key(p.attr.get("kind"))
        if kind is None:
            continue
        # class 는 kind 별 선택 attr(Windows eventlog 엔 없다) — 자연키 컬럼이라 NULL 대신 "".

        out.append(
            DiskErrorEntry(
                device_id=_attr_key(p.attr.get("device")) or "",
                error_kind=kind,
                error_class=_attr_key(p.attr.get("class")) or "",
                member=_attr_key(p.attr.get("member")),
                count=_int(p.value),
            )
        )
    return out


def _build_net_io(net_ns: Namespace | None) -> list[NetIoEntry]:
    io, pkts = _points(net_ns, "network.io"), _points(net_ns, "network.packets")
    errs, drop = _points(net_ns, "network.errors"), _points(net_ns, "network.dropped")
    speed = _points(net_ns, "network.link.speed")
    devs = _distinct([io, pkts, errs, drop, speed], "device")
    return [
        NetIoEntry(
            iface_id=dev,
            rx_bytes=_int(_match(io, device=dev, direction="receive")),
            tx_bytes=_int(_match(io, device=dev, direction="transmit")),
            rx_packets=_int(_match(pkts, device=dev, direction="receive")),
            tx_packets=_int(_match(pkts, device=dev, direction="transmit")),
            rx_errors=_int(_match(errs, device=dev, direction="receive")),
            tx_errors=_int(_match(errs, device=dev, direction="transmit")),
            rx_dropped=_int(_match(drop, device=dev, direction="receive")),
            tx_dropped=_int(_match(drop, device=dev, direction="transmit")),
            link_speed_bps=_int(_match(speed, device=dev)),
        )
        for dev in devs
    ]


def _build_filesystems(fs_ns: Namespace | None) -> list[FilesystemEntry]:
    usage, inodes = _points(fs_ns, "filesystem.usage"), _points(fs_ns, "filesystem.inodes.usage")
    mounts = _distinct([usage, inodes], "mountpoint")
    return [
        FilesystemEntry(
            mountpoint=mp,
            device_id=_attr_of([usage, inodes], "device", mountpoint=mp),
            fstype=_attr_of([usage], "type", mountpoint=mp),
            used_bytes=_int(_match(usage, mountpoint=mp, state="used")),
            free_bytes=_int(_match(usage, mountpoint=mp, state="free")),
            inodes_used=_int(_match(inodes, mountpoint=mp, state="used")),
            inodes_free=_int(_match(inodes, mountpoint=mp, state="free")),
        )
        for mp in mounts
    ]


def _build_cpu_cores(cpu_time: list[Datapoint]) -> list[CpuCoreEntry]:
    cores = _distinct([cpu_time], "cpu")
    out: list[CpuCoreEntry] = []
    for c in cores:
        if not c.isdigit():
            continue
        out.append(
            CpuCoreEntry(
                core_id=int(c),
                cpu_user_s=_match(cpu_time, cpu=c, state="user"),
                cpu_nice_s=_match(cpu_time, cpu=c, state="nice"),
                cpu_system_s=_match(cpu_time, cpu=c, state="system"),
                cpu_idle_s=_match(cpu_time, cpu=c, state="idle"),
                cpu_iowait_s=_match(cpu_time, cpu=c, state="iowait"),
                cpu_irq_s=_match(cpu_time, cpu=c, state="irq"),
                cpu_softirq_s=_match(cpu_time, cpu=c, state="softirq"),
                cpu_steal_s=_match(cpu_time, cpu=c, state="steal"),
            )
        )
    return out


def _build_pressure(pr_ns: Namespace | None) -> list[PressureEntry]:
    ratio, time = _points(pr_ns, "pressure.stall.ratio"), _points(pr_ns, "pressure.stall.time")
    keys: dict[tuple[str, str], None] = {}
    for pts in (ratio, time):
        for p in pts:
            r, sc = _attr_key(p.attr.get("resource")), _attr_key(p.attr.get("scope"))
            if r is not None and sc is not None:
                keys.setdefault((r, sc), None)
    return [
        PressureEntry(
            resource=r,
            scope=s,
            stall_time_s=_match(time, resource=r, scope=s),
            ratio_avg10=_match(ratio, resource=r, scope=s, window="10"),
            ratio_avg60=_match(ratio, resource=r, scope=s, window="60"),
            ratio_avg300=_match(ratio, resource=r, scope=s, window="300"),
        )
        for (r, s) in keys
    ]


def _service_dicts(data: InventoryInput) -> list[JsonObject] | None:
    if data.services is None:
        return None
    return [{"unit": s.unit, "sub": s.sub, "pid": s.pid, "exe": s.exe} for s in data.services]


def _listen_port_dicts(data: InventoryInput) -> list[JsonObject]:
    return [
        {"proto": p.proto, "addr": p.addr, "port": p.port, "uid": p.uid, "pid": p.pid, "comm": p.comm}
        for p in data.listen_ports
    ]


def _sparse(d: JsonObject) -> JsonObject:
    return {k: v for k, v in d.items() if v is not None}


def _block_device_layout(b: BlockDeviceInfo) -> JsonObject:
    return _sparse(
        {
            "partition_table": b.partition_table,
            "sector_size": b.sector_size,
            "serial": b.serial,
            "wwn": b.wwn,
            "rotational": b.rotational,
            "part_number": b.part_number,
            "part_start_bytes": b.part_start_bytes,
            "part_type": b.part_type,
            "part_name": b.part_name,
            "part_flags": b.part_flags,
            "fs_uuid": b.fs_uuid,
            "fs_label": b.fs_label,
            "block_size": b.block_size,
            "mount_options": b.mount_options,
            "fs_freq": b.fs_freq,
            "fs_passno": b.fs_passno,
            "lvm_vg": b.lvm_vg,
            "lvm_lv": b.lvm_lv,
            "lvm_segtype": b.lvm_segtype,
            "lvm_stripes": b.lvm_stripes,
            "lvm_stripe_size_kib": b.lvm_stripe_size_kib,
            "raid_level": b.raid_level,
            "raid_chunk_kib": b.raid_chunk_kib,
            "raid_metadata": b.raid_metadata,
            "raid_uuid": b.raid_uuid,
            "crypt_type": b.crypt_type,
        }
    )


def _net_interface_layout(n: NetInterfaceInfo) -> JsonObject:
    return _sparse(
        {
            "mtu": n.mtu,
            "dns": n.dns,
            "routes": [{"dest": r.dest, "via": r.via} for r in n.routes] if n.routes else None,
            "bond_mode": n.bond_mode,
            "vlan_id": n.vlan_id,
        }
    )


def _lvm_vg_layout(v: LvmVgInfo) -> JsonObject:
    return _sparse({"vg_uuid": v.vg_uuid, "extent_size_bytes": v.extent_size_bytes, "pv_ids": v.pv_ids})


def to_inventory_create(data: InventoryInput) -> ServerInventoryCreate:
    """검증된 inventory 메시지를 서버 inventory 저장 DTO로 변환한다."""
    services = _service_dicts(data)
    listen_ports = _listen_port_dicts(data)
    return ServerInventoryCreate(
        agent_id=str(data.agent_id),
        composite_id=data.composite_id,
        machine_id=data.machine_id,
        hostname=data.hostname,
        agent_version=data.agent_version,
        collected_at=data.collected_at,
        boot_time=data.boot_time,
        agent_started_at=data.agent_started_at,
        os_family=data.os_family,
        os_id=data.os_id,
        os_version=data.os_version,
        os_codename=data.os_codename,
        kernel_version=data.kernel_version,
        cpu_cores=data.cpu_cores,
        cpu_model=data.cpu_model,
        mem_total_bytes=data.mem_total_bytes,
        block_devices=[
            {
                "name": b.name,
                "type": b.type,
                "size_bytes": b.size_bytes,
                "fstype": b.fstype,
                "mountpoint": b.mountpoint,
                "parent": b.parent,
                "id": b.id,
                "id_type": b.id_type,
                **_block_device_layout(b),
            }
            for b in data.block_devices
        ],
        net_interfaces=[
            {
                "name": n.name,
                "id": n.id,
                "id_type": n.id_type,
                "kind": n.kind,
                "speed_mbps": n.speed_mbps,
                "addresses": [
                    {
                        "address": a.address,
                        "prefix": a.prefix,
                        "family": a.family,
                        **({"origin": a.origin} if a.origin is not None else {}),
                    }
                    for a in n.addresses
                ],
                "gateway": n.gateway,
                **_net_interface_layout(n),
            }
            for n in data.net_interfaces
        ],
        lvm_vgs=[
            {
                "name": v.name,
                "size_bytes": v.size_bytes,
                "free_bytes": v.free_bytes,
                "data_percent": v.data_percent,
                "metadata_percent": v.metadata_percent,
                **_lvm_vg_layout(v),
            }
            for v in data.lvm_vgs
        ],
        ip_external=data.ip_external,
        services=services,
        listen_ports=listen_ports,
        service_categories=compute_service_categories(services, listen_ports),
        arch=data.arch,
        bits=data.bits,
        boot_firmware=data.boot_firmware,
        secure_boot=data.secure_boot,
        edition=data.edition,
        product_name=data.product_name,
        timezone=data.timezone,
        rtc_utc=data.rtc_utc,
        boot=(
            {
                "kernel_cmdline": data.boot.kernel_cmdline,
                "root_ref_type": data.boot.root_ref_type,
                "grub_install_target": data.boot.grub_install_target,
            }
            if data.boot is not None
            else None
        ),
        nonblock_mounts=(
            [
                {
                    "source": m.source,
                    "target": m.target,
                    "fstype": m.fstype,
                    "options": m.options,
                    "fs_freq": m.fs_freq,
                    "fs_passno": m.fs_passno,
                }
                for m in data.nonblock_mounts
            ]
            if data.nonblock_mounts is not None
            else None
        ),
    )


def build_placeholder_inventory(data: MetricsInput) -> ServerInventoryCreate:
    """inventory보다 먼저 도착한 metrics를 저장할 수 있도록 최소 서버 DTO를 만든다."""
    return ServerInventoryCreate(
        agent_id=str(data.agent_id),
        composite_id=data.composite_id,
        machine_id=data.machine_id,
        hostname=str(data.agent_id),
        agent_version=data.agent_version,
        collected_at=data.collected_at,
        boot_time=data.boot_time,
        agent_started_at=data.agent_started_at,
        os_family=data.os_family,
        os_id=None,
        os_version=None,
        os_codename=None,
        kernel_version=None,
        cpu_cores=None,
        cpu_model=None,
        mem_total_bytes=None,
        block_devices=[],
        net_interfaces=[],
        lvm_vgs=[],
        ip_external=None,
        services=None,
        listen_ports=[],
        service_categories=[],
    )
