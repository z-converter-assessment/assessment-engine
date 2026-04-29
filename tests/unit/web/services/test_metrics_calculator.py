from datetime import datetime, timezone

import pytest

from db.repositories.outbound import (
    DashboardRaw,
    DiskIoRaw,
    MetricPairRaw,
    MountUsageRaw,
    NetIoRaw,
)
from web.services.metrics_calculator import (
    build_dashboard,
    compute_cpu,
    compute_disk_io,
    compute_mem,
    compute_mounts,
    compute_net_io,
    compute_swap,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2024, 1, 1, 0, 1, 0, tzinfo=timezone.utc)
_T1 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # 60s 이전


def _metric(collected_at=_T0, **kwargs) -> MetricPairRaw:
    defaults = dict(
        cpu_user=100, cpu_nice=0, cpu_system=50, cpu_idle=800,
        cpu_iowait=10, cpu_irq=5, cpu_softirq=5, cpu_steal=0,
        mem_total_kb=8_000_000, mem_free_kb=1_000_000,
        mem_available_kb=3_000_000, mem_buffers_kb=200_000,
        mem_cached_kb=1_500_000, swap_total_kb=2_000_000,
        swap_free_kb=1_000_000, load_1m=0.5, load_5m=0.4, load_15m=0.3,
    )
    defaults.update(kwargs)
    return MetricPairRaw(collected_at=collected_at, **defaults)


def _disk_row(device="sda", collected_at=_T0, reads=200, writes=100, sr=4000, sw=2000) -> DiskIoRaw:
    return DiskIoRaw(device=device, collected_at=collected_at,
                     reads_completed=reads, writes_completed=writes,
                     sectors_read=sr, sectors_written=sw)


def _net_row(iface="eth0", collected_at=_T0, rx_b=2048, tx_b=1024,
             rx_p=20, tx_p=10, rx_e=0, tx_e=0) -> NetIoRaw:
    return NetIoRaw(interface=iface, collected_at=collected_at,
                    rx_bytes=rx_b, tx_bytes=tx_b,
                    rx_packets=rx_p, tx_packets=tx_p,
                    rx_errors=rx_e, tx_errors=tx_e)


class TestComputeCpu:
    def test_no_readings_returns_none(self):
        assert compute_cpu(None, None) is None

    def test_single_reading_returns_none_pcts(self):
        result = compute_cpu(_metric(), None)
        assert result is not None
        assert result.usage_pct is None
        assert result.user_pct is None

    def test_two_readings_calculates_usage_pct(self):
        cur  = _metric(_T0, cpu_user=200, cpu_nice=0, cpu_system=100, cpu_idle=1600,
                       cpu_iowait=20, cpu_irq=10, cpu_softirq=10, cpu_steal=0)
        prev = _metric(_T1, cpu_user=100, cpu_nice=0, cpu_system=50, cpu_idle=800,
                       cpu_iowait=10, cpu_irq=5, cpu_softirq=5, cpu_steal=0)
        result = compute_cpu(cur, prev)
        assert result is not None
        assert result.usage_pct is not None
        assert 0 < result.usage_pct < 100

    def test_zero_delta_total_returns_none(self):
        m = _metric()
        result = compute_cpu(m, m)  # 동일한 값 → delta_total = 0
        assert result.usage_pct is None

    def test_counter_reset_negative_delta_returns_none(self):
        cur  = _metric(_T0, cpu_user=50)   # 낮아짐 (reset)
        prev = _metric(_T1, cpu_user=100)
        result = compute_cpu(cur, prev)
        # delta_total 자체가 음수 → None
        assert result.usage_pct is None


class TestComputeMem:
    def test_no_reading_returns_none(self):
        assert compute_mem(None) is None

    def test_used_calculated_from_total_minus_available(self):
        m = _metric(mem_total_kb=8_000_000, mem_available_kb=3_000_000)
        result = compute_mem(m)
        assert result is not None
        assert result.used_kb == 5_000_000

    def test_usage_pct_calculated(self):
        m = _metric(mem_total_kb=1_000_000, mem_available_kb=500_000)
        result = compute_mem(m)
        assert result.usage_pct == 50.0

    def test_none_available_yields_none_used(self):
        m = _metric(mem_available_kb=None)
        result = compute_mem(m)
        assert result.used_kb is None
        assert result.usage_pct is None


class TestComputeSwap:
    def test_zero_swap_total_returns_none(self):
        m = _metric(swap_total_kb=0)
        assert compute_swap(m) is None

    def test_used_calculated_correctly(self):
        m = _metric(swap_total_kb=2_000_000, swap_free_kb=500_000)
        result = compute_swap(m)
        assert result is not None
        assert result.used_kb == 1_500_000
        assert result.usage_pct == 75.0


class TestComputeDiskIo:
    def test_single_sample_returns_none_rates(self):
        result = compute_disk_io([_disk_row()])
        assert len(result) == 1
        assert result[0].read_iops is None
        assert result[0].write_iops is None

    def test_two_samples_calculates_iops_and_kbps(self):
        cur  = _disk_row("sda", _T0, reads=200, writes=100, sr=4000, sw=2000)
        prev = _disk_row("sda", _T1, reads=100, writes=50,  sr=2000, sw=1000)
        result = compute_disk_io([cur, prev])
        assert len(result) == 1
        snap = result[0]
        # dt=60s, d_reads=100 → 100/60 ≈ 1.7
        assert snap.read_iops == round(100 / 60, 1)
        assert snap.write_iops == round(50 / 60, 1)
        # sectors: d=2000, *512/1024/60
        assert snap.read_kbps == round(2000 * 512 / 1024 / 60, 1)

    def test_counter_reset_returns_none(self):
        cur  = _disk_row("sda", _T0, reads=50)
        prev = _disk_row("sda", _T1, reads=200)  # reset
        result = compute_disk_io([cur, prev])
        assert result[0].read_iops is None

    def test_zero_interval_returns_none(self):
        cur  = _disk_row("sda", _T0)
        prev = _disk_row("sda", _T0)  # 동일 시각
        result = compute_disk_io([cur, prev])
        assert result[0].read_iops is None

    def test_multiple_devices_returned_sorted(self):
        rows = [_disk_row("sdb", _T0), _disk_row("sda", _T0)]
        result = compute_disk_io(rows)
        assert [r.device for r in result] == ["sda", "sdb"]


class TestComputeNetIo:
    def test_single_sample_returns_none_rates(self):
        result = compute_net_io([_net_row()])
        assert result[0].rx_kbps is None

    def test_two_samples_calculates_kbps_and_pps(self):
        cur  = _net_row("eth0", _T0, rx_b=3072, tx_b=1024, rx_p=70, tx_p=10)
        prev = _net_row("eth0", _T1, rx_b=1024, tx_b=512,  rx_p=10, tx_p=5)
        result = compute_net_io([cur, prev])
        snap = result[0]
        # d_rx=2048 bytes, dt=60s → 2048/1024/60
        assert snap.rx_kbps == round(2048 / 1024 / 60, 1)
        assert snap.rx_pps == round(60 / 60, 1)

    def test_counter_reset_returns_none(self):
        cur  = _net_row("eth0", _T0, rx_b=100)
        prev = _net_row("eth0", _T1, rx_b=999)
        result = compute_net_io([cur, prev])
        assert result[0].rx_kbps is None

    def test_multiple_interfaces_returned_sorted(self):
        rows = [_net_row("lo", _T0), _net_row("eth0", _T0)]
        result = compute_net_io(rows)
        assert [r.interface for r in result] == ["eth0", "lo"]


class TestComputeMounts:
    def test_usage_pct_calculated(self):
        m = MountUsageRaw(mount="/", total_bytes=100_000, avail_bytes=40_000, free_bytes=40_000)
        result = compute_mounts([m])
        assert result[0].usage_pct == 60.0

    def test_sorted_by_mount_path(self):
        rows = [
            MountUsageRaw(mount="/var", total_bytes=100, avail_bytes=50, free_bytes=50),
            MountUsageRaw(mount="/",    total_bytes=100, avail_bytes=50, free_bytes=50),
        ]
        result = compute_mounts(rows)
        assert result[0].mount == "/"
        assert result[1].mount == "/var"

    def test_zero_total_bytes_yields_none_pct(self):
        m = MountUsageRaw(mount="/", total_bytes=0, avail_bytes=0, free_bytes=0)
        result = compute_mounts([m])
        assert result[0].usage_pct is None


class TestBuildDashboard:
    def test_empty_metrics_list_handled(self):
        raw = DashboardRaw(metrics=[], disk_io=[], net_io=[], mounts=[])
        dash = build_dashboard(raw)
        assert dash.cpu is None
        assert dash.memory is None

    def test_single_reading_yields_none_cpu_pcts(self):
        raw = DashboardRaw(metrics=[_metric()], disk_io=[], net_io=[], mounts=[])
        dash = build_dashboard(raw)
        assert dash.cpu is not None
        assert dash.cpu.usage_pct is None

    def test_two_readings_populates_all_fields(self):
        cur  = _metric(_T0, cpu_user=200, cpu_nice=0, cpu_system=100, cpu_idle=1600,
                       cpu_iowait=20, cpu_irq=10, cpu_softirq=10, cpu_steal=0)
        prev = _metric(_T1, cpu_user=100, cpu_nice=0, cpu_system=50,  cpu_idle=800,
                       cpu_iowait=10, cpu_irq=5,  cpu_softirq=5,  cpu_steal=0)
        disk_cur  = _disk_row("sda", _T0, reads=200, writes=100, sr=4000, sw=2000)
        disk_prev = _disk_row("sda", _T1, reads=100, writes=50,  sr=2000, sw=1000)
        raw = DashboardRaw(
            metrics=[cur, prev],
            disk_io=[disk_cur, disk_prev],
            net_io=[],
            mounts=[MountUsageRaw(mount="/", total_bytes=100_000, avail_bytes=60_000, free_bytes=60_000)],
        )
        dash = build_dashboard(raw)
        assert dash.cpu.usage_pct is not None
        assert dash.memory is not None
        assert len(dash.disk_io) == 1
        assert len(dash.mounts) == 1