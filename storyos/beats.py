"""Split a chapter's beats between its two halves, and state the handoff.

Pure Python. A model asked to divide ten beats into two groups of five will
produce a plausible division; arithmetic produces the right one, and the
interesting decision -- what must be true when half A stops -- is derived from
the split rather than invented.

`seam_intent` goes into BOTH halves' beat files. Half A arrives at it; half B
inherits it without ever being shown half A's beats, so B cannot accidentally
re-dramatise work A already did.
"""

from __future__ import annotations

import json

from . import units


def split(beats: list[dict]) -> tuple[list[dict], list[dict]]:
    """Divide beats by cumulative weight, not by count.

    A beat carries an optional `weight` (default 1). Splitting 2/8 by count
    when the first two beats are the chapter's whole argument gives half A
    1,700 words for two beats and half B 1,700 for eight. Weight is the
    writer's estimate of how much page each beat needs.
    """
    if len(beats) < 2:
        raise ValueError(f"a chapter needs at least 2 beats to split, got {len(beats)}")

    weights = [max(0.1, float(b.get("weight", 1))) for b in beats]
    total = sum(weights)

    # The cut that leaves both halves closest to half the weight, with at
    # least one beat on each side.
    best, best_gap = 1, float("inf")
    run = 0.0
    for i in range(len(beats) - 1):
        run += weights[i]
        gap = abs(run - total / 2)
        if gap < best_gap:
            best, best_gap = i + 1, gap

    return beats[:best], beats[best:]


def derive_seam_intent(a: list[dict], b: list[dict]) -> str:
    """What must be true at the handoff, read off the split.

    The last beat of A is where the scene stands when it stops; the first of B
    is what it must be able to continue into. Stating both is what stops B
    re-establishing and stops A resolving.
    """
    last = a[-1].get("beat") or a[-1].get("text") or "the final beat of the first half"
    nxt = b[0].get("beat") or b[0].get("text") or "the first beat of the second half"
    return (f"When the first half stops: {last.rstrip('.')}. "
            f"It must not be resolved. The second half opens into: {nxt.rstrip('.')}.")


def run(env, chapter: int | str) -> dict:
    ch = chapter if isinstance(chapter, int) else units.parse(chapter)[0]
    src = env.beats(ch)
    if not src.is_file():
        raise FileNotFoundError(f"{src.name} not found -- the beats stage runs first")

    data = json.loads(src.read_text())
    beats = data if isinstance(data, list) else data.get("beats", [])
    a, b = split(beats)
    intent = data.get("seam_intent") if isinstance(data, dict) else None
    intent = intent or derive_seam_intent(a, b)

    out = {}
    for part, group in (("a", a), ("b", b)):
        payload = {"unit": units.fmt(ch, part), "seam_intent": intent, "beats": group}
        path = env.beats(units.fmt(ch, part))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n")
        out[part] = {"beats": len(group),
                     "weight": sum(float(x.get("weight", 1)) for x in group)}

    out["seam_intent"] = intent
    return out
