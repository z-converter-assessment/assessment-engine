from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

# asyncio.run()이 태스크마다 새 이벤트 루프를 생성하므로
# 커넥션 풀을 쓰면 루프 간 커넥션 충돌이 발생함 → NullPool 사용
_engine = create_async_engine(settings.database_url, poolclass=NullPool)
WorkerSession = async_sessionmaker(_engine, expire_on_commit=False)