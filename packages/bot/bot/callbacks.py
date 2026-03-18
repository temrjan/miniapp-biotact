"""Callback data factories for the bot."""

from aiogram.filters.callback_data import CallbackData


class LanguageCallback(CallbackData, prefix="lang"):
    """Language selection callback."""

    code: str  # "ru" | "uz"
