"""Transaction owners for buyer proofs, shared limits and reusable sessions."""

import hmac
import math
import re
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.buyer_auth.mail import BuyerMailer
from src.buyer_auth.models import (
    BuyerAuthLimit,
    BuyerEmailChallenge,
    BuyerEmailIdentity,
    BuyerSession,
)
from src.buyer_auth.security import (
    ABSOLUTE_LIFETIME,
    HANDLE_PATTERN,
    IDLE_LIFETIME,
    PROOF_LIFETIME,
    SECRET_PATTERN,
    BuyerAuthError,
    csrf,
    keyed,
    now,
    require_csrf,
    verifier_digest,
)
from src.config import Settings
from src.users.models import User


async def issue_session(
    db: AsyncSession,
    user: User,
    at: datetime,
    *,
    verifier_factory: Callable[[str], str] | None = None,
) -> tuple[BuyerSession, str]:
    """Join the proof owner's transaction; never commit an independent authority."""
    if not user.is_active:
        raise BuyerAuthError()
    session_id = secrets.token_hex(16)
    token = verifier_factory(session_id) if verifier_factory else secrets.token_hex(32)
    row = BuyerSession(
        id=session_id,
        user_id=user.id,
        verifier_digest=verifier_digest(token),
        generation=1,
        created_at=at,
        authenticated_at=at,
        last_used_at=at,
        absolute_expires_at=at + ABSOLUTE_LIFETIME,
        idle_expires_at=at + IDLE_LIFETIME,
    )
    db.add(row)
    await db.flush()
    return row, token


class BuyerAuthService:
    """Each public operation owns short committed transactions, never get_db's rollback."""

    def __init__(
        self,
        config: Settings,
        sessions: async_sessionmaker[AsyncSession],
        mailer: BuyerMailer | None = None,
        clock: Callable[[], datetime] = now,
    ) -> None:
        self.config = config
        self.sessions = sessions
        self.mailer = mailer if mailer is not None else BuyerMailer(config)
        self.clock = clock

    async def _prepare(self, db: AsyncSession) -> None:
        if self.config.database_echo or db.get_bind().engine.echo:
            raise BuyerAuthError(503, "authentication_unavailable")
        await db.execute(text("SET LOCAL lock_timeout = '5s'"))
        await db.execute(text("SET LOCAL statement_timeout = '30s'"))

    async def _lock_email(self, db: AsyncSession, email: str) -> None:
        digest = keyed(self.config, "email-transaction-lock", email)
        lock_id = int.from_bytes(bytes.fromhex(digest[:16]), "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})

    async def _limits(
        self,
        db: AsyncSession,
        at: datetime,
        rules: list[tuple[str, str, int, int]],
    ) -> BuyerAuthError | None:
        # Keys lock in stable order across processes. Events are bounded by the rule's quota.
        prepared = sorted(
            (keyed(self.config, "rate-" + p, value), count, window)
            for p, value, count, window in rules
        )
        rows: list[tuple[BuyerAuthLimit, list[float], int]] = []
        wait = 0
        stamp = at.timestamp()
        for key, count, window in prepared:
            await db.execute(
                insert(BuyerAuthLimit)
                .values(key=key, events=[], expires_at=at + timedelta(seconds=window))
                .on_conflict_do_nothing(index_elements=[BuyerAuthLimit.key])
            )
            row = (
                await db.scalars(
                    select(BuyerAuthLimit).where(BuyerAuthLimit.key == key).with_for_update()
                )
            ).one()
            events = sorted(value for value in row.events if value > stamp - window)
            if len(events) >= count:
                wait = max(wait, math.ceil(events[-count] + window - stamp))
            rows.append((row, events, window))
        if wait:
            return BuyerAuthError(429, "try_later", max(1, wait))
        for row, events, window in rows:
            row.events = sorted([*events, stamp])
            row.expires_at = max(row.expires_at, at + timedelta(seconds=window))
        return None

    async def account(self, purpose: str, peer: str, count: int, window: int) -> None:
        async with self.sessions.begin() as db:
            await self._prepare(db)
            error = await self._limits(db, self.clock(), [(purpose, peer, count, window)])
        if error:
            raise error

    async def account_verification(self, peer: str) -> None:
        await self.account("verify-peer", peer, 100, 600)

    async def _session(
        self,
        db: AsyncSession,
        token: str | None,
        at: datetime,
        *,
        allow_disabled: bool = False,
        allow_inactive: bool = False,
    ) -> tuple[BuyerSession, User] | None:
        if not allow_disabled and not self.config.buyer_sessions_enabled:
            return None
        if not token or not re.fullmatch(SECRET_PATTERN, token):
            return None
        row = await db.scalar(
            select(BuyerSession)
            .where(BuyerSession.verifier_digest == verifier_digest(token))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        at = max(at, self.clock())
        if (
            row is None
            or row.revoked_at is not None
            or at >= min(row.absolute_expires_at, row.idle_expires_at)
        ):
            return None
        user = await db.scalar(
            select(User).where(User.id == row.user_id).execution_options(populate_existing=True)
        )
        if user is None or (not allow_inactive and not user.is_active):
            return None
        return row, user

    async def _refuse_authenticated(
        self,
        db: AsyncSession,
        token: str | None,
        at: datetime,
    ) -> None:
        if await self._session(db, token, at) is not None:
            raise BuyerAuthError(409, "already_authenticated")

    async def reserve(
        self,
        email: str,
        browser: str,
        peer: str,
        session_token: str | None,
    ) -> dict[str, object]:
        at = self.clock()
        handle = secrets.token_hex(16)
        code = f"{secrets.randbelow(1_000_000):06d}"
        browser_digest = keyed(self.config, "browser", browser)
        async with self.sessions.begin() as db:
            await self._prepare(db)
            await self._refuse_authenticated(db, session_token, at)
            await self._lock_email(db, email)
            at = self.clock()
            error = await self._limits(
                db,
                at,
                [
                    ("send-email-cooldown", email, 1, 60),
                    ("send-email-hour", email, 5, 3600),
                    ("send-peer-hour", peer, 30, 3600),
                ],
            )
            if error is None:
                await db.execute(
                    update(BuyerEmailChallenge)
                    .where(
                        BuyerEmailChallenge.email == email,
                        BuyerEmailChallenge.browser_digest == browser_digest,
                        BuyerEmailChallenge.state.in_(["sending", "ready"]),
                    )
                    .values(state="superseded", terminal_at=at)
                )
                db.add(
                    BuyerEmailChallenge(
                        id=handle,
                        email=email,
                        browser_digest=browser_digest,
                        purpose="sign_in",
                        code_digest=keyed(
                            self.config, "sign-in-code", handle, email, browser_digest, code
                        ),
                        state="sending",
                        attempts=0,
                        created_at=at,
                        expires_at=at + PROOF_LIFETIME,
                    )
                )
                # This reservation has committed all its quota before any external I/O.
                resend_at = at + timedelta(seconds=60)
                for purpose, value, count in (
                    ("send-email-hour", email, 5),
                    ("send-peer-hour", peer, 30),
                ):
                    rate = await db.get(
                        BuyerAuthLimit, keyed(self.config, "rate-" + purpose, value)
                    )
                    if rate is not None and len(rate.events) >= count:
                        resend_at = max(
                            resend_at,
                            datetime.fromtimestamp(rate.events[-count], at.tzinfo)
                            + timedelta(hours=1),
                        )
        if error:
            raise error
        # Failure/cancellation deliberately leaves a non-verifiable sending row.
        await self.mailer.send(email, code)
        async with self.sessions.begin() as db:
            await self._prepare(db)
            await self._lock_email(db, email)
            result = await db.scalars(
                update(BuyerEmailChallenge)
                .where(
                    BuyerEmailChallenge.id == handle,
                    BuyerEmailChallenge.state == "sending",
                    BuyerEmailChallenge.expires_at > self.clock(),
                )
                .values(state="ready")
                .returning(BuyerEmailChallenge.id)
            )
            if result.one_or_none() != handle:
                raise BuyerAuthError(503, "authentication_unavailable")
        return {
            "challenge_id": handle,
            "expires_at": at + PROOF_LIFETIME,
            "resend_after": resend_at,
        }

    async def verify(
        self,
        handle: str,
        code: str,
        browser: str,
        peer: str,
        session_token: str | None,
    ) -> tuple[dict[str, object], str, datetime]:
        # This transaction survives a later malformed input/proof/inactive/SQL failure.
        await self.account_verification(peer)
        at = self.clock()
        result: tuple[dict[str, object], str, datetime] | None = None
        async with self.sessions.begin() as db:
            await self._prepare(db)
            await self._refuse_authenticated(db, session_token, at)
            if not re.fullmatch(HANDLE_PATTERN, handle) or not re.fullmatch(r"[0-9]{6}", code):
                raise BuyerAuthError()
            # Read email to select the same serialization owner as resend/identity creation.
            email = await db.scalar(
                select(BuyerEmailChallenge.email).where(BuyerEmailChallenge.id == handle)
            )
            if email is not None:
                await self._lock_email(db, email)
                row = await db.scalar(
                    select(BuyerEmailChallenge)
                    .where(BuyerEmailChallenge.id == handle)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                at = self.clock()
                browser_digest = keyed(self.config, "browser", browser)
                if (
                    row is not None
                    and row.state == "ready"
                    and row.purpose == "sign_in"
                    and at < row.expires_at
                    and row.attempts < 5
                    and hmac.compare_digest(row.browser_digest, browser_digest)
                ):
                    row.attempts += 1
                    expected = keyed(
                        self.config, "sign-in-code", handle, row.email, browser_digest, code
                    )
                    if hmac.compare_digest(row.code_digest, expected):
                        identity = await db.get(BuyerEmailIdentity, email)
                        user = await db.get(User, identity.user_id) if identity else None
                        if identity is None:
                            user = User(telegram_id=None, is_active=True, is_admin=False)
                            db.add(user)
                            await db.flush()
                            db.add(BuyerEmailIdentity(email=email, user_id=user.id, verified_at=at))
                        if user is not None and user.is_active:
                            session, token = await issue_session(db, user, at)
                            row.state, row.terminal_at = "consumed", at
                            result = (
                                self._shape(session, user, token),
                                token,
                                session.absolute_expires_at,
                            )
                        else:
                            row.state, row.terminal_at = "exhausted", at
                    elif row.attempts >= 5:
                        row.state, row.terminal_at = "exhausted", at
        if result is None:
            raise BuyerAuthError()
        return result

    def _shape(self, row: BuyerSession, user: User, token: str) -> dict[str, object]:
        return {
            "user": {"id": user.id, "telegram_id": user.telegram_id},
            "authenticated_at": row.authenticated_at.isoformat().replace("+00:00", "Z"),
            "expires_at": min(row.absolute_expires_at, row.idle_expires_at)
            .isoformat()
            .replace("+00:00", "Z"),
            "csrf_token": csrf(self.config, token, generation=row.generation),
        }

    async def use_session(
        self,
        token: str | None,
        *,
        rotate: bool = False,
        supplied_csrf: str | None = None,
    ) -> tuple[dict[str, object], str, datetime]:
        at = self.clock()
        async with self.sessions.begin() as db:
            await self._prepare(db)
            principal = await self._session(db, token, at)
            if principal is None or token is None:
                raise BuyerAuthError()
            row, user = principal
            at = max(self.clock(), row.last_used_at)
            if rotate:
                require_csrf(csrf(self.config, token, generation=row.generation), supplied_csrf)
                token = secrets.token_hex(32)
                row.verifier_digest = verifier_digest(token)
                row.generation += 1
            row.last_used_at = at
            row.idle_expires_at = min(at + IDLE_LIFETIME, row.absolute_expires_at)
            result = self._shape(row, user, token), token, row.absolute_expires_at
        return result

    async def logout(self, token: str | None, supplied_csrf: str | None) -> None:
        # Without a bearer there is no durable authority to revoke and no SQL is needed.
        if not token or not re.fullmatch(SECRET_PATTERN, token):
            return
        async with self.sessions.begin() as db:
            await self._prepare(db)
            principal = await self._session(
                db, token, self.clock(), allow_disabled=True, allow_inactive=True
            )
            if principal is not None:
                row, _user = principal
                require_csrf(csrf(self.config, token, generation=row.generation), supplied_csrf)
                row.revoked_at = self.clock()
