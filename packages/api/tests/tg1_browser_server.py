"""Loopback-only browser fixture. Real API/SQL; no dotenv or external delivery."""

import os
import sys
from pathlib import Path
from uuid import uuid4

from pydantic_settings import BaseSettings

_original = BaseSettings.__init__


def isolated(self, *args, **kwargs):
    kwargs["_env_file"] = None
    _original(self, *args, **kwargs)


BaseSettings.__init__ = isolated
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Do not inherit process environment as an application configuration source.
os.environ["SECRET_KEY"] = "tg1-browser-synthetic-admin"
os.environ["SENTRY_DSN"] = ""
os.environ["BUYER_SESSIONS_ENABLED"] = "false"
os.environ["BUYER_EMAIL_AUTH_ENABLED"] = "false"
os.environ["BUYER_TELEGRAM_AUTH_ENABLED"] = "false"
os.environ["EMPLOYEE_REFERRAL_K2_SENDER_ENABLED"] = "false"
os.environ["EMPLOYEE_REFERRAL_NOTICES_ENABLED"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = "synthetic-browser-bot-authority"

import psycopg2  # noqa: E402
import uvicorn  # noqa: E402
from alembic.config import Config  # noqa: E402
from psycopg2 import sql  # noqa: E402

from alembic import command  # noqa: E402

admin_url = os.environ["TG1_BROWSER_DATABASE_URL"]
if not admin_url.startswith("postgresql://tg1:tg1_synthetic@127.0.0.1:") or not admin_url.endswith(
    "/postgres"
):
    raise RuntimeError("Browser fixture requires the named synthetic local PostgreSQL owner")
admin = psycopg2.connect(admin_url)
admin.autocommit = True
name = "tg1_browser_" + uuid4().hex
with admin.cursor() as cursor:
    cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
os.environ["DATABASE_URL"] = (
    admin_url.rsplit("/", 1)[0].replace("postgresql://", "postgresql+asyncpg://") + "/" + name
)
try:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    from src import main
    from src.config import Settings

    config = Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        secret_key="tg1-browser-synthetic-admin",
        buyer_sessions_enabled=True,
        buyer_telegram_auth_enabled=os.environ.get("TG1_BROWSER_EMAIL_ONLY") != "1",
        buyer_email_auth_enabled=os.environ.get("TG1_BROWSER_EMAIL_ONLY") == "1",
        smtp_user="sender@example.test",
        smtp_password="synthetic-mail-disabled",
        smtp_from_email="sender@example.test",
        buyer_protection_key="tg1-browser-synthetic-protection-key-at-least-32-bytes",
        buyer_public_origin="https://localhost:4173",
        buyer_telegram_bot_username="synthetic_bot",
        telegram_bot_token="synthetic-browser-bot-authority",
        loyalty_enabled=True,
    )
    main.settings = config
    app = main.create_app()

    async def no_external_delivery(actor, prompt):
        return "unavailable", 0

    app.state.buyer_telegram_auth.sender.send = no_external_delivery
    uvicorn.run(app, host="127.0.0.1", port=8089, access_log=False, log_level="warning")
finally:
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    admin.close()
