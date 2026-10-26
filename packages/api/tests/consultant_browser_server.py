"""Task-owned loopback native browser fixture with a deterministic provider boundary."""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic_settings import BaseSettings

_original = BaseSettings.__init__


def isolated(self, *args, **kwargs):
    kwargs["_env_file"] = None
    _original(self, *args, **kwargs)


BaseSettings.__init__ = isolated
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://fixture:fixture-local-only@127.0.0.1:55439/consultant_browser"
)
os.environ["SECRET_KEY"] = "fixture-only"
os.environ["SENTRY_DSN"] = ""
os.environ["DATABASE_ECHO"] = "false"

import psycopg2  # noqa: E402
import uvicorn  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import Request  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from src.database import async_session_maker, engine  # noqa: E402
from src.main import create_app  # noqa: E402
from tests.test_consultant import seed, service  # noqa: E402


async def populate():
    async with async_session_maker() as db:
        await seed(db)
        await db.execute(
            text(
                "INSERT INTO product_sizes(product_id,size,available_quantity,revision) VALUES(20,'L',3,0),(20,'XL',2,0)"
            )
        )
        await db.commit()
    await engine.dispose()


@asynccontextmanager
async def lifespan(app):
    async with async_session_maker() as db:
        app.state.consultant = service(db)
    app.state.consultant_origins = ["https://localhost:4173"]
    yield


admin = psycopg2.connect("postgresql://fixture:fixture-local-only@127.0.0.1:55439/postgres")
admin.autocommit = True
with admin.cursor() as cursor:
    cursor.execute("CREATE DATABASE consultant_browser")
try:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")
    asyncio.run(populate())
    app = create_app()
    app.router.lifespan_context = lifespan

    @app.post("/__fixture/change")
    async def change(request: Request):
        value = (await request.json())["change"]
        async with async_session_maker.begin() as db:
            if value == "price":
                await db.execute(text("UPDATE products SET price=250 WHERE id=20"))
            elif value == "size":
                await db.execute(
                    text(
                        "UPDATE product_sizes SET available_quantity=0 WHERE product_id=20 AND size='L'"
                    )
                )
        return {"ok": True}

    uvicorn.run(app, host="127.0.0.1", port=8089, access_log=False, log_level="warning")
finally:
    # Only the database created above is owned by this fixture.
    with admin.cursor() as cursor:
        cursor.execute("DROP DATABASE consultant_browser WITH (FORCE)")
    admin.close()
