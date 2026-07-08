"""Windows agent wire payload Pydantic 검증 (payload-schema v3.3 — Linux/Windows 동일 계약).

Windows 고유값이 InventoryInput/MetricsInput wire 검증을 통과하고 DTO 로 매핑돼 reject/truncate/손실
0 인지 회귀 가드 — POSIX uid 부재(null), OS 개념 부재(cpu_stat nice/iowait/irq/softirq/steal null · load null ·
mem buffers/cached null), PhysicalDrive 디바이스명, NDIS 긴 net interface, mac_addresses.
cpu_stat 은 int|None — Windows null 발행이 non-null 스키마로 DLQ 되던 결함(#C1) 회귀 가드. 스키마를
좁히는 변경(예: cpu_stat required-실값化, load not-null)이 Windows 호환을 깨면 본 테스트가 잡는다.
"""

import json

from assessment_engine.consumer.mappers import to_inventory_create, to_metric_create
from assessment_engine.consumer.schemas import InventoryInput, MetricsInput

_LONG_IFACE = "X" * 200  # WFP/QoS 필터 드라이버 체인 이름 (NDIS 256 한계 내)
_MACS = ["02:42:ac:11:00:03", "fa:16:3e:7a:1b:5c"]


def _meta() -> dict:
    return {
        "agent_id": "00000000-0000-4000-8000-000000000001",
        "composite_id": "a" * 64,
        "machine_id": "12345678-1234-1234-1234-123456789abc",  # Windows MachineGuid
        "agent_version": "4.0.0",
        "collected_at": "2026-05-27T00:00:00Z",
        "hostname": "WIN-HOST",
        "message_id": "00000000-0000-4000-8000-000000000001",
        "agent_started_at": "2026-05-27T00:00:00Z",
        "boot_time": "2026-05-26T00:00:00Z",
    }


def _windows_inventory() -> dict:
    return {
        **_meta(),
        "message_type": "inventory",
        "os_family": "windows",
        "os_id": "windows",
        "os_version": "23H2",
        "os_codename": None,
        "kernel_version": "22631.3155",
        "cpu_cores": 4,
        "cpu_model": "Intel(R) Core(TM) i7",
        "mem_total_kb": 8388608,
        "swap_total_kb": 0,
        # 구조화 interface — Windows coarse kind(physical/loopback/tunnel/virtual), gateway 있음.
        "interfaces": [
            {
                "name": "Ethernet0",
                "address": "10.0.0.5",
                "prefix": 24,
                "family": "ipv4",
                "kind": "physical",
                "gateway": "10.0.0.1",
            }
        ],
        "ip_external": None,
        "mac_addresses": _MACS,
        "disks": [{"name": "PhysicalDrive0", "size_bytes": 256000000000, "type": None, "major": None, "minor": None}],
        "mounts": [
            {
                "mount": "C:\\",
                "total_bytes": 256000000000,
                "free_bytes": 128000000000,
                "avail_bytes": 128000000000,
                "fstype": "NTFS",
                "major": None,
                "minor": None,
            }
        ],
        "services": [],  # Windows SCM 성공, running 0개 — 빈 배열 (null 아님)
        "listen_ports": [{"proto": "tcp", "addr": "0.0.0.0", "port": 445, "uid": None, "pid": 4, "comm": "System"}],
    }


def _windows_metrics() -> dict:
    return {
        **_meta(),
        "message_type": "metrics",
        "os_family": "windows",
        # GetSystemTimes — user/system/idle 실값, nice/iowait/irq/softirq/steal 은 OS 개념 부재로 null (0 날조 금지)
        "cpu_stat": {
            "user": 1000,
            "nice": None,
            "system": 500,
            "idle": 8000,
            "iowait": None,
            "irq": None,
            "softirq": None,
            "steal": None,
        },
        "mem_total_kb": 8388608,
        "mem_free_kb": 4000000,
        "mem_available_kb": 4000000,
        "mem_buffers_kb": None,  # Windows 1:1 대응 부재
        "mem_cached_kb": None,
        "swap_total_kb": 0,
        "swap_free_kb": 0,
        "load_1m": None,  # Windows load average 부재
        "load_5m": None,
        "load_15m": None,
        "disk_io": [
            {
                "device": "PhysicalDrive0",
                "reads_completed": 100,
                "writes_completed": 50,
                "sectors_read": 2000,
                "sectors_written": 1000,
                "major": None,
                "minor": None,
            }
        ],
        "mounts": [
            {"mount": "C:\\", "total_bytes": 256000000000, "free_bytes": 128000000000, "avail_bytes": 128000000000}
        ],
        "net_io": [
            {
                "interface": _LONG_IFACE,
                "rx_bytes": 1000,
                "tx_bytes": 2000,
                "rx_packets": 10,
                "tx_packets": 20,
                "rx_errors": 0,
                "tx_errors": 0,
            }
        ],
        # Windows saturation — 물리 디스크별 큐만 채우고 cpu_run_queue/mem_paging_rate 는 null.
        # 엔진은 per-device max(디스크당 임계) 로 축약 (합 아님).
        "saturation": {
            "disk_queue": [
                {"device": "PhysicalDrive0", "queue": 1.5},
                {"device": "PhysicalDrive1", "queue": 3.0},
            ],
            "cpu_run_queue": None,
            "mem_paging_rate": None,
        },
    }


# ─── inventory wire 검증 ────────────────────────────────────────────────────


def test_windows_inventory_wire_parses() -> None:
    data = InventoryInput.model_validate_json(json.dumps(_windows_inventory()))
    assert data.os_family == "windows"
    assert data.mac_addresses == _MACS
    assert data.listen_ports[0].uid is None  # POSIX uid 부재
    assert data.disks[0].name == "PhysicalDrive0"
    assert data.services == []  # SCM 성공 0개 — null 아님


def test_windows_inventory_to_dto_preserves_mac() -> None:
    data = InventoryInput.model_validate_json(json.dumps(_windows_inventory()))
    dto = to_inventory_create(data)
    assert dto.mac_addresses == _MACS
    assert dto.os_family == "windows"
    # 구조화 interface 가 DTO JSONB payload 로 손실 없이 매핑 (bare address + prefix + coarse kind + gateway).
    assert dto.interfaces == [
        {
            "name": "Ethernet0",
            "address": "10.0.0.5",
            "prefix": 24,
            "family": "ipv4",
            "kind": "physical",
            "gateway": "10.0.0.1",
        }
    ]


def test_mac_addresses_defaults_empty_when_absent() -> None:
    payload = _windows_inventory()
    del payload["mac_addresses"]  # 미발행 시 빈 배열 default
    data = InventoryInput.model_validate_json(json.dumps(payload))
    assert data.mac_addresses == []


# ─── metrics wire 검증 ──────────────────────────────────────────────────────


def test_windows_metrics_wire_parses() -> None:
    data = MetricsInput.model_validate_json(json.dumps(_windows_metrics()))
    assert data.os_family == "windows"
    assert data.cpu_stat is not None
    assert data.cpu_stat.user == 1000 and data.cpu_stat.idle == 8000  # 실측 축 보존
    assert data.cpu_stat.iowait is None and data.cpu_stat.steal is None  # OS 개념 부재 null (0 아님, #C1)
    assert data.load_1m is None and data.load_15m is None
    assert data.mem_cached_kb is None
    assert data.net_io[0].interface == _LONG_IFACE  # 긴 인터페이스 수용
    assert data.disk_io[0].device == "PhysicalDrive0"
    # Windows saturation — disk_queue 만 채우고 cpu/mem 축은 null.
    assert data.saturation is not None
    assert data.saturation.cpu_run_queue is None and data.saturation.mem_paging_rate is None
    assert [e.queue for e in data.saturation.disk_queue] == [1.5, 3.0]


def test_windows_metrics_to_dto() -> None:
    data = MetricsInput.model_validate_json(json.dumps(_windows_metrics()))
    dto = to_metric_create(data)
    assert dto.cpu_user == 1000 and dto.cpu_idle == 8000  # 실측 축 보존
    assert dto.cpu_iowait is None and dto.cpu_steal is None  # OS 개념 부재 null 전파 (#C1)
    assert dto.load_1m is None
    assert dto.mem_cached_kb is None
    assert dto.disk_io[0].device == "PhysicalDrive0"
    # saturation 은 per-device max 로 축약 저장 (합 아님) — 3.0 이 가장 바쁜 디스크.
    assert dto.sat_disk_queue == 3.0
    assert dto.sat_cpu_run_queue is None
    assert dto.sat_mem_paging_rate is None


def test_windows_metrics_saturation_measured_path() -> None:
    """perflib 실측 시 cpu_run_queue(Processor Queue Length)·mem_paging_rate(Pages/sec)가 DTO 로 손실 없이 전파.

    disk_queue 만 채우던 미측정 경로(위)와 대비 — S1/S2 로 엔진이 세 축을 os-aware 소비하려면 ingest 가
    raw 값을 보존해야 한다. rate 환산·임계는 엔진 SQL/recommendation 몫(여기선 raw 저장만 검증).
    """
    payload = _windows_metrics()
    payload["saturation"] = {
        "disk_queue": [{"device": "PhysicalDrive0", "queue": 0.5}],
        "cpu_run_queue": 12.0,  # Processor Queue Length gauge
        "mem_paging_rate": 250000.0,  # Pages/sec 누적 counter (엔진이 delta/dt 로 rate 환산)
    }
    data = MetricsInput.model_validate_json(json.dumps(payload))
    assert data.saturation is not None
    assert data.saturation.cpu_run_queue == 12.0 and data.saturation.mem_paging_rate == 250000.0
    dto = to_metric_create(data)
    assert dto.sat_cpu_run_queue == 12.0
    assert dto.sat_mem_paging_rate == 250000.0
    assert dto.sat_disk_queue == 0.5  # per-device max (단일 디스크)


def test_long_net_interface_within_limit() -> None:
    """net interface 256 한계 내 긴 이름 수용 (c5f9a3b1e7d2 widen 회귀 가드)."""
    payload = _windows_metrics()
    payload["net_io"][0]["interface"] = "Y" * 256
    data = MetricsInput.model_validate_json(json.dumps(payload))
    assert len(data.net_io[0].interface) == 256
