"""wire 파싱 mapper — datapoint-array -> 저장 DTO 값 정합 (계약 예시 fixture)."""

import copy
import json
from pathlib import Path

from assessment_engine.consumer.mappers import to_inventory_create, to_metric_create
from assessment_engine.consumer.schemas import InventoryInput, MetricsInput

_EX = json.loads((Path(__file__).resolve().parents[2] / "docs/reference/contracts/wire-examples.json").read_text())


def _metric(name: str):
    return to_metric_create(MetricsInput.model_validate(_EX[name]))


def _inv(name: str):
    return to_inventory_create(InventoryInput.model_validate(_EX[name]))


def test_cpu_host_aggregate_from_per_cpu() -> None:
    """cpu.time attr.cpu 합산 -> host CPU (s). per-core 병행 저장."""
    m = _metric("linux_metrics")
    assert m.cpu_user_s == 812.34
    assert m.cpu_idle_s == 58121.7
    assert m.cpu_steal_s == 1.1
    assert len(m.cpu_per_core) == 1
    assert m.cpu_per_core[0].core_id == 0


def test_memory_state_and_commit_bytes() -> None:
    """memory.usage state 조회 + limit/commit (By)."""
    m = _metric("linux_metrics")
    assert m.mem_available_bytes == 1624768512
    assert m.mem_limit_bytes == 2062008320
    assert m.mem_commit_usage_bytes == 355819520
    assert m.mem_oom_kill == 0


def test_paging_direction_type_disambiguation() -> None:
    """paging.operations — direction=in(type 부재)/out / major(type:major) 구분."""
    m = _metric("linux_metrics")
    assert m.paging_in == 0
    assert m.paging_out == 0
    assert m.paging_major == 2161


def test_disk_per_device_axes() -> None:
    """system.disk 를 device 별로 그룹 — io/operation_time/pending 축."""
    m = _metric("linux_metrics")
    assert len(m.disk_io) == 1
    e = m.disk_io[0]
    assert e.device_id.startswith("serial:")
    assert e.io_read_bytes == 10485760
    assert e.io_write_bytes == 734003200
    assert e.io_time_s == 12.44
    assert e.op_write_time_s == 9.21
    assert e.pending_ops == 1.0


def test_filesystem_used_free_inodes() -> None:
    m = _metric("linux_metrics")
    assert len(m.filesystems) == 1
    fs = m.filesystems[0]
    assert fs.mountpoint == "/"
    assert fs.used_bytes == 8123456789
    assert fs.inodes_used == 103956


def test_pressure_resource_scope_grouping() -> None:
    """PSI (resource x scope) 그룹, window 는 ratio 컬럼 평탄화. stall_time canonical."""
    m = _metric("linux_metrics")
    by = {(p.resource, p.scope): p for p in m.pressure}
    assert by[("cpu", "some")].stall_time_s == 68.45
    assert by[("cpu", "some")].ratio_avg10 == 0.0108
    assert by[("io", "some")].stall_time_s == 41.3


def test_disk_errors_variable_dimension() -> None:
    m = _metric("linux_metrics")
    kinds = {(e.error_kind, e.error_class) for e in m.disk_errors}
    assert ("mdraid", "degraded") in kinds
    assert ("btrfs", "corruption") in kinds


def test_windows_pressure_null_conntrack_null() -> None:
    """Windows system.pressure=null -> 빈 리스트. conntrack 미발행 -> None."""
    w = _metric("windows_metrics")
    assert w.pressure == []
    assert w.net_conntrack_usage is None
    assert w.mem_available_bytes == 6442450944
    assert w.cpu_run_queue == 2.0


def test_null_preserved_not_zero() -> None:
    """미측정 축은 None 보존 (0 날조 금지). virtio link.speed null."""
    m = _metric("linux_metrics")
    assert m.net_io[0].link_speed_bps is None


def test_inventory_v2_shape() -> None:
    inv = _inv("linux_inventory")
    assert inv.hostname == "vm-st-lvm"
    assert inv.mem_total_bytes == 2062008320
    assert len(inv.block_devices) == 4
    assert len(inv.lvm_vgs) == 2
    assert inv.net_interfaces[0]["addresses"][0]["address"] == "10.50.60.126"
    assert inv.service_categories  # ingest 사전계산


def test_placeholder_uses_agent_id_for_hostname() -> None:
    """metrics 메시지는 hostname 을 싣지 않는다 -> placeholder hostname = agent_id."""
    from assessment_engine.consumer.mappers import build_placeholder_inventory

    ph = build_placeholder_inventory(MetricsInput.model_validate(_EX["linux_metrics"]))
    assert ph.hostname == str(ph.agent_id)
    assert ph.block_devices == []


def test_inventory_reproduction_mapped() -> None:
    """재현 필드 발행 payload — 스칼라 passthrough + boot dict/nonblock_mounts list 조립."""
    payload = copy.deepcopy(_EX["linux_inventory"])
    payload.update(
        {
            "arch": "x86_64",
            "bits": 64,
            "boot_firmware": "uefi",
            "secure_boot": True,
            "edition": None,
            "product_name": "Windows Server 2019 Standard",
            "timezone": "Asia/Seoul",
            "rtc_utc": True,
            "boot": {"kernel_cmdline": "BOOT_IMAGE=/vmlinuz root=LABEL=root ro", "root_ref_type": "label"},
            "nonblock_mounts": [
                {
                    "source": "tmpfs",
                    "target": "/run",
                    "fstype": "tmpfs",
                    "options": ["rw", "nosuid"],
                    "fs_freq": 0,
                    "fs_passno": 0,
                }
            ],
        }
    )
    inv = to_inventory_create(InventoryInput.model_validate(payload))
    assert inv.arch == "x86_64"
    assert inv.bits == 64
    assert inv.rtc_utc is True
    assert inv.product_name == "Windows Server 2019 Standard"  # 무가공 passthrough (엔진이 os_display 파싱)
    assert inv.boot == {
        "kernel_cmdline": "BOOT_IMAGE=/vmlinuz root=LABEL=root ro",
        "root_ref_type": "label",
        "grub_install_target": None,  # 미발행 -> None 이지만 키는 항상 present
    }
    assert inv.nonblock_mounts == [
        {
            "source": "tmpfs",
            "target": "/run",
            "fstype": "tmpfs",
            "options": ["rw", "nosuid"],
            "fs_freq": 0,
            "fs_passno": 0,
        }
    ]


def test_inventory_reproduction_absent_none() -> None:
    """재현 필드 미발행 payload — None 분기 (data.boot/nonblock_mounts is None -> None)."""
    inv = _inv("linux_inventory")  # fixture 에 재현 필드 없음
    assert inv.arch is None
    assert inv.bits is None
    assert inv.rtc_utc is None
    assert inv.boot is None
    assert inv.nonblock_mounts is None
