"""Query repository 공용 타입·dispatch 상수 (P2 단일 진실).

5 도메인 sub-repository (server / metric / report / attention / task) 가 공유하는
TimeRange · BucketSize · MetricType · dispatch table whitelist 카탈로그.

caller(`web/services/query_service.py`, `web/routers/api.py`, `tests/...`)는 본 모듈에서 import.
"""

from datetime import timedelta
from typing import Literal

from assessment_engine.boot_time import BOOT_TIME_JITTER_TOLERANCE

# boot_time 지터 허용치(초) — reboot_events(server_inventory_history) 의 재부팅 판정 게이트.
# v2 child 시계열(disk_io/net_io)은 boot_time 미보유 -> rate 차트 reset 은 GREATEST(delta,0) 로 흡수(아래).
BOOT_JITTER_SEC = int(BOOT_TIME_JITTER_TOLERANCE.total_seconds())

# ─── chart metric 카탈로그 (router Literal whitelist) ───
# v2 폐기: load.1m/5m/15m(소스 부재 -> cpu.run_queue 대체), swap.usage_percent(런타임 swap 추이 축 폐기),
# disk.queue(sat_disk_queue 폐기 -> disk.io_saturation await 로 단일화).
MetricType = Literal[
    "cpu.usage_percent",
    "cpu.user_percent",
    "cpu.system_percent",
    "cpu.iowait_percent",
    "cpu.nice_percent",
    "cpu.run_queue",
    "cpu.blocked",
    "cpu.psi",
    "mem.usage_percent",
    "mem.available_percent",
    "mem.cached_percent",
    "mem.buffers_percent",
    "mem.psi",
    "disk.read_iops",
    "disk.write_iops",
    "disk.read_kbps",
    "disk.write_kbps",
    "disk.io_saturation",
    "disk.psi",
    "fs.usage_percent",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
    "net.rx_packets_per_sec",
    "net.tx_packets_per_sec",
    "net.retrans_percent",
    "net.drop_percent",
]
# 환경 성능 추이 차트 metric — capacity-weighted(cpu·mem = sum/sum) / 압박(psi %) / 스토리지 사용량(fs.used_bytes 합산) /
# 활동 합산(disk·net rate·pps) / worst-device 포화(disk.io_saturation) / 전사 품질(net.retrans·net.drop).
# 포화 축은 PSI(자원 경합으로 멈춘 시간 %)로 통일 — 간접 지표(run_queue) 대신 직접 지표. 디스크는 worst-device await 유지.
# 서버 상세 성능 추이(MetricType 전체)와 동일 자원별 신호 카탈로그 유지 의무(#F10 화면 간 정합) — cpu.nice_percent
# (CPU 분류 4번째 dimension)·disk.psi(스토리지 PSI)·net.rx/tx_packets_per_sec(네트워크 PPS)·net.drop_percent
# (패킷 드롭율)도 서버 상세에 있는 신호라 여기 포함.
EnvironmentMetricType = Literal[
    "cpu.usage_percent",
    "cpu.user_percent",
    "cpu.system_percent",
    "cpu.iowait_percent",
    "cpu.nice_percent",
    "cpu.psi",
    "mem.usage_percent",
    "mem.psi",
    "fs.used_bytes",
    "disk.read_iops",
    "disk.write_iops",
    "disk.read_kbps",
    "disk.write_kbps",
    "disk.io_saturation",
    "disk.psi",
    "net.rx_bytes_per_sec",
    "net.tx_bytes_per_sec",
    "net.rx_packets_per_sec",
    "net.tx_packets_per_sec",
    "net.retrans_percent",
    "net.drop_percent",
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

# TimeRange → 평가 윈도우(일, float). SQL interval 은 fraction 지원(0.25 days = 6h). TIME_RANGE_TD 단일 소스 파생.
DIAGNOSTIC_RANGE_DAYS: dict[str, float] = {r: td.total_seconds() / 86400 for r, td in TIME_RANGE_TD.items()}

# 진단 발행·분류 기본 윈도우 — service default·UI 기본값 단일 진실 (#F10). recommendation.WINDOW_DAYS(14d)와 정합.
DIAGNOSTIC_DEFAULT_TIME_RANGE = "14d"

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

# CPU 누적 시간(seconds). delta로 % 계산 (LAG 기반). 성분 COALESCE — Windows 는 nice/iowait/irq/softirq/steal
# 이 null(OS 개념 부재)이라 raw 합이 X+NULL=NULL 로 전파되면 delta null -> 전량 제외돼 Windows CPU 추이 차트가
# 빈다(#C2, cagg·compute_cpu 동일). per-component(user/system/iowait) 분자는 bare 유지 — Windows iowait 는 null
# 이라 d_num null 로 자연 제외(N/A), COALESCE 하면 측정 0(iowait 여유)으로 오인.
_CPU_TOTAL_EXPR = (
    "COALESCE(cpu_user_s,0)+COALESCE(cpu_nice_s,0)+COALESCE(cpu_system_s,0)+COALESCE(cpu_idle_s,0)"
    "+COALESCE(cpu_iowait_s,0)+COALESCE(cpu_irq_s,0)+COALESCE(cpu_softirq_s,0)+COALESCE(cpu_steal_s,0)"
)
_CPU_NUMERATOR: dict[str, str] = {
    "cpu.usage_percent": (
        "COALESCE(cpu_user_s,0)+COALESCE(cpu_nice_s,0)+COALESCE(cpu_system_s,0)+COALESCE(cpu_iowait_s,0)"
        "+COALESCE(cpu_irq_s,0)+COALESCE(cpu_softirq_s,0)+COALESCE(cpu_steal_s,0)"
    ),
    "cpu.user_percent": "cpu_user_s",
    "cpu.system_percent": "cpu_system_s",
    "cpu.iowait_percent": "cpu_iowait_s",
    "cpu.nice_percent": "cpu_nice_s",  # Linux 전용(Windows null -> 분자 null, 자연 제외)
}

# (dim_col, value_col) — disk/net rate per dimension. table 명은 metric.py 에서 결합
# (types.py 는 ORM import 안 함 — circular 회피). v2: device->device_id, iface->iface_id, sectors->io_bytes(이미 By).
_RATE_PER_DIM_DEFS: dict[str, tuple[str, str]] = {
    "disk.read_iops": ("device_id", "ops_read"),
    "disk.write_iops": ("device_id", "ops_write"),
    # 처리량 — io_*_bytes 는 By 단위 -> KB 는 /1024.
    "disk.read_kbps": ("device_id", "io_read_bytes / 1024.0"),
    "disk.write_kbps": ("device_id", "io_write_bytes / 1024.0"),
    "net.rx_bytes_per_sec": ("iface_id", "rx_bytes"),
    "net.tx_bytes_per_sec": ("iface_id", "tx_bytes"),
    "net.rx_packets_per_sec": ("iface_id", "rx_packets"),
    "net.tx_packets_per_sec": ("iface_id", "tx_packets"),
}

# 데이터 볼륨 술어 (v2) — kind 컬럼 폐기. 가상 fs(tmpfs/overlay 등)와 /boot 를 fstype/mountpoint 로 제외.
# fstype null(미상)은 데이터로 포함(안전). 정적 상수만이라 f-string 안전 (#C5).
_VIRTUAL_FSTYPES = (
    "'tmpfs','devtmpfs','overlay','squashfs','proc','sysfs','cgroup','cgroup2','mqueue','debugfs',"
    "'tracefs','securityfs','pstore','bpf','configfs','ramfs','autofs','hugetlbfs','fusectl','nsfs',"
    "'efivarfs','binfmt_misc'"
)
_DATA_VOLUME_SQL_FILTER = f"(fstype IS NULL OR fstype NOT IN ({_VIRTUAL_FSTYPES})) AND mountpoint NOT LIKE '/boot%'"
# cagg(server_filesystem_5m) 변형 — fstype 대표값은 fstype_any 컬럼.
_DATA_VOLUME_CAGG_FILTER = (
    f"(fstype_any IS NULL OR fstype_any NOT IN ({_VIRTUAL_FSTYPES})) AND mountpoint NOT LIKE '/boot%'"
)

# 환경 시점값 capacity-weighted (시점별 sum(numerator)/sum(denominator) * 100). server_metrics 컬럼(v2 By).
# guard = 분자 성분이 실측된 행만 집계(미측정 성분 null 을 0 으로 삼키지 않음, #C2). Windows 는 mem_cached/buffered
# 가 null(OS 미측정)이라 IS NOT NULL 가드로 gap 표시. swap.usage_percent 폐기(런타임 swap 추이 축 폐기).
_ENV_SCALAR_WEIGHTED: dict[str, tuple[str, str, str]] = {
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
    "mem.cached_percent": ("mem_cached_bytes", "mem_limit_bytes", "mem_limit_bytes > 0 AND mem_cached_bytes IS NOT NULL"),
    "mem.buffers_percent": (
        "mem_buffered_bytes",
        "mem_limit_bytes",
        "mem_limit_bytes > 0 AND mem_buffered_bytes IS NOT NULL",
    ),
}

# 물리 disk/iface 필터 (v2) — 시계열 device 집계를 물리 단위로 한정. agent 가 물리 disk 위 LVM/RAID/crypt/swap LV,
# 물리 NIC 위 bridge/virtual 을 각각 시계열로 발행하므로, 무필터 SUM 은 물리 disk I/O 에 그 위 논리볼륨 I/O(디스크
# 통과분)를 더해 이중·삼중집계된다. inventory 로 판정: block_device type=='disk' / net_interface kind in
# (physical, bond_master) 만 포함. 판정은 시계열 device_id/iface_id = inventory (id_type):(id) 재구성 조인.
# fail-closed: inventory 에 물리로 확인된 device_id/iface_id 만 포함 — 알려진 것만 인정(#F9 참고). Windows
# agent 가 인벤토리엔 없는 합성/숨김 항목(디스크 id_type=aggregate 집계 pseudo-device, 인터페이스 id_type=name
# "if7/if8/if9" 숨김 어댑터)을 시계열에는 그대로 발행하므로, fail-open(매칭 안 되면 유지)이면 이들이 물리
# 디바이스로 오인 통과한다. 신규 미동기화 디바이스가 다음 인벤토리 수집 전까지 잠시 누락되는 트레이드오프보다
# 합성 항목의 상시 이중집계·오염이 더 크다고 판단해 fail-closed 채택.
# Windows 디스크는 metrics 수집기가 (id_type:id) 대신 name 으로 device_id 를 발행("name:PhysicalDrive0")—
# inventory 는 그 디스크를 mbrsig/gptid/serial 등 다른 id_type 로 식별해 1차 조인이 실패한다(win2003·win2025·
# win2012r2v11 전부 동일 패턴, agent 쪽 inventory/metrics 식별자 불일치). 'name:' || name 매칭을 OR 로 보강.
# 상관 서브쿼리 — bare server_id/device_id/iface_id 참조라 raw hypertable·cagg 양쪽 컬럼 스코프에서 동작.
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
