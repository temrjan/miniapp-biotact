"""Operator-only size stock inspection, activation, and bounded payment recovery.

Run from packages/api using the privileged deployment environment. Default is inspection:

  python scripts/size_stock.py initialize
  python scripts/size_stock.py recover --order-id 123

Apply requires an operator-owned authorized JSON record (see read_evidence), actor and reason:

  python scripts/size_stock.py initialize --apply --action pause --actor NAME --reason TEXT --evidence FILE
  python scripts/size_stock.py initialize --apply --action initialize --actor NAME --reason TEXT --evidence FILE
  python scripts/size_stock.py recover --order-id 123 --expected-revision 4 --apply --action bind --actor NAME --reason TEXT --evidence FILE

Recovery actions: bind, verify_cancel, annulled, settled, full_refund, no_invoice.
There is no creation-claim reset, replacement invoice, or public force-release operation.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

import src.models_registry  # noqa: F401
from src.database import async_session_maker, engine
from src.products.operations import (
    initialize_vest,
    inspect_order,
    inspect_vest,
    read_evidence,
    recover_order,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("operation", choices=("initialize", "recover"))
    parser.add_argument(
        "--apply", action="store_true", help="Explicit authorized mutation; absent means inspect"
    )
    parser.add_argument("--order-id", type=int)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument(
        "--action",
        choices=(
            "pause",
            "initialize",
            "bind",
            "verify_cancel",
            "annulled",
            "settled",
            "full_refund",
            "no_invoice",
        ),
    )
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if args.operation == "recover" and args.order_id is None:
        parser.error("recover requires --order-id")
    if args.apply:
        if not all((args.actor, args.reason, args.action, args.evidence)):
            parser.error("apply requires --actor, --reason, --action, --evidence")
        if args.operation == "recover" and (
            args.expected_revision is None or args.expected_revision < 0
        ):
            parser.error("recovery apply requires a non-negative --expected-revision")
        if (args.operation == "initialize") != (args.action in ("pause", "initialize")):
            parser.error("action does not belong to this operation")
    return args


async def main() -> int:
    args = arguments()
    try:
        evidence = (
            read_evidence(args.evidence, actor=args.actor, action=args.action)
            if args.apply
            else None
        )
        async with async_session_maker() as db:
            if not args.apply:
                state = (
                    await inspect_vest(db)
                    if args.operation == "initialize"
                    else await inspect_order(db, args.order_id)
                )
                print(
                    json.dumps(
                        {"outcome": "inspection", "state": state}, default=str, ensure_ascii=False
                    )
                )
                return 0
            assert evidence is not None
            common = {
                "action": args.action,
                "actor": args.actor,
                "reason": args.reason,
                "evidence_ref": str(args.evidence.resolve()),
                "evidence": evidence,
            }
            if args.operation == "initialize":
                outcome = await initialize_vest(db, **common)
            else:
                outcome = await recover_order(
                    db, order_id=args.order_id, expected_revision=args.expected_revision, **common
                )
            print(json.dumps({"outcome": outcome}))
            return 0 if outcome in ("applied", "unchanged", "already_initialized") else 2
    except HTTPException as exc:
        print(
            json.dumps(
                {
                    "outcome": "held"
                    if exc.status_code == 500
                    or exc.detail
                    in (
                        {"code": "recovery_needs_evidence"},
                        {"code": "legacy_commitments_unresolved"},
                    )
                    else "conflict",
                    "detail": exc.detail,
                }
            )
        )
        return 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"outcome": "conflict", "detail": str(exc)}))
        return 2
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
