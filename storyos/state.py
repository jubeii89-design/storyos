"""The ledger as a fold over deltas, never a file mutated in place.

`state.json` is derived. Every delta in `deltas/` is written once by one stage,
so the ledger can always be rebuilt from them. A `state.json` updated in place
can be left in a condition no replay explains: a crash between half A's apply
and half B's leaves it half-written while `completed_stages` correctly reports
the stage as finished, and nothing afterwards can tell the difference.

A delta is additive and flat:

    {"unit": "07a", "provisional": true,
     "characters": [{"name": "Yara", "location": "bay nine",
                     "knows": "the crate is warm"}],
     "world":      [{"name": "bay nine", "fact": "nobody books it"}],
     "places":     []}

Rows merge by `name`. Later units win field by field, so a character's
`location` can change in chapter 12 without losing the `first_seen` recorded in
chapter 1. A final non-provisional delta for a chapter replaces that chapter's
provisional halves, because `seam` and `revise` may have changed the prose the
halves were read from.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import units

SECTIONS = ("characters", "world", "places")


def _sort_key(path: Path) -> tuple[int, int, str]:
    """Numeric order, with a chapter's final delta after both its halves."""
    stem = path.stem.removeprefix("ch")
    try:
        chapter, part = units.parse(stem)
    except units.BadUnit:
        return (10**6, 0, path.name)
    return (chapter, {"a": 0, "b": 1, "": 2}[part], path.name)


def rebuild(env, *, write: bool = True) -> dict:
    """Fold every delta into a ledger. Idempotent by construction."""
    state: dict[str, dict[str, dict]] = {s: {} for s in SECTIONS}
    deltas = sorted((env.root / "deltas").glob("ch*.json"), key=_sort_key)

    for path in deltas:
        try:
            chapter, part = units.parse(path.stem.removeprefix("ch"))
        except units.BadUnit:
            # A backup or an editor's stray file in deltas/ used to raise out
            # of rebuild, which broke state_rebuild and chapter_commit for
            # every remaining chapter. It is not a delta; skip it.
            continue
        # Every delta is applied, halves before the chapter's final one. An
        # earlier version skipped a chapter's halves once its final delta
        # existed, which silently deleted any character the final delta did
        # not happen to mention -- a character introduced in half b vanished
        # from the ledger the moment the chapter was committed. Ordering
        # already does the job: the final delta is applied last, so its
        # fields win, and rows it never mentions survive untouched.
        try:
            delta = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError as x:
            raise ValueError(f"{path.name} is not valid JSON: {x}") from None

        unit = delta.get("unit") or path.stem.removeprefix("ch")
        provisional = bool(delta.get("provisional", bool(part)))

        for section in SECTIONS:
            for row in delta.get(section) or []:
                if not isinstance(row, dict) or not row.get("name"):
                    raise ValueError(
                        f"{path.name}: every {section} row needs a name; got {row!r}")
                key = row["name"].strip().lower()
                existing = state[section].get(key, {})
                merged = {**existing, **{k: v for k, v in row.items() if v is not None}}
                merged.setdefault("first_seen", unit)
                merged["last_seen"] = unit
                merged["provisional"] = provisional
                state[section][key] = merged

    out = {s: sorted(state[s].values(), key=lambda r: r["name"].lower())
           for s in SECTIONS}

    if write:
        env.state.write_text(json.dumps(out, indent=2) + "\n")
    return out


def write_delta(env, unit: str, rows: dict, *, provisional: bool | None = None) -> Path:
    """Write one unit's delta. One writer per file, enforced by the path."""
    chapter, part = units.parse(unit)
    if provisional is None:
        provisional = bool(part)

    payload = {"unit": units.fmt(chapter, part), "provisional": provisional}
    for section in SECTIONS:
        payload[section] = rows.get(section, [])

    path = env.delta(unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written through a temporary file and renamed. A delta is a model
    # extraction -- it cannot be regenerated without paying for the call
    # again -- and a truncate-then-write interrupted midway leaves JSON that
    # rebuild cannot parse, which breaks the fold for the rest of the book.
    # os.replace is atomic on the same filesystem.
    tmp = path.with_suffix(".json.writing")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def names(state: dict) -> set[str]:
    """Every name the book knows, for `review.verify_entities`."""
    out: set[str] = set()
    for section in SECTIONS:
        for row in state.get(section) or []:
            if n := row.get("name"):
                out.update(n.split())
            for alias in row.get("aliases") or []:
                out.update(alias.split())
    return out
