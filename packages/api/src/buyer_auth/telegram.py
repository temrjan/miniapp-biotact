"""Durable Telegram approvals. Only completion joins the existing S3 issuer."""

import asyncio
import hmac
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.buyer_auth.models import BuyerAuthLimit, BuyerSession, BuyerTelegramRequest
from src.buyer_auth.security import (
    HANDLE_PATTERN,
    PROOF_LIFETIME,
    SECRET_PATTERN,
    BuyerAuthError,
    context_deadline,
    csrf,
    keyed,
    require_csrf,
    verifier_digest,
)
from src.buyer_auth.service import BuyerAuthService, issue_session
from src.buyer_auth.telegram_transport import TelegramSender
from src.users.models import User

PROMPT_COOLDOWN = 60


def timestamp(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


class TelegramAuthService:
    def __init__(self, auth: BuyerAuthService) -> None:
        self.auth = auth
        self.sender = TelegramSender(auth.config)

    def enabled(self) -> None:
        if not (
            self.auth.config.buyer_sessions_enabled and self.auth.config.buyer_telegram_auth_enabled
        ):
            raise BuyerAuthError(404, "buyer_auth_disabled")

    def digest(self, browser: str) -> str:
        context_deadline(self.auth.config, browser, self.auth.clock())
        return keyed(self.auth.config, "telegram-browser", browser)

    async def lock_context(self, db: AsyncSession, digest: str) -> None:
        key = int.from_bytes(bytes.fromhex(digest[:16]), "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    async def account(self, peer: str, *, poll: bool = False, actor: bool = False) -> None:
        await self.auth.account(
            "telegram-actor" if actor else "telegram-poll" if poll else "telegram-verify",
            peer,
            100,
            600,
        )

    def shape(self, row: BuyerTelegramRequest) -> dict[str, object]:
        return {
            "request_id": row.id,
            "state": row.state,
            "created_at": timestamp(row.created_at),
            "expires_at": timestamp(row.expires_at),
            "comparison": self.comparison(row),
            "bot_url": f"https://t.me/{self.auth.config.buyer_telegram_bot_username}?start=web_{row.id}",
            "delivery": "uncertain" if row.delivery == "sending" else row.delivery,
        }

    def comparison(self, row: BuyerTelegramRequest) -> str:
        return (
            f"{int(keyed(self.auth.config, 'telegram-comparison', row.id)[:8], 16) % 1000000:06d}"
        )

    def prompt(self, row: BuyerTelegramRequest) -> dict[str, str]:
        return {
            "site": self.auth.config.buyer_public_origin,
            "created_at": timestamp(row.created_at),
            "expires_at": timestamp(row.expires_at),
            "comparison": self.comparison(row),
            "confirm_data": f"web:{row.id}:confirm",
            "decline_data": f"web:{row.id}:decline",
        }

    def expire(self, row: BuyerTelegramRequest) -> None:
        if row.state in {"pending", "approved"} and self.auth.clock() >= row.expires_at:
            row.state, row.terminal_at = "expired", self.auth.clock()

    async def row(
        self, db: AsyncSession, handle: str, digest: str | None = None
    ) -> BuyerTelegramRequest:
        if not re.fullmatch(HANDLE_PATTERN, handle):
            raise BuyerAuthError()
        row = await db.scalar(
            select(BuyerTelegramRequest)
            .where(BuyerTelegramRequest.id == handle)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.purpose != "login"
            or (digest is not None and not hmac.compare_digest(digest, row.browser_digest))
        ):
            raise BuyerAuthError()
        self.expire(row)
        return row

    async def barrier(self, db: AsyncSession, digest: str) -> BuyerTelegramRequest | None:
        row: BuyerTelegramRequest | None = await db.scalar(
            select(BuyerTelegramRequest).where(
                BuyerTelegramRequest.browser_digest == digest,
                BuyerTelegramRequest.state == "completed",
            )
        )
        return row

    async def hint(self, db: AsyncSession, token: str | None) -> int | None:
        if not token or not re.fullmatch(SECRET_PATTERN, token):
            return None
        user = await db.scalar(
            select(User)
            .join(BuyerSession, BuyerSession.user_id == User.id)
            .where(
                BuyerSession.verifier_digest == verifier_digest(token),
                BuyerSession.revoked_at.is_(None),
                User.is_active.is_(True),
            )
        )
        return user.telegram_id if user else None

    async def prompt_limit(
        self, db: AsyncSession, actor: int, peer: str | None = None
    ) -> BuyerAuthError | None:
        rules = [
            ("telegram-prompt-cooldown", str(actor), 1, PROMPT_COOLDOWN),
            ("telegram-prompt-hour", str(actor), 5, 3600),
        ]
        if peer is not None:
            rules.append(("telegram-prompt-peer", peer, 30, 3600))
        return await self.auth._limits(db, self.auth.clock(), rules)

    async def defer_prompt(
        self, db: AsyncSession, actor: int, retry_after: int, at: datetime | None = None
    ) -> None:
        """Extend the one-event cooldown under its shared SQL lock, never reserve again."""
        deadline = (at if at is not None else self.auth.clock()) + timedelta(seconds=retry_after)
        key = keyed(self.auth.config, "rate-telegram-prompt-cooldown", str(actor))
        event = (deadline - timedelta(seconds=PROMPT_COOLDOWN)).timestamp()
        await db.execute(
            insert(BuyerAuthLimit)
            .values(key=key, events=[], expires_at=deadline)
            .on_conflict_do_nothing(index_elements=[BuyerAuthLimit.key])
        )
        row = (
            await db.scalars(
                select(BuyerAuthLimit).where(BuyerAuthLimit.key == key).with_for_update()
            )
        ).one()
        # A future event encodes the provider's not-before deadline in the existing
        # one-per-minute window. Preserve later reservations/restrictions and retention.
        row.events = [max([event, *row.events])]
        row.expires_at = max(row.expires_at, deadline)

    async def report_retry_after(self, actor: int, retry_after: int) -> None:
        """The trusted bot can extend a restriction, never reserve or grant authority."""
        self.enabled()
        async with self.auth.sessions.begin() as db:
            await self.auth._prepare(db)
            self.enabled()
            await self.defer_prompt(db, actor, retry_after)

    async def _deliver(self, handle: str, actor: int, prompt: dict[str, str]) -> dict[str, object]:
        # The reservation committed. Transport never holds SQL locks.
        outcome, retry = await self.sender.send(actor, prompt)
        observed = self.auth.clock()
        async with self.auth.sessions.begin() as db:
            await self.auth._prepare(db)
            row = await self.row(db, handle)
            row.delivery = outcome
            if retry:
                await self.defer_prompt(db, actor, retry, observed)
            return self.shape(row)

    async def create(
        self, key: str, browser: str, peer: str, token: str | None
    ) -> dict[str, object]:
        self.enabled()
        digest = self.digest(browser)
        if not re.fullmatch(HANDLE_PATTERN, key):
            raise BuyerAuthError(422, "invalid_request")
        actor = None
        async with self.auth.sessions.begin() as db:
            await self.auth._prepare(db)
            await self.lock_context(db, digest)
            self.enabled()
            at = self.auth.clock()
            previous = await db.scalar(
                select(BuyerTelegramRequest)
                .where(
                    BuyerTelegramRequest.browser_digest == digest,
                    BuyerTelegramRequest.request_key == key,
                )
                .with_for_update()
            )
            if previous is not None:
                # A retry cannot revive an old request or send again.
                self.expire(previous)
                principal = await self.auth._session(db, token, at)
                if principal is not None and principal[0].id != previous.completed_session_id:
                    raise BuyerAuthError(409, "already_authenticated")
                return self.shape(previous)
            if await self.barrier(db, digest) is not None:
                raise BuyerAuthError(409, "already_authenticated")
            await self.auth._refuse_authenticated(db, token, at)
            error = await self.auth._limits(db, at, [("telegram-create-peer", peer, 30, 3600)])
            if error is None:
                actor = await self.hint(db, token)
                await db.execute(
                    update(BuyerTelegramRequest)
                    .where(
                        BuyerTelegramRequest.browser_digest == digest,
                        BuyerTelegramRequest.state.in_(["pending", "approved"]),
                    )
                    .values(state="cancelled", terminal_at=at)
                )
                row = BuyerTelegramRequest(
                    id=secrets.token_hex(16),
                    browser_digest=digest,
                    request_key=key,
                    purpose="login",
                    state="pending",
                    created_at=at,
                    expires_at=min(
                        at + PROOF_LIFETIME, context_deadline(self.auth.config, browser, at)
                    ),
                    expected_actor=actor,
                    delivery="not_requested",
                )
                db.add(row)
                await db.flush()
                if actor is not None:
                    prompt_error = await self.prompt_limit(db, actor, peer)
                    if prompt_error is None:
                        row.delivery = "sending"
                    else:
                        actor = None
                        row.delivery = "unavailable"
                result = self.shape(row)
                handle, prompt = row.id, self.prompt(row)
        if error:
            raise error
        if actor is not None:
            self.enabled()
            worker = asyncio.create_task(self._deliver(handle, actor, prompt))
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Observe/persist any provider restriction before releasing this owner.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not worker.cancelled():
                    worker.exception()
                raise
        return result

    async def inspect(
        self, handle: str, browser: str, peer: str, *, cancel: bool = False
    ) -> dict[str, object]:
        self.enabled()
        digest = self.digest(browser)
        await self.account(peer, poll=not cancel)
        async with self.auth.sessions.begin() as db:
            await self.auth._prepare(db)
            await self.lock_context(db, digest)
            self.enabled()
            row = await self.row(db, handle, digest)
            if cancel and row.state in {"pending", "approved"}:
                row.state, row.terminal_at = "cancelled", self.auth.clock()
            return self.shape(row)

    async def bridge(
        self, handle: str, actor: int, decision: str | None = None
    ) -> dict[str, object]:
        self.enabled()
        await self.account(str(actor), actor=True)
        async with self.auth.sessions.begin() as db:
            await self.auth._prepare(db)
            self.enabled()
            row = await self.row(db, handle)
            if (row.expected_actor is not None and row.expected_actor != actor) or (
                row.deciding_actor is not None and row.deciding_actor != actor
            ):
                raise BuyerAuthError()
            error = None
            prompt = None
            if decision is None:
                if row.state == "pending":
                    error = await self.prompt_limit(db, actor)
                    if error is None:
                        prompt = self.prompt(row)
            elif row.decision is not None and row.decision != decision:
                error = BuyerAuthError(409, "decision_conflict")
            elif row.state == "pending":
                row.deciding_actor, row.decision = actor, decision
                row.state = "approved" if decision == "confirm" else "declined"
                if row.state == "declined":
                    row.terminal_at = self.auth.clock()
            result: dict[str, object] = {"state": row.state}
            if decision is None:
                result["prompt"] = prompt
        if error:
            raise error
        return result

    def verifier(self, browser: str, handle: str, session_id: str) -> str:
        return keyed(self.auth.config, "telegram-completion-session", browser, handle, session_id)

    async def complete(
        self, handle: str, browser: str, peer: str, token: str | None
    ) -> tuple[dict[str, object], str, datetime]:
        self.enabled()
        digest = self.digest(browser)
        await self.account(peer)
        result = None
        error = None
        async with self.auth.sessions.begin() as db:
            await self.auth._prepare(db)
            await self.lock_context(db, digest)
            self.enabled()
            row = await self.row(db, handle, digest)
            at = self.auth.clock()
            principal = await self.auth._session(db, token, at)
            if principal is not None and principal[0].id != row.completed_session_id:
                error = BuyerAuthError(409, "already_authenticated")
            elif row.state == "completed":
                session = await db.scalar(
                    select(BuyerSession)
                    .where(BuyerSession.id == row.completed_session_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                user = await db.scalar(
                    select(User)
                    .where(User.id == row.completed_user_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                verifier = self.verifier(browser, handle, row.completed_session_id or "")
                if (
                    session is None
                    or user is None
                    or not user.is_active
                    or session.revoked_at is not None
                    or self.auth.clock()
                    >= min(row.expires_at, session.absolute_expires_at, session.idle_expires_at)
                    or session.generation != row.completed_generation
                    or not hmac.compare_digest(session.verifier_digest, verifier_digest(verifier))
                ):
                    error = BuyerAuthError()
                else:
                    result = (
                        self.auth._shape(session, user, verifier),
                        verifier,
                        session.absolute_expires_at,
                    )
            elif row.state == "pending":
                error = BuyerAuthError(409, "approval_pending")
            elif row.state != "approved" or row.deciding_actor is None:
                error = BuyerAuthError()
            elif await self.barrier(db, digest) is not None:
                error = BuyerAuthError(409, "already_authenticated")
            else:
                # ON CONFLICT waits for concurrent ordinary bot/Mini App creation,
                # then reads its canonical winner without rolling back this proof.
                await db.execute(
                    insert(User)
                    .values(telegram_id=row.deciding_actor, is_active=True, is_admin=False)
                    .on_conflict_do_nothing(index_elements=[User.telegram_id])
                )
                user = await db.scalar(
                    select(User)
                    .where(User.telegram_id == row.deciding_actor)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if user is None or not user.is_active:
                    error = BuyerAuthError()
                else:
                    session, verifier = await issue_session(
                        db,
                        user,
                        self.auth.clock(),
                        verifier_factory=lambda session_id: self.verifier(
                            browser, handle, session_id
                        ),
                    )
                    row.state, row.terminal_at = "completed", self.auth.clock()
                    row.completed_session_id, row.completed_user_id = session.id, user.id
                    row.completed_generation = session.generation
                    result = (
                        self.auth._shape(session, user, verifier),
                        verifier,
                        session.absolute_expires_at,
                    )
        if error:
            raise error
        if result is None:
            raise BuyerAuthError()
        return result

    async def logout(self, token: str | None, browser: str | None, supplied: str | None) -> None:
        try:
            digest = self.digest(browser) if browser else None
        except BuyerAuthError:
            digest = None
        if digest is None:
            await self.auth.logout(token, supplied)
            return
        async with self.auth.sessions.begin() as db:
            await self.auth._prepare(db)
            await self.lock_context(db, digest)
            principal = await self.auth._session(
                db, token, self.auth.clock(), allow_disabled=True, allow_inactive=True
            )
            if principal is not None and token is not None:
                require_csrf(
                    csrf(self.auth.config, token, generation=principal[0].generation), supplied
                )
                principal[0].revoked_at = self.auth.clock()
            elif browser is not None:
                require_csrf(csrf(self.auth.config, browser), supplied)
            rows = (
                await db.scalars(
                    select(BuyerTelegramRequest)
                    .where(BuyerTelegramRequest.browser_digest == digest)
                    .order_by(BuyerTelegramRequest.id)
                    .with_for_update()
                )
            ).all()
            for row in rows:
                if row.state in {"pending", "approved"}:
                    row.state, row.terminal_at = "cancelled", self.auth.clock()
                if row.completed_session_id:
                    session = await db.scalar(
                        select(BuyerSession)
                        .where(BuyerSession.id == row.completed_session_id)
                        .with_for_update()
                    )
                    if session is not None and session.revoked_at is None:
                        session.revoked_at = self.auth.clock()
