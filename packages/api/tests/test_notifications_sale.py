"""Sale order lines use saved purchase terms in both sales notifications."""

from decimal import Decimal

import pytest

from src.notifications.service import TelegramNotifier


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    messages: list[str] = []

    async def capture(self: TelegramNotifier, text: str, parse_mode: str = "HTML") -> bool:
        messages.append(text)
        return True

    monkeypatch.setattr(TelegramNotifier, "send_message", capture)
    return messages


async def notify(method: str, items: list[dict[str, object]]) -> None:
    notifier = TelegramNotifier()
    await getattr(notifier, method)(order_number="ORD-SALE", total=313500.0, items=items)


def item_lines(message: str) -> str:
    return message.split("📦 <b>Товары:</b>\n", 1)[1].split("\n\n💰", 1)[0]


@pytest.mark.parametrize("method", ["notify_order_created", "notify_payment_success"])
@pytest.mark.parametrize("quantity", [1, 2])
async def test_sale_line_shows_saved_unit_prices_and_quantity(
    sent: list[str], method: str, quantity: int
) -> None:
    items = [
        {
            "product_name": "Жилетка двусторонняя Healthy Lifestyle",
            "quantity": quantity,
            "product_price": Decimal("313500.00"),
            "is_sale_at_order": True,
            "regular_price_at_order": Decimal("330000.00"),
        },
        {"product_name": "BIFOLAK® ACTIVE", "quantity": 1, "product_price": Decimal("75500.00")},
    ]

    await notify(method, items)

    assert item_lines(sent[0]) == (
        "1. Жилетка двусторонняя Healthy Lifestyle\n"
        "Акция: 330 000 → 313 500 сум\n"
        f"{quantity} шт. × 313 500 сум\n"
        "2. BIFOLAK® ACTIVE - 1 шт × 75,500 сум"
    )


@pytest.mark.parametrize("method", ["notify_order_created", "notify_payment_success"])
async def test_sale_line_preserves_fractional_prices(sent: list[str], method: str) -> None:
    await notify(
        method,
        [
            {
                "product_name": "Fractional",
                "quantity": 1,
                "product_price": Decimal("313500.05"),
                "is_sale_at_order": True,
                "regular_price_at_order": Decimal("330000.50"),
            }
        ],
    )

    assert item_lines(sent[0]) == (
        "1. Fractional\nАкция: 330 000,50 → 313 500,05 сум\n1 шт. × 313 500,05 сум"
    )


@pytest.mark.parametrize("method", ["notify_order_created", "notify_payment_success"])
@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"is_sale_at_order": False, "regular_price_at_order": Decimal("80000.00")},
        {"is_sale_at_order": None, "regular_price_at_order": Decimal("80000.00")},
        {"is_sale_at_order": True},
        {"is_sale_at_order": True, "regular_price_at_order": None},
    ],
)
async def test_legacy_and_incomplete_snapshots_keep_exact_ordinary_line(
    sent: list[str], method: str, snapshot: dict[str, object]
) -> None:
    await notify(
        method,
        [{"product_name": "BIFOLAK® ACTIVE", "quantity": 1, "product_price": 75500} | snapshot],
    )

    assert item_lines(sent[0]) == "1. BIFOLAK® ACTIVE - 1 шт × 75,500 сум"


@pytest.mark.parametrize("method", ["notify_order_created", "notify_payment_success"])
async def test_sale_line_escapes_name_and_keeps_release_form(sent: list[str], method: str) -> None:
    await notify(
        method,
        [
            {
                "product_name": "<BIFOLAK & ACTIVE>",
                "quantity": 1,
                "product_price": Decimal("313500.00"),
                "is_sale_at_order": True,
                "regular_price_at_order": Decimal("330000.00"),
                "slug": "bifolak_active_caps",
            },
            {
                "product_name": "Deleted & saved",
                "quantity": 1,
                "product_price": Decimal("313500.00"),
                "is_sale_at_order": True,
                "regular_price_at_order": Decimal("330000.00"),
                "slug": None,
            },
        ],
    )

    assert item_lines(sent[0]) == (
        "1. &lt;BIFOLAK &amp; ACTIVE&gt; (капсулы)\n"
        "Акция: 330 000 → 313 500 сум\n1 шт. × 313 500 сум\n"
        "2. Deleted &amp; saved\nАкция: 330 000 → 313 500 сум\n"
        "1 шт. × 313 500 сум"
    )
