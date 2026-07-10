"""Windows agent wire payload Pydantic 검증 (wire — Linux/Windows 동일 계약).

Windows 고유값이 InventoryInput/MetricsInput wire 검증을 통과하고 DTO 로 매핑돼 reject/truncate/손실
0 인지 회귀 가드 — POSIX uid 부재(null), OS 개념 부재(cpu.time nice/iowait/irq/softirq/steal 미발행 point ·
memory.usage buffered/cached 미발행 · PSI 부재로 system.pressure null), gptid/mbrsig block_device 식별자,
NDIS 긴 net interface 이름(net_interfaces[].name), MAC 안정키(net_interface id, id_type=mac).

v2 는 상태/축 부재를 point 미발행 또는 value null 로 표현 — 엔진이 이를 0 으로 날조하지 않고 None 보존해야 한다
(#B·#C1). 스키마를 좁히는 변경(예: 특정 state required, pressure not-null)이 Windows 호환을 깨면 본 테스트가 잡는다.
"""

import json

from assessment_engine.consumer.mappers import to_inventory_create, to_metric_create
from assessment_engine.consumer.schemas import InventoryInput, MetricsInput

_MAC = "02:42:ac:11:00:03"  # net_interface 안정키 (id, id_type=mac). v1 mac_addresses 폐기
_NET_DEV = f"mac:{_MAC}"  # metrics network.io device 축 = "mac:"+MAC (inventory id 와 조인 정합)
# metrics device 축 = inventory (id_type):(id) 재구성값.
_DISK0 = "gptid:{3484c6ca-d135-4483-a716-9207f855c8db}"  # PhysicalDrive0 (GPT signature)
_DISK1 = "mbrsig:2579770672"  # 두 번째 물리 디스크 (MBR signature)


def _meta() -> dict:
    return {
        "schema_version": "1.0",
        "agent_id": "00000000-0000-4000-8000-000000000001",
        "composite_id": "a" * 64,
        "machine_id": "12345678-1234-1234-1234-123456789abc",  # Windows MachineGuid
        "agent_version": "2.0.0",
        "collected_at": "2026-05-27T00:00:00Z",
        "message_id": "00000000-0000-4000-8000-000000000001",
        "agent_started_at": "2026-05-27T00:00:00Z",
        "boot_time": "2026-05-26T00:00:00Z",
    }


def _windows_inventory() -> dict:
    return {
        **_meta(),
        "message_type": "inventory",
        "os_family": "windows",
        "hostname": "WIN-HOST",
        "os_id": "windows",
        "os_version": "10.0.14393",
        "os_codename": None,
        "kernel_version": "14393",
        "cpu_cores": 4,
        "cpu_model": "Intel(R) Core(TM) i7",
        "mem_total_bytes": 8589934592,  # v2 By (v1 mem_total_kb 폐기)
        "ip_external": None,
        # 정규화 block_device — Windows 물리 디스크(type=disk, gptid/mbrsig 식별자) + 볼륨(type=volume).
        # swap 은 v1 컬럼이 아니라 type=swap 노드 (여기선 미포함).
        "block_devices": [
            {
                "name": "PhysicalDrive0", "type": "disk", "size_bytes": 256000000000,
                "fstype": None, "mountpoint": None, "parent": None,
                "id": "{3484c6ca-d135-4483-a716-9207f855c8db}", "id_type": "gptid",
            },
            {
                "name": "C:", "type": "volume", "size_bytes": 256000000000,
                "fstype": "ntfs", "mountpoint": "C:",
                "parent": "{3484c6ca-d135-4483-a716-9207f855c8db}",
                "id": "{86c0d2cb-c087-4dd4-aafb-671200504e25}", "id_type": "volguid",
            },
        ],
        # 안정키 id=MAC + 다중 IP addresses[]. name 은 표시용(coarse kind physical, gateway 있음).
        "net_interfaces": [
            {
                "name": "Ethernet0", "id": _MAC, "id_type": "mac", "kind": "physical",
                "speed_mbps": None,
                "addresses": [{"address": "10.0.0.5", "prefix": 24, "family": "ipv4"}],
                "gateway": "10.0.0.1",
            }
        ],
        "lvm_vgs": [],  # Windows 미발행
        "services": [],  # Windows SCM 성공, running 0개 — 빈 배열 (null 아님)
        "listen_ports": [{"proto": "tcp", "addr": "0.0.0.0", "port": 445, "uid": None, "pid": 4, "comm": "System"}],
    }


def _windows_metrics() -> dict:
    return {
        **_meta(),
        "message_type": "metrics",
        "os_family": "windows",
        # GetSystemTimes — user/system/idle 실값 point, nice/iowait/irq/softirq/steal 은 OS 개념 부재로 point 미발행
        # (0 날조 금지 #C1). cpu.run_queue 는 미측정 경로라 value null.
        "system.cpu": {
            "cpu.time": {
                "type": "counter", "unit": "s",
                "points": [
                    {"attr": {"cpu": "0", "state": "user"}, "value": 1000.0},
                    {"attr": {"cpu": "0", "state": "system"}, "value": 500.0},
                    {"attr": {"cpu": "0", "state": "idle"}, "value": 8000.0},
                ],
            },
            "cpu.logical.count": {"type": "gauge", "unit": "cpu", "points": [{"attr": {}, "value": 4}]},
            "cpu.run_queue": {
                "type": "gauge", "unit": "tasks",
                "points": [{"attr": {"source": "processor_queue"}, "value": None}],
            },
        },
        # available 실값, cached/buffered 는 Windows 1:1 대응 부재로 point 미발행.
        "system.memory": {
            "memory.usage": {
                "type": "gauge", "unit": "By",
                "points": [{"attr": {"state": "available"}, "value": 4000000000}],
            },
            "memory.limit": {"type": "gauge", "unit": "By", "points": [{"attr": {}, "value": 8589934592}]},
        },
        # Windows 물리 디스크별 큐(disk.pending_operations gauge) — v1 saturation.disk_queue 대체.
        # v2 는 device별 보존 (v1 per-device max 축약 sat_disk_queue 폐기).
        "system.disk": {
            "disk.io": {
                "type": "counter", "unit": "By",
                "points": [{"attr": {"device": _DISK0, "direction": "write"}, "value": 512000}],
            },
            "disk.pending_operations": {
                "type": "gauge", "unit": "operations",
                "points": [
                    {"attr": {"device": _DISK0}, "value": 1.5},
                    {"attr": {"device": _DISK1}, "value": 3.0},
                ],
            },
        },
        "system.network": {
            "network.io": {
                "type": "counter", "unit": "By",
                "points": [
                    {"attr": {"device": _NET_DEV, "direction": "receive"}, "value": 1000},
                    {"attr": {"device": _NET_DEV, "direction": "transmit"}, "value": 2000},
                ],
            },
            "network.link.speed": {  # 가상 NIC — link speed 미측정 null
                "type": "gauge", "unit": "bit/s",
                "points": [{"attr": {"device": _NET_DEV}, "value": None}],
            },
        },
        # 미측정 경로 — Pages/sec(paging.operations) value null.
        "system.paging": {
            "paging.operations": {
                "type": "counter", "unit": "operations",
                "points": [{"attr": {"direction": "in"}, "value": None}],
            },
        },
        "system.pressure": None,  # Windows PSI 미지원 (키 present + 값 null)
    }


# --- inventory wire 검증 ---


def test_windows_inventory_wire_parses() -> None:
    data = InventoryInput.model_validate_json(json.dumps(_windows_inventory()))
    assert data.os_family == "windows"
    # v2 MAC = net_interface id (id_type=mac). v1 mac_addresses 폐기.
    assert data.net_interfaces[0].id == _MAC and data.net_interfaces[0].id_type == "mac"
    assert data.listen_ports[0].uid is None  # POSIX uid 부재
    assert data.block_devices[0].name == "PhysicalDrive0" and data.block_devices[0].id_type == "gptid"
    assert data.services == []  # SCM 성공 0개 — null 아님
    assert data.lvm_vgs == []  # Windows LVM 미발행


def test_windows_inventory_to_dto_preserves_mac() -> None:
    data = InventoryInput.model_validate_json(json.dumps(_windows_inventory()))
    dto = to_inventory_create(data)
    assert dto.os_family == "windows"
    assert dto.mem_total_bytes == 8589934592  # v2 By
    # MAC 은 v2 net_interfaces[].id(id_type=mac)로 JSONB 손실 없이 매핑 (v1 mac_addresses 폐기).
    ni = dto.net_interfaces[0]
    assert ni["id"] == _MAC and ni["id_type"] == "mac"
    assert ni["name"] == "Ethernet0" and ni["kind"] == "physical" and ni["gateway"] == "10.0.0.1"
    # 구조화 addresses(bare address + prefix + family) 손실 없이 매핑.
    assert ni["addresses"] == [{"address": "10.0.0.5", "prefix": 24, "family": "ipv4"}]


def test_net_interfaces_default_empty_when_absent() -> None:
    payload = _windows_inventory()
    del payload["net_interfaces"]  # 미발행 시 빈 배열 default
    data = InventoryInput.model_validate_json(json.dumps(payload))
    assert data.net_interfaces == []


# --- metrics wire 검증 ---


def test_windows_metrics_wire_parses() -> None:
    data = MetricsInput.model_validate_json(json.dumps(_windows_metrics()))
    assert data.os_family == "windows"
    # 필수 4 네임스페이스 present.
    assert data.system_cpu is not None and data.system_memory is not None
    assert data.system_disk is not None and data.system_network is not None
    assert "cpu.time" in data.system_cpu
    # cpu.time 은 실측 축(user/idle)만 point 발행, iowait/steal 은 OS 개념 부재로 미발행 (0 날조 금지 #C1).
    states = {p.attr.get("state") for p in data.system_cpu["cpu.time"].points}
    assert "user" in states and "idle" in states
    assert "iowait" not in states and "steal" not in states
    assert data.system_pressure is None  # Windows PSI 미지원


def test_windows_metrics_to_dto() -> None:
    data = MetricsInput.model_validate_json(json.dumps(_windows_metrics()))
    dto = to_metric_create(data)
    assert dto.cpu_user_s == 1000.0 and dto.cpu_idle_s == 8000.0  # 실측 축 보존
    assert dto.cpu_iowait_s is None and dto.cpu_steal_s is None  # OS 개념 부재 null 전파 (#C1)
    assert dto.cpu_run_queue is None  # 미측정 경로 (value null 보존)
    assert dto.mem_available_bytes == 4000000000
    assert dto.mem_cached_bytes is None and dto.mem_buffered_bytes is None  # Windows 1:1 대응 부재
    # 디스크 큐 = device별 disk.pending_operations 보존 (v1 sat_disk_queue per-device max 축약 폐기).
    pending = {e.device_id: e.pending_ops for e in dto.disk_io}
    assert pending == {_DISK0: 1.5, _DISK1: 3.0}


def test_windows_metrics_saturation_measured_path() -> None:
    """perflib 실측 시 cpu.run_queue(Processor Queue Length)·paging.operations(Pages/sec)가 DTO 로 손실 없이 전파.

    미측정 경로(위, value null)와 대비 — 엔진이 세 축을 os-aware 소비하려면 ingest 가 raw 값을 보존해야 한다.
    v1 saturation(sat_cpu_run_queue·mem_paging_rate·sat_disk_queue) 폐기 — v2 는 cpu.run_queue gauge +
    per-device disk.pending_operations + paging.operations counter raw 저장. rate/임계는 엔진 SQL/recommendation 몫.
    """
    payload = _windows_metrics()
    payload["system.cpu"]["cpu.run_queue"]["points"] = [
        {"attr": {"source": "processor_queue"}, "value": 12.0}  # Processor Queue Length gauge
    ]
    payload["system.disk"]["disk.pending_operations"]["points"] = [{"attr": {"device": _DISK0}, "value": 0.5}]
    payload["system.paging"]["paging.operations"]["points"] = [
        {"attr": {"direction": "in"}, "value": 250000}  # Pages/sec 누적 counter (엔진이 delta/dt 로 rate 환산)
    ]
    data = MetricsInput.model_validate_json(json.dumps(payload))
    dto = to_metric_create(data)
    assert dto.cpu_run_queue == 12.0
    assert dto.paging_in == 250000
    assert [e.pending_ops for e in dto.disk_io] == [0.5]  # 단일 디스크 큐 보존


def test_long_net_interface_name_within_limit() -> None:
    """net_interface name 256 한계 내 긴 이름 수용 (NDIS/WFP 필터 체인 이름, widen 회귀 가드)."""
    payload = _windows_inventory()
    payload["net_interfaces"][0]["name"] = "Y" * 256
    data = InventoryInput.model_validate_json(json.dumps(payload))
    assert len(data.net_interfaces[0].name) == 256
