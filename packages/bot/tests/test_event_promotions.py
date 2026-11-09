"""Event QR entry and /qr_stats on the actual dispatcher, Redis FSM and PostgreSQL 16.

Spec event-qr-discount S3: R-01 (bot part), R-30…R-34, R-40, R-41. The schema comes from the API
migrations; only Telegram delivery is stubbed. Expected texts are the spec §4 drafts written out
literally, independent of the implementation.
"""

import importlib
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Update
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.handlers
import bot.middlewares.database
import bot.services.event_promotions
from bot.core.config import Settings, settings
from bot.event_entry import EVENT_KEY
from bot.handlers import auth, common, employee_referrals, event_promotions, loyalty, web_login
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.logging import LoggingMiddleware
from bot.middlewares.referral_entry import ReferralEntryMiddleware
from bot.services.event_stats import promotion_stats, render_report
from tests.test_employee_referral_entry import A, StubSession, ids

API_ROOT = Path(__file__).resolve().parents[2] / "api"
TABLES = (
    "event_promotion_scans, event_promotion_grants, order_items, orders, event_promotions, users"
)
ADMIN = 777

STARTS = datetime(2026, 11, 27, 19, 0, tzinfo=UTC)  # 2026-11-28 00:00 Tashkent
ENDS = datetime(2026, 12, 31, 19, 0, tzinfo=UTC)  # 2027-01-01 00:00 Tashkent
INSIDE = datetime(2026, 11, 30, 10, 0, tzinfo=UTC)
CODE = "nutriforum2026"

LANG_PROMPT = "🌐 Tilni tanlang / Выберите язык:"
PREFIX = "🎁 Скидка 5% для гостей NUTRIFORUM 2026 / NUTRIFORUM 2026 mehmonlari uchun 5% chegirma"
BASE_RU = (
    "👋 Привет, Synthetic!\n\nДобро пожаловать в <b>BIOTACT DEUTSCHLAND</b> — "
    "пробиотики и витаминно-минеральные комплексы немецкого качества."
)
BASE_UZ = (
    "👋 Salom, Synthetic!\n\n<b>BIOTACT DEUTSCHLAND</b>ga xush kelibsiz — "
    "nemis sifatidagi probiotiklar va vitamin-mineral komplekslar."
)
EVENT_RU = (
    "👋 Здравствуйте, Synthetic! Добро пожаловать в BIOTACT DEUTSCHLAND — пробиотики и "
    "витаминно-минеральные комплексы немецкого качества. 🎁 Для гостей NUTRIFORUM 2026 действует "
    "скидка 5% до 31.12.2026 — она применится в корзине. Нажмите «Войти», чтобы открыть магазин."
)
EVENT_UZ = (
    "👋 Assalomu alaykum, Synthetic! BIOTACT DEUTSCHLAND'ga xush kelibsiz — nemis sifatidagi "
    "probiotiklar va vitamin-mineral komplekslar. 🎁 NUTRIFORUM 2026 mehmonlari uchun 31.12.2026 "
    "gacha 5% chegirma amal qiladi — u savatchada qo'llaniladi. Do'konni ochish uchun «Kirish» "
    "tugmasini bosing."
)


class Clock:
    """Replaces the window clock; with several values each call consumes one until the last."""

    def __init__(self, *values: datetime) -> None:
        self.values = list(values)

    def __call__(self) -> datetime:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


@pytest.fixture(scope="session")
def database_url():
    if not os.environ.get("TG1_API_INTEGRATION"):
        pytest.skip("Explicit TG1_API_INTEGRATION and synthetic PostgreSQL required")
    import psycopg2
    from psycopg2 import sql

    admin = psycopg2.connect(os.environ["S1_TEST_DATABASE_URL"])
    admin.autocommit = True
    name = "eqd_bot_" + uuid4().hex
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = os.environ["DATABASE_URL"].rsplit("/", 1)[0] + "/" + name
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        yield url
    finally:
        with admin.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
        admin.close()


@pytest.fixture
async def ev(database_url, monkeypatch):
    monkeypatch.setattr(settings, "event_promotions_enabled", True)
    monkeypatch.setattr(settings, "admin_ids", [ADMIN])
    # Fresh Router graph per dispatcher, in the production order of bot.handlers.
    for module in (common, employee_referrals, loyalty, event_promotions, auth, web_login):
        importlib.reload(module)
    handlers = importlib.reload(bot.handlers)
    monkeypatch.setattr(common, "sync_customer_to_crm", AsyncMock())
    monkeypatch.setattr(common, "sync_customer_full", AsyncMock())
    clock = Clock(INSIDE)
    monkeypatch.setattr(bot.services.event_promotions, "utcnow", clock)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(bot.middlewares.database, "async_session_maker", sessions)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        await connection.execute(text("DROP FUNCTION IF EXISTS eqd_refuse() CASCADE"))
    namespace = "eqd-test-" + uuid4().hex
    storage = RedisStorage.from_url(
        os.environ["BOT_TEST_REDIS_URL"],
        key_builder=DefaultKeyBuilder(prefix=namespace, with_bot_id=True),
        state_ttl=120,
        data_ttl=120,
    )
    transport = StubSession()
    telegram = Bot(settings.bot_token.get_secret_value(), session=transport)
    dp = Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())
    dp.update.outer_middleware(ReferralEntryMiddleware())
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(DatabaseMiddleware())
    dp.include_router(handlers.router)

    async def feed(message=None, callback=None, contact=None, chat_type="private", uid=10):
        update_id = next(ids)
        chat_id = uid if chat_type == "private" else -20
        base = {
            "message_id": update_id,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": uid, "is_bot": False, "first_name": "Synthetic"},
        }
        if callback:
            payload = {
                "callback_query": {
                    "id": str(update_id),
                    "from": base["from"],
                    "chat_instance": "stub",
                    "data": callback,
                    "message": base,
                }
            }
        else:
            if message:
                command = message.split(maxsplit=1)[0]
                base["text"] = message
                base["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]
            if contact:
                base["contact"] = {
                    "phone_number": "998000000000",
                    "first_name": "Synthetic",
                    "user_id": contact,
                }
            payload = {"message": base}
        await dp.feed_update(telegram, Update.model_validate({"update_id": update_id, **payload}))

    def sent():
        return [call for call in transport.calls if call.__api_method__ == "sendMessage"]

    async def sql(statement, **params):
        async with engine.begin() as connection:
            return (await connection.execute(text(statement), params)).all()

    async def run(statement, **params):
        async with engine.begin() as connection:
            await connection.execute(text(statement), params)

    def state(uid=10):
        return FSMContext(storage, StorageKey(bot_id=telegram.id, chat_id=uid, user_id=uid))

    async def promotion(code=CODE, title="NUTRIFORUM 2026", starts=STARTS, ends=ENDS, created=None):
        rows = await sql(
            "INSERT INTO event_promotions (code, title, percent, starts_at, ends_at, created_at) "
            "VALUES (:code, :title, 5, :starts, :ends, :created) RETURNING id",
            code=code,
            title=title,
            starts=starts,
            ends=ends,
            created=created or datetime.now(UTC),
        )
        return rows[0][0]

    async def user(uid, phone="+998000000001", first="Synthetic", last=None, username=None):
        rows = await sql(
            "INSERT INTO users (telegram_id, first_name, last_name, username, phone, language_code,"
            " is_active, is_admin) VALUES (:uid, :first, :last, :username, :phone, 'ru', true, false)"
            " RETURNING id",
            uid=uid,
            first=first,
            last=last,
            username=username,
            phone=phone,
        )
        return rows[0][0]

    async def scans(uid=10):
        rows = await sql(
            "SELECT s.outcome FROM event_promotion_scans s JOIN users u ON u.id = s.user_id "
            "WHERE u.telegram_id = :uid ORDER BY s.id",
            uid=uid,
        )
        return [row[0] for row in rows]

    async def grants(uid=10):
        return await sql(
            "SELECT g.id, g.granted_at, g.is_new_user FROM event_promotion_grants g "
            "JOIN users u ON u.id = g.user_id WHERE u.telegram_id = :uid ORDER BY g.id",
            uid=uid,
        )

    try:
        yield SimpleNamespace(
            feed=feed,
            sent=sent,
            sql=sql,
            run=run,
            state=state,
            promotion=promotion,
            user=user,
            scans=scans,
            grants=grants,
            clock=clock,
            sessions=sessions,
            transport=transport,
        )
    finally:
        owned = [key async for key in storage.redis.scan_iter(match=namespace + ":*")]
        if owned:
            await storage.redis.delete(*owned)
        await dp.fsm.close()
        await telegram.session.close()
        await engine.dispose()


def button(call):
    rows = call.reply_markup.inline_keyboard
    assert len(rows) == 1
    assert len(rows[0]) == 1
    return rows[0][0].text, rows[0][0].web_app.url


async def test_r30_r32_r34_new_user_inside_window_gets_language_first_then_event_welcome(ev):
    await ev.promotion()
    await ev.feed("/start ev_" + CODE)
    assert [call.text for call in ev.sent()] == [PREFIX + "\n\n" + LANG_PROMPT]
    assert await ev.scans() == ["granted"]
    grants = await ev.grants()
    assert len(grants) == 1
    assert grants[0][2] is True  # is_new_user from the single resolution (R-32)
    assert len(await ev.sql("SELECT id FROM users WHERE telegram_id = 10")) == 1
    await ev.feed(callback="lang:ru")
    await ev.feed(callback="skip_phone")
    last = ev.sent()[-1]
    assert last.text == EVENT_RU
    assert button(last) == ("Войти", settings.webapp_url)
    assert EVENT_KEY not in await ev.state().get_data()


async def test_r31_repeat_scan_records_existing_grant_and_keeps_grant(ev):
    await ev.promotion()
    await ev.user(10)
    await ev.feed("/start ev_" + CODE)
    before = await ev.grants()
    ev.clock.values = [INSIDE + timedelta(days=3)]
    await ev.feed("/start ev_" + CODE)
    assert await ev.scans() == ["granted", "existing_grant"]
    assert await ev.grants() == before
    assert before[0][1] == INSIDE
    assert before[0][2] is False
    assert [call.text for call in ev.sent()] == [EVENT_RU, EVENT_RU]


@pytest.mark.parametrize(
    ("now", "former_holder", "outcome"),
    [
        (STARTS, False, "granted"),
        (ENDS - timedelta(microseconds=1), False, "granted"),
        (STARTS - timedelta(microseconds=1), False, "outside_window"),
        (ENDS, True, "outside_window"),
    ],
)
async def test_r31_window_is_checked_first_at_its_bounds(ev, now, former_holder, outcome):
    promotion_id = await ev.promotion()
    user_id = await ev.user(10)
    if former_holder:
        await ev.run(
            "INSERT INTO event_promotion_grants (promotion_id, user_id, granted_at, is_new_user)"
            " VALUES (:p, :u, :at, false)",
            p=promotion_id,
            u=user_id,
            at=INSIDE,
        )
    before = await ev.grants()
    ev.clock.values = [now]
    await ev.feed("/start ev_" + CODE)
    assert await ev.scans() == [outcome]
    if outcome == "granted":
        assert len(await ev.grants()) == 1
        assert ev.sent()[-1].text == EVENT_RU
    else:
        assert await ev.grants() == before
        assert ev.sent()[-1].text == BASE_RU
        assert EVENT_KEY not in await ev.state().get_data()


@pytest.mark.parametrize("existing", [False, True])
async def test_r31_forced_write_failure_gives_ordinary_flow_and_keeps_user(ev, existing):
    await ev.promotion()
    if existing:
        await ev.user(10)
    # A real PostgreSQL error inside the savepoint, after the scan row was written.
    await ev.run(
        "CREATE FUNCTION eqd_refuse() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'synthetic grant failure'; END $$"
    )
    await ev.run(
        "CREATE TRIGGER eqd_refuse BEFORE INSERT ON event_promotion_grants "
        "FOR EACH ROW EXECUTE FUNCTION eqd_refuse()"
    )
    await ev.feed("/start ev_" + CODE)
    assert [call.text for call in ev.sent()] == [BASE_RU if existing else LANG_PROMPT]
    assert len(await ev.sql("SELECT id FROM users WHERE telegram_id = 10")) == 1
    assert await ev.sql("SELECT count(*) FROM event_promotion_scans") == [(0,)]
    assert await ev.sql("SELECT count(*) FROM event_promotion_grants") == [(0,)]
    assert EVENT_KEY not in await ev.state().get_data()


@pytest.mark.parametrize(
    ("args", "switch"),
    [
        ("ev_unknown", True),
        ("ev_bad.code", True),
        ("ev_" + "a" * 41, True),
        ("ev_" + CODE, False),  # R-01: bot switch OFF
    ],
)
async def test_r30_r01_ordinary_welcome_without_event_behavior(ev, monkeypatch, args, switch):
    monkeypatch.setattr(settings, "event_promotions_enabled", switch)
    await ev.promotion()
    await ev.user(10)
    await ev.feed("/start " + args)
    assert [call.text for call in ev.sent()] == [BASE_RU]
    assert await ev.sql("SELECT count(*) FROM event_promotion_scans") == [(0,)]
    assert await ev.sql("SELECT count(*) FROM event_promotion_grants") == [(0,)]


@pytest.mark.parametrize("now", [STARTS - timedelta(microseconds=1), ENDS])
async def test_r30_r31_new_user_outside_window_gets_plain_language_step(ev, now):
    """A new user scanning before the window opens or at its end gets no event line (spec §7.3)."""
    await ev.promotion()
    ev.clock.values = [now]
    await ev.feed("/start ev_" + CODE)
    assert [call.text for call in ev.sent()] == [LANG_PROMPT]
    assert await ev.scans() == ["outside_window"]
    assert await ev.grants() == []
    assert EVENT_KEY not in await ev.state().get_data()


def test_r01_bot_switch_is_off_by_default():
    """R-01, spec §7 step 1: the bot deploys with its switch OFF, like the API."""
    assert Settings.model_fields["event_promotions_enabled"].default is False


async def test_r30_new_user_with_unknown_code_gets_language_step(ev):
    await ev.feed("/start ev_unknown")
    assert [call.text for call in ev.sent()] == [LANG_PROMPT]
    assert len(await ev.sql("SELECT id FROM users WHERE telegram_id = 10")) == 1


async def test_r30_group_chat_gets_no_reply(ev):
    await ev.promotion()
    await ev.feed("/start ev_" + CODE, chat_type="group")
    assert ev.sent() == []
    assert await ev.sql("SELECT count(*) FROM event_promotion_scans") == [(0,)]


@pytest.mark.parametrize("later", ["/start qr_ABCDEFGHJKMN", "/start ref_39_" + A, "/start"])
async def test_r33_later_start_retires_event_intent(ev, monkeypatch, later):
    # With the loyalty program live, its qr_ route keeps FSM state: only the middleware retires.
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    await ev.promotion()
    await ev.feed("/start ev_" + CODE)
    assert EVENT_KEY in await ev.state().get_data()
    await ev.feed(later)
    assert EVENT_KEY not in await ev.state().get_data()
    if later.startswith("/start qr_"):
        assert ev.sent()[-1].text.startswith("📱 Поделитесь номером телефона, чтобы активировать")
    else:
        await ev.feed(callback="skip_phone")
        assert ev.sent()[-1].text == BASE_RU


async def test_r33_stale_language_button_after_later_start_is_ignored(ev):
    await ev.promotion()
    await ev.feed("/start ev_" + CODE)
    await ev.feed("/start ev_" + CODE)
    data = await ev.state().get_data()
    assert EVENT_KEY in data
    assert not data.get("registration_language")
    count = len(ev.sent())
    await ev.feed(callback="lang:uz")
    assert len(ev.sent()) == count
    assert await ev.sql("SELECT language_code FROM users WHERE telegram_id = 10") == [("ru",)]
    await ev.feed(callback="skip_phone")
    assert ev.sent()[-1].text == EVENT_RU


@pytest.mark.parametrize("ended", [False, True])
@pytest.mark.parametrize("exit_line", [228, 287, 355, 390])
async def test_r34_each_welcome_exit(ev, exit_line, ended):
    await ev.promotion()
    expected, base, shop, enter = EVENT_RU, BASE_RU, "🛒 Открыть магазин", "Войти"
    if exit_line == 228:
        await ev.user(10)
        if ended:
            # The scan is inside the window; the grant has ended when the welcome is sent.
            ev.clock.values = [INSIDE, ENDS]
        await ev.feed("/start ev_" + CODE)
    else:
        await ev.feed("/start ev_" + CODE)
        if ended:
            ev.clock.values = [ENDS]
        if exit_line == 287:
            await ev.run("UPDATE users SET phone = '+998000000002' WHERE telegram_id = 10")
            await ev.feed(callback="lang:uz")
            expected, base, shop, enter = EVENT_UZ, BASE_UZ, "🛒 Do'konni ochish", "Kirish"
        else:
            await ev.feed(callback="lang:ru")
            if exit_line == 355:
                await ev.feed(contact=10)
            else:
                await ev.feed(callback="skip_phone")
    last = ev.sent()[-1]
    if ended:
        assert (last.text, button(last)) == (base, (shop, settings.webapp_url))
    else:
        assert (last.text, button(last)) == (expected, (enter, settings.webapp_url))
    assert EVENT_KEY not in await ev.state().get_data()


async def test_r34_grant_holder_by_plain_start_gets_base_welcome(ev):
    promotion_id = await ev.promotion()
    user_id = await ev.user(10)
    await ev.run(
        "INSERT INTO event_promotion_grants (promotion_id, user_id, granted_at, is_new_user)"
        " VALUES (:p, :u, :at, false)",
        p=promotion_id,
        u=user_id,
        at=INSIDE,
    )
    await ev.feed("/start")
    assert [call.text for call in ev.sent()] == [BASE_RU]


@pytest.mark.parametrize("switch", [True, False])
async def test_r40_only_admin_in_private_chat_gets_report(ev, monkeypatch, switch):
    monkeypatch.setattr(settings, "event_promotions_enabled", switch)
    await ev.promotion()
    await ev.feed("/qr_stats", uid=10)
    await ev.feed("/qr_stats", uid=ADMIN, chat_type="group")
    assert ev.sent() == []
    await ev.feed("/qr_stats", uid=ADMIN)
    report = ev.sent()
    assert len(report) == 1
    assert report[0].chat_id == ADMIN
    assert report[0].parse_mode is None
    assert report[0].text.startswith(
        "📊 Статистика QR-акций\n\nnutriforum2026 — NUTRIFORUM 2026, 5%"
    )


def at(day, hour=10):
    return datetime(2026, 12, day, hour, 0, tzinfo=UTC)


async def seed_order(
    ev,
    number,
    user_id,
    status,
    total,
    *,
    promotion=None,
    points=0,
    created,
    paid=None,
    delivery=None,
    discounts=(),
):
    rows = await ev.sql(
        "INSERT INTO orders (order_number, user_id, status, payment_status, subtotal, delivery_fee,"
        " total, currency, points_applied, promotion_id, created_at, paid_at, delivery_name)"
        " VALUES (:n, :u, 'PENDING', :s, :t, 0, :t, 'UZS', :pts, :p, :c, :paid, :d) RETURNING id",
        n=number,
        u=user_id,
        s=status,
        t=Decimal(total),
        pts=points,
        p=promotion,
        c=created,
        paid=paid,
        d=delivery,
    )
    for discount in discounts:
        await ev.run(
            "INSERT INTO order_items (order_id, product_name, product_price, quantity, total,"
            " promotion_discount) VALUES (:o, 'Synthetic', 1, 1, 1, :d)",
            o=rows[0][0],
            d=Decimal(discount),
        )


async def test_r41_report_figures(ev):
    await ev.promotion(code="old", title="OLD", created=at(1))
    promo = await ev.promotion(created=at(2))
    ali = await ev.user(101, first="Ali", last="Valiev", username="ali")
    bek = await ev.user(102, first="Bek")
    nameless = await ev.user(103, first=None)
    other = await ev.user(104, first="Other")
    for user_id, outcome in [
        (ali, "granted"),
        (ali, "existing_grant"),
        (bek, "granted"),
        (nameless, "outside_window"),
        (nameless, "granted"),
    ]:
        await ev.run(
            "INSERT INTO event_promotion_scans (promotion_id, user_id, scanned_at, outcome)"
            " VALUES (:p, :u, :at, :o)",
            p=promo,
            u=user_id,
            at=at(1),
            o=outcome,
        )
    for user_id, granted, new, opened in [
        (ali, at(3), True, at(3, 11)),
        (bek, at(4), False, None),
        (nameless, at(5), False, at(5, 12)),
    ]:
        await ev.run(
            "INSERT INTO event_promotion_grants (promotion_id, user_id, granted_at, is_new_user,"
            " first_opened_at) VALUES (:p, :u, :g, :n, :o)",
            p=promo,
            u=user_id,
            g=granted,
            n=new,
            o=opened,
        )
    # Promotion orders: three paid, one refunded, one unpaid. Points on the refunded and unpaid
    # orders must not count: points used are summed over paid orders only (R-41).
    await seed_order(
        ev,
        "EV-1",
        ali,
        "paid",
        95000,
        promotion=promo,
        points=5000,
        created=at(6),
        paid=at(6, 10),
        discounts=(2500, 2500),
    )
    await seed_order(
        ev,
        "EV-2",
        bek,
        "paid",
        47500,
        promotion=promo,
        created=at(7),
        paid=at(7, 11),
        discounts=(2500,),
    )
    await seed_order(
        ev,
        "EV-3",
        nameless,
        "paid",
        19000,
        promotion=promo,
        created=at(8),
        paid=at(8, 12),
        delivery="Dilnoza D.",
        discounts=(1000,),
    )
    await seed_order(
        ev,
        "EV-4",
        nameless,
        "refunded",
        10000,
        promotion=promo,
        points=3000,
        created=at(9),
        discounts=(500,),
    )
    await seed_order(
        ev,
        "EV-5",
        ali,
        "unpaid",
        20000,
        promotion=promo,
        points=2000,
        created=at(9),
        discounts=(1000,),
    )
    # Ordinary orders: Ali's paid order after his scan and Bek's paid order at the very instant
    # of his grant count (granted_at <= created_at < ends_at); the others do not.
    await seed_order(ev, "OR-6", bek, "paid", 30000, created=at(4), paid=at(4))
    await seed_order(ev, "OR-1", ali, "paid", 30000, created=at(10), paid=at(10))
    await seed_order(ev, "OR-2", ali, "paid", 30000, created=at(2), paid=at(2))
    await seed_order(ev, "OR-3", bek, "paid", 30000, created=ENDS, paid=ENDS)
    await seed_order(ev, "OR-4", bek, "unpaid", 30000, created=at(10))
    await seed_order(ev, "OR-5", other, "paid", 30000, created=at(10), paid=at(10))

    async with ev.sessions() as session:
        stats = await promotion_stats(session)
    assert [item.code for item in stats] == [CODE, "old"]
    item = stats[0]
    assert (item.scans, item.scan_users, item.scans_outside) == (5, 3, 1)
    assert (item.grants, item.grants_new, item.grants_existing, item.opened) == (3, 1, 2, 2)
    assert (item.orders_created, item.orders_paid, item.orders_refunded) == (5, 3, 1)
    assert item.paid_amount == Decimal("161500")
    assert item.points_used == 5000
    assert item.discount_given == Decimal("8500")
    assert item.holders_other_paid == 2
    assert [(b.name, b.username, b.order_number, b.total) for b in item.buyers] == [
        ("Ali Valiev", "ali", "EV-1", Decimal("95000")),
        ("Bek", None, "EV-2", Decimal("47500")),
        ("Dilnoza D.", None, "EV-3", Decimal("19000")),
    ]
    empty = stats[1]
    assert (empty.scans, empty.grants, empty.orders_created, empty.holders_other_paid) == (
        0,
        0,
        0,
        0,
    )
    assert empty.paid_amount == 0

    await ev.feed("/qr_stats", uid=ADMIN)
    report = ev.sent()[0].text
    for line in [
        "nutriforum2026 — NUTRIFORUM 2026, 5%",
        "Период: 28.11.2026 – 31.12.2026 (Ташкент)",
        "Сканы: 5, уникальных: 3, вне периода: 1",
        "Скидки выданы: 3 (новые: 1, существующие: 2)",
        "Открыли магазин: 2",
        "Заказы со скидкой: создано 5, оплачено 3, возвращено 1",
        "Оплачено: 161 500 сум, баллами: 5 000",
        "Скидка дана: 8 500 сум",
        "Оплаченные заказы держателей без скидки: 2",
        "• Ali Valiev @ali — №EV-1, 95 000 сум, 06.12.2026 15:00",
        "• Bek — №EV-2, 47 500 сум, 07.12.2026 16:00",
        "• Dilnoza D. — №EV-3, 19 000 сум, 08.12.2026 17:00",
    ]:
        assert line in report.split("\n")
    assert report.index("nutriforum2026 —") < report.index("old — OLD")


@pytest.mark.parametrize(
    ("first", "last"),
    [
        ("Б" * 100, "Ж" * 100),
        # Astral characters take two UTF-16 units each; Telegram counts units (R-41).
        ("😀" * 100, "Ж" * 100),
    ],
    ids=["cyrillic", "astral"],
)
async def test_r41_long_report_is_split_at_line_boundaries(ev, first, last):
    promo = await ev.promotion()
    buyer = await ev.user(201, first=first, last=last, username="u" * 32)
    for number in range(60):
        await seed_order(
            ev, f"EV-{number:04d}", buyer, "paid", 95000, promotion=promo, created=at(6), paid=at(6)
        )
    await ev.feed("/qr_stats", uid=ADMIN)
    parts = [call.text for call in ev.sent()]
    assert len(parts) > 1
    assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in parts)
    async with ev.sessions() as session:
        full = render_report(await promotion_stats(session))
    assert "\n".join(parts) == full
    buyer_lines = [line for part in parts for line in part.split("\n") if line.startswith("• ")]
    assert len(buyer_lines) == 60
    assert all(line.endswith("95 000 сум, 06.12.2026 15:00") for line in buyer_lines)
