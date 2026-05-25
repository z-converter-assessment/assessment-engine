"""Alembic env — async engine + Base.metadata 연결.

DB URL은 WebSettings.database_url (asyncpg). alembic.ini의 sqlalchemy.url은 비워두고
런타임에 주입 — 동일 환경변수 정책(.env / secret 채널) 활용.

본 env는 schema 관리 진입점이라 어느 컴포넌트도 아님 — 자체 WebSettings 인스턴스화
(db/session·db/redis와 동일 패턴, Composition Root #F4).

모든 ORM 모델 import 의무 — 안 그러면 Base.metadata에 누락되어 autogenerate가 drop 처리.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from assessment_engine.config import WebSettings
from assessment_engine.db.models import (  # noqa: F401  — Base.metadata 등록
    diagnostic_job,
    rag_document,
    server_disk_io,
    server_inventory,
    server_inventory_history,
    server_metrics,
    server_mount_usage,
    server_net_io,
    task,
)
from assessment_engine.db.models.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 런타임 주입: 환경변수 → WebSettings → alembic config
_settings = WebSettings()
config.set_main_option("sqlalchemy.url", _settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """offline 모드 — DB 연결 없이 SQL 출력. CI 검증·dry-run용."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _include_object(object_, name, type_, reflected, compare_to):
    """autogenerate 비교 시 TimescaleDB·pgvector 자동 생성 객체 제외.

    1. `create_hypertable`이 partition 키 정렬용으로 `{table}_collected_at_idx`를 자동 생성.
       ORM `Base.metadata`엔 없으므로 autogenerate가 매번 "remove" 처리 — false positive.
    2. ADR 0024: pgvector HNSW 인덱스 (`rag_documents_embedding_hnsw_idx`) + `embedding vector(1024)` 컬럼.
       SQLAlchemy autogenerate 가 vector 타입·HNSW 인덱스 인식 못 함 — false positive.
       본 컬럼·인덱스 는 alembic revision 안 raw SQL (op.execute) 로 관리, autogenerate 비교 대상 제외.
    """
    if type_ == "index" and name:
        if name.endswith("_collected_at_idx"):
            return False
        if name == "rag_documents_embedding_hnsw_idx":
            return False
    if type_ == "column" and name == "embedding" and object_.table.name == "rag_documents":
        return False
    if type_ == "table" and name == "rag_documents":
        # rag_documents 테이블 자체는 alembic revision 안 raw SQL (vector 타입 표현 제한)
        # autogenerate 가 vector 컬럼 누락 형태로 잘못 인식 — 본 테이블 자체 비교 제외.
        return False
    return True


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
