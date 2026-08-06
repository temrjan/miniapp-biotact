"""Payment notification: how much the card actually collected.

The accountants asked for it (06.08): the order sum and the redeemed points stood side by side and
the difference — what the customer paid in money — was worked out in the head. The sibling
new-order notification has printed that figure all along, so this fixes a mismatch rather than
inventing new arithmetic.

Hits the notifier directly — no DB, no network: ``send_message`` is swapped out and the text is
asserted. There are THREE distinct cases; printing the line unconditionally would be a new lie.
"""

import pytest

from src.notifications.service import TelegramNotifier

CARD_LINE = "Итого оплачено картой:"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the notification text instead of sending it to Telegram."""
    captured: list[str] = []

    async def _capture(self: TelegramNotifier, text: str, parse_mode: str = "HTML") -> bool:
        captured.append(text)
        return True

    monkeypatch.setattr(TelegramNotifier, "send_message", _capture)
    return captured


ITEMS = [{"product_name": "BIFOLAK® ACTIVE, капсулы", "quantity": 1, "product_price": 75500}]


async def _notify(points_applied: int, total: float = 75500.0) -> None:
    await TelegramNotifier().notify_payment_success(
        order_number="ORD-TEST",
        total=total,
        items=ITEMS,
        points_applied=points_applied,
    )


async def test_partial_redeem_shows_what_the_card_collected(sent: list[str]) -> None:
    # Частичное списание — ровно тот случай, ради которого бухгалтерия просила строку.
    await _notify(points_applied=5700)

    message = sent[0]
    assert CARD_LINE in message
    assert "69 800" in message  # 75 500 − 5 700
    assert "Списано баллов:" in message


async def test_card_only_order_has_no_card_line(sent: list[str]) -> None:
    # Без баллов уплаченное и есть «Сумма» — вторая строка была бы дублем.
    await _notify(points_applied=0)

    message = sent[0]
    assert CARD_LINE not in message
    assert "Списано баллов:" not in message
    assert "75 500" in message


async def test_full_points_coverage_has_no_card_line(sent: list[str]) -> None:
    # Карта не участвовала: «оплачено картой 0» было бы шумом, и заголовок уже всё сказал.
    await _notify(points_applied=80000)

    message = sent[0]
    assert "ОПЛАЧЕНО БАЛЛАМИ" in message
    assert CARD_LINE not in message


async def test_points_exactly_equal_total_is_full_coverage(sent: list[str]) -> None:
    # Граница, разделяющая соседние случаи: на `>` вместо `>=` этот тест краснеет.
    await _notify(points_applied=75500)

    message = sent[0]
    assert "ОПЛАЧЕНО БАЛЛАМИ" in message
    assert CARD_LINE not in message
