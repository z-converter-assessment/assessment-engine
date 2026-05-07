"""smoke test — 컨테이너·engine·session·repo·schema가 모두 살아있는지 확인.

T1 셋업의 파이프라인 검증용. 본 테스트가 통과하면 후속 테스트들이 같은 인프라를 사용.
"""
import pytest

from assessment_engine.db.repositories.collect_repository import CollectRepository
from tests.factories import make_inventory


pytestmark = pytest.mark.asyncio


async def test_engine_alive(db_session):
    """가장 단순한 round-trip — DB 연결·query·async session 모두 정상."""
    from sqlalchemy import text
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


async def test_timescaledb_extension_loaded(db_session):
    """schema bootstrap이 CREATE EXTENSION timescaledb를 실행했는지."""
    from sqlalchemy import text
    result = await db_session.execute(
        text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")
    )
    assert result.scalar_one() == 1


async def test_repo_upsert_roundtrip(collect_repo: CollectRepository):
    """collect repo로 inventory upsert + find — 가장 짧은 read-write 사이클."""
    inv = make_inventory(machine_id="smoke-001", hostname="smoke-host")
    server_id = await collect_repo.upsert_server(inv)
    assert server_id > 0

    found = await collect_repo.find_server_id("smoke-001")
    assert found == server_id