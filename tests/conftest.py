from datetime import datetime, timezone

import pytest

from db.repositories.base_query_repository import ServerDTO, ServerMetricDTO
from web.api.view_models import ServerItem, MetricHistoryItem


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def server_dto():
    now = _now()
    return ServerDTO(
        id=1, hostname="server01", cpu_cores=4, mem_total_mb=8192,
        created_at=now, updated_at=now,
    )


@pytest.fixture
def metric_dto():
    now = _now()
    return ServerMetricDTO(
        id=1, server_id=1, collected_at=now, created_at=now,
        cpu_user_pct=20.0, cpu_system_pct=5.0, cpu_iowait_pct=1.0,
        mem_used_mb=4096, mem_available_mb=4096, swap_used_mb=0,
        disk_read_iops=None, disk_write_iops=None,
        disk_read_bytes=None, disk_write_bytes=None,
        disk_usage=[], net_rx_bytes=None, net_tx_bytes=None,
        load_1m=1.0,
    )


@pytest.fixture
def server_item():
    return ServerItem(
        id=1, hostname="server01", cpu_cores=4, mem_total_mb=8192,
        updated_at="2026-04-24 10:00:00",
        cpu_user_pct=20.0, mem_used_mb=4096, load_1m=1.0,
    )


@pytest.fixture
def history_item():
    return MetricHistoryItem(
        collected_at="2026-04-24 10:00:00",
        cpu_user_pct=20.0, cpu_system_pct=5.0, cpu_iowait_pct=1.0,
        mem_used_mb=4096, swap_used_mb=0, load_1m=1.0, disk_usage=[],
    )
