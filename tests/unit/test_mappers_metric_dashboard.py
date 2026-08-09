from datetime import UTC, datetime, timedelta

import pytest

from assessment_engine.db.dtos.outbound import (
    DiskIoRaw,
    MetricPairRaw,
    MountUsageRaw,
    NetIoRaw,
    SaturationRaw,
)
from assessment_engine.domain.boot_time import is_counter_reset
from assessment_engine.web.services.mappers.metric_dashboard import (
    _clip_to_remaining,
    _delta_rate,
    _group_by_dim,
    _psi_supported,
    build_saturation_signals,
    compute_cpu,
    compute_disk_io,
    compute_mem,
    compute_mounts,
    compute_net_io,
)
from tests.approx import approx

_BOOT_A = datetime(2026, 1, 1, tzinfo=UTC)
_BOOT_B = datetime(2026, 5, 9, tzinfo=UTC)


def test_group_by_dim_groups_by_key():
    rows = [("sda", 1), ("sdb", 2), ("sda", 3)]
    grouped = _group_by_dim(rows, key=lambda r: r[0])
    assert grouped == {"sda": [("sda", 1), ("sda", 3)], "sdb": [("sdb", 2)]}


@pytest.mark.parametrize(
    ("cur", "prev", "dt", "expected"),
    [
        (200, 100, 10.0, 10.0),
        (100, 100, 10.0, 0.0),
        (50, 100, 10.0, None),
        (None, 100, 10.0, None),
        (200, None, 10.0, None),
    ],
)
def test_delta_rate(cur: int | None, prev: int | None, dt: float, expected: float | None):
    assert _delta_rate(cur, prev, dt) == expected


@pytest.mark.parametrize(
    ("raw", "room", "expected"),
    [
        (None, 50.0, None),
        (10.0, 50.0, 10.0),
        (60.0, 50.0, 50.0),
        (10.0, -5.0, 0.0),
    ],
)
def test_clip_to_remaining(raw: float | None, room: float, expected: float | None):
    assert _clip_to_remaining(raw, room) == expected


def _cpu_pair(
    t: datetime,
    user: float,
    idle: float,
    *,
    boot_time: datetime | None = None,
    agent_started_at: datetime | None = None,
) -> MetricPairRaw:
    return MetricPairRaw(
        collected_at=t,
        cpu_user_s=user,
        cpu_nice_s=0,
        cpu_system_s=0,
        cpu_idle_s=idle,
        cpu_iowait_s=0,
        cpu_irq_s=0,
        cpu_softirq_s=0,
        cpu_steal_s=0,
        mem_limit_bytes=None,
        mem_free_bytes=None,
        mem_available_bytes=None,
        mem_buffered_bytes=None,
        mem_cached_bytes=None,
        mem_used_bytes=None,
        boot_time=boot_time,
        agent_started_at=agent_started_at,
    )


def test_compute_cpu_returns_none_without_cur():
    assert compute_cpu(None, None) is None


def test_compute_cpu_returns_unset_pcts_without_prev():
    cur = _cpu_pair(datetime.now(UTC), 100, 900)
    snap = compute_cpu(cur, None)
    assert snap is not None
    assert snap.usage_pct is None
    assert snap.user_pct is None


def test_compute_cpu_calculates_percent_from_seconds_delta():
    t1 = datetime.now(UTC)
    t2 = t1 + timedelta(seconds=60)
    prev = _cpu_pair(t1, 100, 900)
    cur = _cpu_pair(t2, 200, 1700)
    snap = compute_cpu(cur, prev)
    assert snap is not None
    assert snap.usage_pct == approx(11.1, abs=0.1)
    assert snap.user_pct == approx(100 / 900 * 100, abs=0.1)


def test_compute_cpu_handles_counter_reset():
    t1 = datetime.now(UTC)
    prev = _cpu_pair(t1, 200, 1700)
    cur = _cpu_pair(t1 + timedelta(seconds=60), 100, 900)
    snap = compute_cpu(cur, prev)
    assert snap is not None
    assert snap.usage_pct is None


def test_compute_cpu_returns_none_when_boot_time_changed():
    t1 = datetime.now(UTC)
    prev = _cpu_pair(t1, 100, 900, boot_time=_BOOT_A)
    cur = _cpu_pair(t1 + timedelta(seconds=60), 200, 1700, boot_time=_BOOT_B)
    snap = compute_cpu(cur, prev)
    assert snap is not None
    assert snap.usage_pct is None
    assert snap.user_pct is None


def test_compute_cpu_normal_when_only_agent_restart():
    t1 = datetime.now(UTC)
    agent1 = datetime(2026, 5, 9, 1, tzinfo=UTC)
    agent2 = datetime(2026, 5, 9, 2, tzinfo=UTC)
    prev = _cpu_pair(t1, 100, 900, boot_time=_BOOT_A, agent_started_at=agent1)
    cur = _cpu_pair(t1 + timedelta(seconds=60), 200, 1700, boot_time=_BOOT_A, agent_started_at=agent2)
    snap = compute_cpu(cur, prev)
    assert snap is not None
    assert snap.usage_pct is not None


def _win_cpu_pair(t: datetime, user: float, system: float, idle: float) -> MetricPairRaw:
    return MetricPairRaw(
        collected_at=t,
        cpu_user_s=user,
        cpu_nice_s=None,
        cpu_system_s=system,
        cpu_idle_s=idle,
        cpu_iowait_s=None,
        cpu_irq_s=None,
        cpu_softirq_s=None,
        cpu_steal_s=None,
        mem_limit_bytes=None,
        mem_free_bytes=None,
        mem_available_bytes=None,
        mem_buffered_bytes=None,
        mem_cached_bytes=None,
        mem_used_bytes=None,
        boot_time=None,
        agent_started_at=None,
    )


def test_compute_cpu_windows_coalesce_null_components():
    t1 = datetime.now(UTC)
    prev = _win_cpu_pair(t1, 100, 50, 900)
    cur = _win_cpu_pair(t1 + timedelta(seconds=60), 300, 150, 1500)
    snap = compute_cpu(cur, prev)
    assert snap is not None
    assert snap.usage_pct == approx(33.3, abs=0.1)
    assert snap.user_pct == approx(200 / 900 * 100, abs=0.1)
    assert snap.iowait_pct is None


@pytest.mark.parametrize(
    ("cur", "prev", "expected"),
    [
        (_BOOT_A, _BOOT_A, False),
        (_BOOT_A, _BOOT_B, True),
        (None, _BOOT_A, False),
        (_BOOT_A, None, False),
        (None, None, False),
    ],
)
def test_is_counter_reset(cur: datetime | None, prev: datetime | None, expected: bool):
    assert is_counter_reset(cur, prev) is expected


def _mem_pair(total: int | None, available: int | None, cached: int | None, buffers: int | None) -> MetricPairRaw:
    return MetricPairRaw(
        collected_at=datetime.now(UTC),
        cpu_user_s=0,
        cpu_nice_s=0,
        cpu_system_s=0,
        cpu_idle_s=0,
        cpu_iowait_s=0,
        cpu_irq_s=0,
        cpu_softirq_s=0,
        cpu_steal_s=0,
        mem_limit_bytes=total,
        mem_free_bytes=None,
        mem_available_bytes=available,
        mem_buffered_bytes=buffers,
        mem_cached_bytes=cached,
        mem_used_bytes=None,
    )


def test_compute_mem_returns_none_when_total_missing():
    assert compute_mem(_mem_pair(None, 1000, 100, 50)) is None


def test_compute_mem_basic():
    snap = compute_mem(_mem_pair(1000, 400, 100, 50))
    assert snap is not None
    assert snap.usage_pct == approx(60.0, abs=0.1)
    assert snap.cached_pct == approx(10.0, abs=0.1)
    assert snap.buffers_pct == approx(5.0, abs=0.1)


def test_compute_mem_clips_cached_when_overflow():
    snap = compute_mem(_mem_pair(total=10000, available=100, cached=1000, buffers=500))
    assert snap is not None
    assert snap.usage_pct == approx(99.0, abs=0.1)
    assert snap.cached_pct == approx(1.0, abs=0.1)
    assert snap.buffers_pct == 0.0


def _disk(
    device_id: str,
    t: datetime,
    ops_read: int,
    ops_write: int,
    io_read: int = 0,
    io_write: int = 0,
    *,
    boot_time: datetime | None = None,
) -> DiskIoRaw:
    return DiskIoRaw(
        device_id=device_id,
        collected_at=t,
        io_read_bytes=io_read,
        io_write_bytes=io_write,
        ops_read=ops_read,
        ops_write=ops_write,
        boot_time=boot_time,
        agent_started_at=None,
    )


def test_compute_disk_io_groups_by_device():
    t1 = datetime.now(UTC)
    t2 = t1 + timedelta(seconds=60)
    rows = [
        _disk("sda", t2, 200, 100),
        _disk("sda", t1, 100, 50),
        _disk("dm-0", t2, 50, 25),
        _disk("dm-0", t1, 0, 0),
        _disk("sda1", t2, 30, 15),
        _disk("sda1", t1, 0, 0),
    ]
    result = compute_disk_io(rows)
    assert result is not None
    assert [s.device for s in result] == ["dm-0", "sda", "sda1"]


def test_compute_disk_io_single_row_returns_none_rates():
    t1 = datetime.now(UTC)
    result = compute_disk_io([_disk("sda", t1, 100, 50)])
    assert result is not None
    assert result[0].read_iops is None
    assert result[0].write_iops is None


def test_compute_disk_io_returns_none_on_system_reboot():
    t1 = datetime.now(UTC)
    t2 = t1 + timedelta(seconds=60)
    rows = [
        _disk("sda", t2, 200, 100, boot_time=_BOOT_B),
        _disk("sda", t1, 100, 50, boot_time=_BOOT_A),
    ]
    result = compute_disk_io(rows)
    assert result is not None
    assert result[0].read_iops is None
    assert result[0].write_iops is None
    assert result[0].read_kbps is None


def _net(
    iface_id: str,
    t: datetime,
    rx: int,
    tx: int,
    rxp: int = 0,
    txp: int = 0,
    *,
    boot_time: datetime | None = None,
) -> NetIoRaw:
    return NetIoRaw(
        iface_id=iface_id,
        collected_at=t,
        rx_bytes=rx,
        tx_bytes=tx,
        rx_packets=rxp,
        tx_packets=txp,
        rx_errors=0,
        tx_errors=0,
        boot_time=boot_time,
        agent_started_at=None,
    )


def test_compute_net_io_rate():
    t1 = datetime.now(UTC)
    t2 = t1 + timedelta(seconds=10)
    rows = [
        _net("eth0", t2, 10240, 5120, 100, 50),
        _net("eth0", t1, 0, 0, 0, 0),
    ]
    snap = compute_net_io(rows)[0]
    assert snap.rx_kbps == approx(1.0, abs=0.1)
    assert snap.tx_kbps == approx(0.5, abs=0.1)
    assert snap.rx_pps == approx(10.0, abs=0.1)
    assert snap.tx_pps == approx(5.0, abs=0.1)


def test_compute_net_io_returns_none_on_system_reboot():
    t1 = datetime.now(UTC)
    t2 = t1 + timedelta(seconds=10)
    rows = [
        _net("eth0", t2, 10240, 5120, 100, 50, boot_time=_BOOT_B),
        _net("eth0", t1, 0, 0, 0, 0, boot_time=_BOOT_A),
    ]
    snap = compute_net_io(rows)[0]
    assert snap.rx_kbps is None
    assert snap.tx_kbps is None
    assert snap.rx_pps is None


def test_compute_mounts_filters_virtual():
    now = datetime.now(UTC)
    rows = [
        MountUsageRaw(
            mountpoint="/",
            used_bytes=5 * 10**9,
            free_bytes=5 * 10**9,
            fstype="ext4",
            collected_at=now,
        ),
        MountUsageRaw(
            mountpoint="/proc",
            used_bytes=0,
            free_bytes=0,
            fstype="proc",
            collected_at=now,
        ),
        MountUsageRaw(
            mountpoint="/snap/core/123",
            used_bytes=10**8,
            free_bytes=0,
            fstype="squashfs",
            collected_at=now,
        ),
    ]
    result = compute_mounts(rows)
    assert result is not None
    paths = [m.mount for m in result]
    assert "/" in paths
    assert "/proc" not in paths
    assert "/snap/core/123" not in paths


@pytest.mark.parametrize(
    ("kernel", "expected"),
    [
        ("5.15.0-91-generic", True),
        ("4.20.1", True),
        ("4.19.99", False),
        ("3.10.0-1160.el7.x86_64", False),
        (None, None),
        ("", None),
        ("garbage", None),
    ],
)
def test_psi_supported(kernel: str | None, expected: bool | None):
    assert _psi_supported(kernel) == expected


def test_saturation_signals_old_kernel_psi_not_applicable():
    sat = SaturationRaw(psi_cpu=None, psi_mem=None, psi_io=None)
    signals = build_saturation_signals(
        os_family="linux",
        kernel_version="3.10.0-1160.el7.x86_64",
        run_queue_total=None,
        cores=2,
        steal_pct=None,
        sat=sat,
    )
    cpu_psi = next(s for s in signals["cpu"] if s.key == "cpu_psi")
    assert cpu_psi.state == "not_applicable"
    assert "구커널" in (cpu_psi.na_reason or "")


def test_saturation_signals_new_kernel_psi_no_data_when_unmeasured():
    sat = SaturationRaw(psi_cpu=None)
    signals = build_saturation_signals(
        os_family="linux",
        kernel_version="5.15.0",
        run_queue_total=None,
        cores=2,
        steal_pct=None,
        sat=sat,
    )
    cpu_psi = next(s for s in signals["cpu"] if s.key == "cpu_psi")
    assert cpu_psi.state == "no_data"


def test_saturation_signals_unknown_kernel_psi_falls_through():
    sat = SaturationRaw(psi_cpu=12.5)
    signals = build_saturation_signals(
        os_family="linux",
        kernel_version=None,
        run_queue_total=None,
        cores=2,
        steal_pct=None,
        sat=sat,
    )
    cpu_psi = next(s for s in signals["cpu"] if s.key == "cpu_psi")
    assert cpu_psi.state == "measured"
    assert cpu_psi.value == 12.5
