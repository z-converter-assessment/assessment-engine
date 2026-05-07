"""metrics_calculator — delta 기반 percent/rate 계산."""
from datetime import datetime, timedelta, timezone

import pytest

from assessment_engine.db.repositories.outbound import (
    DiskIoRaw,
    MetricPairRaw,
    MountUsageRaw,
    NetIoRaw,
)
from assessment_engine.web.services.metrics_calculator import (
    _clip_to_remaining,
    _delta_rate,
    _group_by_dim,
    compute_cpu,
    compute_disk_io,
    compute_mem,
    compute_mounts,
    compute_net_io,
    compute_swap,
)


# ─── helper 단위 ──────────────────────────────────────────────────────────

def test_group_by_dim_groups_by_key():
    rows = [("sda", 1), ("sdb", 2), ("sda", 3)]
    grouped = _group_by_dim(rows, key=lambda r: r[0])
    assert grouped == {"sda": [("sda", 1), ("sda", 3)], "sdb": [("sdb", 2)]}


@pytest.mark.parametrize("cur, prev, dt, expected", [
    (200, 100, 10.0, 10.0),       # (200-100)/10
    (100, 100, 10.0, 0.0),
    (50, 100, 10.0, None),         # counter reset
    (None, 100, 10.0, None),
    (200, None, 10.0, None),
])
def test_delta_rate(cur, prev, dt, expected):
    assert _delta_rate(cur, prev, dt) == expected


@pytest.mark.parametrize("raw, room, expected", [
    (None, 50.0, None),
    (10.0, 50.0, 10.0),     # raw < room
    (60.0, 50.0, 50.0),     # raw > room → clip
    (10.0, -5.0, 0.0),      # room 음수 → 0
])
def test_clip_to_remaining(raw, room, expected):
    assert _clip_to_remaining(raw, room) == expected


# ─── compute_cpu ──────────────────────────────────────────────────────────

def _cpu_pair(t: datetime, user, idle) -> MetricPairRaw:
    return MetricPairRaw(
        collected_at=t,
        cpu_user=user, cpu_nice=0, cpu_system=0, cpu_idle=idle,
        cpu_iowait=0, cpu_irq=0, cpu_softirq=0, cpu_steal=0,
        mem_total_kb=None, mem_free_kb=None, mem_available_kb=None,
        mem_buffers_kb=None, mem_cached_kb=None,
        swap_total_kb=None, swap_free_kb=None,
        load_1m=None, load_5m=None, load_15m=None,
    )


def test_compute_cpu_returns_none_without_cur():
    assert compute_cpu(None, None) is None


def test_compute_cpu_returns_unset_pcts_without_prev():
    cur = _cpu_pair(datetime.now(timezone.utc), 100, 900)
    snap = compute_cpu(cur, None)
    assert snap is not None
    assert snap.usage_pct is None and snap.user_pct is None


def test_compute_cpu_calculates_percent_from_jiffies_delta():
    """user 100 → 200 (Δ100), idle 900 → 1700 (Δ800), total Δ900. usage = 100-(800/900*100) ≈ 11.1"""
    t1 = datetime.now(timezone.utc)
    t2 = t1 + timedelta(seconds=60)
    prev = _cpu_pair(t1, 100, 900)
    cur = _cpu_pair(t2, 200, 1700)
    snap = compute_cpu(cur, prev)
    assert snap.usage_pct == pytest.approx(11.1, abs=0.1)
    assert snap.user_pct == pytest.approx(100 / 900 * 100, abs=0.1)


def test_compute_cpu_handles_counter_reset():
    """delta_total <= 0이면 모든 percent None."""
    t1 = datetime.now(timezone.utc)
    prev = _cpu_pair(t1, 200, 1700)
    cur = _cpu_pair(t1 + timedelta(seconds=60), 100, 900)  # 감소
    snap = compute_cpu(cur, prev)
    assert snap.usage_pct is None


# ─── compute_mem ──────────────────────────────────────────────────────────

def _mem_pair(total, available, cached, buffers) -> MetricPairRaw:
    return MetricPairRaw(
        collected_at=datetime.now(timezone.utc),
        cpu_user=0, cpu_nice=0, cpu_system=0, cpu_idle=0,
        cpu_iowait=0, cpu_irq=0, cpu_softirq=0, cpu_steal=0,
        mem_total_kb=total, mem_free_kb=None, mem_available_kb=available,
        mem_buffers_kb=buffers, mem_cached_kb=cached,
        swap_total_kb=None, swap_free_kb=None,
        load_1m=None, load_5m=None, load_15m=None,
    )


def test_compute_mem_returns_none_when_total_missing():
    assert compute_mem(_mem_pair(None, 1000, 100, 50)) is None


def test_compute_mem_basic():
    """total=1000kb, available=400kb → used=600kb (60%)"""
    snap = compute_mem(_mem_pair(1000, 400, 100, 50))
    assert snap.usage_pct == pytest.approx(60.0, abs=0.1)
    assert snap.cached_pct == pytest.approx(10.0, abs=0.1)
    assert snap.buffers_pct == pytest.approx(5.0, abs=0.1)


def test_compute_mem_clips_cached_when_overflow():
    """cached가 used 이후 남은 공간보다 크면 잘린다 (stacked bar 100% 초과 방지)."""
    # used=99%, cached_raw=10%, remaining=1% → cached_pct=1%
    snap = compute_mem(_mem_pair(total=10000, available=100, cached=1000, buffers=500))
    assert snap.usage_pct == pytest.approx(99.0, abs=0.1)
    assert snap.cached_pct == pytest.approx(1.0, abs=0.1)
    assert snap.buffers_pct == 0.0


# ─── compute_swap ─────────────────────────────────────────────────────────

def test_compute_swap_returns_none_when_total_zero():
    pair = MetricPairRaw(
        collected_at=datetime.now(timezone.utc),
        cpu_user=0, cpu_nice=0, cpu_system=0, cpu_idle=0,
        cpu_iowait=0, cpu_irq=0, cpu_softirq=0, cpu_steal=0,
        mem_total_kb=None, mem_free_kb=None, mem_available_kb=None,
        mem_buffers_kb=None, mem_cached_kb=None,
        swap_total_kb=0, swap_free_kb=0,
        load_1m=None, load_5m=None, load_15m=None,
    )
    assert compute_swap(pair) is None


# ─── compute_disk_io ──────────────────────────────────────────────────────

def _disk(device, t, reads, writes, sr=0, sw=0) -> DiskIoRaw:
    return DiskIoRaw(
        device=device, collected_at=t,
        reads_completed=reads, writes_completed=writes,
        sectors_read=sr, sectors_written=sw,
    )


def test_compute_disk_io_classifies_into_three_groups():
    t1 = datetime.now(timezone.utc)
    t2 = t1 + timedelta(seconds=60)
    rows = [
        _disk("sda", t2, 200, 100), _disk("sda", t1, 100, 50),
        _disk("dm-0", t2, 50, 25),  _disk("dm-0", t1, 0, 0),
        _disk("sda1", t2, 30, 15),  _disk("sda1", t1, 0, 0),
    ]
    phys, lvm, part = compute_disk_io(rows)
    assert [s.device for s in phys] == ["sda"]
    assert [s.device for s in lvm] == ["dm-0"]
    assert [s.device for s in part] == ["sda1"]


def test_compute_disk_io_single_row_returns_none_rates():
    """페어가 없으면 rate 계산 불가 → None."""
    t1 = datetime.now(timezone.utc)
    snap_list, _, _ = compute_disk_io([_disk("sda", t1, 100, 50)])
    assert snap_list[0].read_iops is None
    assert snap_list[0].write_iops is None


# ─── compute_net_io ───────────────────────────────────────────────────────

def _net(iface, t, rx, tx, rxp=0, txp=0) -> NetIoRaw:
    return NetIoRaw(
        interface=iface, collected_at=t,
        rx_bytes=rx, tx_bytes=tx,
        rx_packets=rxp, tx_packets=txp,
        rx_errors=0, tx_errors=0,
    )


def test_compute_net_io_rate():
    t1 = datetime.now(timezone.utc)
    t2 = t1 + timedelta(seconds=10)
    rows = [
        _net("eth0", t2, 10240, 5120, 100, 50),
        _net("eth0", t1, 0, 0, 0, 0),
    ]
    snap = compute_net_io(rows)[0]
    # rx_kbps = 10240 / 1024 / 10 = 1.0 KB/s
    assert snap.rx_kbps == pytest.approx(1.0, abs=0.1)
    assert snap.tx_kbps == pytest.approx(0.5, abs=0.1)
    assert snap.rx_pps == pytest.approx(10.0, abs=0.1)
    assert snap.tx_pps == pytest.approx(5.0, abs=0.1)


# ─── compute_mounts ───────────────────────────────────────────────────────

def test_compute_mounts_filters_virtual():
    rows = [
        MountUsageRaw(mount="/", total_bytes=10**10, avail_bytes=5*10**9, free_bytes=5*10**9, collected_at=datetime.now(timezone.utc)),
        MountUsageRaw(mount="/proc", total_bytes=0, avail_bytes=0, free_bytes=0, collected_at=datetime.now(timezone.utc)),
        MountUsageRaw(mount="/snap/core/123", total_bytes=10**8, avail_bytes=0, free_bytes=0, collected_at=datetime.now(timezone.utc)),
    ]
    result = compute_mounts(rows)
    paths = [m.mount for m in result]
    assert "/" in paths
    assert "/proc" not in paths
    assert "/snap/core/123" not in paths