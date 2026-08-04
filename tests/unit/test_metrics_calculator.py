"""metrics_calculator — delta 기반 percent/rate 계산 (wire)."""

from datetime import UTC, datetime, timedelta

import pytest

from assessment_engine.boot_time import is_counter_reset
from assessment_engine.db.dtos.outbound import (
    DiskIoRaw,
    MetricPairRaw,
    MountUsageRaw,
    NetIoRaw,
    SaturationRaw,
)
from assessment_engine.web.services.metrics_calculator import (
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


# --- helper 단위 ----------------------------------------------------------


def test_group_by_dim_groups_by_key():
    rows = [("sda", 1), ("sdb", 2), ("sda", 3)]
    grouped = _group_by_dim(rows, key=lambda r: r[0])
    assert grouped == {"sda": [("sda", 1), ("sda", 3)], "sdb": [("sdb", 2)]}


@pytest.mark.parametrize(
    ("cur", "prev", "dt", "expected"),
    [
        (200, 100, 10.0, 10.0),  # (200-100)/10
        (100, 100, 10.0, 0.0),
        (50, 100, 10.0, None),  # counter reset
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
        (10.0, 50.0, 10.0),  # raw < room
        (60.0, 50.0, 50.0),  # raw > room → clip
        (10.0, -5.0, 0.0),  # room 음수 → 0
    ],
)
def test_clip_to_remaining(raw: float | None, room: float, expected: float | None):
    assert _clip_to_remaining(raw, room) == expected


# --- compute_cpu ----------------------------------------------------------


def _cpu_pair(
    t: datetime,
    user: float,
    idle: float,
    *,
    boot_time: datetime | None = None,
    agent_started_at: datetime | None = None,
) -> MetricPairRaw:
    # CPU 시간은 seconds counter(cpu_*_s), 메모리는 By(mem_*_bytes).
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
    """user 100->200 (d100), idle 900->1700 (d800), total d900. usage = 100-(800/900*100) ~= 11.1 (s counter)."""
    t1 = datetime.now(UTC)
    t2 = t1 + timedelta(seconds=60)
    prev = _cpu_pair(t1, 100, 900)
    cur = _cpu_pair(t2, 200, 1700)
    snap = compute_cpu(cur, prev)
    assert snap is not None
    assert snap.usage_pct == approx(11.1, abs=0.1)
    assert snap.user_pct == approx(100 / 900 * 100, abs=0.1)


def test_compute_cpu_handles_counter_reset():
    """delta_total <= 0이면 모든 percent None (옛 데이터 fallback — boot_time NULL)."""
    t1 = datetime.now(UTC)
    prev = _cpu_pair(t1, 200, 1700)
    cur = _cpu_pair(t1 + timedelta(seconds=60), 100, 900)  # 감소
    snap = compute_cpu(cur, prev)
    assert snap is not None
    assert snap.usage_pct is None


def test_compute_cpu_returns_none_when_boot_time_changed():
    """두 시점의 boot_time이 다르면 시스템 재부팅 → reset 확정 (delta는 양수여도 무시)."""
    t1 = datetime.now(UTC)
    prev = _cpu_pair(t1, 100, 900, boot_time=_BOOT_A)
    cur = _cpu_pair(t1 + timedelta(seconds=60), 200, 1700, boot_time=_BOOT_B)
    snap = compute_cpu(cur, prev)
    assert snap is not None
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
    assert snap is not None
    assert snap.usage_pct is not None  # 정상 계산


def _win_cpu_pair(t: datetime, user: float, system: float, idle: float) -> MetricPairRaw:
    """Windows cpu_stat — nice/iowait/irq/softirq/steal 은 OS 개념 부재로 None (#C1)."""
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
    """Windows nice/iowait/... None 이어도 cpu_total = user+system+idle COALESCE 합으로 CPU% 실측(#C2/C3).

    성분 하나가 None 이라고 total 을 None 으로 만들면 Windows CPU 가 항상 N/A 가 된다.
    user 100->300(+200), system 50->150(+100), idle 900->1500(+600). total delta 900.
    usage = 100 - (600/900*100) = 약 33.3.
    iowait 는 None 이라 iowait_pct 는 None(N/A 보존).
    """
    t1 = datetime.now(UTC)
    prev = _win_cpu_pair(t1, 100, 50, 900)
    cur = _win_cpu_pair(t1 + timedelta(seconds=60), 300, 150, 1500)
    snap = compute_cpu(cur, prev)
    assert snap is not None
    assert snap.usage_pct == approx(33.3, abs=0.1)  # null 전파로 N/A 되지 않음
    assert snap.user_pct == approx(200 / 900 * 100, abs=0.1)
    assert snap.iowait_pct is None  # Windows iowait 미측정 보존


# --- is_counter_reset helper (assessment_engine.boot_time) -----------------


@pytest.mark.parametrize(
    ("cur", "prev", "expected"),
    [
        (_BOOT_A, _BOOT_A, False),  # 동일
        (_BOOT_A, _BOOT_B, True),  # 다름 → reset
        (None, _BOOT_A, False),  # 한쪽 NULL → fallback
        (_BOOT_A, None, False),
        (None, None, False),  # 둘 다 NULL (옛 데이터)
    ],
)
def test_is_counter_reset(cur: datetime | None, prev: datetime | None, expected: bool):
    assert is_counter_reset(cur, prev) is expected


# --- compute_mem ----------------------------------------------------------


def _mem_pair(total: int | None, available: int | None, cached: int | None, buffers: int | None) -> MetricPairRaw:
    # total 축은 mem_limit_bytes, 회수가능 세부는 mem_cached_bytes/mem_buffered_bytes (모두 By).
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
    """total=1000B, available=400B → used=600B (60%)"""
    snap = compute_mem(_mem_pair(1000, 400, 100, 50))
    assert snap is not None
    assert snap.usage_pct == approx(60.0, abs=0.1)
    assert snap.cached_pct == approx(10.0, abs=0.1)
    assert snap.buffers_pct == approx(5.0, abs=0.1)


def test_compute_mem_clips_cached_when_overflow():
    """cached가 used 이후 남은 공간보다 크면 잘린다 (stacked bar 100% 초과 방지)."""
    # used=99%, cached_raw=10%, remaining=1% → cached_pct=1%
    snap = compute_mem(_mem_pair(total=10000, available=100, cached=1000, buffers=500))
    assert snap is not None
    assert snap.usage_pct == approx(99.0, abs=0.1)
    assert snap.cached_pct == approx(1.0, abs=0.1)
    assert snap.buffers_pct == 0.0


# --- compute_disk_io ------------------------------------------------------


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
    # 안정키 device_id, ops_*(operations counter), io_*_bytes(By counter).
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
    """device type 축이 없어 물리/LVM/파티션 분류 없이 device_id 별 flat 그룹 (device_id 정렬, 3-tuple 아님)."""
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
    # 단일 flat 리스트, device_id 문자열 정렬 (dm-0 < sda < sda1).
    assert [s.device for s in result] == ["dm-0", "sda", "sda1"]


def test_compute_disk_io_single_row_returns_none_rates():
    """페어가 없으면 rate 계산 불가 → None."""
    t1 = datetime.now(UTC)
    result = compute_disk_io([_disk("sda", t1, 100, 50)])
    assert result is not None
    assert result[0].read_iops is None
    assert result[0].write_iops is None


def test_compute_disk_io_returns_none_on_system_reboot():
    """boot_time 변경 시 reset 확정 — delta가 양수여도 무시."""
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


# --- compute_net_io -------------------------------------------------------


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
    # 안정키 iface_id(mac:..), rx/tx_bytes(By counter). kind 축은 inventory 조인으로 이관(NetIoRaw 부재).
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
    # rx_kbps = 10240 / 1024 / 10 = 1.0 KB/s
    assert snap.rx_kbps == approx(1.0, abs=0.1)
    assert snap.tx_kbps == approx(0.5, abs=0.1)
    assert snap.rx_pps == approx(10.0, abs=0.1)
    assert snap.tx_pps == approx(5.0, abs=0.1)


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


# --- compute_mounts -------------------------------------------------------


def test_compute_mounts_filters_virtual():
    """가상 필터는 is_data_volume(fstype, mountpoint) 기준이다. proc/squashfs 제외."""
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


# --- PSI 지원 판정 + 포화 신호 (구커널 N/A · 4-2) --------------------------


@pytest.mark.parametrize(
    ("kernel", "expected"),
    [
        ("5.15.0-91-generic", True),  # Linux 4.20+ = 지원
        ("4.20.1", True),  # 정확히 경계
        ("4.19.99", False),  # 경계 직전 = 미지원
        ("3.10.0-1160.el7.x86_64", False),  # centos7 = 구조적 미지원
        (None, None),  # 커널 미상 = 판정 보류
        ("", None),  # 빈 문자열 = 판정 보류
        ("garbage", None),  # 파싱 불가 = 판정 보류
    ],
)
def test_psi_supported(kernel: str | None, expected: bool | None):
    assert _psi_supported(kernel) == expected


def test_saturation_signals_old_kernel_psi_not_applicable():
    """구커널(Linux <4.20)의 PSI 는 Windows 와 동일 not_applicable — "수집 대기" 오인 방지(4-2)."""
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
    """신커널(4.20+)인데 PSI 값 미수집이면 not_applicable 아닌 no_data(수집 대기) — 구커널과 구분(4-2)."""
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
    """커널 미상이면 PSI 판정 보류 — 값 있으면 measured, 없으면 no_data(N/A 강제 안 함)."""
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
