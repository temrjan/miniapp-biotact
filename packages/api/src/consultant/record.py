"""The turn record handed to the recorder hook, and in-process counters without text
(spec r2 OP-02, OP-03; spec.opyt r2 §3 TR-01 to TR-05)."""

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.consultant.contracts import Fault, Turn

logger = logging.getLogger(__name__)


@dataclass
class TurnRecord:
    """One consultant turn after the SENSITIVE filter. Every field after `path` is null
    when the turn ended before the step that produces it."""

    request_id: str
    tab_id: str
    created: datetime
    # The question as typed, before a check reply is replaced by the checked message.
    question: str
    answer: str | None = None
    fault_code: str | None = None
    http_status: int | None = None
    path: str = "failure"
    token_epoch: str | None = None
    graf_outcome: str | None = None
    situation: str | None = None
    template: str | None = None
    offered_product_ids: list[int] | None = None
    # served model, choice, confidence, top-3, latency, error class, input_is_question (TR-03)
    jev: dict[str, Any] | None = None
    # model, effort, calls (latency, outcome, target, text, check_id: TR-05), reading,
    # intent, objections with acceptance, agreement (TR-02), text target, equal-to-A flag,
    # failure trigger
    luna: dict[str, Any] | None = None
    facts: dict[str, list[Any]] | None = None
    versions: dict[str, Any] | None = None
    response_status: str | None = None  # TR-01
    asked_question: dict[str, Any] | None = None  # TR-04 (a)
    resolution: dict[str, Any] | None = None  # TR-04 (b)
    # Not part of the record contract: the asked question before delivery is known.
    pending_question: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @staticmethod
    def start(request: Turn) -> "TurnRecord":
        return TurnRecord(
            str(request.request_id), str(request.tab_id), datetime.now(UTC), request.question
        )

    def luna_part(self) -> dict[str, Any]:
        if self.luna is None:
            self.luna = {
                "model": None,
                "effort": None,
                "calls": [],
                "reading": None,
                "intent": None,
                "objections": [],
                "agreement": None,
                "text_target": None,
                "equal_to_a": None,
                "failure_trigger": None,
            }
        return self.luna

    def deliver(self, response: dict[str, Any]) -> None:
        self.answer = response["answer"]
        self.http_status = 200
        self.response_status = response["status"]  # TR-01
        self.asked_question = self.pending_question

    def fail(self, fault: Fault) -> None:
        self.fault_code, self.http_status = fault.code, fault.status
        self.answer = self.response_status = self.asked_question = None
        if fault.code == "answer_unavailable":
            self.path = "failure"


def noop(record: TurnRecord) -> None:
    """The default recorder: PR-5 replaces it with the journal writer."""


Recorder = Callable[[TurnRecord], None]


class Counters:
    """In-process counts by fixed keys (no text). `line()` gives the key=value line."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def add(self, key: str, n: int = 1) -> None:
        self.counts[key] += n

    def latency(self, seconds: float | None) -> None:
        if seconds is None:
            self.add("luna.latency.timeout")
            return
        bucket = next((b for b in (2, 4, 8, 16) if seconds < b), None)
        self.add(f"luna.latency.lt{bucket}" if bucket else "luna.latency.timeout")

    def drain(self) -> str:
        line = " ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))
        self.counts.clear()
        return line


async def report(counters: Counters, interval: float = 300) -> None:
    """Log the counters as one key=value line every interval and reset them. Runs as a
    task of the runtime, outside any request context."""
    while True:
        await asyncio.sleep(interval)
        logger.info("consultant counters %s", counters.drain() or "-")
