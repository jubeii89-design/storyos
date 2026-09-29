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


def _normalise(text: str) -> str:
    """Fold the differences that make an honest quote fail a literal match.

    Models routinely restyle quotation marks, dashes and whitespace while
    quoting accurately. Penalising that would throw away real findings, so
    normalise both sides before comparing -- but keep the words themselves
    untouched, because word-level invention is exactly what we are hunting.
    """
    text = unicodedata.normalize("NFKC", text)
    for a, b in (("–", "-"), ("—", "-"), ("…", "...")):
        text = text.replace(a, b)
    # Quotation marks are the single most commonly restyled character in a
    # quote -- a reviewer citing a line of dialogue routinely drops the
    # surrounding marks. Keeping them cost a valid finding in testing, so they
    # are folded away entirely on both sides. Word-level invention, which is
    # what this check exists to catch, is untouched by this.
    text = re.sub(r"[\"'‘’“”«»]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclasses.dataclass(slots=True)
class Finding:
    reviewer: str
    kind: str                 # weakest_paragraph | promise_break | continuity_conflict
    quote: str
    why: str
    defect_code: str = ""
    para_index: int | None = None
    rewrite_hint: str = ""
    status: Status = Status.UNVERIFIED
    span: tuple[int, int] | None = None
    match_ratio: float = 0.0

    @property
    def quote_words(self) -> int:
        return len(self.quote.split())

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
        return bad


def verify_quotes(review: Review, chapter_text: str, *, floor: float = 0.90) -> tuple[list[Finding], list[Finding]]:
    """Split findings into (verified, hallucinated) by locating each quote.

    Returns findings with `span` set to real character offsets, so the UI can
    highlight the passage instead of making you hunt for it. A quote that
    cannot be located above `floor` similarity is not a stylistic near-miss --
    it is evidence the reviewer did not read what it claims to have read, and
    the finding built on it is worthless.
    """
    hay = _normalise(chapter_text)
    # Map normalised offsets back to the original string.
    index: list[int] = []
    buf: list[str] = []
    prev_space = True
    for i, ch in enumerate(unicodedata.normalize("NFKC", chapter_text)):
        folded = _normalise(ch)
        if not folded:
            if not prev_space and buf:
                buf.append(" ")
                index.append(i)
                prev_space = True
            continue
        buf.append(folded)
        index.append(i)
        prev_space = False
    hay = "".join(buf).strip()

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

        end = min(pos + len(needle), len(index) - 1)
        f.span = (index[pos], index[end])
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
