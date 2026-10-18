"""Measure the writer's prose. Do not describe it.

A style card written as adjectives -- "lyrical but spare", "muscular", "wry" --
constrains nothing, because every model already believes it writes that way.
A card written as numbers can be matched and, more importantly, can be checked
afterwards: `checks/style_drift.py` reads the same fields back off a draft.

What is measured here was chosen because it varies between authors and survives
translation into an instruction. Sentence-length *distribution* rather than its
mean, because two authors with identical means read nothing alike if one varies
and the other does not -- and uniform sentence length is the single most
reliable tell of generated prose. Conjunction openings, em-dash rate and
semicolon rate because they are habits a writer barely notices and a model will
not reproduce unless told. Dialogue share because it sets the page's texture
before a single word is read.

Everything is stdlib. The drafting loop cannot be broken by an upstream release.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

# Sentence break after terminal punctuation, allowing a closing quote or
# bracket between the punctuation and the space. Deliberately simple: an
# abbreviation like "Mr." will split wrongly now and then, which moves a
# distribution by a fraction of a percent and is not worth a dependency.
SENT = re.compile(r'(?<=[.!?])["\'’”)\]]*\s+')
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
PARA = re.compile(r"\n\s*\n")

# Both straight and curly, because a writer's files contain whichever their
# editor produced that year.
DIALOGUE = re.compile(r'["“”]')
QUOTED = re.compile(r'["“]([^"“”]{2,})["”]')

OPENERS = ("and", "but", "so", "yet", "or", "because", "though", "still", "then")

# -ly adverbs, minus the words that end in -ly without being one. Catching
# these matters: "only" and "really" are frequent enough to move the rate by a
# third on some prose.
NOT_ADVERB = {
    "only", "early", "family", "likely", "lonely", "lovely", "ugly", "holy",
    "silly", "really", "ally", "rely", "apply", "reply", "supply", "july",
    "italy", "daily", "oily", "jelly", "belly", "fully", "duly", "truly",
}
ADVERB = re.compile(r"\b([a-z]{4,}ly)\b", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.split(text.strip()) if s.strip()]


def _words(text: str) -> list[str]:
    return WORD.findall(text)


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def measure(text: str) -> dict:
    """Every number the card holds, from one body of prose."""
    sents = _sentences(text)
    words = _words(text)
    paras = [p for p in PARA.split(text.strip()) if p.strip()]

    if not sents or not words:
        raise ValueError("nothing to measure: no sentences found")

    lens = [len(_words(s)) for s in sents]
    lens = [n for n in lens if n]
    para_sents = [len(_sentences(p)) for p in paras]
    para_words = [len(_words(p)) for p in paras]

    quoted_words = sum(len(_words(m.group(1))) for m in QUOTED.finditer(text))
    adverbs = [m.group(1) for m in ADVERB.finditer(text)
               if m.group(1).lower() not in NOT_ADVERB]
    openers = sum(1 for s in sents
                  if (w := _words(s)) and w[0].lower() in OPENERS)

    # Type-token ratio over a fixed window, because TTR falls as any text
    # grows and comparing it across samples of different length is meaningless.
    window = [w.lower() for w in words[:2000]]
    ttr = _pct(len(set(window)), len(window))

    per_k = lambda n: round(1000.0 * n / len(words), 2)

    return {
        "words": len(words),
        "sentences": len(sents),
        "paragraphs": len(paras),

        "sentence_len": {
            "mean": round(statistics.fmean(lens), 2),
            "median": statistics.median(lens),
            "stdev": round(statistics.stdev(lens), 2) if len(lens) > 1 else 0.0,
            "p10": _quantile(lens, 0.10),
            "p90": _quantile(lens, 0.90),
            "max": max(lens),
            # The shape, which is the part that actually carries voice.
            "pct_under_6": _pct(sum(1 for n in lens if n < 6), len(lens)),
            "pct_6_to_20": _pct(sum(1 for n in lens if 6 <= n <= 20), len(lens)),
            "pct_over_30": _pct(sum(1 for n in lens if n > 30), len(lens)),
        },

        "paragraph": {
            "mean_sentences": round(statistics.fmean(para_sents), 2) if para_sents else 0.0,
            "mean_words": round(statistics.fmean(para_words), 2) if para_words else 0.0,
            # Only meaningful beside mean_words. Tested against two opposite
            # samples, terse prose scored 57% here and periodic prose 71% --
            # because the periodic paragraphs ARE single 90-word sentences.
            # Alone this field says nothing; paired with mean_words it
            # separates "short punchy paragraph" from "one long sentence".
            "pct_single_sentence": _pct(sum(1 for n in para_sents if n == 1),
                                        len(para_sents)),
        },

        "dialogue": {
            "pct_of_words": _pct(quoted_words, len(words)),
            "pct_paragraphs_with_speech": _pct(
                sum(1 for p in paras if DIALOGUE.search(p)), len(paras)),
        },

        "habits": {
            "adverbs_per_1k": per_k(len(adverbs)),
            "commas_per_sentence": round(text.count(",") / len(sents), 2),
            "semicolons_per_1k": per_k(text.count(";")),
            "emdash_per_1k": per_k(text.count("—") + text.count("--")),
            "colon_per_1k": per_k(text.count(":")),
            "pct_sentences_opening_conjunction": _pct(openers, len(sents)),
            "type_token_ratio_2k": ttr,
        },
    }


def _quantile(values: list[int], q: float) -> float:
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    i = q * (len(s) - 1)
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (i - lo), 1)


MIN_WORDS = 2000


def from_comps(comps_dir: Path) -> dict:
    """Measure every sample in `comps/` as one body of prose.

    One body rather than an average of per-file numbers, because a 300-word
    fragment would otherwise weigh as much as a 20,000-word chapter.
    """
    files = sorted(p for p in comps_dir.glob("**/*")
                   if p.suffix.lower() in (".txt", ".md") and p.is_file())
    if not files:
        raise FileNotFoundError(
            f"no .txt or .md samples in {comps_dir}. "
            f"Put at least {MIN_WORDS:,} words of your own prose there -- "
            f"without it the style card is a guess and the draft reads like anyone's."
        )

    text = "\n\n".join(p.read_text(errors="replace") for p in files)
    card = measure(text)
    card["source"] = {
        "files": [p.name for p in files],
        "measured": True,
        "sufficient": card["words"] >= MIN_WORDS,
    }
    if card["words"] < MIN_WORDS:
        card["source"]["warning"] = (
            f"only {card['words']:,} words measured; {MIN_WORDS:,}+ gives stable "
            f"numbers. The distribution fields are the least reliable below that."
        )
    return card


def unmeasured_card(note: str) -> dict:
    """A card with no targets, because nothing has been measured.

    This used to invent sixteen numbers and call them "mid-range values for
    contemporary commercial fiction". There is no such corpus behind them --
    they were made up, and then handed to the model under the heading
    "Measured style targets", which is a lie told to the one party that cannot
    check it.

    An absent target is honest and a fabricated one is not, and the difference
    shows up in the prose: invented numbers push every draft toward a style
    nobody chose, while no numbers leave the model's own judgement in place
    and leave the writer a visible gap to fill.

    `build_prose_prompt` omits the style segment entirely when `measured` is
    false, so nothing downstream can mistake this for data.
    """
    return {
        "words": 0, "sentences": 0, "paragraphs": 0,
        "source": {"files": [], "measured": False, "sufficient": False,
                   "warning": note},
    }


def compare(a: dict, b: dict) -> list[tuple[str, float, float]]:
    """Fields where two cards differ enough to be audible, widest gap first.

    Used to prove the extractor works: two different authors must separate.
    A card that cannot tell two writers apart is measuring the wrong things.
    """
    rows: list[tuple[str, float, float]] = []
    for group in ("sentence_len", "paragraph", "dialogue", "habits"):
        for k in a.get(group, {}):
            av, bv = a[group].get(k), b.get(group, {}).get(k)
            if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
                scale = max(abs(av), abs(bv), 1e-9)
                rows.append((f"{group}.{k}", av, bv, abs(av - bv) / scale))
    rows.sort(key=lambda r: -r[3])
    return [(n, x, y) for n, x, y, _ in rows]


def run(env, *, force: bool = False) -> dict:
    """The `style_card` stage: measure `comps/`, write `style.json`."""
    if env.style.exists() and not force:
        return json.loads(env.style.read_text())

    try:
        card = from_comps(env.comps_dir)
    except FileNotFoundError as x:
        card = unmeasured_card(str(x))

    env.style.write_text(json.dumps(card, indent=2) + "\n")
    return card
