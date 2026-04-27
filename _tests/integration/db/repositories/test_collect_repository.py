import pytest

from db.repositories.collect_repository import CollectRepository
from db.repositories.inbound import ServerInventoryCreate, ServerMetricCreate

pytestmark = pytest.mark.integration


class TestFindServerId:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_not_found_returns_none(self, db_session):
        repo = CollectRepository(db_session)
        result = await repo.find_server_id("nonexistent-machine-id")
        assert result is None

    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_found_returns_server_id(self, db_session):
        # 사전조건: upsert_server 로 등록된 서버가 있어야 한다
        pass


class TestUpsertServer:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_insert_new_server(self, db_session):
        repo = CollectRepository(db_session)
        await repo.upsert_server(ServerInventoryCreate())
        await db_session.commit()

    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_update_existing_server(self, db_session):
        # 동일 machine_id 로 두 번 upsert → 두 번째는 갱신
        pass


class TestInsertMetric:
    @pytest.mark.skip(reason="NotImplementedError — ORM 모델 필드 정의 후 구현 예정")
    async def test_inserts_metric_for_known_server(self, db_session):
        repo = CollectRepository(db_session)
        await repo.insert_metric(server_id=1, data=ServerMetricCreate())
        await db_session.commit()