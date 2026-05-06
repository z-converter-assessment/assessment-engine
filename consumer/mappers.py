from consumer.schemas import InventoryInput, MetricsInput
from db.repositories.inbound import ServerInventoryCreate, ServerMetricCreate


def to_inventory_create(data: InventoryInput) -> ServerInventoryCreate:
    return ServerInventoryCreate(
        machine_id=data.machine_id,
        hostname=data.hostname,
        agent_version=data.agent_version,
        collected_at=data.collected_at,
        os_id=data.os_id,
        os_version=data.os_version,
        os_codename=data.os_codename,
        kernel_version=data.kernel_version,
        cpu_cores=data.cpu_cores,
        cpu_model=data.cpu_model,
        mem_total_kb=data.mem_total_kb,
        swap_total_kb=data.swap_total_kb,
        boot_time=data.boot_time,
        ip_internal=data.ip_internal,
        ip_external=data.ip_external,
        disks=[{"name": d.name, "size_bytes": d.size_bytes, "type": d.type} for d in data.disks],
        mounts=[{"mount": m.mount, "fstype": m.fstype, "total_bytes": m.total_bytes} for m in data.mounts],
        services=[{"unit": s.unit, "sub": s.sub} for s in data.services] if data.services is not None else None,
        listen_ports=[
            {"proto": p.proto, "addr": p.addr, "port": p.port, "uid": p.uid, "pid": p.pid, "comm": p.comm}
            for p in data.listen_ports
        ],
    )


def to_metric_create(data: MetricsInput) -> ServerMetricCreate:
    cpu = data.cpu_stat
    return ServerMetricCreate(
        collected_at=data.collected_at,
        cpu_user=cpu.user if cpu else None,
        cpu_nice=cpu.nice if cpu else None,
        cpu_system=cpu.system if cpu else None,
        cpu_idle=cpu.idle if cpu else None,
        cpu_iowait=cpu.iowait if cpu else None,
        cpu_irq=cpu.irq if cpu else None,
        cpu_softirq=cpu.softirq if cpu else None,
        cpu_steal=cpu.steal if cpu else None,
        mem_total_kb=data.mem_total_kb,
        mem_free_kb=data.mem_free_kb,
        mem_available_kb=data.mem_available_kb,
        mem_buffers_kb=data.mem_buffers_kb,
        mem_cached_kb=data.mem_cached_kb,
        swap_total_kb=data.swap_total_kb,
        swap_free_kb=data.swap_free_kb,
        load_1m=data.load_1m,
        load_5m=data.load_5m,
        load_15m=data.load_15m,
        disk_io=[
            {
                "device": d.device,
                "reads_completed": d.reads_completed,
                "writes_completed": d.writes_completed,
                "sectors_read": d.sectors_read,
                "sectors_written": d.sectors_written,
            }
            for d in data.disk_io
        ],
        mounts=[
            {
                "mount": m.mount,
                "total_bytes": m.total_bytes,
                "free_bytes": m.free_bytes,
                "avail_bytes": m.avail_bytes,
            }
            for m in data.mounts
        ],
        net_io=[
            {
                "interface": n.interface,
                "rx_bytes": n.rx_bytes,
                "tx_bytes": n.tx_bytes,
                "rx_packets": n.rx_packets,
                "tx_packets": n.tx_packets,
                "rx_errors": n.rx_errors,
                "tx_errors": n.tx_errors,
            }
            for n in data.net_io
        ],
    )