"""E2E dry-run: a GENERATED code survives the QR-deeplink transport and activates (Stage 4).

The generator (``codes.py``) and the activation service (``service.py``) were built in separate PRs
and had never been exercised together against a real generated code (0 batches in prod history).
This ties the seam end to end: a code from ``generate_batch`` is wrapped exactly as the box/deeplink
carries it (``qr_<code>`` + the mini-app's ``quote(code)``), recovered by the bot's prefix-strip,
and then accrues cashback via the activation service — with a red→green proof that the strip is
load-bearing.

The two string transforms mirror the bot (``packages/bot/bot/handlers/loyalty.py`` ``QR_PREFIX`` and
``keyboards/inline.py`` ``quote(code, safe="")``). api and bot are separate packages, so the
cross-package format contract is asserted here against the REAL generated output, not a hand-written
code.
"""

import asyncio
import os
import secrets
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.schemas import TelegramUser
from src.common.exceptions import NotFoundException
from src.config import settings
from src.loyalty.codes import _ALPHABET, generate_batch, generate_code
from src.loyalty.models import QrCode, QrCodeStatus
from src.loyalty.schemas import ActivationStatus
from src.loyalty.service import activate_code, get_balance
from src.products.models import Product
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"

# Mirrors packages/bot/bot/handlers/loyalty.py — the prefix the box QR / deeplink encodes.
QR_PREFIX = "qr_"

# packages/api — resolved at module level so the async CLI test does no Path I/O inline.
_API_ROOT = Path(__file__).resolve().parent.parent


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A sessionmaker on the same test DB — activation owns its own transaction (like activation tests)."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_product_and_code(sm: async_sessionmaker[AsyncSession]) -> tuple[int, str]:
    """Commit a priced product + one generated code via the real batch generator; return (pid, code)."""
    async with sm() as s, s.begin():
        product = Product(
            name="Test BAD", slug=f"bad-{secrets.token_hex(4)}", price=Decimal("100000")
        )
        s.add(product)
        await s.flush()
        _, qrs = await generate_batch(s, product.id, 1)
        return product.id, qrs[0].code


async def _seed_user(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> None:
    """Bot-first: the user row exists before the mini-app opens."""
    async with sm() as s, s.begin():
        s.add(User(telegram_id=telegram_id, first_name="Scanner"))


# --------------------------------------------------------------------------- #
# format contract — the generated code is transport-safe (no DB)
# --------------------------------------------------------------------------- #


def test_generated_code_survives_deeplink_transport() -> None:
    """A real generated code needs no URL-encoding and round-trips through the qr_ strip.

    Contract between the generator and the bot: if ``generate_code`` ever emitted a char outside
    Crockford base32, ``quote`` would percent-encode it (mutating what ``?code=`` carries) — this
    asserts the actual generator output is transport-safe on both hops the code takes.
    """
    # Deterministic contract: EVERY char of the alphabet is URL-safe, so no generated code can
    # ever need percent-encoding (a sampled code catches an unsafe alphabet only probabilistically).
    assert quote(_ALPHABET, safe="") == _ALPHABET

    code = generate_code()
    # mini-app carries ?code=quote(code); for Crockford base32 quote is a no-op
    assert quote(code, safe="") == code
    # the box encodes qr_<code>; the bot recovers the stored code by stripping the prefix
    assert (QR_PREFIX + code).removeprefix(QR_PREFIX) == code


# --------------------------------------------------------------------------- #
# full loop — generated code -> deeplink strip -> activate -> cashback -> replay
# --------------------------------------------------------------------------- #


async def test_generated_code_activates_and_replay_is_idempotent(
    test_db: AsyncSession, maker
) -> None:
    """The recovered code accrues cashback once; a re-scan is an idempotent replay."""
    await _seed_product_and_code(maker)
    await _seed_user(maker, 42001)
    tg = TelegramUser(id=42001, first_name="Scanner")

    async with maker() as s:
        code = (await s.execute(select(QrCode.code))).scalar_one()
    # The box carries qr_<code>; the bot strips the prefix before the mini-app calls activate.
    recovered = (QR_PREFIX + code).removeprefix(QR_PREFIX)

    async with maker() as s:
        first = await activate_code(s, telegram_user=tg, code=recovered)
    assert first.status is ActivationStatus.ACTIVATED
    assert first.points_awarded > 0
    assert first.balance == first.points_awarded

    async with maker() as s:
        replay = await activate_code(s, telegram_user=tg, code=recovered)
    assert replay.status is ActivationStatus.ALREADY_ACTIVATED
    assert replay.points_awarded == first.points_awarded

    async with maker() as s:
        uid = (await s.execute(select(User.id).where(User.telegram_id == 42001))).scalar_one()
        assert await get_balance(s, uid) == first.points_awarded
        used = (await s.execute(select(QrCode.status))).scalar_one()
        assert used == QrCodeStatus.USED


async def test_activation_requires_stripped_code_not_raw_deeplink(
    test_db: AsyncSession, maker
) -> None:
    """The bot's prefix-strip is load-bearing: the raw ``qr_<code>`` arg is not a stored code (404).

    If the bot ever forwarded the un-stripped deeplink arg, every scan would 404 — this pins that
    only the bare code (what the generator stores) activates.
    """
    await _seed_product_and_code(maker)
    await _seed_user(maker, 42002)
    tg = TelegramUser(id=42002, first_name="Raw")

    async with maker() as s:
        code = (await s.execute(select(QrCode.code))).scalar_one()
    async with maker() as s:
        with pytest.raises(NotFoundException):
            await activate_code(s, telegram_user=tg, code=QR_PREFIX + code)  # un-stripped


# --------------------------------------------------------------------------- #
# CLI regression — the batch script must run standalone (loads the model registry)
# --------------------------------------------------------------------------- #


async def test_cli_generate_batch_runs_standalone(test_db: AsyncSession, maker, tmp_path) -> None:
    """Regression: running the CLI as a fresh process must load the full model registry.

    The script imports ``loyalty.codes`` but not the registry; standalone it once crashed with an
    unresolved ``Consent -> User`` mapper (0 batches ever generated in prod). This runs the real CLI
    end to end — drop the ``import src.models_registry`` line in the script and this goes red.
    """
    slug = f"cli-{secrets.token_hex(4)}"
    async with maker() as s, s.begin():
        s.add(Product(name="CLI BAD", slug=slug, price=Decimal("50000")))

    out = tmp_path / "batch.csv"
    cmd = [
        sys.executable,
        "scripts/generate_qr_batch.py",
        "--slug",
        slug,
        "--quantity",
        "2",
        "--out",
        str(out),
    ]
    # to_thread: keep the blocking subprocess/read off the event loop. Args are fully
    # test-controlled (interpreter + fixed script path + a generated slug).
    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        cwd=_API_ROOT,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
    )
    assert result.returncode == 0, result.stderr
    content = await asyncio.to_thread(out.read_text)
    lines = content.strip().splitlines()
    assert lines[0] == "code,product_id"
    assert len(lines) == 1 + 2  # header + 2 generated codes

    async with maker() as s:
        n = (await s.execute(select(func.count()).select_from(QrCode))).scalar_one()
    assert n == 2
