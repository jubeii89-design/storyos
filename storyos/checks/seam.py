"""Detect the four defects two independently drafted half-chapters produce at
their join: phrasing repeated across the boundary, a second opening that
re-establishes what the first already established, the same distinctive gesture
in both halves, and a first half that lands an ending it was not supposed to
reach. All four are answerable without a model, so no model is asked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from sys import argv, exit

NGRAM = 4
MIN_CONTENT_WORDS = 2      # per n-gram, before it counts as repeated phrasing
SETTING_FLOOR = 0.55
MIN_SETTING_WORDS = 6      # below this, two sentences are similar by accident
NEAR_IDENTICAL = 0.93      # so a short line must be all but repeated verbatim
MIN_SHARED_REFS = 3        # too few and any two passages share something

# PROVISIONAL, and labelled so rather than left to look settled. Measured on
# five hand-written cases: the two real restatements scored 0.33 and 1.00, the
# three non-defects all scored 0.00. The gap is wide and the threshold sits in
# it -- but the cases are mine, which is the circularity this detector was
# rewritten to escape. It stays a warning, never an error, until
# `storyos seam calibrate` has re-derived it from the writer's own prose:
# sample passage pairs drawn far apart in comps/, which are non-defects by
# construction, and take a high quantile of their reuse ratio.
REUSE_RATIO = 0.20


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str            # "error" blocks the join; "warn" is a craft note
    message: str
    evidence: str
    a_span: tuple[int, int] | None
    b_span: tuple[int, int] | None


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "can", "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "he", "her", "hers", "him", "his", "i", "if", "in", "into", "is", "it",
    "its", "may", "me", "might", "must", "my", "not", "of", "on", "one", "or",
    "our", "out", "she", "should", "so", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "those", "to", "up",
    "us", "was", "we", "were", "what", "when", "which", "who", "will", "with",
    "would", "you", "your", "all", "any", "both", "each", "every", "very",
    "over", "just", "only", "still", "like", "back", "down", "away", "again",
}

# Deliberately excludes turned, looked, reached, stepped, hands, breath,
# smiled and glanced. Those are among the most common words in English prose;
# flagging them would fire on every chapter and teach the writer to ignore
# this check. What is left is distinctive enough that seeing it twice within
# 600 words is a genuine tell.
GESTURES = {
    "nodded", "shrugged", "exhaled", "inhaled", "swallowed", "frowned",
    "sighed", "blinked", "winced", "flinched", "grimaced", "shivered",
    "trembled", "clenched", "unclenched", "tightened", "loosened", "gripped",
    "jaw", "shoulders", "knuckles", "throat", "spine", "temples", "eyebrow",
}

# Each must be unambiguous closure. Bare "finally", "was over" and "had
# passed" were removed: they match ordinary mid-scene sentences ("an hour had
# passed", "the argument was over the money") and drowned the real hits.
CLOSURES = [
    "for the first time", "for the last time", "at last", "it was over",
    "it was done", "it was finished", "nothing would be the same",
    "would never be the same", "there was no going back", "the die was cast",
    "and that was that", "and with that", "had come to an end", "never again",
    "finally understood", "finally knew", "finally saw", "nothing left to say",
]
CLOSURE_RE = [(m, re.compile(r"\b" + re.escape(m) + r"\b", re.I)) for m in CLOSURES]

WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")
# Sentence break after terminal punctuation, allowing a closing quote or
# bracket to sit between the punctuation and the space.
SENT_RE = re.compile(r'(?<=[.!?])["\'’”)\]]*\s+')


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0).lower(), m.start(), m.end()) for m in WORD_RE.finditer(text)]


def _is_content(word: str) -> bool:
    return len(word) >= 4 and word not in STOPWORDS


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_RE.split(text.strip()) if s.strip()]


def _references(text: str) -> set[str]:
    """The concrete things a passage names.

    Content words long enough to be specific, minus the gesture and body
    vocabulary, which recurs in all prose and says nothing about setting.
    This is a bag of tokens, not meaning: a half that renames the gulls as
    birds defeats it, and that case is left to the `seam` stage, which reads
    both halves and can see a synonym.
    """
    out = set()
    for w, _, _ in _tokens(text):
        s = _strip(w)
        if len(s) >= 5 and s not in STOPWORDS and s not in GESTURES:
            out.add(s)
    return out


def _strip(word: str) -> str:
    return word.lower().strip(".,!?;:—-\"'‘’“”")


def check_seam(half_a: str, half_b: str, *, window_words: int = 300) -> list[Finding]:
    findings: list[Finding] = []

    tok_a = _tokens(half_a)
    tok_b = _tokens(half_b)
    a_win = tok_a[max(0, len(tok_a) - window_words):]
    b_win = tok_b[:window_words]

    # 1. Repeated phrasing across the boundary.
    b_index: dict[tuple[str, ...], tuple[int, int]] = {}
    for i in range(len(b_win) - NGRAM + 1):
        run = b_win[i:i + NGRAM]
        b_index.setdefault(tuple(w for w, _, _ in run), (run[0][1], run[-1][2]))

    hits: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for i in range(len(a_win) - NGRAM + 1):
        run = a_win[i:i + NGRAM]
        key = tuple(w for w, _, _ in run)
        if sum(_is_content(w) for w in key) < MIN_CONTENT_WORDS:
            continue
        hit = b_index.get(key)
        if hit is not None:
            hits.append(((run[0][1], run[-1][2]), hit))

    # A duplicated passage produces one hit per sliding window, so reporting
    # them raw turns a single defect into thirty findings and buries the other
    # three checks. Merge hits that overlap on both sides into the one run of
    # repeated text they actually describe.
    runs: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for a_sp, b_sp in sorted(hits):
        if runs and a_sp[0] <= runs[-1][0][1] and b_sp[0] <= runs[-1][1][1]:
            pa, pb = runs[-1]
            runs[-1] = ((pa[0], max(pa[1], a_sp[1])), (pb[0], max(pb[1], b_sp[1])))
        else:
            runs.append((a_sp, b_sp))

    for a_sp, b_sp in runs:
        text = half_a[a_sp[0]:a_sp[1]]
        words = len(text.split())
        # One short shared phrase across 600 words is coincidence. A long run,
        # or more than one run, is the second half reusing the first half's
        # sentences, and the join will read as a stutter.
        severe = words >= 8 or len(runs) >= 2
        findings.append(Finding(
            "repeat_ngram", "error" if severe else "warn",
            f"{words} words of phrasing repeated across the join",
            text if words <= 14 else " ".join(text.split()[:12]) + " …",
            a_sp, b_sp,
        ))

    # 2. The second half opening the way the first half opened.
    #
    # Short sentences are compared on a stricter floor. SequenceMatcher gives
    # spuriously high ratios on short strings -- '"No," he said.' scored 65%
    # against '"I logged a mass."', which is two unrelated lines of dialogue
    # sharing quotes, a comma and a full stop. At that rate the check fires on
    # clean prose and gets switched off, so a short line must be nearly
    # identical before it counts as a restatement.
    sent_a, sent_b = _sentences(half_a)[:5], _sentences(half_b)[:3]
    for bs in sent_b:
        best = max(
            ((SequenceMatcher(None, a.lower(), bs.lower()).ratio(), a) for a in sent_a),
            default=(0.0, ""),
        )
        short = min(len(bs.split()), len(best[1].split())) < MIN_SETTING_WORDS
        floor = NEAR_IDENTICAL if short else SETTING_FLOOR
        if best[0] >= floor:
            # rfind would be as arbitrary as find; locate the match nearest
            # the join, which is the occurrence a reader would be comparing.
            at = half_a.rfind(best[1])
            bt = half_b.find(bs)
            findings.append(Finding(
                "restated_setting", "warn",
                f"opening re-establishes the first half's opening ({best[0]:.0%})",
                bs[:80], (at, at + len(best[1])) if at >= 0 else None,
                (bt, bt + len(bs)) if bt >= 0 else None,
            ))

    # 2b. The second half re-introducing places and things the first half
    # already established.
    #
    # The ratio check above compares character sequences, so it only sees
    # near-verbatim restatement -- which repeat_ngram already catches. Measured
    # on three cases it contributed no unique detection at all. The defect it
    # was meant to find is informational: half B tells the reader again, in its
    # own words, what half A already said.
    #
    # Shared concrete references survive paraphrase where phrasing does not: a
    # writer renaming the gulls as birds will still call the dock a dock. So
    # this counts the specific things B's opening names that A's opening
    # already named, and fires only when B introduces almost nothing of its own
    # -- a half that re-treads old ground rather than one that mentions it in
    # passing.
    a_refs = _references(" ".join(_sentences(half_a)[:5]))
    b_refs = _references(" ".join(_sentences(half_b)[:3]))
    if a_refs and b_refs:
        shared = a_refs & b_refs
        reuse = len(shared) / len(a_refs)
        if len(shared) >= MIN_SHARED_REFS and reuse >= REUSE_RATIO:
            at = half_a.rfind(_sentences(half_a)[0])
            findings.append(Finding(
                "restated_references", "warn",
                f"the second half re-names {len(shared)} of the {len(a_refs)} "
                f"things the first half established ({reuse:.0%} of them)",
                ", ".join(sorted(shared)[:6]),
                (at, at + len(_sentences(half_a)[0])) if at >= 0 else None,
                (0, len(_sentences(half_b)[0]))))

    # 3. The same distinctive gesture on both sides of the join.
    b_gest = {w: (s, e) for w, s, e in b_win if _strip(w) in GESTURES}
    done: set[str] = set()
    for w, s, e in a_win:
        g = _strip(w)
        if g in GESTURES and g not in done and g in b_gest:
            done.add(g)
            findings.append(Finding(
                "repeat_gesture", "warn", "same gesture on both sides of the join",
                g, (s, e), b_gest[g],
            ))

    # 4. A first half that resolved instead of handing over.
    tail = _sentences(half_a)[-2:]
    for sent in tail:
        for marker, rx in CLOSURE_RE:
            if rx.search(sent):
                at = half_a.find(sent)
                findings.append(Finding(
                    "false_ending", "warn",
                    "first half closes as a resolution, not a mid-scene break",
                    marker, (at, at + len(sent)) if at >= 0 else None, None,
                ))
                break

    return findings


if __name__ == "__main__":
    if len(argv) != 3:
        print("usage: seam.py <half_a.md> <half_b.md>")
        exit(2)

    found = check_seam(Path(argv[1]).read_text(), Path(argv[2]).read_text())
    for f in found:
        print(f"{f.severity.upper():<5}  {f.code:<17}  {f.message}  — {f.evidence!r}")
    if not found:
        print("clean")
    exit(1 if any(f.severity == "error" for f in found) else 0)
