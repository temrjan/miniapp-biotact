"""The journal writer (spec.opyt r2 JW-01 to JW-09, JL-01 to JL-03, JS-01, JG-01).

The recorder only queues the turn record. One background task writes the records in queue
order, each in its own transaction; a failed write is counted and dropped. Nothing here runs
on the request path except `Journal.record`, and nothing here logs customer text."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.consultant.enigma.nets import tokens
from src.consultant.record import Counters, TurnRecord, noop
from src.journal.models import Conversation, JournalTurn

logger = logging.getLogger(__name__)

# Ends the writer after the records queued before it (JW-09).
STOP = None


class Journal:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions
        self.queue: asyncio.Queue[TurnRecord | None] = asyncio.Queue()
        self.counters = Counters()

    def record(self, record: TurnRecord) -> None:
        """The recorder (JW-01): append without waiting; no I/O, no task, no exception."""
        self.queue.put_nowait(record)
        self.counters.add("queued")

    async def run(self) -> None:
        """The writer (JW-02): queue order, until STOP; CancelledError is propagated."""
        while (record := await self.queue.get()) is not STOP:
            await self.write(record)

    async def write(self, record: TurnRecord) -> None:
        if record.fault_code == "duplicate_request":
            # JW-03: the turn with this request_id is or was stored by its first record.
            self.counters.add("skipped_duplicate")
            return
        try:
            async with self.sessions() as db:
                if await self.store(db, record):
                    await db.commit()
                    self.counters.add("written")
                else:
                    self.counters.add("ignored")
        except Exception as exc:
            # JW-08: the session rolled back on exit; the record is dropped. Only the request
            # id and the exception class are logged.
            name = type(exc).__name__
            self.counters.add("failed." + name)
            logger.warning(
                "consultant journal write failed request_id=%s error=%s", record.request_id, name
            )

    async def store(self, db: AsyncSession, record: TurnRecord) -> bool:
        """One record in the open transaction. False when its request_id is already stored
        (JW-05); the caller then leaves the transaction uncommitted."""
        conversation = None
        if record.token_epoch is not None:
            conversation = await self.conversation(db, record)
        jev, luna = record.jev or {}, record.luna or {}
        agreed = (
            record.http_status == 200
            and luna.get("agreement") == "agree"
            and jev.get("input_is_question") is True
            and jev.get("choice") is not None
        )
        row = await db.scalar(
            insert(JournalTurn)
            .values(
                request_id=uuid.UUID(record.request_id),
                conversation_id=conversation,
                tab_id=uuid.UUID(record.tab_id),
                created_at=record.created,
                question=record.question,
                answer=record.answer,
                http_status=record.http_status,
                fault_code=record.fault_code,
                response_status=record.response_status,
                path=record.path,
                graf_outcome=record.graf_outcome,
                situation=record.situation,
                template=record.template,
                offered_product_ids=record.offered_product_ids or [],
                jev=record.jev,
                luna=record.luna,
                facts=record.facts,
                versions=record.versions,
                asked_question=record.asked_question,
                resolution=record.resolution,
                tokens=tokens(record.question),
                # JL-01: Jev at least 0.7 and Luna sure of the same situation, on the typed
                # question, delivered.
                agreed_situation=jev["choice"] if agreed else None,
                agreed_source="jev_luna" if agreed else None,
            )
            .on_conflict_do_nothing(index_elements=["request_id"])
            .returning(JournalTurn.id)
        )
        if row is None:
            return False
        if record.http_status == 200 and record.resolution is not None and conversation is not None:
            await self.confirm(db, conversation, row, record.resolution)
        return True

    @staticmethod
    async def conversation(db: AsyncSession, record: TurnRecord) -> int:
        """JW-04: upsert the conversation of (tab_id, token_epoch)."""
        values = {
            "channel": "website",
            "tab_id": uuid.UUID(record.tab_id),
            "token_epoch": record.token_epoch,
            "started_at": record.created,
            "last_turn_at": record.created,
        }
        statement = insert(Conversation).values(**values)
        identity = await db.scalar(
            statement.on_conflict_do_update(
                index_elements=["tab_id", "token_epoch"],
                set_={
                    "started_at": func.least(
                        Conversation.started_at, statement.excluded.started_at
                    ),
                    "last_turn_at": func.greatest(
                        Conversation.last_turn_at, statement.excluded.last_turn_at
                    ),
                },
            ).returning(Conversation.id)
        )
        assert identity is not None
        return int(identity)

    @staticmethod
    async def confirm(
        db: AsyncSession, conversation: int, row: int, resolution: dict[str, Any]
    ) -> None:
        """JL-02: label the latest earlier asking row of the conversation that is not a
        repeat, when it is unlabelled and the confirmed situation is one of its options.
        Older rows are never tried and an existing label is never replaced."""
        target = (
            await db.execute(
                select(JournalTurn.id, JournalTurn.agreed_situation, JournalTurn.asked_question)
                .where(
                    JournalTurn.conversation_id == conversation,
                    JournalTurn.id < row,
                    JournalTurn.http_status == 200,
                    JournalTurn.answer.is_not(None),
                    JournalTurn.asked_question.is_not(None),
                    JournalTurn.asked_question["repeat"].as_boolean().is_(False),
                )
                .order_by(JournalTurn.id.desc())
                .limit(1)
            )
        ).first()
        if target is None or target.agreed_situation is not None:
            return
        situation = resolution.get("situation")
        if situation not in target.asked_question.get("options", []):
            return
        await db.execute(
            update(JournalTurn)
            .where(JournalTurn.id == target.id)
            .values(agreed_situation=situation, agreed_source="confirmed")
        )

    def line(self) -> str:
        """JS-01: the current queue length and the counters since the last line."""
        counts = self.counters.drain()
        return f"queue={self.queue.qsize()}" + (" " + counts if counts else "")


async def report(journal: Journal, interval: float = 300) -> None:
    """Log the JS-01 line every interval and reset the counters. Runs as a task of the
    journal runtime, outside any request context."""
    while True:
        await asyncio.sleep(interval)
        logger.info("consultant journal %s", journal.line())


@asynccontextmanager
async def runtime(
    app: Any, config: Any, sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Journal | None]:
    """JG-01: the journal runs only with `consultant_journal` on and `database_echo` off.
    Entered inside the consultant runtime, so `app.state.consultant` exists throughout.
    Nothing touches the database at startup: a missing schema fails the writes (JW-08)."""
    if not config.consultant_journal:
        yield None
        return
    if config.database_echo:
        # SQLAlchemy echo logs statement parameters, which would hold customer text.
        logger.warning("consultant journal not started: database_echo is on")
        yield None
        return
    consultant = app.state.consultant
    journal = Journal(sessions)
    writer = asyncio.create_task(journal.run())
    reporter = asyncio.create_task(report(journal))
    consultant.recorder = journal.record
    try:
        yield journal
    finally:
        # JW-09: no new records, then the queued ones are written, then the writer stops.
        consultant.recorder = noop
        reporter.cancel()
        try:
            journal.queue.put_nowait(STOP)
            await writer
        finally:
            if not writer.done():
                writer.cancel()
            await asyncio.gather(writer, reporter, return_exceptions=True)
