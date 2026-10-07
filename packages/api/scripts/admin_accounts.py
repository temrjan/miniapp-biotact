"""Private operator CLI. Passwords enter only through confirmed hidden input."""

import argparse
import asyncio
import getpass
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn


class CommandRefused(Exception):
    """Never carry argument, identity or password values."""


class SafeParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise CommandRefused()


def parse_command(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = SafeParser(description="Manage existing administrator password accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "set-password", "revoke"):
        command = commands.add_parser(name)
        command.add_argument("--login", required=True)
        if name == "create":
            command.add_argument("--telegram-id", required=True)
    return parser.parse_args(argv)


def read_password() -> str:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise CommandRefused()
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass("Password: ")
            confirmation = getpass.getpass("Confirm password: ")
        except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
            raise CommandRefused() from None
    if password != confirmation:
        raise CommandRefused()
    return password


async def operate(command: str, login: str, telegram_id: int | None, hashed: str | None) -> None:
    # Import only after --help, parsing and secret validation have completed.
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import src.models_registry  # noqa: F401
    from src.auth.accounts import AccountRefused, change_account, create_account
    from src.config import settings

    engine = create_async_engine(settings.database_url, echo=False, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as db:
            if command == "create":
                if telegram_id is None or hashed is None:
                    raise CommandRefused()
                await create_account(db, login, telegram_id, hashed)
            else:
                await change_account(db, login, hashed=hashed)
    except (AccountRefused, IntegrityError):
        raise CommandRefused() from None
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_command(argv)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from src.auth.passwords import make_hash, normalize_login

        login = normalize_login(args.login)
        telegram_id = None
        if args.command == "create":
            raw = args.telegram_id
            if not raw.isascii() or not raw.isdecimal() or len(raw) > 19:
                raise CommandRefused()
            telegram_id = int(raw)
            if not 1 <= telegram_id <= 9223372036854775807:
                raise CommandRefused()
        hashed = make_hash(read_password()) if args.command != "revoke" else None
    except (CommandRefused, ValueError, EOFError, KeyboardInterrupt):
        print("Account operation refused.", file=sys.stderr)
        return 2
    except Exception:
        # Includes configuration/import failure; never render its payload.
        print("Account service unavailable.", file=sys.stderr)
        return 1
    try:
        asyncio.run(operate(args.command, login, telegram_id, hashed))
    except (CommandRefused, KeyboardInterrupt):
        print("Account operation refused.", file=sys.stderr)
        return 2
    except Exception:
        print("Account service unavailable.", file=sys.stderr)
        return 1
    print(
        {
            "create": "Account created.",
            "set-password": "Password updated.",
            "revoke": "Admin access revoked.",
        }[args.command]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
