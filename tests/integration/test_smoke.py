from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def test_engine_alive(db_session: AsyncSession):
    from sqlalchemy import text

    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


async def test_timescaledb_extension_loaded(db_session: AsyncSession):
    from sqlalchemy import text

    result = await db_session.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'"))
    assert result.scalar_one() == 1
