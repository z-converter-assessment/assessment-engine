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
    "mem.paging_pressure",
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
    "net.congested",
]
# 환경 성능 추이 차트 metric — capacity-weighted(cpu·mem·fs = sum/sum 비율) / 활동 합산(net.rx/tx_bytes_per_sec)
# / 판정 crossing 서버 수(cpu.saturation_hosts·mem.paging_pressure_hosts·disk.saturation_hosts·
# net.congested_hosts).
# CPU 는 사용률(cpu.usage_percent) + 실행 큐 포화 서버 수(cpu.saturation_hosts) 2축만 — CPU 분류(User/System/
# I·O Wait/Nice)·CPU PSI 는 환경(여러 호스트 혼합) 단위에서 제외했다: 분류는 Linux 8-state/Windows 3-state 로
# 구성 자체가 달라 I·O Wait·Nice 라인이 사실상 Linux 전용인데 "환경 CPU 분류"로 노출되면 오인 소지, PSI 는
# Windows 가 전혀 미발행(Linux 4.20+ 전용)이라 "환경 CPU 압박"이 사실상 Linux 만의 값이 된다. 실행 큐는 원
# 카운터(Linux procs_running/Windows Processor Queue Length)·임계가 달라 raw 값은 환경 단일선으로 못 묶지만,
# os별 임계 crossing 판정(recommendation.cpu_saturation_index 와 동일 임계, >=1.0 포화)은 OS 무관 동일 의미라
# "포화 판정 넘은 서버 수"(count)로 환경 전체 단일선 집계가 정당하다 — "윈도우 정규화 보정".
# 서버 상세 성능 추이(MetricType 전체)는 여전히 CPU 분류·PSI·disk.io_saturation(raw await 단일선)·disk.psi·
# disk.read/write_iops·disk.read/write_kbps·net.rx/tx_packets_per_sec·net.retrans_percent·net.drop_percent
# 를 보유(단일 호스트라 OS 혼합 문제 없음, #F10 예외 — 화면 간 신호 카탈로그 정합 의무는 "혼합 집계가 의미
# 있는" 신호에만 적용).
# 네트워크는 활동량(net.rx/tx_bytes_per_sec, 물리 인터페이스만 SUM — device_filters 단일 정책, floating y축)
# + 판정 crossing 서버 수(net.congested_hosts) 2축. PPS(rx/tx_packets_per_sec)는 디스크 IOPS·kbps 와 동일
# 사유(이기종 NIC 를 그냥 더한 숫자는 비교 기준선 없어 해석 불가)로 제외. bytes/s 는 링크 속도 대비 이용률(%)로
# 정규화하는 방안도 검토했으나 link_speed_bps(가상 NIC 다수·구형 OS 미보유)가 fleet 상당수에서 결측이라
# capacity-weighted 비율 자체가 성립 안 해 raw 활동량으로 유지 — 다른 자원처럼 이론적 상한(코어 수·총 용량)이
# 항상 실측되는 것과 다른 네트워크만의 제약. TCP 재전송율·패킷 드롭율은 net.congested_hosts(recommendation.
# assess_network 의 실제 판정 network_congested 와 동일 원자료·임계 3종 — 재전송>1%·드롭>0.5%(저트래픽 게이트
# 적용)·conntrack 고갈>=0.8(게이트 미적용) OR)로 통합 — 두 % 라인이 시각적으로 거의 겹쳐 구분이 안 되는 문제도
# 함께 해결.
# 스토리지도 동일 기준으로 정리 — disk.psi 는 CPU/메모리 PSI 와 동일 사유(Windows 미발행 + 판정 비사용)로
# 제외. disk.read/write_iops·disk.read/write_kbps(순수 활동량 합산)도 제외 — Windows 비대칭은 없으나 여러
# 이기종 디스크를 그냥 더한 절대 숫자(예: "IOPS 400")는 비교 기준선이 없어 그 자체로 해석 불가(높다/낮다 판단
# 불가) — CPU/메모리 이용률(%, 0~100 척도)·응답지연(ms, 20ms 임계)과 달리 판정도 직관도 없는 숫자. disk.
# io_saturation(worst-device MAX 단일선)은 disk.saturation_hosts(판정 crossing 서버 수)로 대체 — 동일
# 임계(RS_DISKIO_AWAIT_MS)를 서버별로 적용, "가장 나쁜 곳 1개"보다 "몇 대가 영향받았는지"가 더 유용.
# 스토리지는 fs.usage_percent(전 서버 사용량/전체 용량 비율, %)로 표시 — 절대 총량(fs.used_bytes)은 서버마다
# 프로비저닝된 용량이 제각각이라 "몇 GB"만으로는 위험도를 못 읽는다(CPU/메모리 사용률과 같은 0~100 척도로
# 통일, y축도 100% 고정). 개별 호스트 위험 판정(용량 85%/30일 runway, 다일 추세 회귀라 짧은 구간에서 계산
# 불가)과는 별개로 함대 전체 사용률 추이를 보여주는 목적.
# 메모리도 동일 사유로 mem.psi 제거, mem.paging_pressure_hosts 신설 — recommendation.mem_pressure_active
# (mem_saturated dual-gate 의 실제 페이징 판정 신호원)와 동일 원자료·임계를 SQL 이식한 "판정 crossing 서버 수".
# CPU 실행 큐와 달리 Linux 페이징은 magnitude 아닌 존재 판정이라(하드폴트 절대 rate 는 디스크 속도 의존이라
# 보편 임계 불가, 의식적으로 존재 판정으로 후퇴시킨 설계) 애초에 연속 지수 자체가 불가능 — 다만 CPU 도 지수
# 대신 카운트로 통일했다(강도 있는 지수보다 "몇 대"가 도메인 지식 없이 바로 읽히고 실행 가능해 일관성·이해도
# 둘 다 낫다는 판단). count 는 분모(온라인 대수) 변동에도 왜곡되지 않는 절대치. Windows Pages Input/sec 이
# 현재 fleet 에서 coverage_gap(perflib 미수집)이라 실측상 0 이 나올 수 있으나, 판정 로직 자체는 cross-OS 로
# 옳고 실제 분류에 쓰이는 신호라 mem.psi(판정 비관여 참고치)보다 정합성이 높다.
EnvironmentMetricType = Literal[
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
