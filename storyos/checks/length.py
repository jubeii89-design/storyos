"""The word-count gate: a band, not a target.

1,500-1,750 words is the aim. The gate fails only below 1,350 or above 2,000,
because redrafting a 1,480-word half to satisfy a round number throws away good
prose, and a half that lands at 1,790 is not a defect. Between the bands it
warns and continues.
"""

from __future__ import annotations

import json

TARGET = (1500, 1750)
HARD = (1350, 2000)


def run(env, unit: str) -> dict:
    path = env.part(unit)
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} not drafted yet")

    words = len(path.read_text().split())
    if words < HARD[0]:
        verdict, ok = "short", False
    elif words > HARD[1]:
        verdict, ok = "long", False
    elif TARGET[0] <= words <= TARGET[1]:
        verdict, ok = "in band", True
    else:
        verdict, ok = "outside target, inside tolerance", True

    result = {"unit": unit, "words": words, "target": list(TARGET),
              "hard": list(HARD), "verdict": verdict, "ok": ok}
    env.words_check(unit).parent.mkdir(parents=True, exist_ok=True)
    env.words_check(unit).write_text(json.dumps(result, indent=2) + "\n")
    return result
