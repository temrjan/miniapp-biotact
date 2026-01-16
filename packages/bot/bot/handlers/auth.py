"""Admin auth handler - verify codes from admin panel."""

import re
import httpx
from aiogram import Router, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message

from bot.core.config import settings


router = Router(name="auth")

# API URL for verifying codes
API_URL = "https://app.biotact.uz/api/v1/admin/auth/verify"

# Regex for 6-character hex code
CODE_PATTERN = re.compile(r'^[A-Fa-f0-9]{6}$')


async def verify_auth_code(code: str, user: dict) -> tuple[bool, str]:
    """Send code to API for verification."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                API_URL,
                json={
                    "code": code,
                    "telegram_id": user["id"],
                    "first_name": user["first_name"],
                    "last_name": user.get("last_name"),
                    "username": user.get("username"),
                },
                headers={
                    "X-Bot-Token": settings.bot_token.get_secret_value(),
                    "Content-Type": "application/json",
                },
            )

            if response.status_code == 200:
                return True, "verified"
            elif response.status_code == 403:
                return False, "not_admin"
            elif response.status_code == 404:
                return False, "not_found"
            else:
                return False, "error"

    except Exception as e:
        print(f"Auth verification error: {e}")
        return False, "error"


@router.message(CommandStart(deep_link=True))
async def cmd_start_with_code(message: Message, command: CommandObject) -> None:
    """Handle /start with auth code deep link."""
    args = command.args

    if not args or not args.startswith("auth_"):
        # Not an auth code, let other handlers process
        return

    code = args.replace("auth_", "")

    if not CODE_PATTERN.match(code):
        await message.answer(
            "❌ Неверный формат кода авторизации."
        )
        return

    user_data = {
        "id": message.from_user.id,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "username": message.from_user.username,
    }

    success, status = await verify_auth_code(code, user_data)

    if success:
        await message.answer(
            "✅ <b>Авторизация успешна!</b>\n\n"
            "Вернитесь в браузер — вход выполнен автоматически."
        )
    elif status == "not_admin":
        await message.answer(
            "❌ <b>Доступ запрещён</b>\n\n"
            "У вас нет прав администратора."
        )
    elif status == "not_found":
        await message.answer(
            "❌ <b>Код не найден</b>\n\n"
            "Код истёк или уже был использован.\n"
            "Запросите новый код на странице входа."
        )
    else:
        await message.answer(
            "❌ <b>Ошибка</b>\n\n"
            "Не удалось проверить код. Попробуйте позже."
        )


@router.message(F.text.regexp(CODE_PATTERN))
async def handle_auth_code_message(message: Message) -> None:
    """Handle direct auth code message."""
    code = message.text.strip().upper()

    user_data = {
        "id": message.from_user.id,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "username": message.from_user.username,
    }

    success, status = await verify_auth_code(code, user_data)

    if success:
        await message.answer(
            "✅ <b>Авторизация успешна!</b>\n\n"
            "Вернитесь в браузер — вход выполнен автоматически."
        )
    elif status == "not_admin":
        await message.answer(
            "❌ <b>Доступ запрещён</b>\n\n"
            "У вас нет прав администратора."
        )
    elif status == "not_found":
        await message.answer(
            "❌ <b>Код не найден</b>\n\n"
            "Возможно код истёк или был использован.\n"
            "Запросите новый код на странице входа."
        )
    else:
        await message.answer(
            "❌ <b>Ошибка</b>\n\n"
            "Не удалось проверить код. Попробуйте позже."
        )
