"""smoke test — 컨테이너·engine·session·schema 기본 인프라 sanity.

본 테스트가 통과하면 후속 integration 테스트들이 같은 인프라를 사용.
repo round-trip 검증은 `test_collect_repository.py` 가 더 깊게 다룸 (중복 회피).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def test_engine_alive(db_session: AsyncSession):
    """가장 단순한 round-trip — DB 연결·query·async session 모두 정상."""
    from sqlalchemy import text

    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


async def test_timescaledb_extension_loaded(db_session: AsyncSession):
    """schema bootstrap이 CREATE EXTENSION timescaledb를 실행했는지."""
    from sqlalchemy import text

    result = await db_session.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'"))
    assert result.scalar_one() == 1
