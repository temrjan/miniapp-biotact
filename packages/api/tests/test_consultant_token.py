"""Token v2 (spec r2 §4.1) at the real signing code: round trip, version 1 reading, tampering,
expiry and binding, malformed bodies and the request bound. Moved unchanged out of
test_consultant_graf_token.py (Graf removal step 1); these properties do not depend on Graf."""

import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest

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


def v1_token(tab, language="ru", ids=(), epoch="v1-epoch", issued=None):
    """A token as the service issued it before PR-3a (context.py at 600c3d6)."""
    issued = int(time.time()) - 5 if issued is None else issued
    raw = json.dumps(
        [1, str(tab), language, list(ids), issued, issued + 1800, epoch], separators=(",", ":")
    ).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return encoded + "." + hmac.new(KEY.encode(), raw, hashlib.sha256).hexdigest()


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
