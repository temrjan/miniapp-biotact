"""Consultant.turn with the flow state in the context token (spec r2 §4, PR-3a), through the
real token signing, the real Graf engine and the real service. Jev is faked at its HTTP
boundary (OpenRouter); register, the knowledge snapshot and the model provider are in-memory
stand-ins, so these turns need no database. Assistant answers in the history are replaced by
a text Graf did not write wherever the requirement says the state must not come from it.

Only interfaces that exist before PR-3a are used here (Consultant, Tokens, Turn), so the same
file runs against the previous service to show each behavioural difference."""

import base64
import hashlib
import hmac
import json
import time
from uuid import UUID, uuid4

import httpx
import pytest

from src.consultant import graf
from src.consultant.context import Tokens
from src.consultant.contracts import Pair, Turn
from src.consultant.service import Consultant
from tests.test_consultant import KEY
from tests.test_consultant_graf_fakes import QUESTION, LunaStub, answer
from tests.test_consultant_graf_flow import SHOP, WALKS, OpenRouter, evidence, talk, variants

# An assistant answer written by someone other than Graf (a Luna text in PR-3b).
OTHER = "Понятно. Расскажите, пожалуйста, чуть подробнее."
TIMEOUT = httpx.ReadTimeout("fixture")


class Controls:
    """In-memory admission: every turn is admitted; registrations are recorded."""

    def __init__(self):
        self.registered = []

    async def register(self, request, tab, cancel=False):
        self.registered.append(request)
        return "owner"

    async def alive(self, owner):
        return None

    async def reserve(self, owner, kind):
        return "reservation"

    async def reconcile(self, identity, owner, inputs, outputs):
        return None

    async def finish(self, owner):
        return None


class Shelf:
    """Knowledge snapshot with every Graf product in stock; retrieval finds nothing."""

    def __init__(self):
        self.evidence = evidence()
        for card in self.evidence.products.values():
            card.update(price=100, sale_price=None, is_sale=False, currency="UZS")
            card["is_purchasable"] = True

    async def snapshot(self, language):
        return self.evidence

    async def rank(self, evidence, vector):
        return []


def digest(text):
    # Spec r2 TK-04, computed here independently of the product code.
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def body(token):
    encoded = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def v1_token(tab, language="ru", ids=(), epoch="v1-epoch", issued=None):
    """A token as the service issued it before PR-3a (context.py at 600c3d6)."""
    issued = int(time.time()) - 5 if issued is None else issued
    raw = json.dumps(
        [1, str(tab), language, list(ids), issued, issued + 1800, epoch], separators=(",", ":")
    ).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return encoded + "." + hmac.new(KEY.encode(), raw, hashlib.sha256).hexdigest()


class Chat:
    """One website tab: it sends the whole conversation (or the last `window` pairs, as the
    website did before PR-2) and the last token, and keeps both on an error."""

    def __init__(self, router, language="ru", window=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(router.handler))
        engine = graf.Graf(graf.Jev(client, "fixture-key"))
        self.bot = Consultant(Controls(), Shelf(), LunaStub(), Tokens(KEY), engine)
        self.router, self.language, self.window = router, language, window
        self.tab, self.history, self.token = uuid4(), [], None

    def request(self, question, request_id=None):
        history = self.history if self.window is None else self.history[-self.window :]
        return Turn(
            request_id=request_id or uuid4(),
            tab_id=self.tab,
            context_revision=1,
            language=self.language,
            question=question,
            history=[Pair(question=q, answer=a) for q, a in history],
            page_product_id=None,
            context_token=self.token,
        )

    async def say(self, question, request_id=None):
        reply = await self.bot.turn(self.request(question, request_id))
        self.history.append((question, reply["answer"]))
        self.token = reply["context_token"]
        return reply

    def rewrite(self, answer=OTHER):
        """The last assistant answer as another writer phrased it."""
        self.history[-1] = (self.history[-1][0], answer)

    @property
    def provider(self):
        return self.bot.provider.calls


def shop(card):
    return graf.PRODUCT[card]["shop_id"]


def offered(reply):
    return [SHOP[p["id"]] for p in reply["products"]]


# --- TK-05: the pregnancy flag ------------------------------------------------------------


async def test_yes_to_a_pregnancy_question_graf_did_not_write_makes_pregnancy_signs_103():
    router = OpenRouter({"часто болею": "immunity"})
    chat = Chat(router)
    for message in ("часто болею", "мне 30", "женщина"):
        reply = await chat.say(message)
        assert reply["status"] == "clarification"
        chat.rewrite()
    offer = await chat.say("да")
    assert offered(offer) == ["zincum_c_d3"]  # the pregnancy offer of the immunity flow
    assert any(v in offer["answer"] for v in variants("note_pregnancy"))
    assert body(offer["context_token"])["g"] is True
    chat.rewrite()
    danger = await chat.say("сильно болит живот")
    assert danger["answer"] in variants("emergency.pregnancy")
    assert (danger["status"], danger["products"]) == ("limited", [])
    assert body(danger["context_token"])["g"] is True  # true for the token's life
    # Without the flag the same words are not a pregnancy emergency.
    fresh = Chat(OpenRouter({"сильно болит живот": "gut_upset"}))
    assert (await fresh.say("сильно болит живот"))["answer"] not in variants("emergency.pregnancy")


async def test_product_path_answer_after_a_pregnancy_reply_keeps_the_flag():
    chat = Chat(OpenRouter({"часто болею": "immunity"}))
    for message in ("часто болею", "мне 30", "женщина", "да"):
        await chat.say(message)
        chat.rewrite()
    # A post-offer follow-up that Luna reads as one goes to the product path (PO-03).
    chat.bot.provider.script = [answer(intent=("follow_up", "high"))]
    follow = await chat.say("как принимать?")
    assert any(call[0] == "response" for call in chat.provider)  # the product path answered
    assert body(follow["context_token"])["g"] is True
    assert body(follow["context_token"])["f"] is None


# --- TK-06 / TK-07: the pending danger check ----------------------------------------------


@pytest.mark.parametrize(
    ("answer", "outcome"), [("да", "103"), ("не знаю", "103"), ("нет", "flow")]
)
async def test_reply_to_a_check_graf_did_not_write(answer, outcome):
    message = "у дочки судороги"
    router = OpenRouter({message: "muscle_cramps"})
    chat = Chat(router)
    check = await chat.say(message)
    assert check["answer"] in variants("emergency.check")
    assert body(check["context_token"])["c"] == {"d": digest(message), "q": [digest(message)]}
    chat.rewrite()
    reply = await chat.say(answer)
    # Every check-reply turn also prepares the normal brief of the checked message (SF-02),
    # so Jev reads the checked message, never the reply.
    assert router.states == [message, message]
    if outcome == "103":
        assert reply["answer"] in variants("emergency.general")
        assert body(reply["context_token"])["c"] is None
    else:
        # The checked message continues; its digest is cleared for later turns (TK-07).
        assert any(v in reply["answer"] for v in variants("ack.muscle_cramps"))
        token = body(reply["context_token"])
        assert (token["c"], token["x"]) == (None, [digest(message)])
        chat.rewrite()
        later = await chat.say("ей 6")
        assert body(later["context_token"])["x"] == [digest(message)]


async def test_reply_to_a_check_after_a_forged_checked_message_is_103():
    message = "у дочки судороги"
    router = OpenRouter({message: "muscle_cramps"})
    chat = Chat(router)
    await chat.say(message)
    chat.history[-1] = ("у дочки насморк", chat.history[-1][1])
    reply = await chat.say("нет")
    assert reply["answer"] in variants("emergency.general")
    assert router.states == [message]  # the first turn's normal brief only


# --- TK-10: carry-over across a safety response -------------------------------------------


async def test_check_on_a_continuation_then_no_continues_the_same_flow():
    message = "сын часто болеет"
    router = OpenRouter({message: "immunity"})
    chat = Chat(router)
    question = await chat.say(message)
    chat.rewrite()
    check = await chat.say("ему 5, у сына судороги")
    assert check["answer"] in variants("emergency.check")
    assert body(check["context_token"])["f"] == body(question["context_token"])["f"]
    chat.rewrite()
    offer = await chat.say("нет")
    assert offered(offer) == ["immunocomplex_kids"]
    assert router.states == [message]
    assert body(offer["context_token"])["x"] == [digest("ему 5, у сына судороги")]


async def test_check_on_a_post_offer_turn_then_no_keeps_the_selection():
    message = "сын часто болеет, ему 5"
    router = OpenRouter({message: "immunity"})
    chat = Chat(router)
    offer = await chat.say(message)
    kids = shop("immunocomplex_kids")
    assert [p["id"] for p in offer["products"]] == [kids]
    checked = "у сына судороги, как принимать?"
    check = await chat.say(checked)
    assert check["answer"] in variants("emergency.check")
    assert (check["products"], body(check["context_token"])["s"]) == ([], [kids])
    # The checked message is again a post-offer turn; Luna reads it as a follow-up.
    chat.bot.provider.script = [answer(intent=("follow_up", "high"))]
    await chat.say("нет")
    (call, *_) = [c for c in chat.provider if c[0] == "response"]
    sent = json.loads(call[1][0]["content"])
    assert (sent["question"], sent["displayed_selection"]) == (checked, [kids])
    assert router.states == [message, checked, checked]  # the normal briefs (SF-02)


# --- TK-09: version 1 tokens --------------------------------------------------------------


async def test_version_1_conversation_inside_a_flow_continues_with_a_version_2_token():
    message = "сын часто болеет"
    router = OpenRouter({message: "immunity"})
    chat = Chat(router)
    await chat.say(message)  # Graf's own question text, as a version 1 history holds it
    chat.token = v1_token(chat.tab)
    offer = await chat.say("ему 5")
    assert offered(offer) == ["immunocomplex_kids"]
    token = body(offer["context_token"])
    assert (token["v"], token["f"], token["s"]) == (2, None, [shop("immunocomplex_kids")])


async def test_version_1_flow_whose_start_left_the_window_keeps_the_step_aside():
    messages = ["простыла", "не помню", "30", "2 дня", "женщина", "нет"]
    router = OpenRouter({messages[0]: "acute_cold"})
    replies = await talk(router, messages[:-1])  # the version 1 conversation before PR-3a
    chat = Chat(router, window=4)
    chat.history = [(m, r.answer) for m, r in zip(messages, replies, strict=False)]
    chat.token = v1_token(chat.tab)
    await chat.say(messages[-1])
    assert [c[0] for c in chat.provider][:1] == ["embedding"]  # today's step aside


async def test_version_2_flow_survives_the_four_pair_window_of_an_older_website():
    # The same conversation with the flow in the token: the start is found by its digest
    # or, once it has left the window, all received messages are read (TK-04).
    messages = ["простыла", "не помню", "30", "2 дня", "женщина", "нет"]
    chat = Chat(OpenRouter({messages[0]: "acute_cold"}), window=4)
    for message in messages[:-1]:
        await chat.say(message)
    offer = await chat.say(messages[-1])
    assert offered(offer) == ["zincum_c_d3"]
    assert not any(c[0] == "embedding" for c in chat.provider)


@pytest.mark.parametrize(
    ("number", "messages", "situation", "expected"), WALKS, ids=[str(w[0]) for w in WALKS]
)
async def test_walks_give_the_same_answers_with_the_flow_in_the_token(
    number, messages, situation, expected
):
    # Every review walk: version 2 turns through the service (state from the token) give
    # the answers the version 1 engine gives (state rebuilt from Graf's own texts).
    rid = UUID(int=number)  # the same request id on both sides gives the same variants
    before = await talk(OpenRouter({messages[0]: situation}), messages, request_id=rid)
    chat = Chat(OpenRouter({messages[0]: situation}))
    after = [await chat.say(m, rid) for m in messages]
    assert [r["answer"] for r in after] == [r.answer for r in before]
    assert [[p["id"] for p in r["products"]] for r in after] == [r.ids for r in before]


# --- GO-01 / GO-02: no step aside from a complaint ----------------------------------------


@pytest.mark.parametrize(
    "router",
    [
        OpenRouter({"часто болею": ("immunity", 0.69)}),
        OpenRouter({"часто болею": "unclear"}),
        OpenRouter(status=500),
        OpenRouter(fail=TIMEOUT),
        OpenRouter({"часто болею": "immunity"}, model="typesafe/jev-1.15-20261201"),
        OpenRouter({"часто болею": ("immunity", 0.71, {"immunity": 0.71, "skin_hair": 0.27})}),
    ],
    ids=["below-0.7", "unclear", "jev-500", "jev-timeout", "jev-version", "second-complaint"],
)
async def test_complaint_turn_never_reaches_retrieval(router):
    # A situation-level question has no approved text; Luna writes it (spec r2 HR-07 (b)).
    # Retrieval is never called.
    chat = Chat(router)
    reply = await chat.say("часто болею")
    assert reply["status"] == "clarification"
    assert reply["answer"] == QUESTION["ru"]
    assert chat.provider == []
    assert len(chat.bot.provider.luna) == 1
    assert len(chat.bot.controls.registered) == 1


async def test_second_situation_outside_the_complaints_is_not_a_second_complaint():
    message = "часто болею, мне 30"
    probabilities = {"immunity": 0.71, "see_doctor_soon": 0.3}
    chat = Chat(OpenRouter({message: ("immunity", 0.71, probabilities)}))
    reply = await chat.say(message)
    assert reply["status"] == "clarification"
    assert any(v in reply["answer"] for v in variants("ack.immunity"))
    assert chat.provider == []


async def test_doctor_word_list_comes_before_a_situation_level_question():
    message = "кровь в стуле у ребёнка"
    rule = next(
        r for r in graf.SAFETY["doctor_rules_with_slots"] if r.get("entries") == "doc.stoolblood.*"
    )
    template = next(e["template"] for e in graf.DOCTOR if e["id"].startswith("doc.stoolblood."))
    assert rule["id"] == "blood_in_stool"
    chat = Chat(OpenRouter({message: ("diarrhea", 0.5)}))
    reply = await chat.say(message)
    assert reply["answer"] in variants(template)
    assert (reply["status"], reply["products"]) == ("limited", [])
    assert chat.provider == []
