"""Separate supervised delivery process and restricted aggregate operator commands."""

import argparse
import asyncio
import importlib
import json
import logging
import signal
import time
from collections.abc import Sequence
from datetime import timedelta
from typing import NoReturn
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.delivery_privacy import private_operation, private_scope
from src.employee_referrals.delivery_store import Lane, claim_job, complete, now, replay_k2, retry
from src.employee_referrals.paid_models import PaidOutbox
from src.employee_referrals.return_models import ReturnOutbox
from src.employee_referrals.transport import LaneConfig, send_k2, send_notice

logger = logging.getLogger(__name__)


def configs() -> dict[Lane, LaneConfig]:
    return {
        "k2": LaneConfig(
            settings.employee_referral_k2_sender_enabled,
            settings.employee_referral_k2_portal_origin,
            settings.employee_referral_k2_key.get_secret_value(),
        ),
        "notice": LaneConfig(
            settings.employee_referral_notices_enabled,
            token=settings.telegram_notification_bot_token or settings.telegram_bot_token,
            chat=settings.telegram_chat_id,
        ),
    }


@private_operation
async def readiness(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Cutover correspondence only; never manufacture missing display snapshots."""
    async with sessions() as session:
        source_count = sum(
            [
                await session.scalar(select(func.count()).select_from(PaidOutbox)) or 0,
                await session.scalar(select(func.count()).select_from(ReturnOutbox)) or 0,
            ]
        )
        for model in (K2Delivery, NoticeDelivery):
            if await session.scalar(select(func.count()).select_from(model)) != source_count:
                raise ValueError("Storage unavailable")


async def deliver_one(
    sessions: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    lane: Lane,
    config: LaneConfig,
) -> bool:
    if not config.enabled or not config.valid(lane):
        return False
    claim = await claim_job(sessions, lane)
    if claim is None:
        return False
    if lane == "k2":
        result = await send_k2(client, config, claim.body, claim.snapshot, claim.digest)
    else:
        if claim.notice is None:
            raise ValueError("Missing display payload")
        result = await send_notice(client, config, claim.notice)
    await complete(sessions, claim, result)
    return True


async def run_lane(
    sessions: async_sessionmaker[AsyncSession], lane: Lane, config: LaneConfig, stop: asyncio.Event
) -> None:
    """Exceptions restart this lane only; cancellation abandons its durable lease."""
    async with httpx.AsyncClient(
        trust_env=False, follow_redirects=False, transport=httpx.AsyncHTTPTransport(retries=0)
    ) as client:
        monitor = 0.0
        while not stop.is_set():
            category = "disabled" if not config.enabled else "active"
            worked = False
            try:
                if not config.valid(lane):
                    category = "configuration"
                elif config.enabled:
                    async with asyncio.timeout(8):
                        await readiness(sessions)
                    worked = await deliver_one(sessions, client, lane, config)
            except Exception:
                # A supervised process boundary exposes only a fixed category.
                category = "storage"
            if time.monotonic() >= monitor:
                logger.info("Referral worker cycle lane=%s status=%s", lane, category)
                monitor = time.monotonic() + 20
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except TimeoutError:
                    continue


@private_operation
async def health(
    sessions: async_sessionmaker[AsyncSession], config: dict[Lane, LaneConfig]
) -> tuple[dict[str, object], int]:
    if any(not c.valid(lane) for lane, c in config.items()):
        return {"result": "configuration"}, 2
    await readiness(sessions)
    output: dict[str, object] = {}
    degraded = False
    async with sessions() as session:
        clock = await now(session)
        for lane in ("k2", "notice"):
            model = K2Delivery if lane == "k2" else NoticeDelivery
            rows = (
                await session.execute(
                    select(model.state, func.count(), func.min(model.created_at)).group_by(
                        model.state
                    )
                )
            ).all()
            counts = {
                "pending": 0,
                "leased": 0,
                "blocked": 0,
                "completed": 0,
                "legacy_unknown": 0,
                "ack_unready": 0,
            }
            ages: dict[str, int] = dict.fromkeys(("pending", "leased", "blocked"), 0)
            for state, count, created in rows:
                counts["completed" if state in {"sent", "acknowledged"} else state] = count
                if state in ages:
                    ages[state] = max(0, int((clock - created).total_seconds()))
            if lane == "k2":
                acks = (
                    await session.scalars(
                        select(K2Delivery.ack).where(K2Delivery.state == "acknowledged")
                    )
                ).all()
                for ack in acks:
                    if ack is not None:
                        projection = ack.get("projection")
                        if isinstance(projection, dict) and (
                            projection.get("has_conflict") is True
                            or projection.get("accounting_state") != "confirmed"
                        ):
                            counts["ack_unready"] += 1
            aged = (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(
                        model.state.in_(("pending", "leased", "blocked")),
                        model.created_at < clock - timedelta(minutes=15),
                    )
                )
                or 0
            )
            categories = (
                await session.execute(
                    select(model.error_category, func.count())
                    .where(model.state == "blocked")
                    .group_by(model.error_category)
                )
            ).all()
            from src.employee_referrals.delivery_store import CATEGORIES

            safe: dict[str, int] = {}
            for category, count in categories:
                label = category if category in CATEGORIES else "storage"
                safe[label] = safe.get(label, 0) + count
            output[lane] = {
                "enabled": config[lane].enabled,
                "counts": counts,
                "oldest_age_seconds": ages,
                "aged": aged,
                "categories": safe,
            }
            degraded |= config[lane].enabled and bool(
                counts["blocked"] or counts["ack_unready"] or aged
            )
    return output, 1 if degraded else 0


class Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise ValueError("Invalid operator request")


async def command(argv: Sequence[str]) -> int:
    parser = Parser(add_help=False)
    parser.add_argument("action", choices=("run", "health", "retry", "replay-k2"))
    parser.add_argument("--lane", choices=("k2", "notice"))
    parser.add_argument("--event-id")
    try:
        args = parser.parse_args(argv)
        event = None
        if args.action in {"retry", "replay-k2"}:
            if (
                (args.action == "retry" and args.lane is None)
                or (args.action == "replay-k2" and args.lane is not None)
                or args.event_id is None
            ):
                raise ValueError("Invalid operator request")
            event = UUID(args.event_id)
            if event.version != 4 or str(event) != args.event_id:
                raise ValueError("Invalid operator request")
        elif args.lane is not None or args.event_id is not None:
            raise ValueError("Invalid operator request")
        if settings.database_echo:
            raise ValueError("Invalid configuration")
    except ValueError:
        print('{"result":"configuration","count":0}')
        return 2
    importlib.import_module("src.models_registry")
    config = configs()
    try:
        with private_scope():
            engine = create_async_engine(
                settings.database_url,
                echo=False,
                hide_parameters=True,
                pool_pre_ping=True,
                connect_args={"timeout": 5, "command_timeout": 5},
            )
            sessions = async_sessionmaker(engine, expire_on_commit=False)
    except Exception:
        print('{"result":"storage","count":0}')
        return 2
    try:
        if args.action == "run":
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            tasks = [
                asyncio.create_task(run_lane(sessions, lane, cfg, stop))
                for lane, cfg in config.items()
            ]

            def shutdown() -> None:
                stop.set()
                for task in tasks:
                    task.cancel()

            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, shutdown)
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                if not stop.is_set():
                    raise
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(sig)
            return 0
        async with asyncio.timeout(8):
            if args.action == "health":
                data, code = await health(sessions, config)
            else:
                if event is None:
                    raise ValueError("Missing event")
                lane: Lane = "k2" if args.lane == "k2" else "notice"
                if args.action == "replay-k2":
                    result, count, code = await replay_k2(sessions, event, config["k2"])
                else:
                    result, count, code = await retry(sessions, lane, event, config[lane])
                data = {"result": result, "count": count}
        print(json.dumps(data, sort_keys=True))
        return code
    except Exception:
        print('{"result":"storage","count":0}')
        return 2
    finally:
        with private_scope():
            await engine.dispose()


def main() -> None:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        code = asyncio.run(command(sys.argv[1:]))
    except Exception:
        print('{"result":"storage","count":0}')
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
