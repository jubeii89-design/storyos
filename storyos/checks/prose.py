"""The phrases the rules forbid, actually enforced.

`prompts/rules_common.md` tells the model these are rejected. That sentence was
false until this module existed: the list lived in the prompt and nothing read
the draft afterwards. A rule asserted and never enforced trains a model to
treat the rest of the rules as advisory too.

Matching is literal and case-insensitive on word boundaries. No judgement is
made here about whether a phrase is bad in context -- these are rejected on
sight, which is what the rules promise, and a writer who wants one keeps it by
editing the list rather than by arguing with a checker.
"""

from __future__ import annotations

import dataclasses
import json
import re

# Kept in lockstep with the list in project.py's RULES_COMMON. A phrase in one
# and not the other is the defect this module was written to remove.
BANNED = (
    "a testament to", "a mix of", "a symphony of", "a tapestry of",
    "heart hammered", "heart pounded", "heart raced",
    "a shiver ran down", "shivers down",
    "little did she know", "little did he know", "unbeknownst to",
    "the air was thick with", "the silence was deafening",
    "couldn't help but", "could not help but",
    "washed over her", "washed over him",
    "in that moment, she realised", "in that moment, he realised",
    "in that moment, she realized", "in that moment, he realized",
    "everything changed", "as if on cue",
)

_PATTERNS = tuple((p, re.compile(r"\b" + re.escape(p).replace(r"\ ", r"\s+") + r"\b",
                                 re.IGNORECASE)) for p in BANNED)


@dataclasses.dataclass(frozen=True, slots=True)
class Hit:
    phrase: str
    span: tuple[int, int]
    line: int
    context: str


def find(text: str) -> list[Hit]:
    hits: list[Hit] = []
    for phrase, rx in _PATTERNS:
        for m in rx.finditer(text):
            a, b = max(0, m.start() - 50), min(len(text), m.end() + 50)
            hits.append(Hit(phrase, (m.start(), m.end()),
                            text.count("\n", 0, m.start()) + 1,
                            text[a:b].replace("\n", " ").strip()))
    return sorted(hits, key=lambda h: h.span[0])


def run(env, unit: str) -> dict:
    """Check one drafted half. Writes its result beside the word count."""
    path = env.part(unit)
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} not drafted yet")

    hits = find(path.read_text(errors="replace"))
    result = {
        "unit": unit, "ok": not hits, "banned_phrases": len(hits),
        "hits": [{"phrase": h.phrase, "line": h.line, "span": list(h.span),
                  "context": h.context} for h in hits],
    }
    from .. import units
    out = env.root / "checks" / f"ch{units.fmt(*units.parse(unit))}.prose.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    return result
