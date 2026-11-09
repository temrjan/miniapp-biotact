"""A fake Luna at the Provider.call boundary (spec r2 §12 test doubles). Each call returns a
scripted Responses body; the default script echoes the approved text for the target the
brief implies, so a turn delivers Luna's text equal to Graf's approved text. A fake Luna
proves the service's reaction, not the model."""

import copy
import json
from collections.abc import Callable
from typing import Any

from src.consultant.contracts import Fault
from tests.test_consultant import ProviderStub

QUESTION = {
    "ru": "Подскажите, пожалуйста, что именно вас беспокоит?",
    "uz": "Iltimos, aynan nima bezovta qilayotganini yozing?",
}
FACT = {"state": "unknown", "quote": "", "confidence": "low"}


def neutral() -> dict[str, Any]:
    return {
        "situation": "none",
        "situation_confidence": "low",
        "second_situation": "none",
        "facts": {
            "whom": {**FACT, "value": None},
            "sex": {**FACT, "value": None},
            "age": {**FACT, "number": None, "unit": None},
            "duration": {**FACT, "number": None, "unit": None},
            "temperature": {**FACT, "number": None},
            "pregnant": {**FACT, "value": None},
            "intolerance": {"present": False, "quote": ""},
            "danger": {"present": False, "quote": ""},
            "topic_change": {"present": False, "quote": ""},
        },
        "age_candidates": [],
        "objections": [],
        "check_reply": {"value": "not_applicable", "quote": "", "confidence": "low"},
        "intent": {"value": "not_applicable", "confidence": "low"},
    }


def target(brief: dict[str, Any]) -> str:
    second = brief.get("second_call")
    if second:
        return str(second["target"])
    safety = brief.get("safety")
    if safety and safety["primary"] in {"emergency", "check"}:
        return str(safety["primary"])
    decision = brief.get("decision")
    if decision is None:
        return "check"
    if decision["kind"] in {"clarify", "confirm", "choose", "post_offer"}:
        return "question"
    if brief.get("turn") == "post_offer":
        return "question"
    return "decision"


def echo(brief: dict[str, Any]) -> dict[str, Any]:
    kind = target(brief)
    texts = brief["texts"]
    body = texts.get(kind) if kind != "question" else QUESTION[brief["language"]]
    if body is None:
        body = QUESTION[brief["language"]]
    return {
        "reading": neutral(),
        "decision": {"action": "follow", "situation": "none"},
        "text": {"target": kind, "body": body},
    }


def answer(**change: Any) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A step that starts from the echo and changes parts of it. Keys: target, body,
    situation, confidence, facts (merged per fact), objections (a list or a callable over
    the brief), check_reply, intent, danger, topic_change, candidates, action (Luna's
    decision; default "follow", "check" with danger), decided (its situation)."""

    def step(brief: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(echo(brief))
        reading = out["reading"]
        if "target" in change:
            out["text"]["target"] = change["target"]
            texts = brief["texts"]
            kind = change["target"]
            out["text"]["body"] = texts.get(kind) or QUESTION[brief["language"]]
        if "body" in change:
            out["text"]["body"] = change["body"]
        if "situation" in change:
            reading["situation"] = change["situation"]
            reading["situation_confidence"] = change.get("confidence", "high")
        for name, value in change.get("facts", {}).items():
            reading["facts"][name].update(value)
        objections = change.get("objections", [])
        reading["objections"] = objections(brief) if callable(objections) else objections
        if "check_reply" in change:
            value, quote, confidence = change["check_reply"]
            reading["check_reply"] = {"value": value, "quote": quote, "confidence": confidence}
        if "intent" in change:
            value, confidence = change["intent"]
            reading["intent"] = {"value": value, "confidence": confidence}
        if "danger" in change:
            reading["facts"]["danger"] = {"present": True, "quote": change["danger"]}
            out["decision"]["action"] = "check"
        if "action" in change:
            out["decision"]["action"] = change["action"]
        if "decided" in change:
            out["decision"]["situation"] = change["decided"]
        if "topic_change" in change:
            reading["facts"]["topic_change"] = {"present": True, "quote": change["topic_change"]}
        if "candidates" in change:
            reading["age_candidates"] = change["candidates"]
        return out

    return step


def body(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(result)}],
            },
        ],
        "usage": {"input_tokens": 3000, "output_tokens": 200},
    }


def objection(brief: dict[str, Any], prefix: str, quote: str, **extra: Any) -> dict[str, Any]:
    """An objection to the first listed hit whose id starts with prefix."""
    hit = next(h for h in brief["hits"] if h["id"].startswith(prefix))
    return {
        "hit_id": hit["id"],
        "reason": extra.get("reason", "negated"),
        "quote": quote,
        "confidence": extra.get("confidence", "high"),
    }


class LunaStub(ProviderStub):
    """ProviderStub for the product path plus Luna through Provider.call. `luna` holds
    every Luna payload; `script` holds one step per call (a callable over the brief, a
    raw body dict, or an exception to raise); an empty script echoes."""

    def __init__(self, script: list[Any] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.luna: list[dict[str, Any]] = []
        self.script = list(script or [])
        self.hold: Any = None

    @property
    def briefs(self) -> list[dict[str, Any]]:
        return [json.loads(p["input"][1]["content"][0]["text"]) for p in self.luna]

    async def call(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert route == "responses"
        self.luna.append(payload)
        if self.hold is not None:
            await self.hold.wait()
        brief = json.loads(payload["input"][1]["content"][0]["text"])
        step = self.script.pop(0) if self.script else echo
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, dict):
            return step
        result = step(brief)
        if isinstance(result, Fault):
            raise result
        return body(result)
