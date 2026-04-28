from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from web.deps import get_service
from web.main import app
from web.view_models import (
    CollectionStatusItem,
    MetricDashboard,
    MetricSeriesItem,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _make_service() -> MagicMock:
    svc = MagicMock()
    svc.get_collection_status = AsyncMock(return_value=[])
    svc.get_latest_metric = AsyncMock(return_value=None)
    svc.get_metric_snapshots = AsyncMock(return_value=[])
    svc.get_metric_chart = AsyncMock(return_value=[])
    return svc


def _client(svc: MagicMock) -> TestClient:
    app.dependency_overrides[get_service] = lambda: svc
    return TestClient(app, raise_server_exceptions=True)


def _dashboard() -> MetricDashboard:
    return MetricDashboard(
        collected_at=_NOW, cpu=None, load_1m=None, load_5m=None, load_15m=None,
        memory=None, swap=None, disk_io=[], net_io=[], mounts=[],
    )


class TestCollectionStatusApi:
    def test_returns_200_with_list(self):
        svc = _make_service()
        svc.get_collection_status = AsyncMock(return_value=[
            CollectionStatusItem(last_metric_at=_NOW, last_inventory_at=_NOW, is_online=True)
        ])
        client = _client(svc)
        resp = client.get("/api/v1/servers/1/collection-status")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_server_id_passed_to_service(self):
        svc = _make_service()
        client = _client(svc)
        client.get("/api/v1/servers/42/collection-status")
        svc.get_collection_status.assert_called_once_with(42)


class TestLatestMetricApi:
    def test_not_found_returns_404(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/api/v1/servers/1/metrics/latest")
        assert resp.status_code == 404

    def test_found_returns_200_json(self):
        svc = _make_service()
        svc.get_latest_metric = AsyncMock(return_value=_dashboard())
        client = _client(svc)
        resp = client.get("/api/v1/servers/1/metrics/latest")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_server_id_passed_to_service(self):
        svc = _make_service()
        client = _client(svc)
        client.get("/api/v1/servers/7/metrics/latest")
        svc.get_latest_metric.assert_called_once_with(7)


class TestMetricSnapshotsApi:
    def test_returns_200_list(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/api/v1/servers/1/metrics/snapshots")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_default_params_used(self):
        svc = _make_service()
        client = _client(svc)
        client.get("/api/v1/servers/1/metrics/snapshots")
        svc.get_metric_snapshots.assert_called_once_with(1, None, 10)

    def test_cursor_param_forwarded(self):
        svc = _make_service()
        client = _client(svc)
        client.get("/api/v1/servers/1/metrics/snapshots?cursor=2024-01-01T00:00:00")
        args = svc.get_metric_snapshots.call_args
        assert args[0][1] is not None  # cursor 전달됨

    def test_limit_param_forwarded(self):
        svc = _make_service()
        client = _client(svc)
        client.get("/api/v1/servers/1/metrics/snapshots?limit=25")
        svc.get_metric_snapshots.assert_called_once_with(1, None, 25)


class TestMetricChartApi:
    def test_missing_metric_type_returns_422(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/api/v1/servers/1/metrics/chart")
        assert resp.status_code == 422

    def test_invalid_metric_type_returns_422(self):
        svc = _make_service()
        client = _client(svc)
        resp = client.get("/api/v1/servers/1/metrics/chart?metric_type=invalid.type")
        assert resp.status_code == 422

    def test_returns_200_list(self):
        svc = _make_service()
        svc.get_metric_chart = AsyncMock(return_value=[
            MetricSeriesItem(collected_at=_NOW, value=50.0, dimension=None)
        ])
        client = _client(svc)
        resp = client.get("/api/v1/servers/1/metrics/chart?metric_type=cpu.usage_percent")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_default_time_params_used(self):
        svc = _make_service()
        client = _client(svc)
        client.get("/api/v1/servers/1/metrics/chart?metric_type=cpu.usage_percent")
        args = svc.get_metric_chart.call_args[0]
        assert args[3] == "1h"   # time_range 기본값
        assert args[4] == "5m"   # bucket 기본값
        assert args[5] == "avg"  # agg 기본값

    def test_all_params_forwarded_to_service(self):
        svc = _make_service()
        client = _client(svc)
        client.get(
            "/api/v1/servers/3/metrics/chart"
            "?metric_type=disk.read_iops&dimension=sda&time_range=6h&bucket=1h&agg=max"
        )
        args = svc.get_metric_chart.call_args[0]
        assert args[0] == 3
        assert args[1] == "disk.read_iops"
        assert args[2] == "sda"
        assert args[3] == "6h"
        assert args[4] == "1h"
        assert args[5] == "max"