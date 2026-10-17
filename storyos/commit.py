"""Commit a chapter, and rebuild the manuscript from what is on disk.

`chapter_commit` is the only stage that writes `chapters/chNN.md`, the name you
open to read. It promotes the highest-numbered version -- v2 from `revise` if
it exists, else v1 from `seam` -- and writes the chapter's final
non-provisional delta.

`manuscript.md` is concatenated from the committed chapters every time rather
than appended to. Appending would duplicate chapter 12 the first time a crashed
commit is re-run, and would interleave chapters if the queue were drained out
of order.
"""

from __future__ import annotations

import json

from . import units
from .state import rebuild, write_delta


def run(env, chapter: int | str, *, deltas: dict | None = None) -> dict:
    ch = chapter if isinstance(chapter, int) else units.parse(chapter)[0]
    versions = env.chapter_versions(ch)
    if not versions:
        raise FileNotFoundError(
            f"chapter {ch:02d} has no versions to commit. "
            f"`seam` writes v1 and `revise` writes v2.")

    src = env.chapter(ch, max(versions))
    env.chapter(ch).write_text(src.read_text())

    # The chapter's final delta supersedes its two provisional halves, because
    # seam and revise may have changed the prose those halves were read from.
    write_delta(env, units.fmt(ch), deltas or {}, provisional=False)
    state = rebuild(env)

    manuscript = rebuild_manuscript(env)
    return {"chapter": units.fmt(ch), "from": src.name,
            "words": len(src.read_text().split()),
            "manuscript_words": manuscript["words"],
            "chapters_in_manuscript": manuscript["chapters"],
            "ledger_rows": sum(len(state[s]) for s in state)}


def rebuild_manuscript(env) -> dict:
    """Concatenate committed chapters in numeric order. Never appends."""
    found = []
    for p in (env.root / "chapters").glob("ch*.md"):
        if ".v" in p.name:
            continue
        try:
            found.append((units.parse(p.stem.removeprefix("ch"))[0], p))
        except units.BadUnit:
            continue

    found.sort()
    parts = []
    for n, p in found:
        parts.append(f"# Chapter {n}\n\n{p.read_text().strip()}\n")

    body = "\n\n".join(parts)
    env.manuscript.write_text(body + ("\n" if body else ""))
    return {"chapters": len(found), "words": len(body.split())}
