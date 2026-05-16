"""report·overview·attention 관련 mapper — 본 세션(v3~v5) 추가 함수 단위 테스트."""
from datetime import UTC, datetime, timedelta

import pytest

from assessment_engine.db.repositories.outbound import (
    EnvironmentUtilizationRaw,
    ReportRowRaw,
    ServerDetail,
)
from assessment_engine.web.services.mappers import (
    _DONUT_SEGMENT_FROM_REC,
    _OS_EOL,
    _RISK_FROM_RECOMMENDATION,
    _UTIL_COLOR_HIGH,
    _UTIL_COLOR_LOW,
    _UTIL_COLOR_MID,
    _UTIL_COLOR_NONE,
    _UTIL_DONUT_CIRC,
    build_environment_overview,
    build_report_summary_bullets,
    build_risk_donut_segments,
    build_role_distribution,
    compute_report_avg_p95,
    compute_report_totals_from_raw,
    to_agent_unstable_item,
    to_capacity_warning_item,
    to_disk_days_warning_item,
    to_inventory_export_entry,
    to_os_eol_warning_item,
    to_report_row_item,
)

# ─── 헬퍼 ────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 12, tzinfo=UTC)


def _raw(
    *,
    server_id=1, public_id="a", hostname="h",
    os_id="ubuntu", os_version="22.04", kernel_version="5.15",
    ip_internal=("10.0.0.1",), services=None,
    cpu_p95=None, cpu_peak=None, mem_p95=None, mem_peak=None,
    load_15m_max=None, swap_used=False,
    iowait_p95=None, iowait_peak=None,
    cpu_cores=2, mem_total_kb=2 * 1024 * 1024,
    disks=None, boot_time=None,
    worst_mount=None, worst_used=None, worst_days=None,
    reboot_count=0,
    disk_iops=None, disk_throughput=None, net_rx=None, net_tx=None,
) -> ReportRowRaw:
    return ReportRowRaw(
        server_id=server_id, public_id=public_id, hostname=hostname,
        os_id=os_id, os_version=os_version, kernel_version=kernel_version,
        ip_internal=list(ip_internal) if ip_internal else None,
        services=list(services) if services else None,
        last_seen_at=_NOW,
        cpu_p95_pct=cpu_p95, cpu_peak_pct=cpu_peak,
        mem_p95_pct=mem_p95, mem_peak_pct=mem_peak,
        load_15m_max=load_15m_max, swap_used=swap_used,
        iowait_p95_pct=iowait_p95, iowait_peak_pct=iowait_peak,
        cpu_cores=cpu_cores, mem_total_kb=mem_total_kb,
        disks=disks if disks is not None else [{"size_bytes": 50 * 10**9}],
        boot_time=boot_time if boot_time is not None else _NOW - timedelta(days=30),
        worst_mount=worst_mount, worst_mount_used_pct=worst_used,
        worst_mount_days_until_full=worst_days, reboot_count=reboot_count,
        disk_iops_baseline=disk_iops, disk_throughput_kbps=disk_throughput,
        net_rx_kbps=net_rx, net_tx_kbps=net_tx,
    )


# ─── _RISK_FROM_RECOMMENDATION 매핑 (옵션 B) ─────────────────────────────

@pytest.mark.parametrize("rec, risk_level, risk_label", [
    ("under_provisioned", "high", "고위험"),
    ("shutdown",          "attention", "주의 필요"),
    ("idle",              "attention", "주의 필요"),
    ("over_provisioned",  "attention", "주의 필요"),
    ("optimal",           "normal", "정상"),
    ("insufficient_data", "normal", "정상"),
])
def test_risk_mapping_all_recommendations(rec, risk_level, risk_label):
    level, label, badge = _RISK_FROM_RECOMMENDATION[rec]
    assert level == risk_level
    assert label == risk_label
    assert badge.startswith("rec-")


# ─── to_report_row_item — saturation/variance/uptime 파생 ────────────────

def test_report_row_saturation_ratio_calculated():
    raw = _raw(load_15m_max=4.0, cpu_cores=2)
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.saturation_ratio == 2.0  # 4 / 2


def test_report_row_saturation_none_when_cores_missing():
    raw = _raw(load_15m_max=4.0, cpu_cores=None)
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.saturation_ratio is None


def test_report_row_cpu_variance():
    raw = _raw(cpu_p95=50.0, cpu_peak=100.0)
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.cpu_variance_ratio == 2.0


def test_report_row_uptime_days():
    boot = _NOW - timedelta(days=30, hours=5)
    raw = _raw(boot_time=boot)
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.uptime_days == 30


def test_report_row_uptime_none_when_boot_time_missing():
    raw = _raw(boot_time=None)
    # boot_time을 None으로 override해도 _raw 기본값이 들어가므로 직접 dataclass 생성
    raw.boot_time = None
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.uptime_days is None


def test_report_row_under_provisioned_maps_to_high():
    raw = _raw(cpu_p95=95.0, cpu_peak=99.0, mem_p95=92.0, mem_peak=98.0, swap_used=True)
    item = to_report_row_item(raw, is_online=True, now=_NOW)
    assert item.risk_level == "high"
    assert item.risk_label == "고위험"


# ─── build_role_distribution ─────────────────────────────────────────────

def test_role_distribution_counts_categories():
    raws = [
        _raw(server_id=1, services=[{"unit": "nginx.service", "sub": "running"}]),
        _raw(server_id=2, services=[{"unit": "postgresql.service", "sub": "running"}]),
        _raw(server_id=3, services=[{"unit": "nginx.service", "sub": "running"}]),
        _raw(server_id=4, services=None),
    ]
    dist = build_role_distribution(raws)
    assert dist["web"] == 2
    assert dist["db"] == 1
    assert dist["unknown"] == 1


# ─── compute_report_totals_from_raw ──────────────────────────────────────

def test_report_totals_sum_vcpu_memory_disk():
    raws = [
        _raw(server_id=1, cpu_cores=4, mem_total_kb=8 * 1024 * 1024,
             disks=[{"size_bytes": 50 * 10**9}, {"size_bytes": 100 * 10**9}]),
        _raw(server_id=2, cpu_cores=2, mem_total_kb=4 * 1024 * 1024,
             disks=[{"size_bytes": 30 * 10**9}]),
    ]
    t = compute_report_totals_from_raw(raws)
    assert t.total_vcpus == 6
    assert t.total_memory_gb == 12          # (8 + 4) GB
    assert t.total_disk_gb == 180           # (50 + 100 + 30) GB


def test_report_totals_handles_null_fields():
    raws = [_raw(cpu_cores=None, mem_total_kb=None, disks=[])]
    t = compute_report_totals_from_raw(raws)
    assert t.total_vcpus == 0
    assert t.total_memory_gb == 0
    assert t.total_disk_gb == 0


# ─── build_environment_overview ──────────────────────────────────────────

def _detail(*, id_, hostname, cpu_cores, mem_total_kb, disk_size, role_unit=None):
    return ServerDetail(
        id=id_, public_id=f"p{id_}", machine_id=f"m{id_}", hostname=hostname,
        agent_version="1.0", os_id="ubuntu", os_version="22.04", os_codename="jammy",
        kernel_version="5.15", cpu_cores=cpu_cores, cpu_model="x86",
        mem_total_kb=mem_total_kb, swap_total_kb=0, boot_time=None,
        ip_internal=["10.0.0.1"], ip_external=None,
        disks=[{"size_bytes": disk_size}], mounts=[],
        services=[{"unit": role_unit, "sub": "running"}] if role_unit else [],
        listen_ports=[], last_seen_at=_NOW,
    )


def test_environment_overview_aggregates():
    details = [
        _detail(id_=1, hostname="db-01", cpu_cores=4, mem_total_kb=8 * 1024 * 1024,
                disk_size=50 * 10**9, role_unit="postgresql.service"),
        _detail(id_=2, hostname="web-01", cpu_cores=2, mem_total_kb=4 * 1024 * 1024,
                disk_size=100 * 10**9, role_unit="nginx.service"),
    ]
    ov = build_environment_overview(details, online_count=1)
    assert ov.total == 2
    assert ov.online == 1
    assert ov.offline == 1
    assert ov.total_vcpus == 6
    assert ov.total_memory_gb == 12.0
    assert ov.total_disk_gb == 150
    assert ov.role_distribution == {"db": 1, "web": 1}


def test_environment_overview_memory_keeps_decimal():
    # 작은 환경에서 정수 절사 시 정보 손실 — 2.5 GB가 2 GB로 보이지 않게.
    details = [_detail(id_=1, hostname="x", cpu_cores=1,
                       mem_total_kb=int(2.5 * 1024 * 1024), disk_size=10**9)]
    ov = build_environment_overview(details, online_count=1)
    assert ov.total_memory_gb == 2.5


def test_environment_overview_utilization_default_empty():
    # utilization=None → 막대 빈 list
    details = [_detail(id_=1, hostname="x", cpu_cores=1, mem_total_kb=1024*1024, disk_size=10**9)]
    ov = build_environment_overview(details, online_count=1)
    assert ov.utilization == []
    assert ov.util_sample_size == 0


@pytest.mark.parametrize("pct, expected_color", [
    (10.0,  _UTIL_COLOR_LOW),    # < 60 → 녹색
    (59.9,  _UTIL_COLOR_LOW),    # 임계 직전
    (60.0,  _UTIL_COLOR_MID),    # 60 → 노랑
    (79.9,  _UTIL_COLOR_MID),    # 임계 직전
    (80.0,  _UTIL_COLOR_HIGH),   # 80 → 빨강
    (95.0,  _UTIL_COLOR_HIGH),
    (None,  _UTIL_COLOR_NONE),   # 표본 부재
])
def test_environment_overview_utilization_bar_color(pct, expected_color):
    details = [_detail(id_=1, hostname="x", cpu_cores=1, mem_total_kb=1024*1024, disk_size=10**9)]
    util = EnvironmentUtilizationRaw(
        cpu_avg_pct=pct, mem_avg_pct=pct, disk_avg_pct=pct, sample_size=1,
    )
    ov = build_environment_overview(details, online_count=1, utilization=util)
    assert len(ov.utilization) == 3
    assert ov.utilization[0].label == "CPU"
    assert ov.utilization[1].label == "메모리"
    assert ov.utilization[2].label == "디스크"
    for bar in ov.utilization:
        assert bar.bar_color == expected_color
    assert ov.util_sample_size == 1


@pytest.mark.parametrize("pct, expected_dash", [
    (0.0,    0.0),               # 0% → dash 0
    (50.0,   263.89 / 2),        # 50% → 절반
    (100.0,  263.89),            # 100% → full
    (150.0,  263.89),            # over → clamp to 100%
    (-10.0,  0.0),               # 음수 → clamp to 0
    (None,   0.0),               # 표본 부재
])
def test_environment_overview_utilization_dash_length(pct, expected_dash):
    details = [_detail(id_=1, hostname="x", cpu_cores=1, mem_total_kb=1024*1024, disk_size=10**9)]
    util = EnvironmentUtilizationRaw(
        cpu_avg_pct=pct, mem_avg_pct=pct, disk_avg_pct=pct, sample_size=1,
    )
    ov = build_environment_overview(details, online_count=1, utilization=util)
    for bar in ov.utilization:
        assert abs(bar.dash_length - expected_dash) < 0.1


# ─── 위험도 분포 도넛 ────────────────────────────────────────────────────

def test_risk_donut_segments_order_and_colors():
    """segments는 _DONUT_SEGMENT_DEFS 순서(under/over/normal) 고정."""
    segs, total, under = build_risk_donut_segments(
        {"under": 1, "over": 2, "normal": 7}
    )
    assert [s.key for s in segs] == ["under", "over", "normal"]
    assert [s.label for s in segs] == ["언더 프로비저닝", "오버 프로비저닝", "정상"]
    assert total == 10
    assert under == 1


def test_risk_donut_segments_dash_accumulates():
    """dash_offset은 이전 segments 누적 음수 — 시계방향 시작 위치."""
    segs, total, _ = build_risk_donut_segments(
        {"under": 1, "over": 1, "normal": 1}
    )
    expected_each = _UTIL_DONUT_CIRC / 3
    assert abs(segs[0].dash_length - expected_each) < 0.1
    assert segs[0].dash_offset == 0.0
    assert abs(segs[1].dash_offset - (-expected_each)) < 0.1
    assert abs(segs[2].dash_offset - (-2 * expected_each)) < 0.1
    assert total == 3


def test_risk_donut_segments_zero_count_zero_length():
    """count=0 segment는 dash_length 0, 다음 segment offset 안 밀어냄."""
    segs, _, _ = build_risk_donut_segments({"under": 0, "over": 0, "normal": 5})
    assert segs[0].dash_length == 0
    assert segs[1].dash_offset == 0
    assert abs(segs[2].dash_length - _UTIL_DONUT_CIRC) < 0.1


def test_risk_donut_segments_empty_total():
    """total=0이면 모든 dash 0."""
    segs, total, under = build_risk_donut_segments({})
    assert total == 0
    assert under == 0
    assert all(s.dash_length == 0 for s in segs)


@pytest.mark.parametrize("rec, expected_key", [
    ("under_provisioned", "under"),
    ("over_provisioned",  "over"),
    ("shutdown",          "over"),  # 사양 큰 상태 — over로 흡수
    ("idle",              "over"),
    ("optimal",           "normal"),
    ("insufficient_data", "normal"),
])
def test_donut_segment_from_rec_mapping(rec, expected_key):
    assert _DONUT_SEGMENT_FROM_REC[rec] == expected_key


# ─── CapacityWarningItem.triggers (3종 항상 노출) ────────────────────────

def test_capacity_warning_triggers_always_three_categories():
    """CapacityWarningItem.triggers는 3종(스왑/CPU/메모리) 항상 — count 0 자리 포함, 카드 본질 명시."""
    item = to_capacity_warning_item(_raw(swap_used=True))
    labels = [t.label for t in item.triggers]
    assert labels == ["스왑", "CPU", "메모리"]
    # swap만 active, 나머지 inactive
    active = {t.label: t.active for t in item.triggers}
    assert active == {"스왑": True, "CPU": False, "메모리": False}


def test_capacity_warning_triggers_multi_active():
    """한 서버가 swap+CPU+메모리 동시 trigger 가능 — 각 active=True 독립."""
    item = to_capacity_warning_item(_raw(swap_used=True, cpu_p95=95.0, mem_p95=90.0))
    active = {t.label: t.active for t in item.triggers}
    assert active == {"스왑": True, "CPU": True, "메모리": True}


def test_capacity_warning_triggers_colors_hue_separated():
    """3 카테고리 색이 hue 별 명확히 분리 — 단일 진실(`_CAPACITY_TRIGGER_COLORS`)."""
    item = to_capacity_warning_item(_raw(swap_used=True, cpu_p95=95.0, mem_p95=90.0))
    colors = {t.label: t.color for t in item.triggers}
    assert colors == {
        "스왑":   "#dc2626",  # 빨강
        "CPU":    "#2563eb",  # 파랑
        "메모리": "#8b5cf6",  # 보라
    }
    assert len(set(colors.values())) == 3


# ─── build_report_summary_bullets — 신호 9종 트리거 ──────────────────────

def test_bullets_empty_when_no_rows():
    assert build_report_summary_bullets([]) == ["대상 서버 없음."]


def test_bullets_high_risk_signal():
    raws = [_raw(hostname="db-01", cpu_p95=95.0, cpu_peak=99.0,
                 mem_p95=92.0, mem_peak=98.0, swap_used=True)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert any("고위험" in b and "db-01" in b for b in bullets)


def test_bullets_iowait_signal_threshold_20pct():
    raws = [_raw(hostname="io-01", iowait_p95=25.0, cpu_p95=50.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert any("I/O wait" in b and "io-01" in b for b in bullets)


def test_bullets_mount_imminent_signal():
    raws = [_raw(hostname="full-01", worst_mount="/data",
                 worst_used=90.0, worst_days=12,
                 cpu_p95=50.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert any("임박" in b and "full-01" in b and "/data" in b for b in bullets)


def test_bullets_reboot_signal_threshold_3():
    raws = [_raw(hostname="unstable-01", reboot_count=4, cpu_p95=50.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert any("재부팅" in b and "unstable-01" in b for b in bullets)


def test_bullets_saturation_signal():
    # Saturation 시그널은 양식 B(엔지니어)에만 노출 — 큐잉 이론 시그널.
    raws = [_raw(hostname="sat-01", load_15m_max=5.0, cpu_cores=2,
                 cpu_p95=50.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="engineer")
    assert any("Saturation" in b and "sat-01" in b for b in bullets)


def test_bullets_cpu_variance_signal():
    # CPU 변동성 시그널은 양식 B(엔지니어)에만 노출 — sizing 전략 영향.
    raws = [_raw(hostname="var-01", cpu_p95=30.0, cpu_peak=80.0,
                 mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="engineer")
    assert any("변동" in b and "var-01" in b for b in bullets)


def test_bullets_os_eol_signal():
    raws = [_raw(hostname="legacy-01", os_id="centos", os_version="7.9",
                 cpu_p95=50.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert any("EOL" in b and "legacy-01" in b and "centos" in b for b in bullets)


def test_bullets_role_avg_cpu_signal():
    # 역할별 평균 CPU 시그널은 양식 B(엔지니어)에만 노출 — 자원 집약 역할 식별.
    raws = [
        _raw(server_id=1, hostname="db-01", services=[{"unit": "postgresql.service"}],
             cpu_p95=85.0, mem_p95=50.0),
        _raw(server_id=2, hostname="db-02", services=[{"unit": "mysql.service"}],
             cpu_p95=90.0, mem_p95=50.0),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="engineer")
    assert any("db 계열" in b and "평균 CPU p95" in b for b in bullets)


# ─── view="customer" 분기 — engineer 전용 시그널 제외 검증 ────────────────

def test_bullets_customer_view_excludes_saturation():
    """양식 A(customer)는 Saturation 시그널 제외 — 큐잉 이론은 엔지니어 영역."""
    raws = [_raw(hostname="sat-01", load_15m_max=5.0, cpu_cores=2,
                 cpu_p95=50.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="customer")
    assert not any("Saturation" in b for b in bullets)


def test_bullets_customer_view_excludes_cpu_variance():
    """양식 A(customer)는 CPU 변동성 시그널 제외 — sizing 전략은 엔지니어 영역."""
    raws = [_raw(hostname="var-01", cpu_p95=30.0, cpu_peak=80.0, mem_p95=50.0)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="customer")
    assert not any("변동" in b for b in bullets)


def test_bullets_customer_view_excludes_role_avg_cpu():
    """양식 A(customer)는 역할별 평균 CPU 시그널 제외 — 정보 과다."""
    raws = [
        _raw(server_id=1, hostname="db-01", services=[{"unit": "postgresql.service"}],
             cpu_p95=85.0, mem_p95=50.0),
        _raw(server_id=2, hostname="db-02", services=[{"unit": "mysql.service"}],
             cpu_p95=90.0, mem_p95=50.0),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="customer")
    assert not any("평균 CPU p95" in b for b in bullets)


def test_compute_report_avg_p95_simple_average():
    """CPU·메모리 p95 단순 산술 평균."""
    raws = [
        _raw(server_id=1, cpu_p95=20.0, mem_p95=40.0),
        _raw(server_id=2, cpu_p95=40.0, mem_p95=60.0),
        _raw(server_id=3, cpu_p95=60.0, mem_p95=80.0),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    avg_cpu, avg_mem = compute_report_avg_p95(items)
    assert avg_cpu == 40.0
    assert avg_mem == 60.0


def test_compute_report_avg_p95_none_excluded():
    """None 항목은 평균 계산에서 제외."""
    raws = [
        _raw(server_id=1, cpu_p95=30.0, mem_p95=None),
        _raw(server_id=2, cpu_p95=None, mem_p95=50.0),
        _raw(server_id=3, cpu_p95=60.0, mem_p95=70.0),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    avg_cpu, avg_mem = compute_report_avg_p95(items)
    assert avg_cpu == 45.0  # (30 + 60) / 2
    assert avg_mem == 60.0  # (50 + 70) / 2


def test_compute_report_avg_p95_all_none_returns_none():
    """모두 None이면 None (divide-by-zero 회피)."""
    raws = [_raw(server_id=1, cpu_p95=None, mem_p95=None)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    avg_cpu, avg_mem = compute_report_avg_p95(items)
    assert avg_cpu is None
    assert avg_mem is None


def test_compute_report_avg_p95_empty_returns_none():
    """빈 list도 None 반환."""
    avg_cpu, avg_mem = compute_report_avg_p95([])
    assert avg_cpu is None
    assert avg_mem is None


def test_bullets_customer_view_keeps_high_risk_iowait_mount_reboot_eol():
    """양식 A·B 공통 시그널 — 고위험·I/O wait·디스크 임박·재부팅·OS EOL은 customer에도 노출."""
    raws = [
        _raw(hostname="db-01", os_id="centos", os_version="7.9",
             cpu_p95=95.0, cpu_peak=99.0, mem_p95=92.0, mem_peak=98.0,
             swap_used=True, iowait_p95=30.0,
             worst_mount="/data", worst_used=90.0, worst_days=10,
             reboot_count=5),
    ]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws, view="customer")
    # 공통 시그널 5종 모두 활성
    assert any("고위험" in b and "db-01" in b for b in bullets)
    assert any("I/O wait" in b and "db-01" in b for b in bullets)
    assert any("임박" in b and "db-01" in b for b in bullets)
    assert any("재부팅" in b and "db-01" in b for b in bullets)
    assert any("EOL" in b and "db-01" in b for b in bullets)


def test_bullets_normal_fallback():
    # USE Method optimal 영역: CPU_DOWNSIZE 30 < cpu_p95 < CPU_UPSIZE 70
    #                       + MEM_DOWNSIZE 50 < mem_p95 < MEM_UPSIZE 80
    # 다른 신호 발동 안 하는 안전한 중간값
    raws = [_raw(hostname="ok-01",
                 cpu_p95=50.0, cpu_peak=60.0,
                 mem_p95=60.0, mem_peak=68.0,
                 load_15m_max=0.5, cpu_cores=4)]
    items = [to_report_row_item(r, True, _NOW) for r in raws]
    bullets = build_report_summary_bullets(items, raws)
    assert bullets == ["전체 서버가 정상 범위. 추가 조치 불필요."]


# ─── attention 신호 ViewModel 빌더 ───────────────────────────────────────

def test_capacity_warning_item_fields():
    raw = _raw(cpu_p95=95.0, mem_p95=92.0, swap_used=True)
    item = to_capacity_warning_item(raw)
    assert item.cpu_p95_pct == 95.0
    assert item.mem_p95_pct == 92.0
    assert item.swap_used is True
    # 3종 항상 — 셋 다 임계 → 모두 active
    assert [t.label for t in item.triggers] == ["스왑", "CPU", "메모리"]
    assert all(t.active for t in item.triggers)


@pytest.mark.parametrize("cpu_p95, mem_p95, swap_used, expected_active", [
    (None,  None,  True,  [True, False, False]),     # swap만
    (95.0,  92.0,  True,  [True, True, True]),        # 셋 다
    (95.0,  92.0,  False, [False, True, True]),       # cpu + mem (스왑 비활성)
    (95.0,  60.0,  False, [False, True, False]),      # CPU만
    (50.0,  90.0,  False, [False, False, True]),      # 메모리만
    (50.0,  60.0,  False, [False, False, False]),     # 비도달 (3종 모두 비활성)
])
def test_capacity_warning_item_triggers_active_flags(cpu_p95, mem_p95, swap_used, expected_active):
    """triggers는 항상 3종 [스왑, CPU, 메모리]. active flag로 활성 자원 표시."""
    raw = _raw(cpu_p95=cpu_p95, mem_p95=mem_p95, swap_used=swap_used)
    item = to_capacity_warning_item(raw)
    assert [t.label for t in item.triggers] == ["스왑", "CPU", "메모리"]
    assert [t.active for t in item.triggers] == expected_active


def test_capacity_warning_item_trigger_colors_from_single_source():
    """trigger.color는 _CAPACITY_TRIGGER_COLORS와 동일 — 본문 badge와 범례 단일 진실."""
    from assessment_engine.web.services.mappers import _CAPACITY_TRIGGER_COLORS
    item = to_capacity_warning_item(_raw(cpu_p95=95.0, mem_p95=92.0, swap_used=True))
    for t in item.triggers:
        assert t.color == _CAPACITY_TRIGGER_COLORS[t.label]


def test_disk_days_warning_item_fields():
    item = to_disk_days_warning_item("pid", "h", "/data", 12, 87.0)
    assert item.mount_path == "/data"
    assert item.badge_text == "12일"
    assert item.badge_class == "rec-under_provisioned"
    assert item.link_href == "/servers/pid/storage"
    assert item.link_text == "h"
    assert item.meta_text == "87%"


@pytest.mark.parametrize("os_id, os_version, should_match", [
    ("centos", "7.9", True),
    ("rhel",   "7.4", True),
    ("ubuntu", "18.04.5", True),
    ("debian", "10.11", True),
    ("centos", "8",     True),   # CentOS Stream 8 EOL 2024-05-31
    ("ubuntu", "22.04", False),
    ("rocky",  "9.6",   False),
])
def test_os_eol_matching(os_id, os_version, should_match):
    raw = _raw(os_id=os_id, os_version=os_version)
    item = to_os_eol_warning_item(raw)
    assert (item is not None) == should_match


def test_agent_unstable_item_fields():
    item = to_agent_unstable_item("pid", "h", 5)
    assert item.badge_text == "5회"
    assert item.badge_class == "rec-over_provisioned"
    assert item.link_href == "/servers/pid"
    assert item.link_text == "h"


# ─── _OS_EOL dict — 정적 매핑 sanity ─────────────────────────────────────

def test_os_eol_has_known_eol_distros():
    assert ("centos", "7") in _OS_EOL
    assert ("rhel", "7") in _OS_EOL
    assert ("ubuntu", "18.04") in _OS_EOL


# ─── to_inventory_export_entry v2 ─────────────────────────────────────────

def test_inventory_export_with_stats():
    """v3 — recommended_size_class 객체화 + I/O p95/peak + services.listeners."""
    detail = _detail(id_=1, hostname="db-01", cpu_cores=4,
                     mem_total_kb=8 * 1024 * 1024, disk_size=50 * 10**9,
                     role_unit="postgresql.service")
    stats = _raw(hostname="db-01", cpu_p95=95.0, cpu_peak=99.0,
                 mem_p95=92.0, mem_peak=98.0, load_15m_max=5.0, swap_used=True,
                 disk_iops=850, disk_throughput=4500.0,
                 net_rx=300.0, net_tx=180.0)
    entry = to_inventory_export_entry(detail, stats)
    assert entry.hostname == "db-01"
    assert entry.role == "db"
    assert entry.compute["vcpu_count"] == 4
    assert entry.compute["cpu_p95_pct"] == 95.0
    # v3: 객체 {key, label}
    assert entry.compute["recommended_size_class"]["key"] == "under_provisioned"
    assert entry.compute["recommended_size_class"]["label"]  # 한국어 라벨 채움
    assert entry.storage["iops_baseline"] == 850
    assert entry.storage["throughput_kbps_baseline"] == 4500.0
    assert entry.network["rx_kbps_baseline"] == 300.0
    # v3: services 항목에 listeners 키 (ports 대신)
    assert all("listeners" in s for s in entry.services)


def test_inventory_export_without_stats():
    detail = _detail(id_=1, hostname="new-01", cpu_cores=2,
                     mem_total_kb=2 * 1024 * 1024, disk_size=30 * 10**9)
    entry = to_inventory_export_entry(detail, stats=None)
    assert entry.compute["recommended_size_class"]["key"] == "insufficient_data"
    assert entry.compute["cpu_p95_pct"] is None
    assert entry.storage["iops_baseline"] is None
    assert entry.network["rx_kbps_baseline"] is None
    # 본 contract에 없는 필드 (`arch`/`boot_iops_baseline`) 부재 확인
    assert "arch" not in entry.os
    assert "boot_iops_baseline" not in entry.storage


def test_inventory_export_network_addresses_v4_v6_split():
    detail = ServerDetail(
        id=1, public_id="p1", machine_id="m1", hostname="h", agent_version="1.0",
        os_id="ubuntu", os_version="22.04", os_codename="jammy", kernel_version="5.15",
        cpu_cores=2, cpu_model="x86", mem_total_kb=2 * 1024 * 1024,
        swap_total_kb=0, boot_time=None,
        ip_internal=["10.0.0.1", "fe80::1"],
        ip_external=["54.1.2.3"],
        disks=[], mounts=[], services=[], listen_ports=[], last_seen_at=_NOW,
    )
    entry = to_inventory_export_entry(detail, stats=None)
    addrs = entry.network["addresses"]
    assert {"scope": "internal", "family": "v4", "address": "10.0.0.1"} in addrs
    assert {"scope": "internal", "family": "v6", "address": "fe80::1"} in addrs
    assert {"scope": "external", "family": "v4", "address": "54.1.2.3"} in addrs


def test_inventory_export_services_listeners_match_listen_ports():
    """v3 — services.listeners가 listen_ports inventory 매칭으로 proto/address 채움.

    nginx.service(web) -> ports [80, 443] -> listen_ports에서 매칭하여 실제 proto/addr 추출.
    매칭 실패 시 폴백 (tcp/0.0.0.0).
    """
    detail = ServerDetail(
        id=1, public_id="p1", machine_id="m1", hostname="h", agent_version="1.0",
        os_id="ubuntu", os_version="22.04", os_codename="jammy", kernel_version="5.15",
        cpu_cores=2, cpu_model="x86", mem_total_kb=2*1024*1024,
        swap_total_kb=0, boot_time=None,
        ip_internal=["10.0.0.1"], ip_external=None,
        disks=[], mounts=[],
        services=[{"unit": "nginx.service", "sub": "running"}],
        listen_ports=[
            {"port": 80,  "proto": "tcp", "addr": "0.0.0.0"},
            {"port": 443, "proto": "tcp", "addr": "10.0.0.1"},
        ],
        last_seen_at=_NOW,
    )
    entry = to_inventory_export_entry(detail, stats=None)
    nginx = next(s for s in entry.services if s["category"] == "web")
    listener_ports = sorted(item["port"] for item in nginx["listeners"])
    assert listener_ports == [80, 443]
    # 443은 inventory listen_ports 매칭 — 실제 address 추출
    p443 = next(item for item in nginx["listeners"] if item["port"] == 443)
    assert p443["address"] == "10.0.0.1"


def test_inventory_export_services_listeners_fallback_when_no_listen_ports():
    """listen_ports inventory 매칭 실패 시 폴백 (tcp/0.0.0.0)."""
    detail = ServerDetail(
        id=1, public_id="p1", machine_id="m1", hostname="h", agent_version="1.0",
        os_id="ubuntu", os_version="22.04", os_codename="jammy", kernel_version="5.15",
        cpu_cores=2, cpu_model="x86", mem_total_kb=2*1024*1024,
        swap_total_kb=0, boot_time=None,
        ip_internal=["10.0.0.1"], ip_external=None,
        disks=[], mounts=[],
        services=[{"unit": "nginx.service", "sub": "running"}],
        listen_ports=[],  # 비어있음 -> 폴백
        last_seen_at=_NOW,
    )
    entry = to_inventory_export_entry(detail, stats=None)
    nginx = next(s for s in entry.services if s["category"] == "web")
    assert all(
        item["proto"] == "tcp" and item["address"] == "0.0.0.0"
        for item in nginx["listeners"]
    )


# ─── diagnosis (양식 B 판단 컬럼 자동 진단) ───────────────────────────────

@pytest.mark.parametrize("kwargs, expected", [
    ({"swap_used": True},                                  "메모리 부족 (스왑 발생)"),
    ({"iowait_p95": 25.0},                                 "디스크 I/O 병목"),
    ({"cpu_cores": 4, "load_15m_max": 5.0},                "CPU saturation"),
    ({"mem_p95": 85.0},                                    "메모리 압박"),
    ({"cpu_p95": 75.0},                                    "CPU 압박"),
    ({"cpu_p95": 50.0, "cpu_peak": 99.0},                  "변동성 큼 (burst)"),
    ({"cpu_p95": 2.0},                                     "거의 미사용"),
    ({"cpu_p95": 20.0, "mem_p95": 30.0},                   "여유 있음 (축소 검토)"),
    ({"cpu_p95": 50.0, "mem_p95": 60.0},                   "정상"),
])
def test_diagnosis_priority(kwargs, expected):
    """우선순위: 스왑 > I/O 병목 > saturation > 메모리 압박 > CPU 압박 > 변동성 > 미사용 > 여유 > 정상."""
    raw = _raw(**kwargs)
    item = to_report_row_item(raw, True, _NOW)
    assert item.diagnosis == expected


# ─── DISK/NET p95·peak 필드 채움 ───────────────────────────────────────────

def test_report_row_item_disk_net_io_p95_peak_passthrough():
    """ReportRowRaw의 disk·net io p95/peak가 ReportRowItem으로 그대로 전달."""
    raw = _raw(
        disk_iops=120, disk_throughput=850.0, net_rx=300.0, net_tx=180.0,
    )
    raw.disk_iops_p95 = 280.0
    raw.disk_iops_peak = 540.0
    raw.disk_throughput_kbps_p95 = 2100.0
    raw.disk_throughput_kbps_peak = 4800.0
    raw.net_rx_kbps_p95 = 700.0
    raw.net_rx_kbps_peak = 1200.0
    raw.net_tx_kbps_p95 = 420.0
    raw.net_tx_kbps_peak = 900.0
    item = to_report_row_item(raw, True, _NOW)
    assert item.disk_iops_p95 == 280.0
    assert item.disk_iops_peak == 540.0
    assert item.disk_throughput_kbps_p95 == 2100.0
    assert item.disk_throughput_kbps_peak == 4800.0
    assert item.net_rx_kbps_p95 == 700.0
    assert item.net_rx_kbps_peak == 1200.0
    assert item.net_tx_kbps_p95 == 420.0
    assert item.net_tx_kbps_peak == 900.0
