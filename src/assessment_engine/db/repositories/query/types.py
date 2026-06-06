"""Query repository 공용 타입·dispatch 상수 (P2 단일 진실).

5 도메인 sub-repository (server / metric / report / attention / task) 가 공유하는
TimeRange · BucketSize · MetricType · dispatch table whitelist 카탈로그.

caller(`web/services/query_service.py`, `web/routers/api.py`, `tests/...`)는 본 모듈에서 import.
"""

from datetime import timedelta
from typing import Literal

# ─── chart metric 카탈로그 (router Literal whitelist) ───
MetricType = Literal[
    "cpu.usage_percent",
    "cpu.user_percent",
    "cpu.system_percent",
    "cpu.iowait_percent",
    "load.1m",
    "load.5m",
    "load.15m",
    "mem.usage_percent",
    "mem.available_percent",
    "mem.cached_percent",
    "mem.buffers_percent",
    "swap.usage_percent",
    "disk.read_iops",
    "disk.write_iops",
    "fs.usage_percent",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
    "net.rx_packets_per_sec",
    "net.tx_packets_per_sec",
]
# 환경 전체 추이 차트 metric — 대시보드 추이 + 환경 성능 추이(서버상세 성능 추이 풀세트의 환경판).
# capacity-weighted(cpu·mem·disk·fs·swap = sum(num)/sum(den)) / 코어 정규화(load=sum(load)/sum(cores))
# / 합산(disk·net rate).
EnvironmentMetricType = Literal[
    "cpu.usage_percent",
    "cpu.user_percent",
    "cpu.system_percent",
    "cpu.iowait_percent",
    "load.15m",
    "mem.usage_percent",
    "mem.available_percent",
    "mem.cached_percent",
    "mem.buffers_percent",
    "swap.usage_percent",
    "disk.usage_percent",
    "fs.usage_percent",
    "disk.read_iops",
    "disk.write_iops",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
    "net.rx_packets_per_sec",
    "net.tx_packets_per_sec",
]
TimeRange = Literal["15m", "1h", "6h", "24h", "7d", "14d", "30d"]
BucketSize = Literal["1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h", "1d"]
AggFunc = Literal["avg", "max", "p95"]

# TimeRange → timedelta. metric_chart·reboot_events 양쪽 사용 (service·repo 중복 방지).
# 14d는 right-sizing 윈도우(recommendation.WINDOW_DAYS)와 동일 — 보고서·대시보드·차트 일관.
TIME_RANGE_TD: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "14d": timedelta(days=14),
    "30d": timedelta(days=30),
}

# (SQL interval 문자열, Python timedelta) — bucket 단위를 SQL과 Python 양쪽에서 사용.
_BUCKET_INFO: dict[str, tuple[str, timedelta]] = {
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

# TimeRange → 자동 BucketSize — 범위별 적정 분해력. SSR 정적 차트(환경 부하 추이)·동적 fetch 차트 공통.
# chart-utils.js `AUTO_BUCKET` 과 값 동기화 의무 (#F10 TimeRange/BucketSize 단일 진실).
AUTO_BUCKET: dict[str, str] = {
    "15m": "1m",
    "1h": "5m",
    "6h": "15m",
    "24h": "30m",
    "7d": "3h",
    "14d": "6h",
    "30d": "12h",
}

_AGG: dict[str, str] = {
    "avg": "avg(v)",
    "max": "max(v)",
    "p95": "percentile_cont(0.95) WITHIN GROUP (ORDER BY v)",
}

# ─── chart dispatch 매핑 (router Literal로 whitelist된 metric_type만 도달) ───

# CPU 누적 jiffies. delta로 % 계산 (LAG 기반). active/component 모두 분자만 다름.
_CPU_TOTAL_EXPR = "cpu_user+cpu_nice+cpu_system+cpu_idle+cpu_iowait+cpu_irq+cpu_softirq+cpu_steal"
_CPU_NUMERATOR: dict[str, str] = {
    "cpu.usage_percent": "cpu_user+cpu_nice+cpu_system+cpu_iowait+cpu_irq+cpu_softirq+cpu_steal",
    "cpu.user_percent": "cpu_user",
    "cpu.system_percent": "cpu_system",
    "cpu.iowait_percent": "cpu_iowait",
}

# 시점 값. dimension 없음. value_expr는 server_metrics 컬럼/식.
_SCALAR_VALUE_EXPR: dict[str, str] = {
    "load.1m": "load_1m",
    "load.5m": "load_5m",
    "load.15m": "load_15m",
    "mem.usage_percent": (
        "CASE WHEN mem_total_kb > 0 THEN (mem_total_kb - mem_available_kb)::float / mem_total_kb * 100 END"
    ),
    "mem.available_percent": "CASE WHEN mem_total_kb > 0 THEN mem_available_kb::float / mem_total_kb * 100 END",
    "mem.cached_percent": "CASE WHEN mem_total_kb > 0 THEN mem_cached_kb::float  / mem_total_kb * 100 END",
    "mem.buffers_percent": "CASE WHEN mem_total_kb > 0 THEN mem_buffers_kb::float / mem_total_kb * 100 END",
    # swap_total_kb=0 (swap 미설정 VM) 도 0% 반환 — chart 0 line 자연 표시.
    # NULL 반환 시 chart endpoint 가 row 누락 → empty 표시 (운영자 혼란).
    "swap.usage_percent": (
        "CASE WHEN swap_total_kb > 0 THEN (swap_total_kb - swap_free_kb)::float / swap_total_kb * 100 ELSE 0 END"
    ),
}

# (table, dim_col, value_col) 튜플 — disk/net rate per dimension.
# table 명은 ORM 모델 __tablename__ — 동적 lookup 회피 위해 metric.py 본문에서 채움.
# (sub-module 의존성 회피 — types.py는 ORM import 안 함)
_RATE_PER_DIM_DEFS: dict[str, tuple[str, str]] = {
    "disk.read_iops": ("device", "reads_completed"),
    "disk.write_iops": ("device", "writes_completed"),
    "net.rx_bytes_per_sec": ("interface", "rx_bytes"),
    "net.tx_bytes_per_sec": ("interface", "tx_bytes"),
    "net.rx_packets_per_sec": ("interface", "rx_packets"),
    "net.tx_packets_per_sec": ("interface", "tx_packets"),
}

# server_mount_usage 가상 mount 필터 — SQL fragment 단일 진실.
# 모든 mount 합산·집계 SQL이 본 fragment를 적용. 변경 시 device_filters._VIRTUAL_MOUNT_PREFIXES와 동기화.
# 데이터 볼륨 술어 — device_filters.is_data_volume 의 SQL 투영. server_mount_usage.major 활용
# (major==0 = 블록 디바이스 없는 가상 fs: proc/sys/tmpfs/cgroup/overlay). fstype 은 메트릭에 없어 미적용
# (inventory defense 전담 — squashfs 등은 agent 가 메트릭 거의 미발행). major NULL = 마이그레이션 전 행(path fallback).
# 사용자 입력 0 — 정적 상수만이라 f-string 안전 (CLAUDE.md C5 whitelist).
_DATA_VOLUME_SQL_FILTER = """
    (
        mount ~ '^[A-Za-z]:'
        OR (
            (major IS NULL OR major <> 0)
            AND mount <> '/boot'
            AND mount NOT LIKE '/boot/%'
        )
    )
"""

# 환경 시점값 capacity-weighted (시점별 sum(numerator)/sum(denominator) * 100). server_metrics 컬럼.
# metric_trend 그룹2 — environment_utilization(mem)·서버별 _SCALAR_VALUE_EXPR 와 정의 정합(환경은 sum/sum).
_ENV_SCALAR_WEIGHTED: dict[str, tuple[str, str]] = {
    "mem.usage_percent": ("mem_total_kb - mem_available_kb", "mem_total_kb"),
    "mem.available_percent": ("mem_available_kb", "mem_total_kb"),
    "mem.cached_percent": ("mem_cached_kb", "mem_total_kb"),
    "mem.buffers_percent": ("mem_buffers_kb", "mem_total_kb"),
    "swap.usage_percent": ("swap_total_kb - swap_free_kb", "swap_total_kb"),
}

# 물리 disk SQL 제외 — device_filters.is_physical_disk POSIX 번역(가상·LVM·파티션). 변경 시 동기화.
# 환경 disk rate 합산 물리 device 한정(이중 집계 회피). 정적 상수 f-string 안전(#C5).
_PHYS_DISK_SQL_FILTER = """
    device !~ '^(loop[0-9]*|ram[0-9]*|zram[0-9]*|fd[0-9]*|sr[0-9]*|nbd[0-9]*)$'
    AND device !~ '^(dm-[0-9]+|md[0-9]+)$'
    AND device !~ '^(sd[a-z]+[0-9]+|vd[a-z]+[0-9]+|hd[a-z]+[0-9]+)$'
    AND device !~ '^(xvd[a-z]+[0-9]+|nvme[0-9]+n[0-9]+p[0-9]+|mmcblk[0-9]+p[0-9]+)$'
"""

# 가상 network interface SQL 제외 — device_filters._VIRTUAL_IFACE_RE/_WIN_IFACE_FILTER_RE POSIX 번역. 변경 시 동기화.
# bond/team master·br/docker/virbr bridge·vlan sub-interface 제외 — master/member 이중 집계 회피(물리 member 만 합산).
_VIRTUAL_IFACE_SQL_FILTER = """
    interface !~ '^(lo|veth.*|sit[0-9]*|tunl[0-9]*|ip6tnl[0-9]*|gre[0-9]*)$'
    AND interface !~ '^(gretap[0-9]*|erspan[0-9]*|dummy[0-9]*|ifb[0-9]*|nlmon[0-9]*)$'
    AND interface !~ '^(bond[0-9]+|team[0-9]+|br[0-9]+|br-.+|docker[0-9]+|virbr[0-9]+(-nic)?)$'
    AND interface !~ '.+\\.[0-9]+$'
    AND interface !~ '.+-[0-9]{4}$'
"""
