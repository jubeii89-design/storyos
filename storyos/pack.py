"""Build a draft prompt and write it where the inspector can read it.

No model. This stage exists so that every prompt is an artefact on disk before
anything is spent on it: a bad half is almost always a bad context, and the
only way to know which segment caused it is to have kept the segment list.
"""

from __future__ import annotations

import json

from . import units
from .context import build_prose_prompt
from .style import run as style_run


def run(env, unit: str, run_id: str) -> dict:
    chapter, part = units.parse(unit)
    if not part:
        raise units.BadUnit(f"{unit!r} is a whole chapter; packs are built for halves")

    beats_path = env.beats(unit)
    if not beats_path.is_file():
        raise FileNotFoundError(
            f"{beats_path.name} not found -- run `storyos stage split_beats "
            f"{units.fmt(chapter)}` first")
    beats_doc = json.loads(beats_path.read_text())

    rules = _rules(env, part)
    state = json.loads(env.state.read_text()) if env.state.is_file() else {}
    style = style_run(env)

    half_a_text = ""
    prior: list[str] = []
    pred = units.predecessor(unit)
    if part == "b":
        # Half A whole and verbatim. This is the one place full prose earns its
        # tokens: a summarised first half produces a visible discontinuity.
        a_path = env.part(units.fmt(chapter, "a"))
        if not a_path.is_file():
            raise FileNotFoundError(f"half {chapter:02d}a must be drafted before {unit}")
        half_a_text = a_path.read_text()
    elif pred:
        prev = env.chapter(units.parse(pred)[0])
        if prev.is_file():
            prior = [prev.read_text()]

    prompt = build_prose_prompt(
        stage="draft",
        rules=rules,
        style_card=style,
        world_facts={"world": state.get("world", []), "places": state.get("places", [])},
        character_rows=state.get("characters", []),
        beats=beats_doc.get("beats", []),
        prior_chapters=prior,
        chapter_no=chapter,
        part=part,
        half_a_text=half_a_text,
        seam_intent=beats_doc.get("seam_intent", ""),
    )

    report = prompt.report()
    path = env.prompt_pack(run_id, unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "unit": unit, "run": run_id, "stage": "draft",
        "report": report,
        "system": prompt.render_system(),
        "user": prompt.render_user(),
    }, indent=2) + "\n")
    return report


def _rules(env, part: str) -> str:
    """Common rules plus the half-specific ones, in that order.

    Two files rather than one duplicated pair, so the shared rules have a
    single source and cannot drift between halves.
    """
    common = env.prompts_dir / "rules_common.md"
    specific = env.prompts_dir / f"rules_{part}.md"
    missing = [p.name for p in (common, specific) if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"missing prompt rules: {', '.join(missing)}")
    return common.read_text().rstrip() + "\n\n" + specific.read_text()
