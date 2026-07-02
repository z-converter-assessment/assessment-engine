"""Query repository 공용 타입·dispatch 상수 (P2 단일 진실).

5 도메인 sub-repository (server / metric / report / attention / task) 가 공유하는
TimeRange · BucketSize · MetricType · dispatch table whitelist 카탈로그.

caller(`web/services/query_service.py`, `web/routers/api.py`, `tests/...`)는 본 모듈에서 import.
"""

from datetime import timedelta
from typing import Literal

from assessment_engine.boot_time import BOOT_TIME_JITTER_TOLERANCE

# boot_time 지터 허용치(초) — boot_time.BOOT_TIME_JITTER_TOLERANCE 단일 진실 파생.
# metric_trend(차트)와 report(도넛·집계) CPU reset-gate 가 공유하는 SQL bound param 값.
# 둘 다 본 상수를 참조해 동일 게이트(NTP 보정 흔들림을 false-reset 으로 오판 안 함)를 보장.
BOOT_JITTER_SEC = int(BOOT_TIME_JITTER_TOLERANCE.total_seconds())

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
    "disk.read_kbps",
    "disk.write_kbps",
    "disk.queue",
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
    "disk.read_kbps",
    "disk.write_kbps",
    "disk.queue",
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

# (dim_col, value_col) — disk/net rate per dimension. table 명은 metric.py 에서 결합
# (types.py 는 ORM import 안 함 — circular 회피).
_RATE_PER_DIM_DEFS: dict[str, tuple[str, str]] = {
    "disk.read_iops": ("device", "reads_completed"),
    "disk.write_iops": ("device", "writes_completed"),
    # 처리량 — sectors(512B) -> KB. value 표현식이 rate SQL cnt 로 삽입(정적 상수, #C5 안전).
    "disk.read_kbps": ("device", "sectors_read * 512.0 / 1024"),
    "disk.write_kbps": ("device", "sectors_written * 512.0 / 1024"),
    "net.rx_bytes_per_sec": ("interface", "rx_bytes"),
    "net.tx_bytes_per_sec": ("interface", "tx_bytes"),
    "net.rx_packets_per_sec": ("interface", "rx_packets"),
    "net.tx_packets_per_sec": ("interface", "tx_packets"),
}

# 데이터 볼륨 술어 — device kind 태그(agent 공용 분류기)의 SQL 투영. data 만 (boot/image/가상 fs 제외).
# 가상 fs 는 agent pre-drop, Windows drive 도 kind='data' 로 통일 — major/정규식/path 추론 전부 폐기.
# 정적 상수만이라 f-string 안전 (CLAUDE.md C5 whitelist).
_DATA_VOLUME_SQL_FILTER = "kind = 'data'"

# 환경 시점값 capacity-weighted (시점별 sum(numerator)/sum(denominator) * 100). server_metrics 컬럼.
# metric_trend 그룹2 — environment_utilization(mem)과 동일 capacity-weighted 정의(환경은 sum/sum).
_ENV_SCALAR_WEIGHTED: dict[str, tuple[str, str]] = {
    "mem.usage_percent": ("mem_total_kb - mem_available_kb", "mem_total_kb"),
    "mem.available_percent": ("mem_available_kb", "mem_total_kb"),
    "mem.cached_percent": ("mem_cached_kb", "mem_total_kb"),
    "mem.buffers_percent": ("mem_buffers_kb", "mem_total_kb"),
    "swap.usage_percent": ("swap_total_kb - swap_free_kb", "swap_total_kb"),
}

# 물리 disk/iface 술어 — device kind 태그 기반. physical 만 집계(이중 집계 회피).
# disk: partition/lvm/raid/virtual 제외. iface: loopback/bridge/veth/bond/vlan/tunnel/virtual 제외(master/member 이중 집계 회피).
# 정적 상수 f-string 안전(#C5). cagg 정의도 동일 kind 필터.
_PHYS_DISK_SQL_FILTER = "kind = 'physical'"
_PHYS_IFACE_SQL_FILTER = "kind = 'physical'"
