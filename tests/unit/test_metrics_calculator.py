"""metrics_calculator — delta 기반 percent/rate 계산."""

from datetime import UTC, datetime, timedelta

import pytest

from assessment_engine.boot_time import is_counter_reset
from assessment_engine.db.dtos.outbound import (
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

_BOOT_A = datetime(2026, 1, 1, tzinfo=UTC)
_BOOT_B = datetime(2026, 5, 9, tzinfo=UTC)


# ─── helper 단위 ──────────────────────────────────────────────────────────


def test_group_by_dim_groups_by_key():
    rows = [("sda", 1), ("sdb", 2), ("sda", 3)]
    grouped = _group_by_dim(rows, key=lambda r: r[0])
    assert grouped == {"sda": [("sda", 1), ("sda", 3)], "sdb": [("sdb", 2)]}


@pytest.mark.parametrize(
    "cur, prev, dt, expected",
    [
        (200, 100, 10.0, 10.0),  # (200-100)/10
        (100, 100, 10.0, 0.0),
        (50, 100, 10.0, None),  # counter reset
        (None, 100, 10.0, None),
        (200, None, 10.0, None),
    ],
)
def test_delta_rate(cur, prev, dt, expected):
    assert _delta_rate(cur, prev, dt) == expected


@pytest.mark.parametrize(
    "raw, room, expected",
    [
        (None, 50.0, None),
        (10.0, 50.0, 10.0),  # raw < room
        (60.0, 50.0, 50.0),  # raw > room → clip
        (10.0, -5.0, 0.0),  # room 음수 → 0
    ],
)
def test_clip_to_remaining(raw, room, expected):
    assert _clip_to_remaining(raw, room) == expected


# ─── compute_cpu ──────────────────────────────────────────────────────────


def _cpu_pair(
    t: datetime, user, idle, *, boot_time: datetime | None = None, agent_started_at: datetime | None = None
) -> MetricPairRaw:
    return MetricPairRaw(
        collected_at=t,
        cpu_user=user,
        cpu_nice=0,
        cpu_system=0,
        cpu_idle=idle,
        cpu_iowait=0,
        cpu_irq=0,
        cpu_softirq=0,
        cpu_steal=0,
        mem_total_kb=None,
        mem_free_kb=None,
        mem_available_kb=None,
        mem_buffers_kb=None,
        mem_cached_kb=None,
        swap_total_kb=None,
        swap_free_kb=None,
        load_1m=None,
        load_5m=None,
        load_15m=None,
        boot_time=boot_time,
        agent_started_at=agent_started_at,
    )


def test_compute_cpu_returns_none_without_cur():
    assert compute_cpu(None, None) is None


def test_compute_cpu_returns_unset_pcts_without_prev():
    cur = _cpu_pair(datetime.now(UTC), 100, 900)
    snap = compute_cpu(cur, None)
    assert snap is not None
    assert snap.usage_pct is None and snap.user_pct is None


def test_compute_cpu_calculates_percent_from_jiffies_delta():
    """user 100 → 200 (Δ100), idle 900 → 1700 (Δ800), total Δ900. usage = 100-(800/900*100) ≈ 11.1"""
    t1 = datetime.now(UTC)
    t2 = t1 + timedelta(seconds=60)
    prev = _cpu_pair(t1, 100, 900)
    cur = _cpu_pair(t2, 200, 1700)
    snap = compute_cpu(cur, prev)
    assert snap.usage_pct == pytest.approx(11.1, abs=0.1)
    assert snap.user_pct == pytest.approx(100 / 900 * 100, abs=0.1)


def test_compute_cpu_handles_counter_reset():
    """delta_total <= 0이면 모든 percent None (옛 데이터 fallback — boot_time NULL)."""
    t1 = datetime.now(UTC)
    prev = _cpu_pair(t1, 200, 1700)
    cur = _cpu_pair(t1 + timedelta(seconds=60), 100, 900)  # 감소
    snap = compute_cpu(cur, prev)
    assert snap.usage_pct is None


def test_compute_cpu_returns_none_when_boot_time_changed():
    """두 시점의 boot_time이 다르면 시스템 재부팅 → reset 확정 (delta는 양수여도 무시)."""
    t1 = datetime.now(UTC)
    prev = _cpu_pair(t1, 100, 900, boot_time=_BOOT_A)
    cur = _cpu_pair(t1 + timedelta(seconds=60), 200, 1700, boot_time=_BOOT_B)
    snap = compute_cpu(cur, prev)
    assert snap.usage_pct is None
    assert snap.user_pct is None


def test_compute_cpu_normal_when_only_agent_restart():
    """agent_started_at만 다름·boot_time 동일 → 에이전트 재시작이지 시스템 재부팅 아님 → 정상 delta."""
    t1 = datetime.now(UTC)
    agent1 = datetime(2026, 5, 9, 1, tzinfo=UTC)
    agent2 = datetime(2026, 5, 9, 2, tzinfo=UTC)
    prev = _cpu_pair(t1, 100, 900, boot_time=_BOOT_A, agent_started_at=agent1)
    cur = _cpu_pair(t1 + timedelta(seconds=60), 200, 1700, boot_time=_BOOT_A, agent_started_at=agent2)
    snap = compute_cpu(cur, prev)
    assert snap.usage_pct is not None  # 정상 계산


# ─── is_counter_reset helper (assessment_engine.boot_time) ─────────────────


@pytest.mark.parametrize(
    "cur, prev, expected",
    [
        (_BOOT_A, _BOOT_A, False),  # 동일
        (_BOOT_A, _BOOT_B, True),  # 다름 → reset
        (None, _BOOT_A, False),  # 한쪽 NULL → fallback
        (_BOOT_A, None, False),
        (None, None, False),  # 둘 다 NULL (옛 데이터)
    ],
)
def test_is_counter_reset(cur, prev, expected):
    assert is_counter_reset(cur, prev) is expected


# ─── compute_mem ──────────────────────────────────────────────────────────


def _mem_pair(total, available, cached, buffers) -> MetricPairRaw:
    return MetricPairRaw(
        collected_at=datetime.now(UTC),
        cpu_user=0,
        cpu_nice=0,
        cpu_system=0,
        cpu_idle=0,
        cpu_iowait=0,
        cpu_irq=0,
        cpu_softirq=0,
        cpu_steal=0,
        mem_total_kb=total,
        mem_free_kb=None,
        mem_available_kb=available,
        mem_buffers_kb=buffers,
        mem_cached_kb=cached,
        swap_total_kb=None,
        swap_free_kb=None,
        load_1m=None,
        load_5m=None,
        load_15m=None,
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
        collected_at=datetime.now(UTC),
        cpu_user=0,
        cpu_nice=0,
        cpu_system=0,
        cpu_idle=0,
        cpu_iowait=0,
        cpu_irq=0,
        cpu_softirq=0,
        cpu_steal=0,
        mem_total_kb=None,
        mem_free_kb=None,
        mem_available_kb=None,
        mem_buffers_kb=None,
        mem_cached_kb=None,
        swap_total_kb=0,
        swap_free_kb=0,
        load_1m=None,
        load_5m=None,
        load_15m=None,
    )
    assert compute_swap(pair) is None


# ─── compute_disk_io ──────────────────────────────────────────────────────


def _disk(
    device, t, reads, writes, sr=0, sw=0, *, kind: str | None = "physical", boot_time: datetime | None = None
) -> DiskIoRaw:
    return DiskIoRaw(
        device=device,
        collected_at=t,
        reads_completed=reads,
        writes_completed=writes,
        sectors_read=sr,
        sectors_written=sw,
        boot_time=boot_time,
        agent_started_at=None,
        kind=kind,
    )


def test_compute_disk_io_classifies_into_three_groups():
    t1 = datetime.now(UTC)
    t2 = t1 + timedelta(seconds=60)
    rows = [
        _disk("sda", t2, 200, 100, kind="physical"),
        _disk("sda", t1, 100, 50, kind="physical"),
        _disk("dm-0", t2, 50, 25, kind="lvm"),
        _disk("dm-0", t1, 0, 0, kind="lvm"),
        _disk("sda1", t2, 30, 15, kind="partition"),
        _disk("sda1", t1, 0, 0, kind="partition"),
    ]
    phys, lvm, part = compute_disk_io(rows)
    assert [s.device for s in phys] == ["sda"]
    assert [s.device for s in lvm] == ["dm-0"]
    assert [s.device for s in part] == ["sda1"]


def test_compute_disk_io_single_row_returns_none_rates():
    """페어가 없으면 rate 계산 불가 → None."""
    t1 = datetime.now(UTC)
    snap_list, _, _ = compute_disk_io([_disk("sda", t1, 100, 50)])
    assert snap_list[0].read_iops is None
    assert snap_list[0].write_iops is None


def test_compute_disk_io_returns_none_on_system_reboot():
    """boot_time 변경 시 reset 확정 — delta가 양수여도 무시."""
    t1 = datetime.now(UTC)
    t2 = t1 + timedelta(seconds=60)
    rows = [
        _disk("sda", t2, 200, 100, boot_time=_BOOT_B),
        _disk("sda", t1, 100, 50, boot_time=_BOOT_A),
    ]
    phys, _, _ = compute_disk_io(rows)
    assert phys[0].read_iops is None
    assert phys[0].write_iops is None
    assert phys[0].read_kbps is None


# ─── compute_net_io ───────────────────────────────────────────────────────


def _net(
    iface, t, rx, tx, rxp=0, txp=0, *, kind: str | None = "physical", boot_time: datetime | None = None
) -> NetIoRaw:
    return NetIoRaw(
        interface=iface,
        collected_at=t,
        rx_bytes=rx,
        tx_bytes=tx,
        rx_packets=rxp,
        tx_packets=txp,
        rx_errors=0,
        tx_errors=0,
        boot_time=boot_time,
        agent_started_at=None,
        kind=kind,
    )


def test_compute_net_io_rate():
    t1 = datetime.now(UTC)
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


def test_compute_net_io_returns_none_on_system_reboot():
    """boot_time 변경 시 reset 확정 — delta가 양수여도 무시."""
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


# ─── compute_mounts ───────────────────────────────────────────────────────


def test_compute_mounts_filters_virtual():
    now = datetime.now(UTC)
    rows = [
        MountUsageRaw(
            mount="/",
            total_bytes=10**10,
            avail_bytes=5 * 10**9,
            free_bytes=5 * 10**9,
            collected_at=now,
            kind="data",
        ),
        MountUsageRaw(
            mount="/proc",
            total_bytes=0,
            avail_bytes=0,
            free_bytes=0,
            collected_at=now,
            kind="virtual",
        ),
        MountUsageRaw(
            mount="/snap/core/123",
            total_bytes=10**8,
            avail_bytes=0,
            free_bytes=0,
            collected_at=now,
            kind="image",
        ),
    ]
    result = compute_mounts(rows)
    paths = [m.mount for m in result]
    assert "/" in paths
    assert "/proc" not in paths
    assert "/snap/core/123" not in paths
