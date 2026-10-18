"""The pipeline as data: every stage, its scope, and whether a model is needed.

This is the machine-readable twin of `reference/stages.md`. The prose document
explains why each stage exists; this one is what `status` replays against and
what refuses to run a model stage from a Python entry point.

`model=False` is the load-bearing flag. Those stages are answerable by Python,
and a model asked what Python can answer will produce a plausible guess instead
of an answer. Half the pipeline is in that column, which is the point.
"""

from __future__ import annotations

import dataclasses

from . import units


@dataclasses.dataclass(frozen=True, slots=True)
class Stage:
    name: str
    scope: str          # "book" | "chapter" | "half"
    model: bool
    writes: str
    human: bool = False   # the writer does this one; no command runs it


BOOK: tuple[Stage, ...] = (
    Stage("premise",     "book", False, "premise.md -- a paragraph is enough", human=True),
    Stage("genre",       "book", True,  "genre.json"),
    Stage("brainstorm",  "book", True,  "brainstorm.md"),
    Stage("dossier",     "book", True,  "dossier.md"),
    Stage("bible",       "book", True,  "state.json seed"),
    # Pure counting: sentence-length distribution, dialogue share, paragraph
    # length, adverb rate. A measured card cannot drift and costs nothing;
    # a card written as adjectives ("lyrical but spare") constrains nothing.
    Stage("style_card",  "book", False, "style.json -- measured from your own prose"),
    Stage("outline",     "book", True,  "outline.json"),
)

# Per chapter, before either half is drafted.
BEATS: tuple[Stage, ...] = (
    Stage("beats",       "chapter", True,  "beats/chNN.json"),
    Stage("split_beats", "chapter", False, "beats/chNNa.json, chNNb.json + seam_intent"),
)

# Per half, in order.
HALF: tuple[Stage, ...] = (
    Stage("context_pack",  "half", False, "runs/<run>/prompts/chNNx.json"),
    Stage("draft",         "half", True,  "chapters/parts/chNNx.md"),
    Stage("length_gate",   "half", False, "checks/chNNx.words.json"),
    Stage("delta_extract", "half", True,  "deltas/chNNx.json (provisional)"),
    Stage("state_rebuild", "half", False, "state.json (derived fold)"),
)

# Per chapter, once both halves exist.
CHAPTER: tuple[Stage, ...] = (
    Stage("join",            "chapter", False, "chapters/joined/chNN.md + .seam.json"),
    Stage("seam_check",      "chapter", False, "findings in events.jsonl"),
    Stage("seam",            "chapter", True,  "chapters/chNN.v1.md"),
    Stage("seam_verify",     "chapter", False, "checks/chNN.seam.json"),
    Stage("review_a",        "chapter", True,  "review/chNN.a.json"),
    Stage("review_b",        "chapter", True,  "review/chNN.b.json"),
    Stage("reconcile",       "chapter", False, "review/chNN.reconciled.json"),
    Stage("revise",          "chapter", True,  "chapters/chNN.v2.md"),
    Stage("chapter_commit",  "chapter", False, "chapters/chNN.md + deltas/chNN.json"),
)

OUTPUT: tuple[Stage, ...] = (
    Stage("cover",  "book", True,  "cover/front.pdf, back.pdf"),
    Stage("export", "book", False, "manuscript.md + export/*.docx, *.pdf"),
)

ALL: dict[str, Stage] = {
    s.name: s for group in (BOOK, BEATS, HALF, CHAPTER, OUTPUT) for s in group
}


def get(name: str) -> Stage:
    try:
        return ALL[name]
    except KeyError:
        raise KeyError(f"no stage {name!r}. Known: {', '.join(sorted(ALL))}") from None


def plan(chapters: int) -> list[tuple[int, str, str]]:
    """Every `(chapter, part, stage)` the book needs, in execution order.

    Book stages use chapter 0. This is the order `status` walks to find the
    first incomplete step, so it encodes the half-chapter rule directly: both
    halves of a chapter are drafted and extracted before the chapter is joined,
    and the chapter is joined and seam-repaired before any reviewer sees it --
    a reviewer shown a seam spends all three of its findings on the seam.
    """
    out: list[tuple[int, str, str]] = [(0, "", s.name) for s in BOOK]

    for ch in range(1, chapters + 1):
        out += [(ch, "", s.name) for s in BEATS]
        for part in units.PARTS:
            out += [(ch, part, s.name) for s in HALF]
        out += [(ch, "", s.name) for s in CHAPTER]

    out += [(0, "", s.name) for s in OUTPUT]
    return out


def next_step(chapters: int, done: set[tuple[int, str, str]]) -> tuple[int, str, str] | None:
    """The first step in `plan` that has not completed, or None when finished.

    `done` comes from `events.completed_stages`, which derives it by replaying
    the log -- so this survives a crash, and re-running a stage that later
    failed is correct because the last outcome for a key wins.
    """
    for key in plan(chapters):
        if key not in done:
            return key
    return None


def label(key: tuple[int, str, str]) -> str:
    """How a step is written for a human: `07a draft`, or `book outline`."""
    chapter, part, stage = key
    if chapter == 0:
        return f"book {stage}"
    return f"{units.fmt(chapter, part)} {stage}"
