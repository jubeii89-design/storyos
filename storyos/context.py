"""Prompt assembly as labeled, inspectable segments.

The rule this module exists to enforce: **Story OS never builds a prompt with an
f-string.** Every prompt is a list of Segments, each carrying a label, a
fidelity level, and a token count. That costs a little ceremony at the call
site and buys three things that matter more:

1. The Prompt Inspector can show you exactly which segment ate your context
   budget, so when a chapter comes out wrong you can see *why* instead of
   guessing.
2. Two runs of the same stage can be diffed segment-by-segment.
3. Budget enforcement is mechanical. A prose call that would exceed its window
   drops or demotes the lowest-priority segments by rule, rather than the API
   truncating the middle of your world bible at random.

Fidelity is the core idea behind the context strategy. Recent prose goes in
VERBATIM because rhythm and diction carry over. Older material goes in as
STRUCTURED facts, because by chapter 30 you need to know the protagonist's leg
is broken, not to re-read the paragraph where it broke.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import re
from typing import Iterable, Sequence


class Fidelity(enum.StrEnum):
    """How much of the source survives into the prompt."""

    VERBATIM = "verbatim"      # exact text; rhythm and diction carry over
    STRUCTURED = "structured"  # facts as data; JSON ledger rows
    SUMMARY = "summary"        # model-written prose compression
    RULES = "rules"            # static instructions, cacheable


# Priority decides what gets dropped first when over budget. Higher survives.
DEFAULT_PRIORITY: dict[Fidelity, int] = {
    Fidelity.RULES: 100,
    Fidelity.STRUCTURED: 80,
    Fidelity.VERBATIM: 60,
    Fidelity.SUMMARY: 20,
}


def estimate_tokens(text: str) -> int:
    """Offline token estimate, deliberately slightly pessimistic.

    English prose runs ~3.6 chars/token for Claude and GPT tokenizers; dense
    JSON runs nearer 2.8 because punctuation splits. We bias high so the
    budget check errs toward dropping a segment rather than toward a 400 from
    the API mid-run. Swap in a provider's real counter via
    `Prompt.recount(counter)` when exactness matters for billing.
    """
    if not text:
        return 0
    punct = sum(ch in '{}[]":,<>|\\/' for ch in text)
    density = 2.8 if punct / len(text) > 0.06 else 3.6
    return int(len(text) / density) + 1


@dataclasses.dataclass(slots=True)
class Segment:
    label: str
    content: str
    fidelity: Fidelity = Fidelity.STRUCTURED
    source: str | None = None   # e.g. "chapters/ch06.md", "state.json#characters"
    priority: int | None = None
    tokens: int = dataclasses.field(default=0)

    def __post_init__(self) -> None:
        if self.priority is None:
            self.priority = DEFAULT_PRIORITY[self.fidelity]
        if not self.tokens:
            self.tokens = estimate_tokens(self.content)

    def render(self) -> str:
        """Markdown heading, not pseudo-XML.

        The blueprint this replaces used `<world_bible>`-style tags parsed back
        out with regex, which breaks the first time a character says "<" in
        dialogue. Machine-readable state lives in JSON; prompts get headings,
        which models follow at least as well and nothing has to parse.
        """
        return f"## {self.label}\n{self.content.strip()}"

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "fidelity": str(self.fidelity),
            "source": self.source,
            "priority": self.priority,
            "tokens": self.tokens,
            "chars": len(self.content),
        }


@dataclasses.dataclass(slots=True)
class Prompt:
    """An assembled prompt plus the accounting the inspector needs."""

    stage: str
    system_segments: list[Segment] = dataclasses.field(default_factory=list)
    user_segments: list[Segment] = dataclasses.field(default_factory=list)
    dropped: list[Segment] = dataclasses.field(default_factory=list)

    # -- assembly ----------------------------------------------------------

    def system(self, label: str, content: str, **kw) -> "Prompt":
        self.system_segments.append(Segment(label, content, **kw))
        return self

    def user(self, label: str, content: str, **kw) -> "Prompt":
        self.user_segments.append(Segment(label, content, **kw))
        return self

    def user_json(self, label: str, payload, **kw) -> "Prompt":
        kw.setdefault("fidelity", Fidelity.STRUCTURED)
        body = json.dumps(payload, indent=1, ensure_ascii=False, default=str)
        self.user_segments.append(Segment(label, body, **kw))
        return self

    # -- accounting --------------------------------------------------------

    @property
    def segments(self) -> list[Segment]:
        return self.system_segments + self.user_segments

    @property
    def tokens(self) -> int:
        return sum(s.tokens for s in self.segments)

    def render_system(self) -> str:
        return "\n\n".join(s.render() for s in self.system_segments)

    def render_user(self) -> str:
        return "\n\n".join(s.render() for s in self.user_segments)

    def recount(self, counter) -> "Prompt":
        """Replace estimates with a real tokenizer's counts."""
        for s in self.segments:
            s.tokens = counter(s.content)
        return self

    def fit(self, budget_tokens: int) -> "Prompt":
        """Drop lowest-priority segments until under budget.

        Drops whole segments and records them, so the inspector can show you
        what was cut. Never truncates mid-segment: half a character ledger is
        worse than none, because the model then confidently fills the gap.
        """
        order = sorted(
            self.user_segments,
            key=lambda s: (s.priority, -s.tokens),
        )
        while self.tokens > budget_tokens and order:
            victim = order.pop(0)
            self.user_segments.remove(victim)
            self.dropped.append(victim)
        return self

    def report(self) -> dict:
        """Everything the Prompt Inspector screen needs, as one JSON blob."""
        total = self.tokens or 1
        return {
            "stage": self.stage,
            "total_tokens": self.tokens,
            "segments": [
                {**s.to_dict(), "share": round(s.tokens / total, 4)}
                for s in self.segments
            ],
            "dropped": [s.to_dict() for s in self.dropped],
            "by_fidelity": {
                str(f): sum(s.tokens for s in self.segments if s.fidelity is f)
                for f in Fidelity
            },
        }


# --- the tiered-fidelity helpers the stages actually call -----------------


def verbatim_tail(chapters: Sequence[str], target_words: int = 1500) -> tuple[str, int]:
    """Take the last N words of the most recent prose, whole paragraphs only.

    Cutting mid-paragraph teaches the model that paragraphs start abruptly, and
    it will imitate that. So we walk back paragraph by paragraph and overshoot
    rather than split.
    """
    paras: list[str] = []
    words = 0
    for text in reversed(chapters):
        for para in reversed(re.split(r"\n\s*\n", text.strip())):
            para = para.strip()
            if not para:
                continue
            paras.append(para)
            words += len(para.split())
            if words >= target_words:
                return "\n\n".join(reversed(paras)), words
    return "\n\n".join(reversed(paras)), words


def build_prose_prompt(
    *,
    stage: str = "prose",
    rules: str,
    style_card: dict,
    world_facts: dict,
    character_rows: list[dict],
    beats: list[dict],
    prior_chapters: Sequence[str],
    chapter_no: int,
    part: str = "",
    half_a_text: str = "",
    seam_intent: str = "",
    budget_tokens: int = 60_000,
    tail_words: int = 1500,
) -> Prompt:
    """The load-bearing prompt. Note what is absent: earlier chapters' prose.

    The blueprint this replaces passed the entire manuscript on every call.
    That is affordable -- a few dollars across a 40-chapter draft -- but it
    flattens fidelity. At 160k tokens of prior prose the model recalls chapter
    7's plot point about as well as chapter 39's cadence, when what you need is
    the opposite: last chapter's *voice* exactly, chapter 7's *facts* reliably.
    """
    p = Prompt(stage=stage)
    p.system("Operating rules", rules, fidelity=Fidelity.RULES, source="prompts/rules.md")
    p.system(
        "Measured style targets",
        json.dumps(style_card, indent=1),
        fidelity=Fidelity.RULES,
        source="style.json",
    )

    p.user_json("World facts", world_facts, source="state.json#world", priority=85)
    p.user_json(
        "Character state as of now",
        character_rows,
        source="state.json#characters",
        priority=90,
    )
    p.user_json(f"Beats to dramatise for chapter {chapter_no}", beats,
                source=f"beats/ch{chapter_no:02d}.json", priority=95)

    if seam_intent:
        p.user("What must be true at the handoff", seam_intent,
               fidelity=Fidelity.RULES, source=f"beats/ch{chapter_no:02d}.json#seam_intent",
               priority=97)

    if part == "b" and half_a_text:
        # Whole, never summarised, and above every ledger row. A half drafted
        # without its own first half in front of it re-establishes the setting
        # and repeats gestures -- the four defects `checks/seam.py` exists to
        # catch. Paying ~2,300 tokens here is how the join stops being a repair
        # job. If the budget cannot hold this, the budget is wrong.
        p.user(
            f"The first half of this very scene (verbatim) -- you are continuing it mid-scene",
            half_a_text,
            fidelity=Fidelity.VERBATIM,
            source=f"chapters/parts/ch{chapter_no:02d}a.md",
            priority=99,
        )
    elif prior_chapters:
        tail, got = verbatim_tail(prior_chapters, tail_words)
        p.user(
            f"Immediately preceding prose (verbatim, {got} words) -- continue from this voice",
            tail,
            fidelity=Fidelity.VERBATIM,
            source=f"chapters/ch{chapter_no - 1:02d}.md",
            # Above the ledger rows, not below them. Voice is the thing this
            # segment carries and nothing else can supply it; a character row
            # dropped from the prompt costs one fact, and the fact is recoverable
            # at the verify stage. Dropped voice costs the chapter.
            priority=92,
        )

    return p.fit(budget_tokens)
