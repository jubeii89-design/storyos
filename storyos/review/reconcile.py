"""Two-model blind review, with honesty enforced rather than requested.

The instruction "be honest, don't be a yes-man" changes a model's tone and not
its judgment. It will write sycophantic, verbose, position-biased findings
while describing itself as brutally frank. So this module asks for none of
that. It constrains the shape of the answer instead:

- A fixed quota of negatives. Exactly three weakest paragraphs, every time.
  There is no way to be agreeable when the task requires naming a worst.
- No rating scale. Scores collapse to 7/10 and drift upward with length.
- Every claim carries a verbatim quote, which `verify_quotes` string-matches
  against the chapter. A finding whose quote does not literally appear is
  dropped and recorded as hallucinated evidence. This is the load-bearing
  check: it makes a confident invention cost the reviewer its finding.
- Exactly one strongest paragraph. A reviewer that cannot name a strength is
  pattern-matching rather than reading, and the field exists to catch that.
- Two different model families, reviewing blind, neither seeing the other's
  output nor any metadata about who wrote the prose.

Agreement is then computed here, in Python, not argued out by a third model.
A third judge would reintroduce every bias this design removes. Where the two
reviewers disagree, a human decides -- which is affordable because this is a
one-user tool.
"""

from __future__ import annotations

import dataclasses
import difflib
import enum
import random
import re
import unicodedata
from typing import Iterable, Sequence

MIN_QUOTE_WORDS = 12
WEAKEST_QUOTA = 3


class Verdict(enum.StrEnum):
    SHIP = "ship"
    REVISE = "revise"
    REWRITE = "rewrite"


class Status(enum.StrEnum):
    CONFIRMED = "confirmed"    # both reviewers flagged it -- queue for revision
    CONTESTED = "contested"    # one flagged it -- a human decides
    UNVERIFIED = "unverified"  # quote not found in the text -- discarded


_DROPPED = frozenset("\"'‘’“”«»")
_REMAPPED = {"–": "-", "—": "-", "…": "..."}


def _fold(text: str) -> tuple[str, list[int]]:
    """Normalise, and return the map from each folded offset to its origin.

    Models routinely restyle quotation marks, dashes and whitespace while
    quoting accurately. Penalising that would throw away real findings, so both
    sides are folded before comparing -- but the words themselves are untouched,
    because word-level invention is exactly what we are hunting.

    The index map exists so a match can be reported as a span in the original
    text. It is returned from the same pass that builds the folded string
    because the two must agree exactly: an earlier version folded here and in a
    separate loop, the two disagreed about what a deleted apostrophe leaves
    behind (nothing, or a space), and every quote containing a contraction --
    which is most quoted dialogue -- was rejected as fabricated evidence.
    """
    out: list[str] = []
    index: list[int] = []
    prev_space = True

    for i, raw in enumerate(text):
        if raw in _DROPPED:
            continue          # deleted outright; it leaves no space behind
        piece = _REMAPPED.get(raw) or unicodedata.normalize("NFKC", raw)
        if piece.isspace():
            if not prev_space:
                out.append(" ")
                index.append(i)
                prev_space = True
            continue
        for ch in piece.lower():
            out.append(ch)
            index.append(i)   # a one-to-many fold points every char at its source
        prev_space = False

    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def _normalise(text: str) -> str:
    return _fold(text)[0]


@dataclasses.dataclass(slots=True)
class Finding:
    reviewer: str
    kind: str                 # weakest_paragraph | promise_break | continuity_conflict
    quote: str
    why_it_fails: str
    reaches_for: str = ""     # the effect the passage was going after
    works_here: str = ""      # what a revision must not destroy, or "nothing"
    defect_code: str = ""
    para_index: int | None = None
    rewrite_hint: str = ""
    status: Status = Status.UNVERIFIED
    span: tuple[int, int] | None = None
    match_ratio: float = 0.0

    @property
    def quote_words(self) -> int:
        return len(self.quote.split())

    @property
    def why(self) -> str:
        """Display form: the defect, then the intent it was serving."""
        if not self.reaches_for:
            return self.why_it_fails
        return f"{self.why_it_fails} — reaching for: {self.reaches_for}"

    def key(self) -> str:
        """What counts as 'the same finding' when two reviewers raise it.

        Paragraph index alone is too coarse (two reviewers can object to
        different things in one paragraph) and exact quote equality is too
        strict (they rarely select identical spans). Overlapping spans are
        handled in `reconcile`; this key is the cheap first pass.
        """
        return f"{self.kind}:{self.para_index}"


@dataclasses.dataclass(slots=True)
class Review:
    """One reviewer's complete response. Fixed shape, fixed counts."""

    reviewer: str
    model: str
    weakest: list[Finding] = dataclasses.field(default_factory=list)
    promise_breaks: list[Finding] = dataclasses.field(default_factory=list)
    continuity: list[Finding] = dataclasses.field(default_factory=list)
    strongest_quote: str = ""
    verdict: Verdict = Verdict.REVISE
    single_biggest_problem: str = ""

    @property
    def findings(self) -> list[Finding]:
        return self.weakest + self.promise_breaks + self.continuity

    def schema_violations(self) -> list[str]:
        """Shape problems worth surfacing -- a reviewer dodging the quota is signal."""
        bad: list[str] = []
        if len(self.weakest) != WEAKEST_QUOTA:
            bad.append(f"returned {len(self.weakest)} weakest paragraphs, required {WEAKEST_QUOTA}")
        if not self.strongest_quote.strip():
            bad.append("named no strongest paragraph -- likely pattern-matching, not reading")
        if len(self.single_biggest_problem.split()) > 25:
            bad.append("single_biggest_problem exceeded 25 words")
        for f in self.findings:
            if f.quote_words < MIN_QUOTE_WORDS and f.quote.strip().lower() != "absent":
                bad.append(f"{f.kind} quote too short ({f.quote_words} words, need {MIN_QUOTE_WORDS})")
        for f in self.weakest:
            # A reviewer that can name the defect but not the intent behind it
            # usually did not read the passage, it recognised a shape. The
            # missing field is the tell, so it is a violation and not a blank.
            if not f.reaches_for.strip():
                bad.append(f"weakest finding at para {f.para_index} named no intent")
            if not f.works_here.strip():
                bad.append(f"weakest finding at para {f.para_index} left works_here blank "
                           "-- use the literal 'nothing' if there is none")
        return bad


PROPER_NOUN = re.compile(r"\b[A-Z][a-z]{2,}\b")
_SENT_START = re.compile(r"(?:^|(?<=[.!?;:])\s+|\n\s*)")

# Capitalised mid-sentence but not a name. A short list is enough because
# sentence-initial capitals -- which are most of the noise -- are excluded by
# position instead, and a word list can never keep up with those.
_NOT_ENTITIES = {
    "The", "This", "That", "These", "Those", "There", "When", "Where", "What",
    "Which", "While", "After", "Before", "Her", "His", "Their", "They", "She",
    "But", "And", "For", "Not", "Chapter", "Scene", "Reaching", "Absent",
}


def _sentence_starts(text: str) -> set[int]:
    return {m.end() for m in _SENT_START.finditer(text)} | {0}


def verify_entities(findings: Iterable[Finding], state: dict) -> list[Finding]:
    """Discard findings that complain about people or places the book lacks.

    A reviewer under quota pressure will occasionally invent a character to
    object to. The ledger already lists every name in the book, so this is
    answerable without a model: a name in the reviewer's own prose -- not in the
    quote, which is the author's -- that the ledger does not know is a
    fabrication, and the finding resting on it is worthless.

    Known gap: with no lexicon and no NER on this machine, a capitalised token
    is only convicted when it carries a possessive or appears mid-sentence. An
    invented name that occurs *only* at the start of a sentence is
    indistinguishable from an ordinary capitalised opener ("Atmosphere by
    assertion...") and passes. Quote verification is the guard that carries the
    weight; this one is a cheap second net, not a seal.
    """
    known = {n.lower() for n in _ledger_names(state)}
    invented: list[Finding] = []

    for f in findings:
        claim = ". ".join(p for p in (f.why_it_fails, f.reaches_for, f.works_here,
                                     f.rewrite_hint) if p)
        starts = _sentence_starts(claim)
        quote = f.quote.lower()

        for m in PROPER_NOUN.finditer(claim):
            word = m.group(0)
            if word in _NOT_ENTITIES or word.lower() in known:
                continue
            # A name the author used in the quoted passage is the author's, not
            # the reviewer's, even if the ledger has not caught up yet.
            if word.lower() in quote:
                continue
            possessive = claim[m.end():m.end() + 2] in ("'s", "’s")
            if not possessive and m.start() in starts:
                continue
            f.status = Status.UNVERIFIED
            invented.append(f)
            break

    return invented


def _ledger_names(state: dict) -> set[str]:
    names: set[str] = set()
    for section in ("characters", "world", "places"):
        for row in state.get(section, []) or []:
            if isinstance(row, dict):
                for field in ("name", "id", "label", "aliases"):
                    val = row.get(field)
                    if isinstance(val, str):
                        names.update(val.split())
                    elif isinstance(val, list):
                        for v in val:
                            if isinstance(v, str):
                                names.update(v.split())
    return names


def verify_quotes(review: Review, chapter_text: str, *, floor: float = 0.90) -> tuple[list[Finding], list[Finding]]:
    """Split findings into (verified, hallucinated) by locating each quote.

    Returns findings with `span` set to real character offsets, so the UI can
    highlight the passage instead of making you hunt for it. A quote that
    cannot be located above `floor` similarity is not a stylistic near-miss --
    it is evidence the reviewer did not read what it claims to have read, and
    the finding built on it is worthless.
    """
    hay, index = _fold(chapter_text)

    verified: list[Finding] = []
    hallucinated: list[Finding] = []

    for f in review.findings:
        needle = _normalise(f.quote)
        if needle == "absent":           # a promise_break legitimately quotes nothing
            f.status = Status.CONTESTED
            f.match_ratio = 1.0
            verified.append(f)
            continue

        pos = hay.find(needle)
        if pos >= 0:
            f.match_ratio = 1.0
        else:
            matcher = difflib.SequenceMatcher(None, hay, needle, autojunk=False)
            m = matcher.find_longest_match(0, len(hay), 0, len(needle))
            f.match_ratio = (m.size / len(needle)) if needle else 0.0
            pos = m.a if f.match_ratio >= floor else -1

        if pos < 0:
            f.status = Status.UNVERIFIED
            hallucinated.append(f)
            continue

        last = min(pos + len(needle), len(index)) - 1
        f.span = (index[pos], index[last] + 1)   # exclusive end, for slicing
        f.status = Status.CONTESTED       # promoted to CONFIRMED only by agreement
        verified.append(f)

    return verified, hallucinated


def _overlap(a: Finding, b: Finding) -> bool:
    if a.kind != b.kind:
        return False
    if a.span and b.span:
        return a.span[0] < b.span[1] and b.span[0] < a.span[1]
    return a.para_index is not None and a.para_index == b.para_index


@dataclasses.dataclass(slots=True)
class Reconciliation:
    confirmed: list[tuple[Finding, Finding]] = dataclasses.field(default_factory=list)
    contested: list[Finding] = dataclasses.field(default_factory=list)
    hallucinated: list[Finding] = dataclasses.field(default_factory=list)
    schema_violations: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    verdicts: dict[str, str] = dataclasses.field(default_factory=dict)

    @property
    def agreement_rate(self) -> float:
        """Share of verified findings both reviewers independently raised.

        Read it as a diagnostic, not a score. Near 1.0 means one reviewer is
        redundant and you are paying twice for one opinion. Near 0.0 usually
        means the chapter is genuinely confusing rather than that the reviewers
        failed -- two careful readers disagreeing about what is wrong is itself
        a finding about the prose.
        """
        pairs = len(self.confirmed)
        total = pairs + len(self.contested)
        return (pairs / total) if total else 0.0

    def summary(self) -> str:
        v = " / ".join(f"{k}: {x}" for k, x in self.verdicts.items())
        return (
            f"{len(self.confirmed)} confirmed, {len(self.contested)} contested, "
            f"{len(self.hallucinated)} discarded as unverifiable · "
            f"agreement {self.agreement_rate:.0%} · {v}"
        )


def reconcile(review_a: Review, review_b: Review, chapter_text: str) -> Reconciliation:
    """Compare two blind reviews. No model adjudicates; arithmetic does."""
    ver_a, hall_a = verify_quotes(review_a, chapter_text)
    ver_b, hall_b = verify_quotes(review_b, chapter_text)

    rec = Reconciliation(
        hallucinated=hall_a + hall_b,
        schema_violations={
            review_a.reviewer: review_a.schema_violations(),
            review_b.reviewer: review_b.schema_violations(),
        },
        verdicts={review_a.reviewer: str(review_a.verdict), review_b.reviewer: str(review_b.verdict)},
    )

    unpaired_b = list(ver_b)
    for fa in ver_a:
        match = next((fb for fb in unpaired_b if _overlap(fa, fb)), None)
        if match is None:
            rec.contested.append(fa)
            continue
        unpaired_b.remove(match)
        fa.status = match.status = Status.CONFIRMED
        rec.confirmed.append((fa, match))

    rec.contested.extend(unpaired_b)
    return rec


def shuffled_paragraphs(chapter_text: str, seed: int) -> tuple[str, list[int]]:
    """Present paragraphs out of order to one reviewer, to defeat position bias.

    LLM judges weight what they read first. Reviewer B sees the same prose in a
    different order, so any agreement between the two survived that reordering.
    Returns the shuffled text and the mapping back to true indices.
    """
    paras = [p for p in re.split(r"\n\s*\n", chapter_text.strip()) if p.strip()]
    order = list(range(len(paras)))
    random.Random(seed).shuffle(order)
    body = "\n\n".join(f"[P{i}] {paras[i]}" for i in order)
    return body, order
