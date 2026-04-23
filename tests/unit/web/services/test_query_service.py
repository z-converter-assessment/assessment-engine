"""
QueryService 행동 테스트.
DTO 필드 값은 검증하지 않는다 — 반환 여부(None / not None)와 컬렉션 크기에만 집중.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest

from db.repositories.base_query_repository import ServerDTO, ServerMetricDTO
from web.services.query_service import QueryService, _fmt

_KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# 헬퍼 — 최소 필드만 채운 DTO 생성
# ---------------------------------------------------------------------------

def _server(server_id: int = 1, hostname: str = "host01") -> ServerDTO:
    now = datetime.now(timezone.utc)
    return ServerDTO(
        id=server_id, hostname=hostname,
        cpu_cores=4, mem_total_mb=8192,
        created_at=now, updated_at=now,
    )


def _metric(server_id: int = 1) -> ServerMetricDTO:
    now = datetime.now(timezone.utc)
    return ServerMetricDTO(
        id=1, server_id=server_id, collected_at=now, created_at=now,
        cpu_user_pct=20.0, cpu_system_pct=5.0, cpu_iowait_pct=1.0,
        mem_used_mb=4096, mem_available_mb=None, swap_used_mb=0,
        disk_read_iops=None, disk_write_iops=None,
        disk_read_bytes=None, disk_write_bytes=None,
        disk_usage=[], net_rx_bytes=None, net_tx_bytes=None,
        load_1m=1.0,
    )


@pytest.fixture
def repo() -> AsyncMock:
    r = AsyncMock()
    r.list_all = AsyncMock(return_value=[])
    r.get_by_id = AsyncMock(return_value=None)
    r.latest_metric = AsyncMock(return_value=None)
    r.metric_history = AsyncMock(return_value=[])
    return r


# ---------------------------------------------------------------------------
# _fmt
# ---------------------------------------------------------------------------

class TestFmt:
    def test_utc_converted_to_kst(self):
        dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert _fmt(dt) == "2024-01-01 09:00:00"

    def test_output_format(self):
        dt = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
        assert _fmt(dt) == "2024-06-15 21:30:45"


# ---------------------------------------------------------------------------
# list_servers
# ---------------------------------------------------------------------------

class TestListServers:
    async def test_empty_repo_returns_empty(self, repo):
        assert await QueryService(repo).list_servers() == []

    async def test_server_without_metric_excluded(self, repo):
        repo.list_all.return_value = [_server()]
        repo.latest_metric.return_value = None
        assert await QueryService(repo).list_servers() == []

    async def test_server_with_metric_included(self, repo):
        repo.list_all.return_value = [_server(server_id=1)]
        repo.latest_metric.return_value = _metric(server_id=1)
        result = await QueryService(repo).list_servers()
        assert len(result) == 1

    async def test_multiple_servers_all_included(self, repo):
        repo.list_all.return_value = [_server(1, "a"), _server(2, "b")]
        repo.latest_metric.side_effect = [_metric(1), _metric(2)]
        assert len(await QueryService(repo).list_servers()) == 2

    async def test_partial_metrics_excludes_missing(self, repo):
        repo.list_all.return_value = [_server(1), _server(2)]
        repo.latest_metric.side_effect = [_metric(1), None]
        assert len(await QueryService(repo).list_servers()) == 1


# ---------------------------------------------------------------------------
# get_server
# ---------------------------------------------------------------------------

class TestGetServer:
    async def test_server_not_found_returns_none(self, repo):
        assert await QueryService(repo).get_server(1) is None

    async def test_server_without_metric_returns_none(self, repo):
        repo.get_by_id.return_value = _server()
        assert await QueryService(repo).get_server(1) is None

    async def test_server_with_metric_returns_item(self, repo):
        repo.get_by_id.return_value = _server()
        repo.latest_metric.return_value = _metric()
        assert await QueryService(repo).get_server(1) is not None

    async def test_returned_item_has_correct_hostname(self, repo):
        repo.get_by_id.return_value = _server(hostname="web-01")
        repo.latest_metric.return_value = _metric()
        result = await QueryService(repo).get_server(1)
        assert result.hostname == "web-01"


# ---------------------------------------------------------------------------
# get_history
# ---------------------------------------------------------------------------

class TestGetHistory:
    async def test_server_not_found_returns_none(self, repo):
        assert await QueryService(repo).get_history(1) is None

    async def test_returns_tuple_of_server_and_list(self, repo):
        repo.get_by_id.return_value = _server()
        repo.metric_history.return_value = [_metric(), _metric()]
        result = await QueryService(repo).get_history(1)
        assert result is not None
        server, history = result
        assert len(history) == 2

    async def test_empty_history_returns_empty_list(self, repo):
        repo.get_by_id.return_value = _server()
        repo.metric_history.return_value = []
        _, history = await QueryService(repo).get_history(1)
        assert history == []