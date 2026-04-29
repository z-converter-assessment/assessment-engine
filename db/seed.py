"""개발용 더미 데이터. web/main.py lifespan에서 호출 — 주석 처리 시 시딩 건너뜀."""
from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from config import web_settings
from db.models.server_disk_io import ServerDiskIo
from db.models.server_inventory import ServerInventory
from db.models.server_metrics import ServerMetrics
from db.models.server_mount_usage import ServerMountUsage
from db.models.server_net_io import ServerNetIo
from db.redis import get_redis

# ── 서버 정의 ────────────────────────────────────────────────────────────────

_SERVERS = [
    {
        "inv": dict(
            machine_id="a" * 32,
            hostname="web-server-01",
            agent_version="1.0.0",
            os_id="ubuntu", os_version="22.04", os_codename="jammy",
            kernel_version="5.15.0-101-generic",
            cpu_cores=4,
            cpu_model="Intel(R) Xeon(R) CPU E5-2670 @ 2.60GHz",
            mem_total_kb=8 * 1024 * 1024,
            swap_total_kb=2 * 1024 * 1024,
            ip_internal=["192.168.1.10", "192.168.1.11"],
            ip_external=["203.0.113.10"],
            disks=[
                {"name": "sda", "size_bytes": 250 * 1024**3, "type": "SSD"},
                {"name": "sdb", "size_bytes": 1024 * 1024**3, "type": "HDD"},
            ],
            mounts=[
                {"mount": "/",     "fstype": "ext4", "total_bytes": 100 * 1024**3},
                {"mount": "/var",  "fstype": "ext4", "total_bytes":  50 * 1024**3},
                {"mount": "/home", "fstype": "ext4", "total_bytes":  50 * 1024**3},
            ],
        ),
        "online": True,
        "boot_days_ago": 14,
        "profile": "moderate",
        "interfaces": ["eth0", "eth1"],
        "io_devices": ["sda", "sdb"],
    },
    {
        "inv": dict(
            machine_id="b" * 32,
            hostname="db-server-01",
            agent_version="1.0.0",
            os_id="centos", os_version="7", os_codename=None,
            kernel_version="3.10.0-1160.el7.x86_64",
            cpu_cores=8,
            cpu_model="Intel(R) Xeon(R) CPU E5-2690 @ 2.90GHz",
            mem_total_kb=32 * 1024 * 1024,
            swap_total_kb=4 * 1024 * 1024,
            ip_internal=["192.168.1.20"],
            ip_external=None,
            disks=[
                {"name": "sda",     "size_bytes":  500 * 1024**3, "type": "SSD"},
                {"name": "nvme0n1", "size_bytes": 2048 * 1024**3, "type": "NVMe"},
            ],
            mounts=[
                {"mount": "/",     "fstype": "xfs", "total_bytes":  100 * 1024**3},
                {"mount": "/data", "fstype": "xfs", "total_bytes": 1500 * 1024**3},
            ],
        ),
        "online": True,
        "boot_days_ago": 30,
        "profile": "heavy",
        "interfaces": ["ens3"],
        "io_devices": ["sda", "nvme0n1"],
    },
    {
        "inv": dict(
            machine_id="c" * 32,
            hostname="backup-server-01",
            agent_version="1.0.0",
            os_id="debian", os_version="11", os_codename="bullseye",
            kernel_version="5.10.0-26-amd64",
            cpu_cores=2,
            cpu_model="Intel(R) Core(TM) i3-8100 @ 3.60GHz",
            mem_total_kb=4 * 1024 * 1024,
            swap_total_kb=1 * 1024 * 1024,
            ip_internal=["192.168.1.30"],
            ip_external=None,
            disks=[
                {"name": "sda", "size_bytes": 500 * 1024**3, "type": "HDD"},
            ],
            mounts=[
                {"mount": "/",       "fstype": "ext4", "total_bytes":  50 * 1024**3},
                {"mount": "/backup", "fstype": "ext4", "total_bytes": 400 * 1024**3},
            ],
        ),
        "online": False,
        "boot_days_ago": 60,
        "profile": "light",
        "interfaces": ["eth0"],
        "io_devices": ["sda"],
    },
]

# ── 프로파일 정의 ─────────────────────────────────────────────────────────────

# CPU jiffies 비율 (interval당 총량 = cores * 100Hz * 60s)
_CPU = {
    "moderate": {"user": .25, "nice": .01, "system": .08, "idle": .60, "iowait": .04, "irq": .01, "softirq": .01, "steal": .00},
    "heavy":    {"user": .50, "nice": .02, "system": .18, "idle": .20, "iowait": .07, "irq": .02, "softirq": .01, "steal": .00},
    "light":    {"user": .07, "nice": .00, "system": .02, "idle": .89, "iowait": .01, "irq": .00, "softirq": .00, "steal": .00},
}

# 메모리 사용 비율
_MEM = {
    "moderate": {"used": .60, "cached": .20, "buffers": .05, "swap": .10},
    "heavy":    {"used": .85, "cached": .08, "buffers": .03, "swap": .40},
    "light":    {"used": .38, "cached": .30, "buffers": .10, "swap": .05},
}

# 네트워크 I/O: interface → (rx bytes/s, tx bytes/s, rx pps, tx pps)
_NET = {
    "moderate": {
        "eth0": (5 * 1024**2,  1 * 1024**2,  3500,  700),
        "eth1": (512 * 1024,  100 * 1024,    350,    70),
    },
    "heavy": {
        "ens3": (50 * 1024**2, 20 * 1024**2, 35000, 14000),
    },
    "light": {
        "eth0": (200 * 1024, 1 * 1024**2, 140, 700),
    },
}

# 디스크 I/O: device → (reads/s, writes/s, sectors_r/s, sectors_w/s)
_DISK = {
    "moderate": {
        "sda": (50,   20,    400,    160),
        "sdb": ( 5,    2,     40,     16),
    },
    "heavy": {
        "sda":     ( 100,    80,    800,    640),
        "nvme0n1": (2000,  1500,  16000,  12000),
    },
    "light": {
        "sda": (10, 5, 80, 40),
    },
}

# 마운트 사용률
_MOUNT_USAGE = {
    "moderate": {"/": .45, "/var": .60, "/home": .30},
    "heavy":    {"/": .55, "/data": .78},
    "light":    {"/": .35, "/backup": .65},
}


async def seed_demo_data(session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    redis = get_redis()

    for srv in _SERVERS:
        online     = srv["online"]
        profile    = srv["profile"]
        interfaces = srv["interfaces"]
        io_devices = srv["io_devices"]
        inv_data   = srv["inv"]

        inv = ServerInventory(
            **inv_data,
            boot_time=now - timedelta(days=srv["boot_days_ago"]),
            last_seen_at=now - timedelta(seconds=30) if online else now - timedelta(hours=3),
        )
        session.add(inv)
        await session.flush()  # id 확보

        sid    = inv.id
        cores  = inv_data["cpu_cores"]
        mem_kb = inv_data["mem_total_kb"]
        swp_kb = inv_data["swap_total_kb"]
        mounts = inv_data["mounts"]

        cpu_p  = _CPU[profile]
        mem_p  = _MEM[profile]
        net_p  = _NET[profile]
        disk_p = _DISK[profile]
        mnt_p  = _MOUNT_USAGE[profile]

        # 온라인 서버: 최근 12분치 / 오프라인: 3시간 전 3개
        n_readings = 12 if online else 3
        interval   = timedelta(minutes=1)
        if online:
            base = now - timedelta(seconds=30) - interval * n_readings
        else:
            base = now - timedelta(hours=3) - interval * n_readings

        total_j = cores * 100 * 60  # jiffies per interval

        # 누적 카운터 초기값 (서버가 이미 수 시간 돌았다는 전제)
        cpu_c = {k: int(1_000_000 * v) for k, v in cpu_p.items()}
        net_c = {
            iface: {
                "rx_bytes": net_p.get(iface, (0, 0, 0, 0))[0] * 3600,
                "tx_bytes": net_p.get(iface, (0, 0, 0, 0))[1] * 3600,
                "rx_pkt":   net_p.get(iface, (0, 0, 0, 0))[2] * 3600,
                "tx_pkt":   net_p.get(iface, (0, 0, 0, 0))[3] * 3600,
            }
            for iface in interfaces
        }
        disk_c = {
            dev: {
                "reads":  disk_p.get(dev, (0, 0, 0, 0))[0] * 3600,
                "writes": disk_p.get(dev, (0, 0, 0, 0))[1] * 3600,
                "sec_r":  disk_p.get(dev, (0, 0, 0, 0))[2] * 3600,
                "sec_w":  disk_p.get(dev, (0, 0, 0, 0))[3] * 3600,
            }
            for dev in io_devices
        }

        for i in range(n_readings):
            ts = base + interval * i

            # CPU 누적
            for k in cpu_c:
                cpu_c[k] += int(total_j * cpu_p[k])

            # 메모리 스냅샷
            used    = int(mem_kb * mem_p["used"])
            cached  = int(mem_kb * mem_p["cached"])
            buffers = int(mem_kb * mem_p["buffers"])
            free    = mem_kb - used - cached - buffers
            avail   = free + cached
            sw_free = int(swp_kb * (1 - mem_p["swap"]))

            session.add(ServerMetrics(
                server_id=sid, collected_at=ts,
                cpu_user=cpu_c["user"],    cpu_nice=cpu_c["nice"],
                cpu_system=cpu_c["system"], cpu_idle=cpu_c["idle"],
                cpu_iowait=cpu_c["iowait"], cpu_irq=cpu_c["irq"],
                cpu_softirq=cpu_c["softirq"], cpu_steal=cpu_c["steal"],
                mem_total_kb=mem_kb, mem_free_kb=free,
                mem_available_kb=avail, mem_buffers_kb=buffers, mem_cached_kb=cached,
                swap_total_kb=swp_kb, swap_free_kb=sw_free,
                load_1m=round(cores * (cpu_p["user"] + cpu_p["system"]) * 1.1, 2),
                load_5m=round(cores * (cpu_p["user"] + cpu_p["system"]) * 1.0, 2),
                load_15m=round(cores * (cpu_p["user"] + cpu_p["system"]) * 0.9, 2),
            ))

            # 네트워크 I/O
            for iface in interfaces:
                r = net_p.get(iface, (1024, 512, 1, 1))
                net_c[iface]["rx_bytes"] += r[0] * 60
                net_c[iface]["tx_bytes"] += r[1] * 60
                net_c[iface]["rx_pkt"]   += r[2] * 60
                net_c[iface]["tx_pkt"]   += r[3] * 60
                session.add(ServerNetIo(
                    server_id=sid, collected_at=ts, interface=iface,
                    rx_bytes=net_c[iface]["rx_bytes"],
                    tx_bytes=net_c[iface]["tx_bytes"],
                    rx_packets=net_c[iface]["rx_pkt"],
                    tx_packets=net_c[iface]["tx_pkt"],
                    rx_errors=0, tx_errors=0,
                ))

            # 디스크 I/O
            for dev in io_devices:
                d = disk_p.get(dev, (10, 5, 80, 40))
                disk_c[dev]["reads"]  += d[0] * 60
                disk_c[dev]["writes"] += d[1] * 60
                disk_c[dev]["sec_r"]  += d[2] * 60
                disk_c[dev]["sec_w"]  += d[3] * 60
                session.add(ServerDiskIo(
                    server_id=sid, collected_at=ts, device=dev,
                    reads_completed=disk_c[dev]["reads"],
                    writes_completed=disk_c[dev]["writes"],
                    sectors_read=disk_c[dev]["sec_r"],
                    sectors_written=disk_c[dev]["sec_w"],
                ))

            # 마운트 사용량
            for m in mounts:
                mp      = m["mount"]
                total   = m["total_bytes"]
                used_b  = int(total * mnt_p.get(mp, 0.50))
                free_b  = total - used_b
                avail_b = int(free_b * 0.95)
                session.add(ServerMountUsage(
                    server_id=sid, collected_at=ts, mount=mp,
                    total_bytes=total, free_bytes=free_b, avail_bytes=avail_b,
                ))

        # 온라인 서버: Redis online 키를 24h TTL로 세팅 (dev 세션 동안 온라인 유지)
        if online:
            await redis.set(
                web_settings.redis_key_online.format(sid),
                1,
                ex=86400,
            )

    await session.commit()