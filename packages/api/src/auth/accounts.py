"""Transactional operator writes; role remains owned by the existing User."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import AdminPasswordAccount
from src.auth.passwords import normalize_login
from src.users.models import User


class AccountRefused(Exception):
    """Value-free validation/identity/conflict refusal."""


async def create_account(db: AsyncSession, login: str, telegram_id: int, hashed: str) -> None:
    login = normalize_login(login)
    if not 1 <= telegram_id <= 9223372036854775807:
        raise AccountRefused()
    user = await db.scalar(
        select(User)
        .where(User.telegram_id == telegram_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_active:
        raise AccountRefused()
    occupied = await db.scalar(
        select(AdminPasswordAccount.user_id).where(
            (AdminPasswordAccount.login == login) | (AdminPasswordAccount.user_id == user.id)
        )
    )
    if occupied is not None:
        raise AccountRefused()
    db.add(AdminPasswordAccount(user_id=user.id, login=login, password_hash=hashed))
    user.is_admin = True
    await db.flush()


async def change_account(db: AsyncSession, login: str, *, hashed: str | None = None) -> None:
    """Lock User first on all operator paths, then re-read/lock its credential."""
    login = normalize_login(login)
    user_id = await db.scalar(
        select(AdminPasswordAccount.user_id).where(AdminPasswordAccount.login == login)
    )
    if user_id is None:
        raise AccountRefused()
    user = await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    account = await db.scalar(
        select(AdminPasswordAccount)
        .where(AdminPasswordAccount.login == login)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or account is None:
        raise AccountRefused()
    if hashed is None:
        user.is_admin = False
    else:
        account.password_hash = hashed
    await db.flush()
