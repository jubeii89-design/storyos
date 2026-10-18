"""Parse a critic's response, however it arrived.

A pasted reply and an API reply are the same thing once they are text, and
every guard in `reconcile` runs on the parsed object regardless of transport.
That is what makes `kind: "manual"` -- a human copying from a browser -- as
trustworthy as a keyed call: the honesty is enforced here and in
`verify_quotes`, not by whoever typed it.

So this module is deliberately forgiving about *form* and unforgiving about
*substance*. A model that wraps its JSON in prose, fences it in a code block,
or uses `weakest_paragraphs` instead of `weakest` is accommodated, because
fighting that produces no better reviews. A model that omits a quote, invents
one, or dodges the three-paragraph quota is reported, because those are the
failures the whole design exists to catch.
"""

from __future__ import annotations

import json
import re

from .reconcile import WEAKEST_QUOTA, Finding, Review, Verdict

FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)

# Field names seen in the wild from models asked for this shape.
ALIASES = {
    "weakest": ("weakest", "weakest_paragraphs", "weaknesses", "problems"),
    "promise_breaks": ("promise_breaks", "promises", "broken_promises"),
    "continuity": ("continuity", "continuity_conflicts", "contradictions"),
    "strongest_quote": ("strongest_quote", "strongest", "best_paragraph", "strength"),
    "verdict": ("verdict", "overall", "recommendation"),
    "single_biggest_problem": ("single_biggest_problem", "biggest_problem",
                               "one_line", "summary"),
    "quote": ("quote", "text", "passage", "excerpt"),
    "why_it_fails": ("why_it_fails", "why", "problem", "issue", "reason"),
    "reaches_for": ("reaches_for", "intent", "reaching_for", "what_it_reaches_for",
                    "goal"),
    "works_here": ("works_here", "what_works", "what_works_here", "keep", "strength"),
    "para_index": ("para_index", "paragraph", "paragraph_index", "index", "p"),
    "rewrite_hint": ("rewrite_hint", "hint", "suggestion", "fix"),
    "defect_code": ("defect_code", "code", "category", "kind"),
}


class UnparseableReview(ValueError):
    """The response was not JSON at all. Distinct from a badly-shaped review:
    one is a transport problem, the other is a finding about the critic."""


def _pick(d: dict, field: str, default=None):
    for name in ALIASES[field]:
        if name in d and d[name] not in (None, ""):
            return d[name]
    return default


def extract_json(raw: str) -> dict:
    """Find the JSON object in a response that may be wrapped in anything."""
    raw = raw.strip()
    for candidate in _candidates(raw):
        try:
            got = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(got, dict):
            return got
        if isinstance(got, list):
            return {"weakest": got}
    raise UnparseableReview(
        "no JSON object found in the response. Paste the critic's reply "
        "including its braces; surrounding prose is fine."
    )


def _candidates(raw: str) -> list[str]:
    out = []
    if m := FENCE.search(raw):
        out.append(m.group(1))
    out.append(raw)
    # The outermost brace pair, for a reply that opens with commentary.
    i, j = raw.find("{"), raw.rfind("}")
    if 0 <= i < j:
        out.append(raw[i:j + 1])
    i, j = raw.find("["), raw.rfind("]")
    if 0 <= i < j:
        out.append(raw[i:j + 1])
    return out


def _finding(reviewer: str, kind: str, row) -> Finding:
    if isinstance(row, str):
        # A bare string is a quote with no reasoning. Kept, so the quote check
        # can still verify it, but it will fail the schema check below -- which
        # is the correct outcome rather than silently dropping it.
        return Finding(reviewer=reviewer, kind=kind, quote=row, why_it_fails="")

    idx = _pick(row, "para_index")
    try:
        idx = int(idx) if idx is not None else None
    except (TypeError, ValueError):
        idx = None

    return Finding(
        reviewer=reviewer,
        kind=kind,
        quote=str(_pick(row, "quote", "") or ""),
        why_it_fails=str(_pick(row, "why_it_fails", "") or ""),
        reaches_for=str(_pick(row, "reaches_for", "") or ""),
        works_here=str(_pick(row, "works_here", "") or ""),
        defect_code=str(_pick(row, "defect_code", "") or ""),
        para_index=idx,
        rewrite_hint=str(_pick(row, "rewrite_hint", "") or ""),
    )


def parse(raw: str, *, reviewer: str, model: str) -> Review:
    """Text in, `Review` out. Raises only when there is no JSON at all."""
    doc = extract_json(raw)

    # Anchored, not a substring scan in enum order. "do not ship this" and
    # "should not ship, rewrite it" both matched `ship` because it is checked
    # first, so a critic demanding a rewrite was recorded as approving the
    # chapter -- revise was skipped and the escalation counter never moved.
    verdict_raw = str(_pick(doc, "verdict", "revise") or "revise").strip().lower()
    words = set(re.findall(r"[a-z]+", verdict_raw))
    negated = bool({"not", "never", "cannot", "dont", "shouldnt"} & words)
    if "rewrite" in words or (negated and "ship" in words):
        verdict = Verdict.REWRITE
    elif "ship" in words and not negated:
        verdict = Verdict.SHIP
    else:
        verdict = Verdict.REVISE

    review = Review(
        reviewer=reviewer,
        model=model,
        weakest=[_finding(reviewer, "weakest_paragraph", r)
                 for r in (_pick(doc, "weakest", []) or [])],
        promise_breaks=[_finding(reviewer, "promise_break", r)
                        for r in (_pick(doc, "promise_breaks", []) or [])],
        continuity=[_finding(reviewer, "continuity_conflict", r)
                    for r in (_pick(doc, "continuity", []) or [])],
        strongest_quote=str(_pick(doc, "strongest_quote", "") or ""),
        verdict=verdict,
        single_biggest_problem=str(_pick(doc, "single_biggest_problem", "") or ""),
    )
    return review


def prompt_for(chapter_text: str, *, shuffled: bool = False) -> str:
    """The review request. Asks for a shape, never for an attitude.

    There is no instruction here to be honest, harsh, or unsparing. Those
    change a model's tone and not its judgement -- it will write sycophantic
    findings while describing itself as brutally frank. What constrains it is
    the fixed quota, the absence of any rating scale, and the requirement that
    every claim carry a quote that will be string-matched against the chapter.
    """
    order = ("The paragraphs are presented out of order and prefixed [Pn]. "
             "Use the [Pn] number as para_index.\n" if shuffled else "")
    return f"""Read the chapter below and reply with JSON only.

{order}Required shape:

{{
  "weakest": [
    {{"quote": "at least 12 words copied exactly from the chapter",
      "para_index": 0,
      "why_it_fails": "one sentence naming the defect",
      "reaches_for": "the effect the passage was going after",
      "works_here": "what a revision must not destroy, or the word nothing",
      "rewrite_hint": "optional"}}
  ],
  "promise_breaks": [],
  "continuity": [],
  "strongest_quote": "the single strongest passage, copied exactly",
  "verdict": "ship | revise | rewrite",
  "single_biggest_problem": "at most 25 words"
}}

Rules that are checked, not trusted:

- `weakest` must hold exactly {WEAKEST_QUOTA} entries. Not two, not four.
- Every quote must appear in the chapter word for word. Quotes are matched
  against the text; a quote that is not there discards the finding.
- `strongest_quote` is required. Naming no strength at all is itself reported.
- Do not score the chapter. There is no scale.
- Reply with the JSON object and nothing else.

CHAPTER
-------
{chapter_text}
"""
