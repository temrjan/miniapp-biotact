"""User text in admin notifications must not break Telegram's HTML markup.

Messages go out with ``parse_mode="HTML"`` and carry text the customer typed — delivery address
(free-form), name from the Telegram profile, phone. Nothing escaped it. A single ``<`` there makes
the markup invalid, Telegram refuses the message, ``send_message`` swallows the exception and
returns False — and none of the three call sites reads that result. The paid order simply never
reaches the sales group.

Two layers are tested here, and they are not the same thing:
  * escaping — closes the KNOWN cause;
  * plain-text fallback in ``send_message`` — closes the unknown ones (over-long text, a field
    someone forgets to escape later). Its warning is part of the contract: without it a silent
    loss would merely become a silent degradation.
"""

import pytest

from src.notifications.service import TelegramNotifier, esc

DIRTY = 'ул. Пушкина <5> & "дом"'


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    """Capture (text, parse_mode) of every POST instead of hitting Telegram."""
    captured: list[tuple[str, str | None]] = []

    async def _capture(self: TelegramNotifier, text: str, parse_mode: str | None) -> None:
        captured.append((text, parse_mode))

    monkeypatch.setattr(TelegramNotifier, "_post_message", _capture)
    monkeypatch.setattr(TelegramNotifier, "is_configured", True)
    return captured


ITEMS = [{"product_name": "Сет «Мама & малыш»", "quantity": 1, "product_price": 75500}]


async def _notify_created(**kwargs: object) -> None:
    await TelegramNotifier().notify_order_created(
        order_number="ORD-1", total=75500.0, items=ITEMS, **kwargs
    )


async def test_address_with_markup_characters_is_escaped(
    sent: list[tuple[str, str | None]],
) -> None:
    await _notify_created(delivery_address=DIRTY)

    text = sent[0][0]
    assert "&lt;5&gt;" in text
    assert "&amp;" in text
    assert "<5>" not in text


async def test_customer_name_cannot_inject_a_tag(sent: list[tuple[str, str | None]]) -> None:
    await _notify_created(customer_name="<b>Иван</b>")

    text = sent[0][0]
    assert "&lt;b&gt;Иван&lt;/b&gt;" in text


async def test_product_name_with_ampersand_is_escaped(sent: list[tuple[str, str | None]]) -> None:
    # Название товара — снимок из каталога; «&» в нём реален (сеты, «B&Q»-подобные имена).
    await _notify_created()

    assert "Сет «Мама &amp; малыш»" in sent[0][0]


async def test_our_own_markup_survives(sent: list[tuple[str, str | None]]) -> None:
    # Экранируются ЗНАЧЕНИЯ, не шаблон: подписи обязаны остаться жирными.
    await _notify_created(delivery_address=DIRTY)

    text = sent[0][0]
    assert "<b>НОВЫЙ ЗАКАЗ</b>" in text
    assert "<b>Клиент:</b>" in text


async def test_both_notification_methods_escape(sent: list[tuple[str, str | None]]) -> None:
    # Дыру нашли в одном методе — чинить надо оба, иначе половина сообщений останется уязвимой.
    await _notify_created(customer_name="<Иван>")
    await TelegramNotifier().notify_payment_success(
        order_number="ORD-1", total=75500.0, items=ITEMS, customer_name="<Иван>"
    )

    assert len(sent) == 2
    for text, _ in sent:
        assert "&lt;Иван&gt;" in text
        assert "<Иван>" not in text


async def test_refused_markup_falls_back_to_plain_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Telegram refuses the formatted message → resend without parse_mode, and say so in the log."""
    attempts: list[str | None] = []

    async def _refuse_html(self: TelegramNotifier, text: str, parse_mode: str | None) -> None:
        attempts.append(parse_mode)
        if parse_mode is not None:
            raise RuntimeError("400 Bad Request: can't parse entities")

    monkeypatch.setattr(TelegramNotifier, "_post_message", _refuse_html)
    monkeypatch.setattr(TelegramNotifier, "is_configured", True)

    with caplog.at_level("WARNING"):
        delivered = await TelegramNotifier().send_message("<b>x</b>")

    assert delivered is True  # сообщение всё-таки дошло
    assert attempts == ["HTML", None]  # сначала с разметкой, потом без неё
    assert any("plain text" in r.message for r in caplog.records if r.levelname == "WARNING")


async def test_successful_send_does_not_retry_or_warn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Иначе «страховка» превратится в постоянный второй запрос и шум в логах.
    attempts: list[str | None] = []

    async def _accept(self: TelegramNotifier, text: str, parse_mode: str | None) -> None:
        attempts.append(parse_mode)

    monkeypatch.setattr(TelegramNotifier, "_post_message", _accept)
    monkeypatch.setattr(TelegramNotifier, "is_configured", True)

    with caplog.at_level("WARNING"):
        delivered = await TelegramNotifier().send_message("<b>x</b>")

    assert delivered is True
    assert attempts == ["HTML"]
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_esc_leaves_quotes_alone() -> None:
    # quote=False намеренно: кавычки в адресе читаются людьми, а атрибутов в наших сообщениях нет.
    assert esc('дом "5"') == 'дом "5"'
    assert esc("a < b & c") == "a &lt; b &amp; c"
