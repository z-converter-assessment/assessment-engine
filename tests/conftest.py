from datetime import datetime, timezone

import pytest

from db.repositories.dto import ServerDTO, ServerMetricDTO
from web.api.items import DiskInfo, MetricHistoryItem, ServerItem


@pytest.fixture
def server_dto():
    now = datetime.now(timezone.utc)
    return ServerDTO(id=1, hostname="server01", created_at=now, updated_at=now)


@pytest.fixture
def metric_dto():
    now = datetime.now(timezone.utc)
    return ServerMetricDTO(
        id=1, server_id=1,
        recorded_at=now, created_at=now,
        nproc=4, mem_total_mb=8192,
        disks=[{"name": "vda", "size": "30G"}],
        ip_internal=["10.0.0.1"],
        ip_external=["1.2.3.4"],
    )


@pytest.fixture
def server_item():
    now = datetime.now(timezone.utc)
    return ServerItem(
        id=1, hostname="server01",
        updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        nproc=4, mem_total_mb=8192,
        disks=[DiskInfo(name="vda", size="30G")],
        ip_internal=["10.0.0.1"],
        ip_external=["1.2.3.4"],
    )


@pytest.fixture
def history_item():
    now = datetime.now(timezone.utc)
    return MetricHistoryItem(
        recorded_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        nproc=4, mem_total_mb=8192,
        disks=[DiskInfo(name="vda", size="30G")],
        ip_internal=["10.0.0.1"],
        ip_external=["1.2.3.4"],
    )