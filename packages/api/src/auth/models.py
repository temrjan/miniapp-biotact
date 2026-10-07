"""Admin user model for JWT authentication."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class AdminUser(Base):
    """Email/password account of a REMOVED login circuit — kept for schema parity only.

    ⚰️ **No route serves this model since 2026-07-27 (PR-S).** ``jwt_router`` — public
    ``/auth/register``, ``/auth/login`` and friends — was deleted: it minted ``type="access"``
    tokens that ``verify_admin_jwt`` accepted without any role check, so self-registration reached
    every admin endpoint.

    Why the class is still here: ``admin_users`` exists in the migration history, and
    ``alembic/env.py`` only excludes ``_REF_TABLES``. Drop the model while keeping the table and
    autogenerate proposes ``DROP TABLE`` → the ``Migration parity`` CI gate goes red. This class IS
    the table's sole representation in ``Base.metadata`` (imported by ``src/models_registry.py``).

    ⚠️ Do not "restore the login" by adding a router back — that is the hole. Real admin auth lives
    in ``src/auth/admin_router.py`` (Telegram identity + ``User.is_admin``). Removing the table is a
    data decision, deliberately not taken in PR-S.

    ⚠️ Name collision: ``src.auth.dependencies.AdminUser`` is a different symbol — a type alias
    ``Annotated[User, Depends(get_current_admin)]`` over the Telegram ``User``. Check the import.
    """

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    # Profile
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))

    # Email verification
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_token: Mapped[str | None] = mapped_column(String(100))
    verification_token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Password reset
    reset_token: Mapped[str | None] = mapped_column(String(100))
    reset_token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<AdminUser(id={self.id}, email={self.email})>"


class AdminPasswordAccount(Base):
    """Named credential only; current roles and profile belong to User."""

    __tablename__ = "admin_password_accounts"
    __table_args__ = (
        CheckConstraint(
            "login ~ '^[a-z][a-z0-9_.-]{2,63}$' AND login <> 'biotact'",
            name="ck_admin_password_login",
        ),
        CheckConstraint(
            r"password_hash ~ '^\$2b\$12\$[./A-Za-z0-9]{53}$'",
            name="ck_admin_password_hash",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(60))

    def __repr__(self) -> str:
        return f"<AdminPasswordAccount(user_id={self.user_id})>"
