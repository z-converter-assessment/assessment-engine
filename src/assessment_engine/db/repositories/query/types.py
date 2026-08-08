"""Query repository 공용 타입·dispatch 상수 (P2 단일 진실).

5 도메인 sub-repository (server / metric / report / attention / task) 가 공유하는
TimeRange · BucketSize · MetricType · dispatch table whitelist 카탈로그.

caller(`web/services/query/service.py`, `web/routers/api.py`, `tests/...`)는 본 모듈에서 import.
"""

from datetime import timedelta
from typing import Literal

from assessment_engine.domain.boot_time import BOOT_TIME_JITTER_TOLERANCE

BOOT_JITTER_SEC = int(BOOT_TIME_JITTER_TOLERANCE.total_seconds())

type MetricType = Literal[
    "cpu.usage_percent",
    "cpu.user_percent",
    "cpu.system_percent",
    "cpu.iowait_percent",
    "cpu.nice_percent",
    "cpu.run_queue",
    "cpu.saturation",
    "cpu.blocked",
    "cpu.psi",
    "mem.usage_percent",
    "mem.available_percent",
    "mem.cached_percent",
    "mem.buffers_percent",
    "mem.psi",
    "mem.paging_pressure",
    "disk.read_iops",
    "disk.write_iops",
    "disk.read_kbps",
    "disk.write_kbps",
    "disk.io_saturation",
    "disk.saturation",
    "disk.psi",
    "fs.usage_percent",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
    "net.rx_packets_per_sec",
    "net.tx_packets_per_sec",
    "net.retrans_percent",
    "net.drop_percent",
    "net.congested",
]

# 활동 합산(net bytes), 판정 crossing 호스트 수(`*_hosts`). 이기종 장치의 절대치(IOPS·kbps·PPS)와 Windows
# 미발행 신호(PSI)는 그래서 빠진다 — 전자는 비교 기준선이 없고, 후자는 Linux 값이 "환경 전체"로 읽힌다.


type EnvironmentMetricType = Literal[
    "cpu.usage_percent",
    "cpu.saturation_hosts",
    "mem.usage_percent",
    "mem.paging_pressure_hosts",
    "fs.usage_percent",
    "disk.saturation_hosts",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
    "net.congested_hosts",
]
type TimeRange = Literal["15m", "1h", "6h", "24h", "7d", "14d", "30d"]
type BucketSize = Literal["1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h", "1d"]
type AggFunc = Literal["avg", "max", "p95"]


TIME_RANGE_TD: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "14d": timedelta(days=14),
    "30d": timedelta(days=30),
}

# 평가 윈도우(일). 소수가 나와도 SQL interval 이 fraction 을 받는다 (0.25 days = 6h).
DIAGNOSTIC_RANGE_DAYS: dict[str, float] = {r: td.total_seconds() / 86400 for r, td in TIME_RANGE_TD.items()}


DIAGNOSTIC_DEFAULT_TIME_RANGE = "14d"

_BUCKET_INFO: dict[BucketSize, tuple[str, timedelta]] = {
    "1m": ("1 minute", timedelta(minutes=1)),
    "5m": ("5 minutes", timedelta(minutes=5)),
    "15m": ("15 minutes", timedelta(minutes=15)),
    "30m": ("30 minutes", timedelta(minutes=30)),
    "1h": ("1 hour", timedelta(hours=1)),
    "3h": ("3 hours", timedelta(hours=3)),
    "6h": ("6 hours", timedelta(hours=6)),
    "12h": ("12 hours", timedelta(hours=12)),
    "1d": ("1 day", timedelta(days=1)),
}


AUTO_BUCKET: dict[str, str] = {
    "15m": "1m",
    "1h": "5m",
    "6h": "15m",
    "24h": "30m",
    "7d": "3h",
    "14d": "6h",
    "30d": "12h",
}

_AGG: dict[AggFunc, str] = {
    "avg": "avg(v)",
    "max": "max(v)",
    "p95": "percentile_cont(0.95) WITHIN GROUP (ORDER BY v)",
}

# 아래 dispatch 상수는 router Literal 로 whitelist 된 metric_type 만 도달 — SQL f-string 조립이 안전한 근거.

# 분모 성분 COALESCE — Windows 는 nice/iowait/irq/softirq/steal 이 null(OS 개념 부재)이라 raw 합이
# X+NULL=NULL 로 전파되면 delta 가 null 이 돼 Windows CPU 추이 차트가 빈다(cagg·compute_cpu 동일).
# 분자 중 per-component(user/system/iowait)는 bare 유지 — COALESCE 하면 Windows iowait 미측정이 0(여유)으로 읽힌다.
_CPU_TOTAL_EXPR = (
    "COALESCE(cpu_user_s,0)+COALESCE(cpu_nice_s,0)+COALESCE(cpu_system_s,0)+COALESCE(cpu_idle_s,0)"
    "+COALESCE(cpu_iowait_s,0)+COALESCE(cpu_irq_s,0)+COALESCE(cpu_softirq_s,0)+COALESCE(cpu_steal_s,0)"
)
_CPU_NUMERATOR: dict[MetricType | EnvironmentMetricType, str] = {
    "cpu.usage_percent": (
        "COALESCE(cpu_user_s,0)+COALESCE(cpu_nice_s,0)+COALESCE(cpu_system_s,0)+COALESCE(cpu_iowait_s,0)"
        "+COALESCE(cpu_irq_s,0)+COALESCE(cpu_softirq_s,0)+COALESCE(cpu_steal_s,0)"
    ),
    "cpu.user_percent": "cpu_user_s",
    "cpu.system_percent": "cpu_system_s",
    "cpu.iowait_percent": "cpu_iowait_s",
    "cpu.nice_percent": "cpu_nice_s",  # Linux 전용(Windows null -> 분자 null, 자연 제외)
}


_RATE_PER_DIM_DEFS: dict[MetricType | EnvironmentMetricType, tuple[str, str]] = {
    "disk.read_iops": ("device_id", "ops_read"),
    "disk.write_iops": ("device_id", "ops_write"),
    "disk.read_kbps": ("device_id", "io_read_bytes / 1024.0"),
    "disk.write_kbps": ("device_id", "io_write_bytes / 1024.0"),
    "net.rx_bytes_per_sec": ("iface_id", "rx_bytes"),
    "net.tx_bytes_per_sec": ("iface_id", "tx_bytes"),
    "net.rx_packets_per_sec": ("iface_id", "rx_packets"),
    "net.tx_packets_per_sec": ("iface_id", "tx_packets"),
}


_VIRTUAL_FSTYPES = (
    "'tmpfs','devtmpfs','overlay','squashfs','proc','sysfs','cgroup','cgroup2','mqueue','debugfs',"
    "'tracefs','securityfs','pstore','bpf','configfs','ramfs','autofs','hugetlbfs','fusectl','nsfs',"
    "'efivarfs','binfmt_misc'"
)
_DATA_VOLUME_SQL_FILTER = f"(fstype IS NULL OR fstype NOT IN ({_VIRTUAL_FSTYPES})) AND mountpoint NOT LIKE '/boot%'"

_DATA_VOLUME_CAGG_FILTER = (
    f"(fstype_any IS NULL OR fstype_any NOT IN ({_VIRTUAL_FSTYPES})) AND mountpoint NOT LIKE '/boot%'"
)

# (numerator, denominator, guard) — 단위 By. guard 는 분자 성분이 실측된 행만 남긴다: Windows 는
# mem_cached/buffered 가 null(OS 미측정)이라 가드 없이 집계하면 미측정이 0 으로 삼켜진다.
_ENV_SCALAR_WEIGHTED: dict[MetricType | EnvironmentMetricType, tuple[str, str, str]] = {
    "mem.usage_percent": (
        "mem_limit_bytes - mem_available_bytes",
        "mem_limit_bytes",
        "mem_limit_bytes > 0 AND mem_available_bytes IS NOT NULL",
    ),
    "mem.available_percent": (
        "mem_available_bytes",
        "mem_limit_bytes",
        "mem_limit_bytes > 0 AND mem_available_bytes IS NOT NULL",
    ),
    "mem.cached_percent": (
        "mem_cached_bytes",
        "mem_limit_bytes",
        "mem_limit_bytes > 0 AND mem_cached_bytes IS NOT NULL",
    ),
    "mem.buffers_percent": (
        "mem_buffered_bytes",
        "mem_limit_bytes",
        "mem_limit_bytes > 0 AND mem_buffered_bytes IS NOT NULL",
    ),
}


# 상관 서브쿼리 형태는 제약이다 — bare server_id/device_id/iface_id 참조라야 raw hypertable 과 cagg

_PHYS_DISK_SQL_FILTER = (
    "EXISTS (SELECT 1 FROM server_inventory si_pf "
    "CROSS JOIN LATERAL jsonb_array_elements(si_pf.block_devices) bd_pf "
    "WHERE si_pf.id = server_id "
    "AND ((bd_pf->>'id_type') || ':' || (bd_pf->>'id') = device_id "
    "OR 'name:' || (bd_pf->>'name') = device_id) "
    "AND (bd_pf->>'type') = 'disk')"
)
_PHYS_IFACE_SQL_FILTER = (
    "EXISTS (SELECT 1 FROM server_inventory si_pf "
    "CROSS JOIN LATERAL jsonb_array_elements(si_pf.net_interfaces) ni_pf "
    "WHERE si_pf.id = server_id "
    "AND (ni_pf->>'id_type') || ':' || (ni_pf->>'id') = iface_id "
    "AND (ni_pf->>'kind') IN ('physical', 'bond_master'))"
)
