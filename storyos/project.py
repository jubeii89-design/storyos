"""Create an environment: one directory that is one book.

The scaffolder writes only what no later stage owns. It does not invent a
premise, a genre or an outline, because those are the writer's and the
pipeline's, and a file seeded with plausible-looking placeholder content is
worse than an absent one -- it gets drafted against.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .paths import MARKER, Env

VERSION = 1
DEFAULT_CHAPTERS = 40

DIRS = (
    "prompts", "reference", "templates",
    "library/notes",
    "comps",
    "beats",
    "chapters/parts", "chapters/joined",
    "deltas", "checks", "review",
    "cover", "export", "runs",
)

GITIGNORE = """\
# Keys and live credentials. Never committed, never relocated -- rotate.
.env
*.key
*.pem
.google-token.json

# Derived. Rebuilt from deltas/ and chapters/, safe to delete.
state.json
manuscript.md
library/index.sqlite3

# Prompt packs are large and regenerable. The event logs beside them are NOT
# ignored: resume and model escalation are both derived by replaying them.
runs/*/prompts/

__pycache__/
.cache/
.DS_Store
"""

ENV_EXAMPLE = """\
# Keys live here and nowhere else. models.json names the variable, never the
# value, because models.json is edited in the UI and ends up in screenshots.
#
# Nothing in the drafting pipeline needs any of these: Claude Code covers it
# under your subscription. These are only for critics and cover art.

# A critic slot, if you want one that is not local. NOTE: a ChatGPT Plus
# subscription does not include API access -- that is a separate prepaid
# purchase. For a free foreign critic use a local Ollama model, or the
# chatgpt-manual bench row, which makes you the transport.
OPENAI_API_KEY=

# Gemini as a critic, or Nano Banana for cover art.
GOOGLE_API_KEY=
"""

RULES_COMMON = """\
# Operating rules

These apply to every half you draft. They are measured where they can be
measured: a check in `storyos/checks/` reads the result and a reviewer never
sees a question Python already answered.

## Scene, not summary

Dramatise the beats. Do not report them. If three consecutive sentences pass
with no concrete sensory particular, no physical action and no line of
dialogue, the prose has drifted into summary and must be rewritten as scene.

Summary: *They argued about the manifest for an hour.*
Scene: *"Read me the line," she said. "The line itself." He did not pick up the tablet.*

## Specify, do not assert

An asserted feeling is a placeholder for the thing that would have produced it.
Name what the character notices, not what they feel about it.

Asserted: *She felt a wave of dread.*
Specified: *She counted the crates twice and got nine both times. The manifest said eight.*

## The ledger is the world

Every named person, place and object must already exist in `state.json`. If the
scene needs something new, it may be introduced only if the beats call for it.
Inventing a name to fill a sentence breaks continuity 30 chapters later and the
entity check will flag it.

## Style targets are measurements

`style.json` holds numbers taken from the writer's own prose: sentence-length
distribution, dialogue share, paragraph length, adverb rate. Match the
distribution, not the mean -- uniform sentence length reads as machinery.

## Never write these

They mark generated prose more reliably than anything else, and the free check
rejects the draft when it finds them:

- *a testament to*, *a mix of*, *a symphony of*, *a tapestry of*
- *her heart hammered / pounded / raced*, *a shiver ran down*
- *little did she know*, *unbeknownst to*
- *the air was thick with*, *the silence was deafening*
- *she couldn't help but*, *a wave of \\<emotion\\> washed over*
- *in that moment, she realised*, *everything changed*
- any sentence beginning *Little did*, *As if on cue*, or *Somewhere, *

## Length

1,500-1,750 words. Under 1,350 or over 2,000 fails the gate and is redrafted.
Between the bands, a warning only -- a redraft of a 1,480-word half throws away
good prose to satisfy a round number.
"""

RULES_A = """\
# Drafting the FIRST half

You are writing the opening half of a chapter that continues past where you
stop. Another half follows immediately and is written against yours.

## Do not land an ending

This is the rule that matters most here. The half stops mid-motion: on an
unresolved action, an unanswered question, a line nobody has replied to yet.
It does not arrive anywhere.

A first half that resolves gives the second half nothing to do and leaves the
joined chapter with two endings, the first of them false. The free seam check
looks for closure markers in your final two sentences and flags them.

Wrong: *She understood, for the first time, what the station had been built to carry.*
Right: *"All six," he said. She did not answer, and he waited, and the crate ticked.*

## Establish, because the second half will not

Setting, weather, who is present, what time it is, the physical arrangement of
the space -- establish all of it here. The second half is forbidden from
re-establishing any of it, so anything you leave out is simply absent from the
chapter.

## Hand over deliberately

`seam_intent` in the beats file states what must be true when you stop. Arrive
at exactly that state. Do not overshoot it and do not stop short.

## Your last sentence is the next half's antecedent

It will be the final thing in the second half's context before it begins
writing. Make it a sentence that can be continued from -- concrete, in motion,
unambiguous about who is speaking and where everyone stands.
"""

RULES_B = """\
# Drafting the SECOND half

You are continuing a scene already in progress. The complete first half is in
your context, verbatim. Its final sentence is your immediate antecedent: write
as though your first word follows its last word, because it does.

## Do not re-establish the setting

The reader has been in this room for 1,700 words. Naming the room again, or its
smell, or the weather outside it, restarts a scene that is already running.
This is the single most common defect at the join and the seam check measures
it by comparing your opening sentences to the first half's.

Wrong: *The greenhouse gallery smelled of coolant and damp soil.* (the first half opened this way)
Right: *"No," he said. "It isn't."*

## Do not reintroduce anyone

Every character in the first half is still present and still has the
disposition they had. They do not re-enter, and they are not re-described.

## Do not reuse the first half's gestures or images

If someone shrugged in the first half, nobody shrugs here. If the crate was
warm there, it is not discovered to be warm again. Pull a different physical
vocabulary; the check flags a shared distinctive gesture across the join.

## Do not summarise what just happened

It happened on the page, immediately before. A character reflecting on the
previous 1,700 words is the second most common defect here.

## Resolve the chapter, not the book

This half does land. Close the chapter's local question -- the one its beats
posed -- and leave the book's open. A chapter that resolves nothing has no
shape; a chapter that resolves the novel has ended it.

## Continue the voice, do not imitate it

The first half is in your context so that its cadence carries, not so you can
pastiche it. Same register, same sentence rhythm, different sentences.
"""

SEED_STATE = {"characters": [], "world": [], "places": []}


def _find_skill_dir() -> Path | None:
    """Where the installed skill keeps its templates and genre contracts."""
    for cand in (
        Path.home() / ".claude" / "skills" / "storyos",
        Path("/home/user/.claude/skills/storyos"),
        Path(__file__).resolve().parent.parent / ".claude" / "skills" / "storyos",
    ):
        if (cand / "SKILL.md").is_file():
            return cand
    return None


def init_env(
    root: str | Path,
    *,
    title: str | None = None,
    chapters: int = DEFAULT_CHAPTERS,
    force: bool = False,
) -> Env:
    """Create an environment at `root` and return it.

    Refuses a directory that already holds a marker unless `force`, because
    merging into a live book could overwrite a style card or a ledger that took
    real work to produce. `force` rewrites the scaffolding only; it never
    touches prose, deltas, beats or runs.
    """
    root = Path(root).expanduser()
    marker = root / MARKER

    if marker.exists() and not force:
        raise FileExistsError(
            f"{root} is already a Story OS environment. "
            f"Pass force=True to rewrite its scaffolding, which leaves prose, "
            f"beats, deltas and runs untouched."
        )

    root.mkdir(parents=True, exist_ok=True)
    for d in DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)

    title = title or root.resolve().name
    skill = _find_skill_dir()

    # The marker is written last, so an interrupted init leaves a directory
    # that `Env.find` does not claim rather than a half-built environment that
    # it does.
    written: list[str] = []

    def put(rel: str, text: str, *, keep: bool = True) -> None:
        p = root / rel
        if p.exists() and keep and not force:
            return
        p.write_text(text)
        written.append(rel)

    put(".gitignore", GITIGNORE)
    put(".env.example", ENV_EXAMPLE)
    put("prompts/rules_common.md", RULES_COMMON)
    put("prompts/rules_a.md", RULES_A)
    put("prompts/rules_b.md", RULES_B)
    put("state.json", json.dumps(SEED_STATE, indent=2) + "\n")
    put("library.pins.json", "[]\n")

    # models.json comes from the skill when it is installed, so the bench the
    # writer edits is the one the skill documents.
    models_dst = root / "models.json"
    if not models_dst.exists() or force:
        src = skill / "templates" / "models.json" if skill else None
        if src and src.is_file():
            shutil.copyfile(src, models_dst)
            written.append("models.json (from skill)")
        else:
            models_dst.write_text(json.dumps(_fallback_models(), indent=2) + "\n")
            written.append("models.json (fallback -- skill not found)")

    genres_dst = root / "reference" / "genres.md"
    if (not genres_dst.exists() or force) and skill:
        src = skill / "reference" / "genres.md"
        if src.is_file():
            shutil.copyfile(src, genres_dst)
            written.append("reference/genres.md")

    marker.write_text(json.dumps({
        "version": VERSION,
        "title": title,
        "chapters": chapters,
        "half_words": [1500, 1750],
    }, indent=2) + "\n")
    written.append(MARKER)

    return Env.find(root)


def _fallback_models() -> dict:
    """A bench that works with no key and no local model: one Ollama row ready
    to enable, and a paste-driven critic that needs nothing installed."""
    return {
        "version": 1,
        "registry": [
            {"id": "claude-opus-5", "label": "Opus 5", "family": "anthropic",
             "kind": "subagent", "model": "opus",
             "roles": ["brainstorm", "outline", "beats", "draft", "seam", "revise"]},
            {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5", "family": "anthropic",
             "kind": "subagent", "model": "sonnet", "roles": ["draft", "beats", "outline"]},
            {"id": "claude-haiku-4-5", "label": "Haiku 4.5", "family": "anthropic",
             "kind": "subagent", "model": "haiku", "roles": ["delta_extract"]},
            {"id": "ollama-qwen", "label": "Qwen3 14B (local)", "family": "qwen",
             "kind": "openai_compatible", "base_url": "http://localhost:11434/v1",
             "model": "qwen3:14b", "key_env": None, "price_in": 0, "price_out": 0,
             "roles": ["critic"]},
            {"id": "chatgpt-manual", "label": "ChatGPT (paste)", "family": "openai",
             "kind": "manual", "open_url": "https://chatgpt.com/", "key_env": None,
             "price_in": 0, "price_out": 0, "roles": ["critic"]},
        ],
        "critics": {"slot_a": "ollama-qwen", "slot_b": "chatgpt-manual",
                    "slot_b_policy": "on_contest"},
        "stages": {
            "genre": {"model": "claude-opus-5"},
            "brainstorm": {"model": "claude-opus-5"},
            "dossier": {"model": "claude-opus-5"},
            "bible": {"model": "claude-opus-5"},
            "outline": {"model": "claude-opus-5"},
            "beats": {"model": "claude-opus-5"},
            "draft": {"model": "claude-sonnet-5-5", "escalate_to": "claude-opus-5",
                      "escalate_after_failures": 2},
            "delta_extract": {"model": "claude-haiku-4-5"},
            "seam": {"model": "claude-opus-5"},
            "revise": {"model": "claude-opus-5"},
        },
        "cover_renderer": "typographic",
    }
