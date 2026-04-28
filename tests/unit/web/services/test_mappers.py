from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from db.repositories.outbound import (
    CollectionStatusResponse,
    MetricSeriesResponse,
    MountUsageRaw,
    NetIoRaw,
    NetworkWithIoResponse,
    ServerListItemResponse,
    ServerResponse,
    StorageWithUsageResponse,
)
from web.services.mappers import (
    to_collection_status_item,
    to_metric_series_item,
    to_network_detail,
    to_server_detail,
    to_server_list_item,
    to_storage_detail,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _list_dto(**kwargs) -> ServerListItemResponse:
    defaults = dict(id=1, machine_id="mid", hostname="host-01",
                    os_id="ubuntu", os_version="22.04",
                    cpu_cores=4, mem_total_kb=8_000_000, last_seen_at=_NOW)
    defaults.update(kwargs)
    return ServerListItemResponse(**defaults)


def _server_dto(**kwargs) -> ServerResponse:
    defaults = dict(
        id=1, machine_id="mid", hostname="host-01", agent_version="1.0",
        os_id="ubuntu", os_version="22.04", os_codename="jammy",
        kernel_version="5.15", cpu_cores=4, cpu_model="Intel",
        mem_total_kb=2_097_152,  # 2GB in KB
        swap_total_kb=1_048_576,  # 1GB in KB
        boot_time=None, ip_internal=["10.0.0.1"], ip_external=None,
        disks=[{"name": "sda", "size_bytes": 107_374_182_400, "type": "SSD"}],
        mounts=[], last_seen_at=_NOW,
    )
    defaults.update(kwargs)
    return ServerResponse(**defaults)


class TestToServerListItem:
    def test_fields_mapped_correctly(self):
        dto = _list_dto()
        item = to_server_list_item(dto)
        assert item.id == 1
        assert item.hostname == "host-01"
        assert item.last_seen_at == _NOW

    def test_mem_converted_kb_to_gb(self):
        dto = _list_dto(mem_total_kb=1_048_576)  # 1 GB = 1024^2 KB
        item = to_server_list_item(dto)
        assert item.mem_total_gb == 1.0

    def test_is_online_defaults_false(self):
        item = to_server_list_item(_list_dto())
        assert item.is_online is False


class TestToServerDetail:
    def test_all_fields_mapped(self):
        dto = _server_dto()
        detail = to_server_detail(dto)
        assert detail.machine_id == "mid"
        assert detail.hostname == "host-01"
        assert detail.cpu_cores == 4

    def test_disk_size_converted_bytes_to_gb(self):
        dto = _server_dto(disks=[{"name": "sda", "size_bytes": 1_073_741_824, "type": "SSD"}])
        detail = to_server_detail(dto)
        assert detail.disks[0].size_gb == 1.0

    def test_mem_and_swap_converted_to_gb(self):
        dto = _server_dto(mem_total_kb=1_048_576, swap_total_kb=524_288)
        detail = to_server_detail(dto)
        assert detail.mem_total_gb == 1.0
        assert detail.swap_total_gb == 0.5


class TestToStorageDetail:
    def _storage_dto(self, inventory_mounts=None, mount_usage=None):
        return StorageWithUsageResponse(
            server_id=1,
            hostname="host-01",
            disks=[],
            inventory_mounts=inventory_mounts or [],
            mount_usage=mount_usage or [],
        )

    def test_inventory_mounts_merged_with_live_usage(self):
        inv_mounts = [{"mount": "/", "fstype": "ext4", "total_bytes": 100_000_000_000}]
        live = [MountUsageRaw(mount="/", total_bytes=100_000_000_000, avail_bytes=60_000_000_000, free_bytes=60_000_000_000)]
        dto = self._storage_dto(inv_mounts, live)
        result = to_storage_detail(dto)
        assert len(result.mounts) == 1
        assert result.mounts[0].usage_pct is not None

    def test_live_only_mount_appended(self):
        live = [MountUsageRaw(mount="/data", total_bytes=50_000_000_000, avail_bytes=25_000_000_000, free_bytes=25_000_000_000)]
        dto = self._storage_dto([], live)
        result = to_storage_detail(dto)
        assert any(m.mount == "/data" for m in result.mounts)

    def test_mounts_sorted_by_path(self):
        inv_mounts = [
            {"mount": "/var", "fstype": "ext4", "total_bytes": 100},
            {"mount": "/",    "fstype": "ext4", "total_bytes": 200},
        ]
        dto = self._storage_dto(inv_mounts, [])
        result = to_storage_detail(dto)
        assert result.mounts[0].mount == "/"
        assert result.mounts[1].mount == "/var"

    def test_no_usage_data_yields_none_pct(self):
        inv_mounts = [{"mount": "/", "fstype": "ext4", "total_bytes": 100}]
        dto = self._storage_dto(inv_mounts, [])
        result = to_storage_detail(dto)
        assert result.mounts[0].usage_pct is None


class TestToNetworkDetail:
    def test_fields_mapped_correctly(self):
        dto = NetworkWithIoResponse(
            server_id=1, hostname="host-01",
            ip_internal=["10.0.0.1"], ip_external=None, net_io=[],
        )
        result = to_network_detail(dto)
        assert result.server_id == 1
        assert result.ip_internal == ["10.0.0.1"]

    def test_net_io_delta_delegated_to_compute_net_io(self):
        net_rows = [
            NetIoRaw("eth0", _NOW, 2048, 1024, 20, 10, 0, 0),
        ]
        dto = NetworkWithIoResponse(
            server_id=1, hostname="h", ip_internal=[], ip_external=None, net_io=net_rows,
        )
        with patch("web.services.mappers.compute_net_io") as mock_fn:
            mock_fn.return_value = []
            to_network_detail(dto)
            mock_fn.assert_called_once_with(net_rows)


class TestToCollectionStatusItem:
    def test_is_online_flag_set(self):
        dto = CollectionStatusResponse(last_metric_at=_NOW, last_inventory_at=_NOW)
        item = to_collection_status_item(dto, is_online=True)
        assert item.is_online is True

    def test_timestamps_passed_through(self):
        dto = CollectionStatusResponse(last_metric_at=_NOW, last_inventory_at=None)
        item = to_collection_status_item(dto, is_online=False)
        assert item.last_metric_at == _NOW
        assert item.last_inventory_at is None


class TestToMetricSeriesItem:
    def test_fields_mapped_correctly(self):
        dto = MetricSeriesResponse(collected_at=_NOW, value=42.5, dimension="sda")
        item = to_metric_series_item(dto)
        assert item.collected_at == _NOW
        assert item.value == 42.5
        assert item.dimension == "sda"