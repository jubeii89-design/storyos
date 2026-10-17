"""Join two halves, and record exactly what the seam stage is allowed to touch.

`join` itself does no repair -- it concatenates and then writes down the truth
about the result: where the seam is, which paragraphs sit inside the editable
window, and a hash of every paragraph. `seam_verify` later compares those
hashes to prove the repair stayed inside its window.

The constraint is checked rather than requested. A prompt that asks a model to
restrict its own edits is a suggestion, and the one thing a half-chapter
pipeline cannot survive is a repair pass that quietly rewrites prose two
reviewers already passed.
"""

from __future__ import annotations

import hashlib
import json
import re

from . import units

PARA = re.compile(r"\n\s*\n")

# Last 2 paragraphs of A, first 3 of B. Wide enough to rewrite a transition,
# narrow enough that a model cannot restructure a scene inside it.
WINDOW_BEFORE = 2
WINDOW_AFTER = 3
WORD_TOLERANCE = 150


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in PARA.split(text.strip()) if p.strip()]


def phash(para: str) -> str:
    """Hash of a paragraph's words, ignoring whitespace and case.

    Whitespace-insensitive because a repair legitimately reflows a line; word
    changes are what must be caught, and they survive this normalisation.
    """
    norm = " ".join(para.split()).lower()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def run(env, chapter: int | str) -> dict:
    """Write `chapters/joined/chNN.md` and its sidecar. No model involved."""
    ch = chapter if isinstance(chapter, int) else units.parse(chapter)[0]
    a_path, b_path = env.part(units.fmt(ch, "a")), env.part(units.fmt(ch, "b"))

    missing = [p.name for p in (a_path, b_path) if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            f"chapter {ch:02d} cannot be joined: missing {', '.join(missing)}. "
            f"Both halves must be drafted first.")

    a, b = a_path.read_text(), b_path.read_text()
    pa, pb = paragraphs(a), paragraphs(b)
    if not pa or not pb:
        raise ValueError(f"chapter {ch:02d}: a half is empty")

    joined = "\n\n".join(pa + pb) + "\n"
    paras = pa + pb

    # The seam is the offset where B's first paragraph begins.
    seam_para = len(pa)
    seam_offset = sum(len(p) + 2 for p in pa)

    lo = max(0, seam_para - WINDOW_BEFORE)
    hi = min(len(paras), seam_para + WINDOW_AFTER)

    sidecar = {
        "chapter": units.fmt(ch),
        "seam_paragraph": seam_para,
        "seam_offset": seam_offset,
        "window": {"first_paragraph": lo, "last_paragraph": hi - 1,
                   "paragraphs": hi - lo},
        "words": {"a": len(a.split()), "b": len(b.split()),
                  "total": len(joined.split()),
                  "tolerance": WORD_TOLERANCE},
        "paragraphs": [
            {"index": i, "hash": phash(p), "words": len(p.split()),
             "editable": lo <= i < hi, "half": "a" if i < seam_para else "b"}
            for i, p in enumerate(paras)
        ],
    }

    env.joined(ch).parent.mkdir(parents=True, exist_ok=True)
    env.joined(ch).write_text(joined)
    env.seam_sidecar(ch).write_text(json.dumps(sidecar, indent=2) + "\n")
    return sidecar


def verify(env, chapter: int | str) -> list[dict]:
    """Prove the seam stage stayed inside its window. Returns violations.

    Reads `chapters/chNN.v1.md` -- the seam stage's own output -- against the
    sidecar written by `join`. An empty list means the repair was legal.
    """
    ch = chapter if isinstance(chapter, int) else units.parse(chapter)[0]
    side = json.loads(env.seam_sidecar(ch).read_text())
    repaired = env.chapter(ch, 1)

    if not repaired.is_file():
        raise FileNotFoundError(
            f"{repaired.name} not found -- the seam stage has not run for "
            f"chapter {ch:02d}.")

    text = repaired.read_text()
    now = paragraphs(text)
    before = side["paragraphs"]
    editable = {p["index"] for p in before if p["editable"]}
    violations: list[dict] = []

    # Paragraph count may change only inside the window: a repair may merge two
    # paragraphs across the seam or split one. Outside it, the count is fixed.
    outside_before = [p for p in before if not p["editable"]]
    head = [p for p in outside_before if p["index"] < min(editable, default=0)]
    tail = [p for p in outside_before if p["index"] > max(editable, default=0)]

    for p in head:
        if p["index"] >= len(now) or phash(now[p["index"]]) != p["hash"]:
            violations.append({
                "kind": "edited_outside_window", "paragraph": p["index"],
                "half": p["half"],
                "detail": "a paragraph before the window was changed"})

    # Tail paragraphs are matched from the end, so a legal merge inside the
    # window does not shift them out of alignment and cause false positives.
    for n, p in enumerate(reversed(tail), start=1):
        if n > len(now):
            violations.append({"kind": "missing_paragraph", "paragraph": p["index"],
                               "half": p["half"], "detail": "paragraph removed"})
            continue
        if phash(now[-n]) != p["hash"]:
            violations.append({
                "kind": "edited_outside_window", "paragraph": p["index"],
                "half": p["half"],
                "detail": "a paragraph after the window was changed"})

    delta = len(text.split()) - side["words"]["total"]
    if abs(delta) > side["words"]["tolerance"]:
        violations.append({
            "kind": "word_count_drift", "paragraph": side["seam_paragraph"],
            "half": "", "detail": f"word count moved by {delta:+d}, "
                                  f"tolerance is ±{side['words']['tolerance']}"})

    env.seam_check(ch).parent.mkdir(parents=True, exist_ok=True)
    env.seam_check(ch).write_text(json.dumps(
        {"chapter": units.fmt(ch), "ok": not violations,
         "violations": violations}, indent=2) + "\n")
    return violations
