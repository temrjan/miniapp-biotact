"""The consultant journal (spec.opyt r2 §4, PR-5) on PostgreSQL 16: the writer, the
conversation key, the agreed-turn labels, the failure path, the counters line, the switch
and the migration. Records are built directly, or produced by the real service with Enigma's
Jev and Luna doubles as in the Enigma turn tests (the column mapping on every path is there).
Every customer text here is synthetic."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src import main
from src.auth import privacy
from src.config import Settings
from src.consultant.record import TurnRecord, noop
from src.journal.models import Conversation, JournalTurn
from src.journal.writer import Journal, report, runtime
from tests import test_category_schema_migration as migration
from tests.test_consultant_enigma_core import answer
from tests.test_consultant_enigma_turn import QUESTION, SENTENCE, JevDouble, bot, say, turn
from tests.test_consultant_enigma_turn import seed as enigma_seed

scratch = migration.scratch
T0 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
ON = SimpleNamespace(consultant_journal=True, database_echo=False)
LOGGER = "src.journal.writer"


@pytest.fixture
def sessions(test_db):
    return async_sessionmaker(test_db.bind, expire_on_commit=False)


def rec(question="часто болею", *, tab, epoch="epoch-1", at=0, status=200, **fields):
    """A record as the service leaves it: delivered (200) or failed with `fault`."""
    record = TurnRecord(
        str(fields.pop("request_id", uuid.uuid4())),
        str(tab),
        T0 + timedelta(minutes=at),
        question,
    )
    record.token_epoch, record.path, record.http_status = epoch, "hybrid", status
    if status == 200:
        record.answer, record.response_status = "Синтетический ответ?", "clarification"
    else:
        record.fault_code = fields.pop("fault", "answer_unavailable")
    for name, value in fields.items():
        setattr(record, name, value)
    return record


def asking(kind, *options, repeat=False):
    return {"kind": kind, "options": list(options), "repeat": repeat}


def resolved(kind, situation):
    return {"kind": kind, "situation": situation}


async def store(sessions, *records):
    journal = Journal(sessions)
    for record in records:
        await journal.write(record)
    return journal


async def rows(sessions):
    async with sessions() as db:
        return list(await db.scalars(select(JournalTurn).order_by(JournalTurn.id)))


def labels(stored):
    return [
        None if r.agreed_situation is None else (r.agreed_situation, r.agreed_source)
        for r in stored
    ]


async def until(predicate):
    """Bounded wait for the writer task to reach an observed state."""
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    pytest.fail("the writer did not reach the expected state")


# --- JN-02, JN-04, JG-01 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("journal", "echo", "running"),
    [(False, False, False), (True, True, False), (True, False, True)],
    ids=["switch-off", "switch-on-echo", "switch-on"],
)
async def test_the_app_lifespan_runs_the_journal_only_with_its_switch_on_and_echo_off(
    test_db, monkeypatch, caplog, journal, echo, running
):
    assert Settings.model_fields["consultant_journal"].default is False
    monkeypatch.setattr(main.settings, "consultant_journal", journal)
    monkeypatch.setattr(main.settings, "database_echo", echo)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    app = FastAPI()
    before = asyncio.all_tasks()
    async with main.lifespan(app):
        consultant = app.state.consultant
        tasks = {t.get_coro().__qualname__ for t in asyncio.all_tasks() - before}
        recorder = consultant.recorder
        assert (getattr(recorder, "__self__", None).__class__ is Journal) is running
        assert (recorder is noop) is not running
        assert ({"Journal.run", "report"} <= tasks) is running
    assert consultant.recorder is noop
    warnings = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert warnings == (["consultant journal not started: database_echo is on"] if echo else [])


# --- JW-02, JW-06 tokens, JW-09 -------------------------------------------------------------


async def test_queued_records_are_written_in_arrival_order_before_the_runtime_ends(sessions):
    tab = uuid.uuid4()
    consultant = SimpleNamespace(recorder=noop)
    app = SimpleNamespace(state=SimpleNamespace(consultant=consultant))
    records = [
        rec("Yo'tal bor, кашель у ребёнка", tab=tab, at=2),
        rec("второй", tab=tab, at=1),
        rec("третий", tab=tab, at=0),
    ]
    async with runtime(app, ON, sessions) as journal:
        for record in records:
            consultant.recorder(record)
        assert journal.queue.qsize() == 3
    assert consultant.recorder is noop
    stored = await rows(sessions)
    assert [str(r.request_id) for r in stored] == [r.request_id for r in records]
    assert stored[0].tokens == ["yo'tal", "bor", "кашель", "у", "ребёнка"]
    assert journal.counters.counts == {"queued": 3, "written": 3}


# --- JW-03, JW-05 ---------------------------------------------------------------------------


async def test_one_row_per_request_id_and_duplicates_are_not_stored(sessions):
    tab = uuid.uuid4()
    first = rec(tab=tab)
    duplicate = rec(tab=tab, request_id=first.request_id, status=409, fault="duplicate_request")
    cancelled = rec(tab=tab, request_id=first.request_id, status=409, fault="cancelled", at=1)
    journal = await store(sessions, first, duplicate, cancelled)
    (row,) = await rows(sessions)
    assert (str(row.request_id), row.http_status, row.answer) == (
        first.request_id,
        200,
        first.answer,
    )
    async with sessions() as db:
        (conversation,) = await db.scalars(select(Conversation))
    assert conversation.last_turn_at == T0  # the ignored record changed nothing
    assert journal.counters.counts == {"written": 1, "skipped_duplicate": 1, "ignored": 1}


# --- JW-04, JD-01 ---------------------------------------------------------------------------


async def test_conversations_by_tab_and_epoch_and_removal_of_one_conversation(sessions):
    tab = uuid.uuid4()
    later = rec("первый", tab=tab, at=5)
    earlier = rec("второй", tab=tab, at=0)
    between = rec("третий", tab=tab, at=3)
    other = rec("четвёртый", tab=tab, epoch="epoch-2", at=1)
    invalid = rec("пятый", tab=tab, epoch=None, status=400, fault="context_invalid")
    await store(sessions, later, earlier, between, other, invalid)
    async with sessions() as db:
        conversations = list(await db.scalars(select(Conversation).order_by(Conversation.id)))
    assert [
        (c.channel, c.tab_id, c.token_epoch, c.started_at, c.last_turn_at) for c in conversations
    ] == [
        ("website", tab, "epoch-1", T0, T0 + timedelta(minutes=5)),
        ("website", tab, "epoch-2", T0 + timedelta(minutes=1), T0 + timedelta(minutes=1)),
    ]
    first, second = (c.id for c in conversations)
    stored = await rows(sessions)
    assert [r.conversation_id for r in stored] == [first, first, first, second, None]
    # JD-01: the documented manual statements.
    async with sessions.begin() as db:
        await db.execute(text("DELETE FROM consultant_conversation WHERE id = :id"), {"id": first})
        await db.execute(text("DELETE FROM consultant_turn WHERE id = :id"), {"id": stored[4].id})
    assert [r.question for r in await rows(sessions)] == ["четвёртый"]


# --- JL-01 ----------------------------------------------------------------------------------

JEV_LUNA = [
    ("agree", True, 200, True),
    ("same_decision", True, 200, False),
    ("disagree", True, 200, False),
    ("abstain", True, 200, False),
    (None, True, 200, False),
    ("agree", False, 200, False),
    ("agree", True, 503, False),
]


async def test_the_jev_luna_label_needs_agree_on_the_typed_question_and_delivery(sessions):
    tab = uuid.uuid4()
    records = [
        rec(
            tab=tab,
            at=n,
            status=status,
            jev={"choice": "immunity", "confidence": 0.8, "input_is_question": typed},
            luna={"agreement": agreement},
        )
        for n, (agreement, typed, status, _) in enumerate(JEV_LUNA)
    ]
    await store(sessions, *records)
    assert labels(await rows(sessions)) == [
        ("immunity", "jev_luna") if labelled else None for *_, labelled in JEV_LUNA
    ]


# --- JL-02 cases A to G ---------------------------------------------------------------------


def case_steps(case, tab):
    """Each step: a record and, when checked there, the labels of all rows so far."""
    H, X, Y, Z = "immunity", "skin_allergy", "adult_stress_sleep", "constipation"
    jev_luna = {
        "jev": {"choice": H, "confidence": 0.8, "input_is_question": True},
        "luna": {"agreement": "agree"},
    }
    if case == "A":
        return [
            (rec("здравствуйте", tab=tab), None),
            (rec("часто болею", tab=tab, asked_question=asking("confirm", H)), None),
            (rec("да", tab=tab, resolution=resolved("confirm", H)), [None, (H, "confirmed"), None]),
        ]
    if case == "B":
        return [
            (rec("Часто простываю", tab=tab, **jev_luna), None),
            (rec("лучше от бессонницы", tab=tab, asked_question=asking("confirm", Y)), None),
            (
                rec("да", tab=tab, resolution=resolved("confirm", Y)),
                [(H, "jev_luna"), (Y, "confirmed"), None],
            ),
        ]
    if case == "C":
        return [
            (rec("кожа и простуды", tab=tab, asked_question=asking("choose", X, Y)), None),
            (rec("не знаю", tab=tab, asked_question=asking("choose", X, Y, repeat=True)), None),
            (rec("сон", tab=tab, resolution=resolved("choose", Y)), [(Y, "confirmed"), None, None]),
        ]
    if case == "D":
        return [
            (rec("часто болею", tab=tab, asked_question=asking("confirm", H)), None),
            (rec("да", tab=tab, status=503, resolution=resolved("confirm", H)), [None, None]),
            (
                rec("да", tab=tab, resolution=resolved("confirm", H)),
                [(H, "confirmed"), None, None],
            ),
        ]
    if case == "E":
        # The second resolution names the other option: the first label stays.
        return [
            (rec("часто болею и кожа", tab=tab, asked_question=asking("choose", H, X)), None),
            (
                rec(
                    "первое, и давит в груди",
                    tab=tab,
                    graf_outcome="check",
                    resolution=resolved("choose", H),
                ),
                [(H, "confirmed"), None],
            ),
            (
                rec("нет", tab=tab, resolution=resolved("choose", X)),
                [(H, "confirmed"), None, None],
            ),
        ]
    if case == "F":
        return [
            (rec("запор", tab=tab, asked_question=asking("confirm", Z)), None),
            (rec("кожа и сон", tab=tab, asked_question=asking("choose", X, Y)), None),
            (rec("запор всё же", tab=tab, resolution=resolved("choose", Z)), [None, None, None]),
        ]
    assert case == "G"
    return [
        (rec("часто болею", tab=tab, asked_question=asking("confirm", H)), None),
        (rec("да", tab=tab, epoch=None, resolution=resolved("confirm", H)), [None, None]),
    ]


@pytest.mark.parametrize("case", ["A", "B", "C", "D", "E", "F", "G"])
async def test_the_confirmed_label_goes_to_the_latest_asking_row(sessions, case):
    journal = Journal(sessions)
    for n, (record, expected) in enumerate(case_steps(case, uuid.uuid4())):
        record.created = T0 + timedelta(minutes=n)
        await journal.write(record)
        if expected is not None:
            assert labels(await rows(sessions)) == expected, (case, n)
    assert "failed" not in " ".join(journal.counters.counts)


# --- JW-01, JW-08, JG-01 without tables, JS-01 ----------------------------------------------


async def test_a_failed_write_is_counted_and_logged_without_text_and_the_writer_goes_on(
    test_db, sessions, caplog
):
    # Restated with an Enigma consultant in Graf removal step 4 (graf-removal spec r3 §3).
    marker = "СИНТЕТИКА-7731"
    question = "часто болею " + marker
    await enigma_seed(test_db)
    offer = "IMMUNOCOMPLEX® поддерживает иммунитет. " + SENTENCE

    def consultant():
        return bot(
            test_db,
            JevDouble(answer("immunocomplex")),
            say("offer", offer, "immunocomplex"),
            say("ask", QUESTION),
        )

    expected = await consultant().turn(turn(question))
    async with sessions.begin() as db:
        await db.execute(text("ALTER TABLE consultant_turn RENAME TO consultant_turn_away"))
    caplog.set_level(logging.INFO)
    chat = consultant()
    app = SimpleNamespace(state=SimpleNamespace(consultant=chat))
    restore = text("ALTER TABLE IF EXISTS consultant_turn_away RENAME TO consultant_turn")
    try:
        async with runtime(app, ON, sessions) as journal:  # startup without the table
            reply = await chat.turn(turn(question))
            assert journal.queue.qsize() == 1  # the turn did not wait for the writer
            keys = ("status", "answer", "products", "sources", "cautions", "open_product_id")
            assert {k: reply[k] for k in keys} == {k: expected[k] for k in keys}
            await until(lambda: journal.queue.qsize() == 0 and len(journal.counters.counts) > 1)
            async with sessions.begin() as db:
                await db.execute(restore)
            await chat.turn(turn("да"))
    finally:
        async with sessions.begin() as db:
            await db.execute(restore)
    (row,) = await rows(sessions)
    assert row.question == "да"
    # Control: the same handler captured the JW-08 line of the failed record ...
    (failed,) = [r for r in caplog.records if r.name == LOGGER]
    assert failed.getMessage() == (
        f"consultant journal write failed request_id={reply['request_id']} error=ProgrammingError"
    )
    assert failed.exc_info is None
    # ... and no captured record of any logger holds the question text.
    captured = json.dumps([r.getMessage() for r in caplog.records], ensure_ascii=False)
    assert marker not in captured
    assert "болею" not in captured
    assert journal.counters.counts == {
        "queued": 2,
        "failed.ProgrammingError": 1,
        "written": 1,
    }
    # JS-01: the line of report(), logged from a task outside any request context.
    privacy.install()
    caplog.clear()
    task = asyncio.create_task(report(journal, 0.01))
    await until(lambda: any(r.name == LOGGER for r in caplog.records))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    line = next(r.getMessage() for r in caplog.records if r.name == LOGGER)
    assert line == "consultant journal queue=0 failed.ProgrammingError=1 queued=2 written=1"
    assert journal.counters.counts == {}
    # Control: inside a private request context the same handler sees the rewritten line.
    token = privacy._private.set(True)
    try:
        logging.getLogger(LOGGER).info("consultant journal %s", journal.line())
    finally:
        privacy._private.reset(token)
    assert caplog.records[-1].getMessage() == "Private administrator authentication"


# --- JT-01 to JT-03 -------------------------------------------------------------------------

COLUMNS = [
    ("consultant_conversation", "id", "int8", "NO", None, "YES"),
    ("consultant_conversation", "channel", "text", "NO", None, "NO"),
    ("consultant_conversation", "tab_id", "uuid", "NO", None, "NO"),
    ("consultant_conversation", "token_epoch", "text", "NO", None, "NO"),
    ("consultant_conversation", "started_at", "timestamptz", "NO", None, "NO"),
    ("consultant_conversation", "last_turn_at", "timestamptz", "NO", None, "NO"),
    ("consultant_turn", "id", "int8", "NO", None, "YES"),
    ("consultant_turn", "request_id", "uuid", "NO", None, "NO"),
    ("consultant_turn", "conversation_id", "int8", "YES", None, "NO"),
    ("consultant_turn", "tab_id", "uuid", "NO", None, "NO"),
    ("consultant_turn", "created_at", "timestamptz", "NO", None, "NO"),
    ("consultant_turn", "written_at", "timestamptz", "NO", "now()", "NO"),
    ("consultant_turn", "question", "text", "NO", None, "NO"),
    ("consultant_turn", "answer", "text", "YES", None, "NO"),
    ("consultant_turn", "http_status", "int4", "YES", None, "NO"),
    ("consultant_turn", "fault_code", "text", "YES", None, "NO"),
    ("consultant_turn", "response_status", "text", "YES", None, "NO"),
    ("consultant_turn", "path", "text", "NO", None, "NO"),
    ("consultant_turn", "graf_outcome", "text", "YES", None, "NO"),
    ("consultant_turn", "situation", "text", "YES", None, "NO"),
    ("consultant_turn", "template", "text", "YES", None, "NO"),
    ("consultant_turn", "offered_product_ids", "_int4", "NO", "'{}'::integer[]", "NO"),
    ("consultant_turn", "jev", "jsonb", "YES", None, "NO"),
    ("consultant_turn", "luna", "jsonb", "YES", None, "NO"),
    ("consultant_turn", "facts", "jsonb", "YES", None, "NO"),
    ("consultant_turn", "versions", "jsonb", "YES", None, "NO"),
    ("consultant_turn", "asked_question", "jsonb", "YES", None, "NO"),
    ("consultant_turn", "resolution", "jsonb", "YES", None, "NO"),
    ("consultant_turn", "tokens", "_text", "NO", None, "NO"),
    ("consultant_turn", "agreed_situation", "text", "YES", None, "NO"),
    ("consultant_turn", "agreed_source", "text", "YES", None, "NO"),
    ("consultant_turn", "opyt", "jsonb", "YES", None, "NO"),
    ("consultant_turn", "memory", "jsonb", "YES", None, "NO"),
]
CONSTRAINTS = [
    ("consultant_conversation", "PRIMARY KEY (id)"),
    ("consultant_conversation", "UNIQUE (tab_id, token_epoch)"),
    (
        "consultant_turn",
        "CHECK ((agreed_source = ANY (ARRAY['jev_luna'::text, 'confirmed'::text])))",
    ),
    (
        "consultant_turn",
        "FOREIGN KEY (conversation_id) REFERENCES consultant_conversation(id) ON DELETE CASCADE",
    ),
    ("consultant_turn", "PRIMARY KEY (id)"),
    ("consultant_turn", "UNIQUE (request_id)"),
]
COLUMN_QUERY = """
    SELECT table_name, column_name, udt_name, is_nullable, column_default, is_identity
    FROM information_schema.columns
    WHERE table_name IN ('consultant_conversation', 'consultant_turn')
    ORDER BY table_name, ordinal_position"""
CONSTRAINT_QUERY = """
    SELECT c.conrelid::regclass::text, pg_get_constraintdef(c.oid) FROM pg_constraint c
    WHERE c.conrelid::regclass::text IN ('consultant_conversation', 'consultant_turn')
    ORDER BY 1, 2"""
TABLES = """
    SELECT count(*) FROM pg_tables
    WHERE tablename IN ('consultant_conversation', 'consultant_turn')"""
REVISION, PREDECESSOR = "8d1b1791159b", "e9a21c40b735"


def test_the_migration_creates_the_journal_and_refuses_to_drop_stored_rows(scratch):
    scratch.migrate(target=REVISION)
    assert scratch.marker() == REVISION
    assert scratch.execute(COLUMN_QUERY) == COLUMNS
    assert scratch.execute(CONSTRAINT_QUERY) == CONSTRAINTS
    assert scratch.execute(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_consultant_turn_conversation_id_id'"
    ) == [
        (
            "CREATE INDEX ix_consultant_turn_conversation_id_id ON public.consultant_turn "
            "USING btree (conversation_id, id)",
        )
    ]
    for seed in (
        "INSERT INTO consultant_turn (request_id, tab_id, created_at, question, path, tokens)"
        " VALUES (gen_random_uuid(), gen_random_uuid(), now(), 'x', 'hybrid', '{}')",
        "INSERT INTO consultant_conversation (channel, tab_id, token_epoch, started_at,"
        " last_turn_at) VALUES ('website', gen_random_uuid(), 'e', now(), now())",
    ):
        scratch.execute(seed)
        output = scratch.migrate("downgrade", PREDECESSOR, success=False)
        assert "Consultant journal downgrade refused: the journal holds rows" in output
        assert (scratch.marker(), scratch.execute(TABLES)) == (REVISION, [(2,)])
        scratch.execute("DELETE FROM consultant_turn; DELETE FROM consultant_conversation")
    scratch.migrate("downgrade", PREDECESSOR)
    assert (scratch.marker(), scratch.execute(TABLES)) == (PREDECESSOR, [(0,)])
