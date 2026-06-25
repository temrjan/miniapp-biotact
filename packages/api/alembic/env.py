from logging.config import fileConfig
import asyncio

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config, create_async_engine

from alembic import context

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import Base
import src.models_registry  # noqa: F401 — регистрирует ВСЕ модели на Base.metadata
from src.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Сотруднический реферал (referrers / referral_invites + *.referral_code) принадлежит
# ОТДЕЛЬНОМУ прод-сервису ref-bot (общая БД biotact_db); его схема живёт ВНЕ этого репо.
# Исключаем из autogenerate / `alembic check` — НЕ дропаем (живой сервис + данные).
_REF_TABLES = {"referrers", "referral_invites"}
_REF_INDEXES = {
    "ix_orders_referral_code",
    "ix_users_referral_code",
    "ix_referrers_code",
    "ix_referrers_telegram_id",
    "ix_referral_invites_invite_code",
}


def include_object(object_, name, type_, reflected, compare_to):  # noqa: ANN001, ANN201
    if type_ == "table" and name in _REF_TABLES:
        return False
    if (
        type_ == "column"
        and name == "referral_code"
        and getattr(object_, "table", None) is not None
        and object_.table.name in {"users", "orders"}
    ):
        return False
    if type_ == "index" and name in _REF_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    url = settings.database_url.replace("+asyncpg", "")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(
        settings.database_url,
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
