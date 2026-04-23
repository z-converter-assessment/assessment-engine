import pytest
from sqlalchemy import select

from db.models.server_inventory import ServerInventory
from db.repositories.collect_repository import CollectRepository

pytestmark = pytest.mark.integration


# TODO: upsert_server / insert_metric 구현 완료 후 테스트 작성
# 현재 두 메서드 모두 NotImplementedError


class TestFindServerId:
    async def test_not_found_returns_none(self, db_session):
        repo = CollectRepository(db_session)
        assert await repo.find_server_id("nonexistent") is None