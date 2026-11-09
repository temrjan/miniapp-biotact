"""Event QR discount (spec event-qr-discount S3): pricing, quote, order, points, read, operator.

Requests run through the production commit/rollback ``get_db`` semantics of the order redeem
fixtures; expectations are written from the spec's numbers, not computed by the pricing code.
"""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from src.config import settings
from src.employee_referrals.models import ReferralOrderSnapshot
from src.event_promotions import operator
from src.event_promotions.models import EventPromotion, EventPromotionGrant
from src.event_promotions.operator import PromotionCommandError
from src.event_promotions.service import active_promotion
from src.orders.models import Order, OrderItem
from src.products.models import Product, ProductSize
from tests import test_employee_referral_order_capture as capture_fixtures
from tests import test_guest_checkout as guest_fixtures
from tests import test_orders_redeem as redeem_fixtures
from tests.conftest import TEST_DATABASE_URL
from tests.test_orders_redeem import _headers, _payload, _seed_points, _seed_user

_pin_bot_token = redeem_fixtures._pin_bot_token
client = redeem_fixtures.client
maker = redeem_fixtures.maker
sent_messages = redeem_fixtures.sent_messages
checkout = capture_fixtures.checkout
guest = guest_fixtures.guest
scratch = guest_fixtures.scratch

BUYER = 80950
QUOTE = "/api/v1/orders/quote"
ORDERS = "/api/v1/orders"
ME = "/api/v1/event-promotions/me"
NOW = datetime.now(UTC)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "event_promotions_enabled", True)


async def _promotion(
    maker,
    *,
    code: str = "nutriforum2026",
    percent: int = 5,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> int:
    async with maker() as db, db.begin():
        promotion = EventPromotion(
            code=code,
            title="NUTRIFORUM 2026",
            percent=percent,
            starts_at=starts_at or NOW - timedelta(days=1),
            ends_at=ends_at or NOW + timedelta(days=30),
        )
        db.add(promotion)
        await db.flush()
        return promotion.id


async def _grant(maker, promotion_id: int, user_id: int) -> int:
    async with maker() as db, db.begin():
        grant = EventPromotionGrant(promotion_id=promotion_id, user_id=user_id, is_new_user=True)
        db.add(grant)
        await db.flush()
        return grant.id


async def _product(maker, price: str, sale: str | None = None, *, sized: bool = False) -> int:
    async with maker() as db, db.begin():
        product = Product(
            name=f"Product {price}/{sale}",
            slug=f"p-{price}-{sale}-{sized}",
            price=Decimal(price),
            is_sale=sale is not None,
            sale_price=Decimal(sale) if sale is not None else None,
            sizes_initialized_at=NOW if sized else None,
        )
        db.add(product)
        await db.flush()
        if sized:
            db.add(ProductSize(product_id=product.id, size="L", available_quantity=10))
        return product.id


async def _decoy_grants(maker) -> None:
    """A first promotion with two grants of other users, so that the next promotion id and the
    holder's grant id differ, as they do in production (R-12, R-21)."""
    decoy = await _promotion(maker, code="decoy")
    for telegram_id in (80951, 80952):
        await _grant(maker, decoy, await _seed_user(maker, telegram_id))


async def _holder(maker) -> int:
    uid = await _seed_user(maker, BUYER)
    await _grant(maker, await _promotion(maker), uid)
    return uid


def _items(*lines: tuple[int, int, str | None]) -> dict[str, object]:
    body = _payload(lines[0][0])
    body["items"] = [{"product_id": p, "quantity": q, "size": s} for p, q, s in lines]
    return body


# --------------------------------------------------------------------------- R-01, R-10


async def test_switch_off_gives_base_quote_and_inactive_read(client, maker):
    """R-01: an existing grant with the API switch OFF changes nothing."""
    await _holder(maker)
    pid = await _product(maker, "100000")
    quote = (await client.post(QUOTE, headers=_headers(BUYER), json=_payload(pid))).json()
    assert quote["total"] == "100000.00"
    assert quote["lines"][0]["unit_price"] == "100000.00"
    assert not any(key.startswith("promotion_") for key in quote)
    me = await client.get(ME, headers=_headers(BUYER))
    assert me.json() == {"active": False}


@pytest.mark.parametrize(
    ("price", "sale", "quantity", "unit", "line_total", "discount"),
    [
        ("100000", None, 1, "95000.00", "95000.00", "5000.00"),
        ("100000", None, 3, "95000.00", "285000.00", "15000.00"),
        # Half up to a whole sum: 10010 * 0.95 = 9509.5 -> 9510.
        ("10010", None, 1, "9510.00", "9510.00", "500.00"),
        # Half up, not half even: 10030 * 0.95 = 9528.5 -> 9529 (half even would give 9528).
        ("10030", None, 1, "9529.00", "9529.00", "501.00"),
        # Below one half rounds down: 10012 * 0.95 = 9511.4 -> 9511 (rounding up would give 9512).
        ("10012", None, 1, "9511.00", "9511.00", "501.00"),
        # Sale price below the promotion price: unchanged, no attribution.
        ("200000", "150000", 1, "150000.00", "150000.00", "0.00"),
        # Sale price equal to the promotion price: a tie keeps the sale, no attribution.
        ("100000", "95000", 1, "95000.00", "95000.00", "0.00"),
        # Sale price above the promotion price: the promotion wins by the difference.
        ("100000", "99000", 2, "95000.00", "190000.00", "8000.00"),
    ],
)
async def test_quote_line_is_best_of_catalogue_and_promotion(
    client, maker, enabled, price, sale, quantity, unit, line_total, discount
):
    """R-10, R-13: unit price, line total and the quote's promotion amount, percent and end."""
    await _holder(maker)
    pid = await _product(maker, price, sale)
    quote = (
        await client.post(QUOTE, headers=_headers(BUYER), json=_payload(pid, quantity=quantity))
    ).json()
    assert (quote["lines"][0]["unit_price"], quote["lines"][0]["line_total"]) == (unit, line_total)
    assert quote["total"] == line_total
    assert quote["promotion_discount"] == discount
    assert quote["promotion_percent"] == 5
    # _holder's promotion ends at NOW + 30 days.
    assert datetime.fromisoformat(quote["promotion_ends_at"]) == NOW + timedelta(days=30)


async def test_no_grant_keeps_base_quote(client, maker, enabled):
    """R-10: a buyer without a grant keeps the base quote."""
    await _seed_user(maker, BUYER)
    await _promotion(maker)
    pid = await _product(maker, "100000")
    quote = (await client.post(QUOTE, headers=_headers(BUYER), json=_payload(pid))).json()
    assert quote["total"] == "100000.00"
    assert not any(key.startswith("promotion_") for key in quote)


async def test_sized_merged_lines_sum_discount_and_order_stores_reference(client, maker, enabled):
    """R-10 merged sized lines, R-12 reference on a discounted order, none on an undiscounted."""
    await _decoy_grants(maker)
    uid = await _holder(maker)
    sized = await _product(maker, "100000", sized=True)
    body = _items((sized, 1, "L"), (sized, 2, "L"))
    quote = (await client.post(QUOTE, headers=_headers(BUYER), json=body)).json()
    assert quote["total"] == "285000.00"
    response = await client.post(
        ORDERS,
        headers=_headers(BUYER),
        json=body | {"quote_fingerprint": quote["quote_fingerprint"]},
    )
    assert response.status_code == 201, response.text
    async with maker() as db:
        order = await db.get(Order, response.json()["id"])
        item = (await db.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).one()
        promotion_id = await db.scalar(
            select(EventPromotion.id).where(EventPromotion.code == "nutriforum2026")
        )
        grant_id = await db.scalar(
            select(EventPromotionGrant.id).where(EventPromotionGrant.user_id == uid)
        )
    assert grant_id != promotion_id  # the stored reference is the promotion, not the grant
    assert (order.total, order.promotion_id, order.user_id) == (
        Decimal("285000"),
        promotion_id,
        uid,
    )
    assert (item.quantity, item.product_price, item.total) == (3, 95000, 285000)
    assert item.promotion_discount == Decimal("15000.00")
    assert (item.is_sale_at_order, item.regular_price_at_order) == (False, Decimal("100000"))

    sale = await _product(maker, "200000", "150000")
    body = _payload(sale)
    quote = (await client.post(QUOTE, headers=_headers(BUYER), json=body)).json()
    response = await client.post(
        ORDERS,
        headers=_headers(BUYER),
        json=body | {"quote_fingerprint": quote["quote_fingerprint"]},
    )
    assert response.status_code == 201, response.text
    async with maker() as db:
        order = await db.get(Order, response.json()["id"])
        item = (await db.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).one()
    assert (order.promotion_id, item.promotion_discount) == (None, Decimal("0"))


async def test_grant_selection_and_window_bounds(maker, enabled):
    """R-10: highest percent, then latest end, then highest grant id; [starts_at, ends_at)."""
    uid = await _seed_user(maker, BUYER)
    ends = NOW + timedelta(days=10)
    early = await _promotion(maker, code="early", ends_at=ends - timedelta(days=1))
    late = await _promotion(maker, code="late", ends_at=ends)
    await _grant(maker, early, uid)
    await _grant(maker, late, uid)
    async with maker() as db:
        assert (await active_promotion(db, uid)).promotion_id == late
    bigger = await _promotion(maker, code="bigger", percent=7, ends_at=ends - timedelta(days=2))
    await _grant(maker, bigger, uid)
    same = await _promotion(maker, code="same", percent=7, ends_at=ends - timedelta(days=2))
    newest = await _grant(maker, same, uid)
    start = NOW - timedelta(days=1)
    async with maker() as db:
        selected = await active_promotion(db, uid)
        assert (selected.percent, selected.grant_id) == (7, newest)
        # Only the 5% "late" grant is open in its last microsecond; none at its end.
        last = await active_promotion(db, uid, now=ends - timedelta(microseconds=1))
        assert last.promotion_id == late
        assert await active_promotion(db, uid, now=ends) is None
        assert (await active_promotion(db, uid, now=start)).percent == 7
        assert await active_promotion(db, uid, now=start - timedelta(microseconds=1)) is None


# --------------------------------------------------------------------------- R-11


async def test_guest_order_ignores_any_grant(guest, monkeypatch):
    """R-11: the website guest path is priced as at base while a grant exists."""
    monkeypatch.setattr(settings, "event_promotions_enabled", True)
    guest.sql(
        "INSERT INTO users (id,telegram_id,language_code,is_active,is_admin) "
        "VALUES (901,990901,'ru',true,false);"
        "INSERT INTO event_promotions (id,code,title,percent,starts_at,ends_at) "
        "VALUES (1,'g','NUTRIFORUM 2026',5,now()-interval '1 day',now()+interval '1 day');"
        "INSERT INTO event_promotion_grants (promotion_id,user_id,is_new_user) VALUES (1,901,true)"
    )
    result = await guest_fixtures.quote(guest.client, guest_fixtures.ORDINARY)
    assert (result["lines"][0]["unit_price"], result["total"]) == ("60000.00", "120000.00")
    assert not any(key.startswith("promotion_") for key in result)
    assert (await guest_fixtures.place(guest.client)).status_code == 201
    assert guest.sql("SELECT total, promotion_id FROM orders") == [(Decimal("120000.00"), None)]
    assert guest.sql("SELECT promotion_discount FROM order_items") == [(Decimal("0.00"),)]


# --------------------------------------------------------------------------- R-13, R-14, D8


async def test_quote_agreement_for_discounted_orders(client, maker, enabled):
    """R-13: quote required; an expired grant yields quote_mismatch with an undiscounted quote."""
    await _holder(maker)
    pid = await _product(maker, "100000")
    body = _payload(pid)
    refused = await client.post(ORDERS, headers=_headers(BUYER), json=body)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "quote_required"
    quote = (await client.post(QUOTE, headers=_headers(BUYER), json=body)).json()
    async with maker() as db, db.begin():
        promotion = await db.scalar(select(EventPromotion))
        promotion.ends_at = datetime.now(UTC) - timedelta(seconds=1)
    stale = await client.post(
        ORDERS,
        headers=_headers(BUYER),
        json=body | {"quote_fingerprint": quote["quote_fingerprint"]},
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "quote_mismatch"
    assert detail["quote"]["total"] == "100000.00"
    assert not any(key.startswith("promotion_") for key in detail["quote"])
    async with maker() as db:
        assert await db.scalar(select(func.count()).select_from(Order)) == 0

    async with maker() as db, db.begin():
        (await db.scalar(select(EventPromotion))).ends_at = NOW + timedelta(days=30)
    quote = (await client.post(QUOTE, headers=_headers(BUYER), json=body)).json()
    accepted = await client.post(
        ORDERS,
        headers=_headers(BUYER),
        json=body | {"quote_fingerprint": quote["quote_fingerprint"]},
    )
    assert accepted.status_code == 201, accepted.text
    assert Decimal(str(accepted.json()["total"])) == Decimal("95000")


async def test_full_points_cover_uses_reduced_subtotal(client, maker, enabled):
    """R-14: points cover the reduced total; the undiscounted total is refused."""
    uid = await _holder(maker)
    await _seed_points(maker, user_id=uid, amount=100000)
    pid = await _product(maker, "100000")
    quote = (
        await client.post(QUOTE, headers=_headers(BUYER), json=_payload(pid, points_to_apply=95000))
    ).json()
    assert quote["max_redeemable"] == 95000
    refused = await client.post(
        QUOTE, headers=_headers(BUYER), json=_payload(pid, points_to_apply=100000)
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == {
        "error": "invalid_redeem_amount",
        "points_to_apply": 100000,
        "max_redeemable": 95000,
    }
    accepted = await client.post(
        ORDERS,
        headers=_headers(BUYER),
        json=_payload(pid, points_to_apply=95000)
        | {"quote_fingerprint": quote["quote_fingerprint"]},
    )
    assert accepted.status_code == 201, accepted.text
    assert (accepted.json()["payment_status"], accepted.json()["points_applied"]) == ("paid", 95000)


async def test_order_route_refuses_points_above_reduced_total(client, maker, enabled):
    """R-14 on the order route, which validates the points itself: 100 000 points against the
    reduced total of 95 000 are refused and no order remains."""
    uid = await _holder(maker)
    await _seed_points(maker, user_id=uid, amount=100000)
    pid = await _product(maker, "100000")
    body = _payload(pid, points_to_apply=100000)
    # The quote route refuses this amount, so take the agreement the order route itself offers.
    required = await client.post(ORDERS, headers=_headers(BUYER), json=body)
    assert required.json()["detail"]["code"] == "quote_required"
    fingerprint = required.json()["detail"]["quote"]["quote_fingerprint"]
    refused = await client.post(
        ORDERS, headers=_headers(BUYER), json=body | {"quote_fingerprint": fingerprint}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == {
        "error": "invalid_redeem_amount",
        "points_to_apply": 100000,
        "max_redeemable": 95000,
    }
    async with maker() as db:
        assert await db.scalar(select(func.count()).select_from(Order)) == 0


async def test_referral_attribution_and_discount_both_apply(checkout, monkeypatch):
    """D8, spec §5: an employee-referral order of a grant holder keeps capture and gets the discount."""
    h = checkout
    monkeypatch.setattr(settings, "event_promotions_enabled", True)
    async with h.sessions.begin() as writer:
        promotion = EventPromotion(
            code="d8",
            title="NUTRIFORUM 2026",
            percent=5,
            starts_at=NOW - timedelta(days=1),
            ends_at=NOW + timedelta(days=1),
        )
        writer.add(promotion)
        await writer.flush()
        writer.add(EventPromotionGrant(promotion_id=promotion.id, user_id=1, is_new_user=False))
    body = h.body()
    quote = await h.client.post(ORDERS + "/quote", json=body, headers=h.headers)
    assert quote.json()["total"] == "9500.00"
    response = await h.create(body | {"quote_fingerprint": quote.json()["quote_fingerprint"]})
    assert response.status_code == 201, response.text
    async with h.sessions() as reader:
        assert await reader.scalar(select(func.count()).select_from(ReferralOrderSnapshot)) == 1
        order = await reader.get(Order, response.json()["id"])
        item = await reader.scalar(select(OrderItem))
    assert (order.total, order.promotion_id, item.promotion_discount) == (9500, promotion.id, 500)


# --------------------------------------------------------------------------- R-20, R-21


async def test_my_discount_read_and_first_opening(client, maker, enabled):
    """R-20 each response and the 401; R-21 the first opening is kept."""
    ends = (NOW + timedelta(days=30)).replace(microsecond=0)
    unauthenticated = await client.get(ME)
    assert unauthenticated.status_code == 401
    uid = await _seed_user(maker, BUYER)
    inactive = await client.get(ME, headers=_headers(BUYER))
    assert (inactive.status_code, inactive.json()) == (200, {"active": False})
    assert inactive.headers["cache-control"] == "no-store"
    await _decoy_grants(maker)
    promotion_id = await _promotion(maker, ends_at=ends)
    grant_id = await _grant(maker, promotion_id, uid)
    assert grant_id != promotion_id  # the opening marks the holder's grant row, found by its id
    first = await client.get(ME, headers=_headers(BUYER))
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {
        "active": True,
        "title": "NUTRIFORUM 2026",
        "percent": 5,
        "ends_at": ends.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    async with maker() as db:
        opened = (await db.get(EventPromotionGrant, grant_id)).first_opened_at
        others = await db.scalars(
            select(EventPromotionGrant.first_opened_at).where(EventPromotionGrant.id != grant_id)
        )
        assert others.all() == [None, None]
    assert opened is not None
    await client.get(ME, headers=_headers(BUYER))
    async with maker() as db:
        assert (await db.get(EventPromotionGrant, grant_id)).first_opened_at == opened


# --------------------------------------------------------------------------- R-04, D13


async def _promotions(maker) -> list[tuple]:
    async with maker() as db:
        rows = await db.execute(
            select(
                EventPromotion.code,
                EventPromotion.title,
                EventPromotion.percent,
                EventPromotion.starts_at,
                EventPromotion.ends_at,
            ).order_by(EventPromotion.id)
        )
        return [tuple(row) for row in rows]


async def test_operator_create_and_dates_store_exact_tashkent_bounds(maker):
    """R-04 create and D13 dates: exact UTC bounds of Tashkent days."""
    async with maker() as db, db.begin():
        await operator.create(
            db,
            code="nutriforum2026",
            title=" NUTRIFORUM 2026 ",
            percent=5,
            start=date(2026, 11, 28),
            end=date(2026, 12, 31),
        )
    assert await _promotions(maker) == [
        (
            "nutriforum2026",
            "NUTRIFORUM 2026",
            5,
            datetime(2026, 11, 27, 19, tzinfo=UTC),
            datetime(2026, 12, 31, 19, tzinfo=UTC),
        )
    ]
    async with maker() as db, db.begin():
        before, after = await operator.set_dates(
            db, code="nutriforum2026", start=date(2026, 11, 27), end=date(2027, 1, 31)
        )
    assert before.endswith("2026-11-28 00:00 -> 2027-01-01 00:00 (Asia/Tashkent)")
    assert after.endswith("2026-11-27 00:00 -> 2027-02-01 00:00 (Asia/Tashkent)")
    assert (await _promotions(maker))[0][3:] == (
        datetime(2026, 11, 26, 19, tzinfo=UTC),
        datetime(2027, 1, 31, 19, tzinfo=UTC),
    )


CREATE = {
    "code": "fresh",
    "title": "NUTRIFORUM 2026",
    "percent": 5,
    "start": date(2026, 11, 28),
    "end": date(2026, 12, 31),
}


@pytest.mark.parametrize(
    "change",
    [
        {"code": "existing"},
        {"code": "bad_code"},
        {"code": "a" * 41},
        {"code": ""},
        {"code": "fresh\n"},
        {"percent": 0},
        {"percent": 91},
        {"end": date(2026, 11, 27)},
        {"title": "   "},
        {"title": "x" * 61},
    ],
)
async def test_operator_create_refusals_write_nothing(maker, change):
    """R-04: each create refusal writes nothing."""
    async with maker() as db, db.begin():
        await operator.create(db, **(CREATE | {"code": "existing"}))
    before = await _promotions(maker)
    with pytest.raises(PromotionCommandError):
        async with maker() as db, db.begin():
            await operator.create(db, **(CREATE | change))
    assert await _promotions(maker) == before


@pytest.mark.parametrize(
    "change", [{"percent": 1}, {"percent": 90}, {"title": "x" * 60}, {"code": "a" * 40}]
)
async def test_operator_create_accepts_inclusive_limits(maker, change):
    """R-04: percent 1 and 90, a title of 60 and a code of 40 characters are accepted."""
    async with maker() as db, db.begin():
        await operator.create(db, **(CREATE | change))
    expected = CREATE | change
    assert [row[:3] for row in await _promotions(maker)] == [
        (expected["code"], expected["title"], expected["percent"])
    ]


@pytest.mark.parametrize(
    "command",
    [
        lambda db: operator.set_dates(
            db, code="missing", start=date(2026, 12, 1), end=date(2026, 12, 2)
        ),
        lambda db: operator.set_dates(
            db, code="running", start=date(2026, 12, 2), end=date(2026, 12, 1)
        ),
        lambda db: operator.end(db, code="missing"),
        # Ending "now" a promotion that has not started or has ended would leave an empty window
        # or reopen a closed one.
        lambda db: operator.end(db, code="future"),
        lambda db: operator.end(db, code="ended"),
    ],
)
async def test_operator_dates_and_end_refusals_write_nothing(maker, command):
    """R-04, D13: dates and end refusals write nothing."""
    await _promotion(maker, code="running")
    await _promotion(maker, code="future", starts_at=NOW + timedelta(days=1))
    await _promotion(
        maker, code="ended", starts_at=NOW - timedelta(days=2), ends_at=NOW - timedelta(days=1)
    )
    before = await _promotions(maker)
    with pytest.raises(PromotionCommandError):
        async with maker() as db, db.begin():
            await command(db)
    assert await _promotions(maker) == before


async def test_operator_end_closes_a_running_promotion_now(maker):
    """R-04: end sets ends_at to now."""
    await _promotion(maker, code="running")
    at = datetime.now(UTC)
    async with maker() as db, db.begin():
        await operator.end(db, code="running", now=at)
    assert (await _promotions(maker))[0][4] == at


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "event_promotions.py"


async def _script(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the operator script in a fresh interpreter against the test database."""
    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=SCRIPT.parents[1],
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        timeout=120,
        check=False,
    )


async def test_operator_script_commits_and_refuses_with_exit_status(maker):
    """R-04, D13 through the script itself: create, dates and list commit and print; a refusal
    exits with status 1 and writes nothing."""
    created = await _script(
        "create",
        "--code",
        "nutriforum2026",
        "--title",
        "NUTRIFORUM 2026",
        "--percent",
        "5",
        "--start",
        "2026-11-28",
        "--end",
        "2026-12-31",
    )
    assert created.returncode == 0, created.stderr
    assert created.stdout == (
        "created nutriforum2026  5%  2026-11-28 00:00 -> 2027-01-01 00:00 (Asia/Tashkent)\n"
    )
    assert await _promotions(maker) == [
        (
            "nutriforum2026",
            "NUTRIFORUM 2026",
            5,
            datetime(2026, 11, 27, 19, tzinfo=UTC),
            datetime(2026, 12, 31, 19, tzinfo=UTC),
        )
    ]
    moved = await _script(
        "dates", "--code", "nutriforum2026", "--start", "2026-11-27", "--end", "2027-01-31"
    )
    assert moved.returncode == 0, moved.stderr
    assert (await _promotions(maker))[0][3:] == (
        datetime(2026, 11, 26, 19, tzinfo=UTC),
        datetime(2027, 1, 31, 19, tzinfo=UTC),
    )
    listed = await _script("list")
    assert listed.stdout == (
        "nutriforum2026  5%  2026-11-27 00:00 -> 2027-02-01 00:00 (Asia/Tashkent)\n"
    )
    before = await _promotions(maker)
    refused = await _script(
        "create",
        "--code",
        "other",
        "--title",
        "T",
        "--percent",
        "91",
        "--start",
        "2026-11-28",
        "--end",
        "2026-12-31",
    )
    assert refused.returncode == 1
    assert refused.stderr.startswith("refused: ")
    assert await _promotions(maker) == before
