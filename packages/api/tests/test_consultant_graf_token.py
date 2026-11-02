"""Token v2 (spec r2 §4.1) at the real signing code, and Graf's brief decisions where it
stepped aside before (§4.2), through the real engine with Jev faked at its HTTP boundary."""

import base64
import hashlib
import hmac
import json
from uuid import uuid4

import httpx
import pytest

from src.consultant import graf
from src.consultant.context import (
    TOKEN_LIMIT,
    Check,
    Context,
    FlowState,
    Tokens,
    digest,
)
from src.consultant.contracts import Fault, Pair, Turn
from tests.test_consultant import KEY
from tests.test_consultant_graf_flow import OpenRouter, direct, variants
from tests.test_consultant_graf_turns import Chat, v1_token

NOW = 1_800_000_000


def turn(question="x", history=(), token=None, tab=None, language="ru"):
    return Turn(
        request_id=uuid4(),
        tab_id=tab or uuid4(),
        context_revision=1,
        language=language,
        question=question,
        history=[Pair(question=q, answer=a) for q, a in history],
        page_product_id=None,
        # Graf reads no token; a Turn with history needs one to be valid.
        context_token=token or ("fixture" if history else None),
    )


def signed(value, key=KEY):
    raw = json.dumps(value, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return encoded + "." + hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()


def body(token):
    encoded = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def full_flow(**changes):
    flow = FlowState(
        situation="immunity",
        pending="slot",
        template="ask.pregnancy",
        asked=("ask.whom_age", "ask.sex", "ask.pregnancy"),
        options=(),
        first=digest("часто болею"),
        counter=3,
        facts={
            "age": (30.0, "confirmed"),
            "whom": ("self", "parser"),
            "sex": ("female", "confirmed"),
            "pregnant": (True, "luna"),
            "duration": (2.0, "parser"),
            "temperature": (38.5, "parser"),
        },
        flags=frozenset(("child_word", "form_stick", "disease", "after_clarify", "intolerance")),
        topics=("topic_cold", "topic_fever"),
        overruled=(("doc.stoolblood.ru.1", digest("кровь")), ("topic_fever", digest("38"))),
    )
    return FlowState(**{**flow.__dict__, **changes})


# --- TK-01 / TK-02 / TK-03 ---------------------------------------------------------------


def test_version_2_round_trip_keeps_every_field():
    tokens, request = Tokens(KEY), turn()
    check = Check(digest("у дочки судороги"), (digest("у дочки судороги"), digest("и рвота")))
    token, expires = tokens.issue(
        request.tab_id,
        "ru",
        [4, 20],
        "epoch",
        NOW,
        pg=True,
        check=check,
        cleared=(digest("a"), digest("b")),
        flow=full_flow(),
    )
    assert expires == NOW + 1800
    assert body(token)["v"] == 2
    context = tokens.read(request.model_copy(update={"context_token": token}), NOW + 1)
    assert context == Context(
        [4, 20], "epoch", 2, True, check, (digest("a"), digest("b")), full_flow()
    )


def test_issue_always_writes_version_2():
    token, _ = Tokens(KEY).issue(uuid4(), "uz", [], "epoch", NOW)
    assert body(token) == {
        "v": 2,
        "t": body(token)["t"],
        "l": "uz",
        "s": [],
        "i": NOW,
        "e": NOW + 1800,
        "p": "epoch",
        "g": False,
        "c": None,
        "x": [],
        "f": None,
    }


def test_version_1_token_is_still_read():
    request = turn()
    token = v1_token(request.tab_id, ids=(4, 20), epoch="old", issued=NOW)
    context = Tokens(KEY).read(request.model_copy(update={"context_token": token}), NOW + 1799)
    assert context == Context([4, 20], "old", 1)
    with pytest.raises(Fault, match="context_expired"):
        Tokens(KEY).read(request.model_copy(update={"context_token": token}), NOW + 1800)


def tampered(token, part):
    encoded, signature = token.split(".")
    if part == "body":
        raw = bytearray(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        raw[raw.index(b'"g":false') + 4 : raw.index(b'"g":false') + 9] = b"true "
        return base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=") + "." + signature
    if part == "signature":
        return encoded + "." + ("0" if signature[0] != "0" else "1") + signature[1:]
    if part == "other-key":
        return signed(body(token), "another-consultant-key-with-32-bytes")
    if part == "no-signature":
        return encoded
    raise AssertionError(part)


@pytest.mark.parametrize("part", ["body", "signature", "other-key", "no-signature"])
def test_tampered_version_2_token_is_invalid(part):
    tokens, request = Tokens(KEY), turn()
    token, _ = tokens.issue(request.tab_id, "ru", [], "epoch", NOW, flow=full_flow())
    assert tokens.read(request.model_copy(update={"context_token": token}), NOW).version == 2
    with pytest.raises(Fault) as fault:
        tokens.read(request.model_copy(update={"context_token": tampered(token, part)}), NOW)
    assert (fault.value.code, fault.value.status) == ("context_invalid", 400)


def test_expiry_and_binding_of_a_version_2_token():
    tokens, request = Tokens(KEY), turn()
    token, _ = tokens.issue(request.tab_id, "ru", [], "epoch", NOW, flow=full_flow())
    bound = request.model_copy(update={"context_token": token})
    assert tokens.read(bound, NOW + 1799).flow == full_flow()
    with pytest.raises(Fault) as fault:
        tokens.read(bound, NOW + 1800)
    assert (fault.value.code, fault.value.status) == ("context_expired", 409)
    for wrong in (
        bound.model_copy(update={"tab_id": uuid4()}),
        bound.model_copy(update={"language": "uz"}),
    ):
        with pytest.raises(Fault, match="context_invalid"):
            tokens.read(wrong, NOW + 1)
    with pytest.raises(Fault, match="context_invalid"):
        tokens.read(bound, NOW - 1)  # issued in the future


def valid_body(tab):
    return {
        "v": 2,
        "t": str(tab),
        "l": "ru",
        "s": [],
        "i": NOW,
        "e": NOW + 1800,
        "p": "epoch",
        "g": False,
        "c": None,
        "x": [],
        "f": full_flow().dump(),
    }


MALFORMED = [
    ("extra key", lambda b: {**b, "z": 1}),
    ("version 3", lambda b: {**b, "v": 3}),
    ("version as bool", lambda b: {**b, "v": True}),
    ("pg as int", lambda b: {**b, "g": 1}),
    ("short digest in x", lambda b: {**b, "x": ["abc"]}),
    ("upper-case digest in x", lambda b: {**b, "x": [digest("a").upper()]}),
    ("check without quotes", lambda b: {**b, "c": {"d": digest("a")}}),
    ("three selection ids", lambda b: {**b, "s": [1, 2, 3]}),
    ("expiry not issued + 1800", lambda b: {**b, "e": NOW + 1801}),
    ("flow missing key", lambda b: {**b, "f": {k: v for k, v in b["f"].items() if k != "h"}}),
    ("unknown pending kind", lambda b: {**b, "f": {**b["f"], "k": "post_offer"}}),
    ("slot without template", lambda b: {**b, "f": {**b["f"], "t": None}}),
    ("zero counter", lambda b: {**b, "f": {**b["f"], "n": 0}}),
    ("flag bits too large", lambda b: {**b, "f": {**b["f"], "b": 32}}),
    ("unknown fact", lambda b: {**b, "f": {**b["f"], "v": {"weight": [3, "parser"]}}}),
    ("fact of the wrong type", lambda b: {**b, "f": {**b["f"], "v": {"age": ["30", "parser"]}}}),
    ("unknown origin", lambda b: {**b, "f": {**b["f"], "v": {"age": [30, "guess"]}}}),
    ("overruled hit as a string", lambda b: {**b, "f": {**b["f"], "h": ["doc.x"]}}),
]


@pytest.mark.parametrize(("case", "change"), MALFORMED, ids=[m[0] for m in MALFORMED])
def test_signed_but_malformed_version_2_body_is_invalid(case, change):
    request = turn()
    token = signed(change(valid_body(request.tab_id)))
    with pytest.raises(Fault) as fault:
        Tokens(KEY).read(request.model_copy(update={"context_token": token}), NOW)
    assert (fault.value.code, fault.value.status) == ("context_invalid", 400)
    # The unchanged body passes, so each case fails for its own change.
    assert (
        Tokens(KEY)
        .read(request.model_copy(update={"context_token": signed(valid_body(request.tab_id))}), NOW)
        .flow
        == full_flow()
    )


def test_token_limit_is_the_request_bound():
    assert TOKEN_LIMIT == 2048
    assert turn(token="t" * TOKEN_LIMIT).context_token
    with pytest.raises(ValueError, match="2048"):
        turn(token="t" * (TOKEN_LIMIT + 1))


# --- TK-08: size ----------------------------------------------------------------------------


def longest(values):
    return max(values, key=len)


def worst_flow(hits):
    entry = longest([e["id"] for e in graf.ENTRIES])
    templates = sorted(graf.QUESTION_SLOT, key=len, reverse=True)
    options = sorted(graf.COMPLAINTS, key=len, reverse=True)[:2]
    return FlowState(
        situation=longest(graf.COMPLAINTS),
        pending="slot",
        template=templates[0],
        asked=tuple(templates[:6]),
        options=tuple(options),
        first=digest("first"),
        counter=99,
        facts={
            "age": (0.67, "confirmed"),
            "whom": ("daughter", "luna"),
            "sex": ("female", "confirmed"),
            "pregnant": (False, "confirmed"),
            "duration": (240.0, "parser"),
            "temperature": (38.5, "confirmed"),
        },
        flags=frozenset(graf.FLAGS),
        topics=tuple(graf.TOPICS),
        overruled=tuple((entry, digest(str(n))) for n in range(hits)),
    )


def test_worst_case_token_fits_without_dropping_anything():
    assert len(graf.TOPICS) == 17
    tokens, request = Tokens(KEY), turn()
    check = Check(digest("checked"), (digest("q1"), digest("q2")))
    cleared = tuple(digest(str(n)) for n in range(4))
    flow = worst_flow(6)
    token, _ = tokens.issue(
        request.tab_id,
        "ru",
        [9007199254740991, 9007199254740990],
        str(uuid4()),
        NOW,
        pg=True,
        check=check,
        cleared=cleared,
        flow=flow,
    )
    assert len(token) <= TOKEN_LIMIT
    context = tokens.read(request.model_copy(update={"context_token": token}), NOW)
    assert (context.check, context.cleared, context.flow) == (check, cleared, flow)


def issue_big(cleared, hits, asked=6, situation=None):
    tokens, request = Tokens(KEY), turn()
    flow = worst_flow(hits)
    flow = FlowState(**{**flow.__dict__, "asked": (flow.asked * 2)[:asked]})
    if situation is not None:
        flow = FlowState(**{**flow.__dict__, "situation": situation})
    token, _ = tokens.issue(
        request.tab_id,
        "ru",
        [1, 2],
        str(uuid4()),
        NOW,
        pg=True,
        check=Check(digest("c"), (digest("q"),)),
        cleared=cleared,
        flow=flow,
    )
    assert len(token) <= TOKEN_LIMIT
    return tokens.read(request.model_copy(update={"context_token": token}), NOW), flow


def test_overflow_drops_the_oldest_cleared_digests_first():
    cleared = tuple(digest(str(n)) for n in range(30))
    context, flow = issue_big(cleared, 6)
    assert context.cleared == cleared[-len(context.cleared) :]
    assert 0 < len(context.cleared) < 30
    assert context.flow == flow  # nothing else dropped


def test_overflow_then_trims_asked_templates_then_overruled_hits():
    context, flow = issue_big((digest("only"),), 20, asked=12)
    assert context.cleared == ()
    assert context.flow is not None
    assert context.flow.asked == flow.asked[-3:]
    kept = context.flow.overruled
    assert kept == flow.overruled[-len(kept) :]
    assert 0 < len(kept) < 20  # the oldest overruled hits went (caution direction)


def test_overflow_finally_drops_the_flow_state():
    # No real flow is this large; an oversized situation id reaches the last step.
    context, _ = issue_big((digest("only"),), 3, situation="x" * TOKEN_LIMIT)
    assert (context.cleared, context.flow) == ((), None)
    assert (context.pg, context.check) == (True, Check(digest("c"), (digest("q"),)))


# --- TK-04: flow texts ----------------------------------------------------------------------


def flow_at(first, counter):
    return FlowState("immunity", "slot", "ask.age", ("ask.whom_age",), (), digest(first), counter)


def test_flow_texts_start_at_the_counted_message():
    history = [("привет", "a"), ("часто болею", "b"), ("для себя", "c")]
    start, texts = graf.flow_texts(turn("30", history), flow_at("часто болею", 2))
    assert (start, texts) == (1, ["часто болею", "для себя", "30"])


def test_flow_texts_of_a_cut_history_read_every_received_message():
    # An older website sent only the last 4 pairs; the first message is not among them.
    history = [(f"ответ {n}", "a") for n in range(4)]
    start, texts = graf.flow_texts(turn("30", history), flow_at("часто болею", 6))
    assert (start, texts) == (0, [*(q for q, _ in history), "30"])


def test_flow_texts_pick_the_counted_one_of_two_identical_first_messages():
    history = [("часто болею", "a"), ("спасибо", "b"), ("часто болею", "c"), ("для себя", "d")]
    request = turn("30", history)
    assert graf.flow_texts(request, flow_at("часто болею", 2))[0] == 2
    assert graf.flow_texts(request, flow_at("часто болею", 4))[0] == 0
    # A counter that does not point at the digest: the latest earlier message with it.
    assert graf.flow_texts(request, flow_at("часто болею", 3))[0] == 2


# --- GO-01 / GO-02: brief decisions ---------------------------------------------------------


def make(router):
    client = httpx.AsyncClient(transport=httpx.MockTransport(router.handler))
    return graf.Graf(graf.Jev(client, "fixture-key"))


async def first(router, message):
    return await make(router).first(turn(message), direct)


@pytest.mark.parametrize(
    ("router", "kind", "options"),
    [
        (OpenRouter({"часто болею": ("immunity", 0.69)}), "confirm", ("immunity",)),
        (OpenRouter({"часто болею": ("unclear", 0.4)}), "clarify", ()),
        (OpenRouter({"часто болею": ("unclear", 0.95)}), "clarify", ()),
        (OpenRouter({"часто болею": ("emergency", 0.5)}), "clarify", ()),
        (OpenRouter(status=500), "clarify", ()),
        (OpenRouter(fail=httpx.ReadTimeout("fixture")), "clarify", ()),
        (OpenRouter({"часто болею": "immunity"}, model="typesafe/jev-1.15"), "clarify", ()),
        (
            OpenRouter({"часто болею": ("immunity", 0.71, {"immunity": 0.71, "skin_hair": 0.27})}),
            "choose",
            ("immunity", "skin_hair"),
        ),
    ],
)
async def test_where_graf_stepped_aside_the_brief_is_a_situation_level_question(
    router, kind, options
):
    brief = await first(router, "часто болею")
    assert (brief.outcome.kind, brief.outcome.options, brief.outcome.situation) == (
        kind,
        options,
        None,
    )
    assert brief.flow is not None
    assert (brief.flow.pending, brief.flow.options, brief.flow.asked) == (kind, options, (kind,))
    assert (brief.flow.first, brief.flow.counter) == (digest("часто болею"), 1)


@pytest.mark.parametrize(
    "second", [("see_doctor_soon", 0.3), ("other_unhandled", 0.3), ("acute_cold", 0.27)]
)
async def test_no_choose_without_a_second_complaint_of_another_group(second):
    message = "часто болею, мне 30"
    router = OpenRouter({message: ("immunity", 0.71, {"immunity": 0.71, second[0]: second[1]})})
    brief = await first(router, message)
    assert brief.outcome.kind == "question"
    assert brief.outcome.situation == "immunity"


async def test_decide_step_aside_becomes_clarify():
    facts = graf.Facts()
    facts.absorb("часто болею")
    outcome = make(OpenRouter()).settle(graf.Outcome("step_aside", "immunity"), facts)
    assert (outcome.kind, outcome.options) == ("clarify", ())


@pytest.mark.parametrize(("situation", "confidence"), [("diarrhea", 0.5), ("unclear", 0.9)])
async def test_doctor_word_list_replaces_the_question(situation, confidence):
    message = "кровь в стуле у ребёнка"
    brief = await first(OpenRouter({message: (situation, confidence)}), message)
    assert (brief.outcome.kind, brief.outcome.template) == ("doctor", "doctor.general")
    assert brief.flow is None


# --- GO-03: replies to a situation-level question -------------------------------------------


async def ask_then(router, opening, reply, pending_router=None):
    """Graf's situation-level brief for `opening`, its flow state issued as the response
    token would carry it, then the reply as the next turn."""
    bot = make(pending_router or router)
    brief = await bot.first(turn(opening), direct)
    assert brief.flow is not None
    request = turn(reply, [(opening, "Вопрос, который написал не Граф?")])
    return brief, await make(router).continuation(request, brief.flow, direct)


async def test_yes_to_confirm_starts_the_hypothesis_flow_with_both_messages_facts():
    pending = OpenRouter({"часто болею у меня": ("immunity", 0.6)})
    router = OpenRouter()
    brief, after = await ask_then(router, "часто болею у меня", "да, мне 30", pending)
    assert brief.outcome.kind == "confirm"
    assert (after.outcome.kind, after.outcome.situation) == ("question", "immunity")
    assert after.outcome.template == "ask.sex"  # age 30 came with the reply
    assert after.facts.age == 30.0
    assert router.calls == []  # a yes needs no Jev call
    assert after.flow.first == digest("часто болею у меня")
    assert after.flow.counter == 2


async def test_choose_reply_on_an_option_starts_that_flow():
    two = ("immunity", 0.71, {"immunity": 0.71, "skin_hair": 0.27})
    pending = OpenRouter({"часто болею и волосы выпадают": two})
    router = OpenRouter({"волосы, мне 28": ("skin_hair", 0.9)})
    _, after = await ask_then(router, "часто болею и волосы выпадают", "волосы, мне 28", pending)
    assert after.outcome.kind == "offer"
    assert after.outcome.situation == "skin_hair"


async def test_reply_with_a_new_situation_is_a_first_message_turn_with_both_facts():
    pending = OpenRouter({"мне 30, что-то плохо": ("immunity", 0.6)})
    router = OpenRouter({"нет, понос": ("diarrhea", 0.9)})
    _, after = await ask_then(router, "мне 30, что-то плохо", "нет, понос", pending)
    assert after.outcome.kind == "offer"
    assert after.outcome.situation == "diarrhea"
    assert after.facts.age == 30.0  # from the flow's first message


@pytest.mark.parametrize(
    ("reply_route", "kind"),
    [(("unclear", 0.9), "clarify"), (("immunity", 0.9), "clarify"), (("immunity", 0.5), "clarify")],
)
async def test_unresolved_confirm_reply_asks_clarify(reply_route, kind):
    pending = OpenRouter({"часто болею": ("immunity", 0.6)})
    router = OpenRouter({"нет": reply_route})
    _, after = await ask_then(router, "часто болею", "нет", pending)
    assert after.outcome.kind == kind
    assert after.flow.asked == ("confirm", "clarify")


async def test_unresolved_choose_reply_asks_choose_again_then_no_product():
    two = ("immunity", 0.71, {"immunity": 0.71, "skin_hair": 0.27})
    bot = make(OpenRouter({"часто болею и волосы выпадают": two}))
    brief = await bot.first(turn("часто болею и волосы выпадают"), direct)
    history = [("часто болею и волосы выпадают", "Что сначала?")]
    again = await bot.continuation(turn("не знаю", history), brief.flow, direct)
    assert (again.outcome.kind, again.outcome.options) == ("choose", ("immunity", "skin_hair"))
    history.append(("не знаю", "Так что же?"))
    end = await bot.continuation(turn("без понятия", history), again.flow, direct)
    assert (end.outcome.kind, end.outcome.template) == ("no_product", "no_product.general")
    assert end.flow is None


async def test_doctor_words_in_a_reply_to_a_situation_level_question():
    pending = OpenRouter({"часто болею": ("immunity", 0.6)})
    _, after = await ask_then(OpenRouter(), "часто болею", "у сына кровь в стуле", pending)
    assert (after.outcome.kind, after.outcome.template) == ("doctor", "doctor.general")


async def test_confirm_flow_from_a_token_continues_through_the_service():
    # The whole path once: a pending confirm in a signed token, a "да" with a history
    # answer Graf did not write, the next question and its token.
    opening = "часто болею у меня"
    brief = await make(OpenRouter({opening: ("immunity", 0.6)})).first(turn(opening), direct)
    chat = Chat(OpenRouter())
    chat.history = [(opening, "Правильно понимаю, что речь о частых простудах?")]
    chat.token = chat.bot.tokens.issue(chat.tab, "ru", [], "epoch", flow=brief.flow)[0]
    reply = await chat.say("да")
    assert reply["status"] == "clarification"
    assert any(v in reply["answer"] for v in variants("ack.immunity"))
    flow = chat.bot.tokens.read(chat.request("x")).flow
    assert (flow.situation, flow.pending, flow.asked) == (
        "immunity",
        "slot",
        ("confirm", "ask.whom_age"),
    )
    assert (flow.first, flow.counter) == (digest(opening), 2)


async def test_flow_state_with_ids_unknown_to_the_data_is_an_invalid_context():
    chat = Chat(OpenRouter())
    chat.history = [("часто болею", "Вопрос?")]
    unknown = full_flow(situation="no_such_situation")
    chat.token = chat.bot.tokens.issue(chat.tab, "ru", [], "epoch", flow=unknown)[0]
    with pytest.raises(Fault) as fault:
        await chat.say("да")
    assert (fault.value.code, fault.value.status) == ("context_invalid", 400)
