"""Release form in the notification line — derived from the product slug.

Sales decide from this line what to prepare for shipment. Two pairs in the catalogue share a name
and a price and differ only in form (capsules vs sticks), so without the mark the line is
ambiguous and the wrong parcel can go out. Adding the form to the product NAME is forbidden by
management, so the server derives it and only here, in the notification.

The rule is a CONVENTION, not data: a product whose slug does not end with a known suffix stays
unmarked, silently. Test ``test_unknown_form_like_suffix_is_not_guessed`` pins that blind spot on
purpose — the next reader has to see the border of the rule, not discover it from a complaint.
"""

import pytest

from src.notifications.service import TelegramNotifier


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the notification text instead of sending it to Telegram."""
    captured: list[str] = []

    async def _capture(self: TelegramNotifier, text: str, parse_mode: str = "HTML") -> bool:
        captured.append(text)
        return True

    monkeypatch.setattr(TelegramNotifier, "send_message", _capture)
    return captured


async def _line(slug: str | None, *, with_key: bool = True) -> None:
    """Send a one-item paid notification; the text lands in the ``sent`` fixture."""
    item: dict[str, object] = {
        "product_name": "BIFOLAK® ACTIVE",
        "quantity": 1,
        "product_price": 75500,
    }
    if with_key:
        item["slug"] = slug
    await TelegramNotifier().notify_payment_success(
        order_number="ORD-TEST", total=75500.0, items=[item]
    )


async def test_capsules_slug_marks_the_line(sent: list[str]) -> None:
    await _line("bifolak_active_caps")

    assert "BIFOLAK® ACTIVE (капсулы) - 1 шт" in sent[0]


async def test_stick_slug_marks_the_line(sent: list[str]) -> None:
    await _line("bifolak_active_stik")

    assert "BIFOLAK® ACTIVE (стик) - 1 шт" in sent[0]


async def test_slug_without_form_leaves_the_line_untouched(sent: list[str]) -> None:
    # Товар без формы в slug'е: строка обязана остаться символ в символ прежней.
    # Разделитель тысяч здесь запятая — позиции форматируются `{price:,.0f}`, а не `_format_price`.
    await _line("bifolak_neo")

    assert "1. BIFOLAK® ACTIVE - 1 шт × 75,500 сум" in sent[0]
    assert "(" not in sent[0].split("Товары:</b>")[1].split("💰")[0]


async def test_deleted_product_yields_no_mark(sent: list[str]) -> None:
    # `order_items.product_id` — nullable с ON DELETE SET NULL: товар удалили, позиция осталась.
    await _line(None)

    assert "BIFOLAK® ACTIVE - 1 шт" in sent[0]


async def test_unknown_form_like_suffix_is_not_guessed(sent: list[str]) -> None:
    # Граница соглашения: похоже на форму, но в таблице такого нет — не угадываем.
    await _line("bifolak_active_tabs")

    assert "BIFOLAK® ACTIVE - 1 шт" in sent[0]
    assert "капсулы" not in sent[0]
    assert "стик" not in sent[0]


async def test_form_marker_in_the_middle_is_not_a_form(sent: list[str]) -> None:
    """A known marker that is not the ENDING must not mark the line.

    Separates suffix matching from substring matching: ``_caps`` occurs inside this slug, so a
    substring check would confidently print «капсулы» for a product nobody described that way.
    Without this exact value the two implementations are indistinguishable — the mutation survives.
    """
    await _line("bifolak_active_caps_new")

    assert "BIFOLAK® ACTIVE - 1 шт" in sent[0]
    assert "капсулы" not in sent[0]


async def test_missing_slug_key_does_not_break_the_notification(sent: list[str]) -> None:
    # Вызывающих мест три; забытый ключ не имеет права уронить уведомление —
    # это означало бы «отдел продаж не узнал об оплаченном заказе».
    await _line(None, with_key=False)

    assert len(sent) == 1
    assert "BIFOLAK® ACTIVE - 1 шт" in sent[0]
