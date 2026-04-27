"""
QueryRepository 통합 테스트.
모든 메서드가 NotImplementedError 상태이므로 ORM 모델 필드 정의 후 활성화한다.
"""
from datetime import datetime, timezone

import pytest

from db.repositories.query_repository import QueryRepository

pytestmark = pytest.mark.integration


class TestListServers:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_empty_db_returns_empty_list(self, db_session):
        repo = QueryRepository(db_session)
        result = await repo.list_servers(page=1, limit=20, search=None, is_online=None)
        assert result == []

    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_search_filters_results(self, db_session):
        pass

    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_is_online_filter(self, db_session):
        pass

    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_pagination(self, db_session):
        pass


class TestGetServer:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_not_found_returns_none(self, db_session):
        repo = QueryRepository(db_session)
        assert await repo.get_server(9999) is None

    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_found_returns_dto(self, db_session):
        pass


class TestGetStorage:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_not_found_returns_none(self, db_session):
        repo = QueryRepository(db_session)
        assert await repo.get_storage(9999) is None


class TestGetNetwork:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_not_found_returns_none(self, db_session):
        repo = QueryRepository(db_session)
        assert await repo.get_network(9999) is None


class TestGetCollectionStatus:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_returns_list(self, db_session):
        repo = QueryRepository(db_session)
        result = await repo.get_collection_status(1)
        assert isinstance(result, list)


class TestLatestMetric:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_no_metrics_returns_none(self, db_session):
        repo = QueryRepository(db_session)
        assert await repo.latest_metric(9999) is None


class TestMetricSnapshots:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_no_data_returns_empty_list(self, db_session):
        repo = QueryRepository(db_session)
        result = await repo.metric_snapshots(9999, cursor=None, limit=10)
        assert result == []

    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_cursor_pagination(self, db_session):
        pass


class TestMetricChart:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_no_data_returns_empty_list(self, db_session):
        repo = QueryRepository(db_session)
        result = await repo.metric_chart(
            server_id=9999,
            metric_type="cpu.usage_percent",
            dimension=None,
            time_range="1h",
            bucket="5m",
            agg="avg",
        )
        assert result == []