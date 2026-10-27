"""G1 loopback-only browser fixture (spec S2 section 11, CK-19). Test-only; never deployed.

Real guest API and SQL on a disposable, freshly migrated database; the Multicard client runs
unchanged with only its HTTP transport substituted; Telegram ``send_message`` only records texts.
Synthetic origin ``https://localhost:4173`` serves as both the website origin and the callback
base. Readiness: ``GET /api/v1/guest/order`` without a cookie answers 404 ``order_not_found``.

Fixture-only routes (outside ``/api/v1``):
- ``GET /api/__g1/pay/{uuid}``: the substitute payment page (the invoice's ``checkout_url``) with
  two links: ``#g1-pay`` and ``#g1-return``.
- ``GET /api/__g1/pay/{uuid}/paid``: posts an MD5-signed success callback to the API, then
  redirects to the invoice's ``return_url``; ``.../return``: redirects without a callback.
- ``GET /api/__g1/counts``: orders, provider create/delete calls, new-order and paid staff messages.
- ``POST /api/__g1/price`` ``{"product_id": int, "price": "decimal"}``: changes a catalogue price.
"""

import hashlib
import json
import os
import sys
import types
from pathlib import Path
from typing import Literal
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
os.environ["SECRET_KEY"] = "g1-browser-synthetic-admin"
os.environ["SENTRY_DSN"] = ""
os.environ["BUYER_SESSIONS_ENABLED"] = "false"
os.environ["BUYER_EMAIL_AUTH_ENABLED"] = "false"
os.environ["BUYER_TELEGRAM_AUTH_ENABLED"] = "false"
os.environ["EMPLOYEE_REFERRAL_K2_SENDER_ENABLED"] = "false"
os.environ["EMPLOYEE_REFERRAL_NOTICES_ENABLED"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = "synthetic-browser-bot-authority"
os.environ["MULTICARD_BASE_URL"] = "https://multicard.g1.invalid"
os.environ["MULTICARD_SECRET"] = "g1-browser-synthetic-multicard-secret"
os.environ["MULTICARD_STORE_ID"] = "6"

import httpx  # noqa: E402
import psycopg2  # noqa: E402
import uvicorn  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse  # noqa: E402
from psycopg2 import sql  # noqa: E402

from alembic import command  # noqa: E402

ORIGIN = "https://localhost:4173"
SECRET = os.environ["MULTICARD_SECRET"]
admin_url = os.environ["TG1_BROWSER_DATABASE_URL"]
if not admin_url.startswith("postgresql://tg1:tg1_synthetic@127.0.0.1:") or not admin_url.endswith(
    "/postgres"
):
    raise RuntimeError("Browser fixture requires the named synthetic local PostgreSQL owner")
admin = psycopg2.connect(admin_url)
admin.autocommit = True
name = "g1_browser_" + uuid4().hex
with admin.cursor() as cursor:
    cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
os.environ["DATABASE_URL"] = (
    admin_url.rsplit("/", 1)[0].replace("postgresql://", "postgresql+asyncpg://") + "/" + name
)
database = admin_url.rsplit("/", 1)[0] + "/" + name


def query(statement: str, args: tuple = ()) -> list:
    with psycopg2.connect(database) as conn, conn.cursor() as cursor:
        cursor.execute(statement, args)
        return cursor.fetchall() if cursor.description else []


try:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    # Only this fixture's newly migrated disposable database receives these rows.
    query("""
        INSERT INTO categories (id,name,name_uz,slug,description,is_active,is_purchasable,sort_order)
        VALUES (401,'G1 Нутриенты','G1 Nutriyentlar','product','G1 category',true,true,1),
               (402,'G1 Для жизни','G1 Hayot uchun','active-life','G1 sized',true,true,2);
        INSERT INTO products (id,name,name_uz,slug,price,is_sale,sale_price,currency,
                              stock_quantity,is_in_stock,category_id,sort_order,is_active,
                              is_featured,images,advantages,sizes_initialized_at)
        VALUES (4201,'G1 Synthetic ordinary','G1 UZ ordinary','g1-ordinary',60000.00,false,NULL,
                'UZS',9,true,401,1,true,false,'[]','[]',NULL),
               (4202,'G1 Synthetic sale','G1 UZ sale','g1-sale',100000.00,true,80000.00,
                'UZS',9,true,401,2,true,false,'[]','[]',NULL),
               (4203,'G1 Synthetic sized','G1 UZ sized','g1-sized',30000.00,false,NULL,
                'UZS',0,true,402,3,true,false,'[]','[]',now());
        INSERT INTO product_sizes (product_id,size,available_quantity,revision)
        VALUES (4203,'L',5,0),(4203,'XL',5,0);
    """)
    print("G1 seeded disposable database " + name, flush=True)
    from src import main
    from src.config import Settings
    from src.notifications.service import telegram_notifier
    from src.payments import service as payment_service

    config = Settings(
        _env_file=None,
        database_url=os.environ["DATABASE_URL"],
        secret_key="g1-browser-synthetic-admin",
        guest_checkout_enabled=True,
        buyer_protection_key="g1-browser-synthetic-protection-key-at-least-32-bytes",
        buyer_public_origin=ORIGIN,
        payment_callback_base_url=ORIGIN,
        guest_terms_version="terms-g1-browser-synthetic",
        guest_privacy_version="policy-g1-browser-synthetic",
        multicard_store_id="6",
        telegram_bot_token="synthetic-browser-bot-authority",
    )
    main.settings = config
    app = main.create_app()

    invoices: dict[str, dict] = {}
    calls = {"creates": 0, "deletes": 0}
    messages: list[str] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        """Multicard at its HTTP boundary; the real client code builds every request."""
        if request.url.path == "/auth":
            return httpx.Response(200, json={"token": "synthetic-token"})
        if request.method == "POST" and request.url.path == "/payment/invoice":
            calls["creates"] += 1
            uuid = "g1-browser-" + uuid4().hex
            invoices[uuid] = json.loads(request.content)
            pay_url = f"{ORIGIN}/api/__g1/pay/{uuid}"
            return httpx.Response(
                200, json={"success": True, "data": {"uuid": uuid, "checkout_url": pay_url}}
            )
        if request.method == "DELETE":
            calls["deletes"] += 1
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"success": False})

    real_client = httpx.AsyncClient

    def provider_client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(provider), **kwargs)

    payment_service.httpx = types.SimpleNamespace(
        AsyncClient=provider_client,
        HTTPStatusError=httpx.HTTPStatusError,
        HTTPError=httpx.HTTPError,
    )

    async def record(text_value: str, parse_mode: str = "HTML") -> bool:
        messages.append(text_value)
        return True

    telegram_notifier.send_message = record

    @app.get("/api/__g1/pay/{uuid}")
    async def page(uuid: str) -> HTMLResponse:
        if uuid not in invoices:
            return HTMLResponse("unknown invoice", status_code=404)
        return HTMLResponse(
            "<!doctype html><title>G1 synthetic payment</title>"
            f'<a id="g1-pay" href="/api/__g1/pay/{uuid}/paid">Pay (synthetic)</a> '
            f'<a id="g1-return" href="/api/__g1/pay/{uuid}/return">Return without paying</a>'
        )

    @app.get("/api/__g1/pay/{uuid}/{outcome}")
    async def pay(uuid: str, outcome: Literal["paid", "return"]) -> RedirectResponse:
        invoice = invoices[uuid]
        if outcome == "paid":
            amount, number = invoice["amount"], invoice["invoice_id"]
            sign = hashlib.md5(f"6{number}{amount}{SECRET}".encode()).hexdigest()
            transport = httpx.ASGITransport(app=app)
            async with real_client(transport=transport, base_url="http://127.0.0.1") as client:
                answer = await client.post(
                    "/api/v1/payments/callback",
                    json={
                        "store_id": 6,
                        "amount": amount,
                        "invoice_id": number,
                        "uuid": uuid,
                        "ps": "uzcard",
                        "sign": sign,
                    },
                )
                answer.raise_for_status()
        return RedirectResponse(invoice["return_url"], status_code=303)

    @app.get("/api/__g1/counts")
    async def counts() -> dict[str, int]:
        return {
            "orders": query("SELECT count(*) FROM orders")[0][0],
            "creates": calls["creates"],
            "deletes": calls["deletes"],
            "new_order_messages": sum("НОВЫЙ ЗАКАЗ" in m for m in messages),
            "paid_messages": sum("ОПЛАЧЕНО" in m for m in messages),
        }

    @app.post("/api/__g1/price")
    async def price(request: Request) -> JSONResponse:
        data = await request.json()
        query("UPDATE products SET price=%s WHERE id=%s", (data["price"], data["product_id"]))
        return JSONResponse({"ok": True})

    uvicorn.run(app, host="127.0.0.1", port=8089, access_log=False, log_level="warning")
finally:
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    admin.close()
