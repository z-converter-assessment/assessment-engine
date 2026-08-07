"""Alembic env — async engine + Base.metadata 연결.

`alembic.ini` 의 sqlalchemy.url 은 비워 두고 런타임에 WebSettings.database_url 을 주입한다 — 다른
컴포넌트와 같은 환경변수·secret 채널을 쓰기 위해서다. 어느 컴포넌트도 아닌 schema 진입점이라 Settings
인스턴스를 자체 생성한다(#F4 허용 위치).

ORM 모델을 전부 import 해야 한다 — 빠지면 Base.metadata 에 없어 autogenerate 가 drop 으로 처리한다.
"""

# 아래 모델 import 는 이름을 쓰지 않고 등록 부수효과만 취한다.
# pyright: reportUnusedImport=false

import asyncio
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from assessment_engine.config import WebSettings
from assessment_engine.db.models import (  # noqa: F401  — Base.metadata 등록
    diagnostic_job,
    server_cpu_core,
    server_disk_error,
    server_disk_io,
    server_filesystem,
    server_inventory,
    server_inventory_history,
    server_metrics,
    server_net_io,
    server_pressure,
    task,
)
from assessment_engine.db.models.base import Base

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.sql.schema import SchemaItem

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# set_main_option 은 configparser interpolation 을 거친다 — 비밀번호가 URL-quote 되며 담긴 리터럴 '%'(예:
# base64 결과의 '+'/'/' 가 %2B/%2F 로)가 있으면 "invalid interpolation syntax" 로 죽는다. '%' -> '%%' 는
# get 시 interpolation 이 도로 풀어 주는 표준 왕복 패턴.
_settings = WebSettings()  # pyright: ignore[reportCallIssue]
config.set_main_option("sqlalchemy.url", _settings.database_url.replace("%", "%%"))

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


def _include_object(
    object_: SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: SchemaItem | None
) -> bool:
    """autogenerate 비교에서 TimescaleDB 가 자동 생성한 객체 제외.

    `create_hypertable` 이 `{table}_collected_at_idx` 를 자동으로 만든다 — ORM `Base.metadata` 엔 없어
    autogenerate 가 매번 "remove" 로 잡는 false positive 가 된다.
    """
    return not (type_ == "index" and name and name.endswith("_collected_at_idx"))


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
