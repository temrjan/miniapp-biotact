"""Selection provenance and current-question-only deterministic resolution."""

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from uuid import UUID

from src.consultant.contracts import Fault, Turn

SENSITIVE = re.compile(
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:\+?\d[\s()-]*){9,}|\b(?:sk-|Bearer\s|password\s*[:=]|пароль\s*[:=])",
    re.I,
)
BARE = re.compile(
    r"^(?:а\s+)?(?:цена|стоимость|состав|сколько стоит|narxi|narx|tarkibi|tarkib|qancha|price|composition)(?:\s*[?!.])?$",
    re.I,
)
OPEN = re.compile(r"\b(?:open|открой|открои|откройте|och|oching|ochib)\b", re.I)
ORDINALS = ((r"\b(?:first|перв\w*|birinchi\w*)\b", 0), (r"\b(?:second|втор\w*|ikkinchi\w*)\b", 1))
FORMS = {
    "capsule": re.compile(r"\b(?:капсул\w*|kapsul\w*|capsul\w*)\b"),
    "stick": re.compile(r"\b(?:стик\w*|stik\w*|stick\w*)\b"),
}
OPEN_REFERENCE = re.compile(
    r"^(?:перв\w*|втор\w*|из|показанных|показанный|товар|товара|карточку|его|ее|её|этот|эту|"
    r"birinchi\w*|ikkinchi\w*|ko[ʻ‘’ʼ']?rsatilgan(?:lardan)?|mahsulot\w*|uni|shu|bu|"
    r"first|second|the|of|shown|displayed|product|card|it)$"
)


def normalize(value: str) -> str:
    return " ".join(value.casefold().replace("®", "").split())


def pure_open(question: str, aliases: list[str]) -> bool:
    """Recognize only an imperative and its target; other requests still need facts."""
    q = normalize(question).strip(" .!?")
    q = re.sub(r"^(?:пожалуйста|iltimos|please)[, ]+|[, ]+(?:пожалуйста|iltimos|please)$", "", q)
    command = re.fullmatch(r"(?:открой|открои|откройте|open)\s+(.+)", q) or re.fullmatch(
        r"(.+?)\s+(?:och|oching|ochib)", q
    )
    if command is None:
        return False
    target = command[1]
    for alias in sorted({normalize(alias) for alias in aliases}, key=len, reverse=True):
        target = re.sub(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", " ", target)
    return all(OPEN_REFERENCE.fullmatch(word) for word in target.split())


class Tokens:
    def __init__(self, key: str) -> None:
        self.key = key.encode()

    def issue(
        self, tab: UUID, language: str, ids: list[int], epoch: str, now: int | None = None
    ) -> tuple[str, int]:
        issued = int(time.time()) if now is None else now
        body = json.dumps(
            [1, str(tab), language, ids, issued, issued + 1800, epoch], separators=(",", ":")
        ).encode()
        encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
        return encoded + "." + hmac.new(self.key, body, hashlib.sha256).hexdigest(), issued + 1800

    def read(self, turn: Turn, now: int | None = None) -> tuple[list[int], str]:
        try:
            if not turn.context_token or len(self.key) < 32:
                raise ValueError
            encoded, signature = turn.context_token.split(".")
            body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if not hmac.compare_digest(
                signature, hmac.new(self.key, body, hashlib.sha256).hexdigest()
            ):
                raise ValueError
            value = json.loads(body)
            if not isinstance(value, list) or len(value) != 7:
                raise ValueError
            version, tab, language, ids, issued, expires, epoch = value
            clock = int(time.time()) if now is None else now
            if (
                version != 1
                or tab != str(turn.tab_id)
                or language != turn.language
                or not isinstance(ids, list)
                or len(ids) > 2
                or any(type(i) is not int or i <= 0 for i in ids)
                or len(set(ids)) != len(ids)
                or type(issued) is not int
                or type(expires) is not int
                or expires != issued + 1800
                or issued > clock
                or not isinstance(epoch, str)
            ):
                raise ValueError
            if clock >= expires:
                raise Fault("context_expired", 409)
            return ids, epoch
        except (ValueError, TypeError, KeyError):
            raise Fault("context_invalid", 400) from None


@dataclass(frozen=True)
class Subject:
    ids: list[int]
    explicit: bool
    clarify: bool
    open_intent: bool


def resolve(
    question: str,
    aliases: dict[int, list[str]],
    selection: list[int],
    page: int | None,
    fresh: bool,
) -> Subject:
    q = normalize(question)
    matches = [
        (q.find(normalize(alias)), -len(normalize(alias)), pid)
        for pid, names in aliases.items()
        for alias in names
        if normalize(alias) in q
    ]
    # Long aliases consume their own overlapping short aliases (ZINCUM C+D3 vs ZINCUM).
    matches.sort()
    groups: list[tuple[int, int, list[int]]] = []
    for pos, negative_length, pid in matches:
        end = pos - negative_length
        if not groups or pos >= groups[-1][1]:
            groups.append((pos, end, [pid]))
        elif (pos, end) == groups[-1][:2] and pid not in groups[-1][2]:
            groups[-1][2].append(pid)
        elif (
            pos == groups[-1][0]
            and pid not in groups[-1][2]
            and any(pattern.fullmatch(q[end : groups[-1][1]].strip()) for pattern in FORMS.values())
        ):
            # A form suffix narrows a shared name, but must still allow a following
            # second form ("ACTIVE CAPSULE and STICK") to resolve that same name.
            groups[-1][2].append(pid)
    ids: list[int] = []
    for index, (pos, _, group) in enumerate(groups):
        if len(group) > 1:
            # Shared names are resolved by current form words, never DB/alias insertion order.
            stop = groups[index + 1][0] if index + 1 < len(groups) else len(q)
            mentions = sorted(
                (match.start(), form)
                for form, pattern in FORMS.items()
                for match in pattern.finditer(q[pos:stop])
            )
            if not mentions and index == 0:
                mentions = sorted(
                    (match.start(), form)
                    for form, pattern in FORMS.items()
                    for match in pattern.finditer(q[:pos])
                )
            if mentions:
                ordered: list[int] = []
                for _, form in mentions:
                    matching = [
                        pid
                        for pid in group
                        if any(FORMS[form].search(normalize(alias)) for alias in aliases[pid])
                    ]
                    if len(matching) != 1:
                        return Subject([], True, True, bool(OPEN.search(q)))
                    ordered.extend(matching)
                group = ordered
        ids.extend(pid for pid in group if pid not in ids)
    ordinal = [index for pattern, index in ORDINALS if re.search(pattern, q)]
    opened = bool(OPEN.search(q)) and not re.search(
        r"\b(?:не|нет|если|потом|not|never|if|later|ochma\w*|ochmang|agar|keyin)\b", q
    )
    if ordinal:
        if len(ordinal) != 1 or ordinal[0] >= len(selection):
            return Subject([], bool(ids), True, opened)
        chosen = selection[ordinal[0]]
        if ids and ids != [chosen]:
            return Subject([], True, True, opened)
        return Subject([chosen], bool(ids), False, opened)
    if ids:
        return Subject(ids[:2], True, len(ids) > 2 or (opened and len(ids) != 1), opened)
    selected = selection or ([page] if fresh and page in aliases else [])
    singular = len(selected) > 1 and bool(re.search(r"\b(?:его|этот|него|uni|this|it)\b", q))
    return Subject(
        selected,
        False,
        (not selected and bool(BARE.fullmatch(q))) or (opened and len(selected) != 1) or singular,
        opened,
    )
