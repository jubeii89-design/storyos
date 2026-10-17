"""The terminal is the interface until a real one exists, so output is a feature.

Every command answers one question in a form you can act on without reading
source. `status` in particular has to answer "what now" in one line, because
that is the question asked most often and the one a progress bar cannot answer.

A stage that needs a model is refused here. The Claude Code skill drives those
-- it is where the subscription's model access lives -- and a Python command
that silently did nothing for eleven of the twenty-four stages would be a trap.
So those print the skill invocation to use instead, and exit nonzero.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import stages, units
from .events import completed_all, review_failures, run_totals
from .paths import Env, EnvNotFound
from .project import DEFAULT_CHAPTERS, init_env

DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"
GREEN, AMBER, RED = "\033[32m", "\033[33m", "\033[31m"


def _plain() -> bool:
    return not sys.stdout.isatty()


def c(text: str, colour: str) -> str:
    return text if _plain() else f"{colour}{text}{OFF}"


def _env(args) -> Env:
    try:
        return Env.find(getattr(args, "env", None))
    except EnvNotFound as x:
        sys.exit(f"{c('error', RED)}: {x}")


def _done(env: Env) -> tuple[set, str | None]:
    """Folded across every run. `latest_run` is only for the run strip."""
    return completed_all(env.root), env.latest_run()


# -- commands -------------------------------------------------------------

def cmd_init(args) -> int:
    env = init_env(args.dir, title=args.title, chapters=args.chapters, force=args.force)
    rel = env.root
    print(f"{c('created', GREEN)} {rel}")
    print(f"  {env.config['chapters']} chapters, halves of "
          f"{env.config['half_words'][0]:,}-{env.config['half_words'][1]:,} words")
    print()
    print("Next:")
    print(f"  1. cd {rel}")
    print(f"  2. write premise.md -- a paragraph is enough")
    print(f"  3. cp .env.example .env    (only if you want a keyed critic; "
          f"the drafting side needs no key)")
    print(f"  4. storyos status")
    return 0


def cmd_status(args) -> int:
    env = _env(args)
    done, run = _done(env)
    chapters = env.config.get("chapters", DEFAULT_CHAPTERS)

    print(f"{c(env.title, BOLD)}  {DIM if not _plain() else ''}{env.root}{OFF if not _plain() else ''}")

    # Progress by chapter, counting committed chapters rather than stages --
    # a half-drafted chapter is not half a chapter to a reader.
    committed = sum(1 for ch in range(1, chapters + 1)
                    if (ch, "", "chapter_commit") in done)
    drafted_halves = sum(1 for ch in range(1, chapters + 1) for p in units.PARTS
                         if (ch, p, "draft") in done)
    words = _words(env)

    print(f"  {committed}/{chapters} chapters committed · "
          f"{drafted_halves}/{chapters * 2} halves drafted · {words:,} words on disk")

    if not run:
        print(f"\n  {c('no runs yet', DIM)}")
    else:
        t = run_totals(env.events(run))
        print(f"  run {run} · {t['stages_ok']} ok, {t['stages_failed']} failed, "
              f"{t['findings']} findings")

    nxt = stages.next_step(chapters, done)
    if nxt is None:
        print(f"\n  {c('complete', GREEN)} — every stage has finished.")
        return 0

    chapter, part, stage = nxt
    st = stages.get(stage)
    print(f"\n  next: {c(stages.label(nxt), BOLD)}   {DIM if not _plain() else ''}"
          f"{st.writes}{OFF if not _plain() else ''}")

    if st.human:
        print(f"        {c('this one is yours', BOLD)} — open it and write:")
        print(f"        {c(str(env.premise), BOLD)}")
    elif st.model:
        esc = review_failures(env.root, chapter) if chapter else 0
        model = _model_for(env, stage, esc)
        print(f"        needs a model ({model}) — run it through the skill:")
        print(f"        {c(f'/storyos {stage} ' + (units.fmt(chapter, part) if chapter else ''), BOLD)}")
        if esc >= 2:
            print(f"        {c(f'escalated: {esc} rewrite verdicts on this chapter', AMBER)}")
    else:
        arg = units.fmt(chapter, part) if chapter else ""
        print(f"        free — Python answers this one:")
        print(f"        {c(f'storyos stage {stage} {arg}'.strip(), BOLD)}")
    return 0


def cmd_next(args) -> int:
    """Just the next step, one line, for scripting and for muscle memory."""
    env = _env(args)
    done, _ = _done(env)
    nxt = stages.next_step(env.config.get("chapters", DEFAULT_CHAPTERS), done)
    if nxt is None:
        print("complete")
        return 0
    chapter, part, stage = nxt
    print(f"{stage} {units.fmt(chapter, part) if chapter else 'book'}")
    return 0


def cmd_stage(args) -> int:
    st = stages.get(args.stage)
    if st.model:
        unit = args.unit or ""
        sys.exit(
            f"{c('refused', RED)}: {st.name} needs a model, and models are driven by "
            f"the Claude Code skill rather than this command.\n"
            f"  Run:  /storyos {st.name} {unit}".rstrip()
        )
    sys.exit(
        f"{c('not built yet', AMBER)}: {st.name} is a free Python stage and its "
        f"implementation is still to come.\n  It will write: {st.writes}"
    )


def cmd_doctor(args) -> int:
    env = _env(args)
    ok = True

    print(f"{c('environment', BOLD)}")
    for label, p, needed in (
        ("marker", env.marker, True),
        ("bench (models.json)", env.models_path, True),
        ("rules", env.prompts_dir / "rules_common.md", True),
        ("genre contracts", env.genres, False),
        ("premise", env.premise, False),
        ("style card", env.style, False),
        ("outline", env.outline, False),
    ):
        state = c("ok", GREEN) if p.exists() else (c("MISSING", RED) if needed else c("not yet", DIM))
        ok = ok and (p.exists() or not needed)
        print(f"  {label:22} {state}")

    print(f"\n{c('bench', BOLD)}")
    try:
        m = env.models()
    except Exception as x:
        print(f"  {c('unreadable', RED)}: {x}")
        return 1

    reg = {r["id"]: r for r in m.get("registry", []) if "id" in r}
    keys = set(env.key_names())
    crit = m.get("critics", {})

    for slot in ("slot_a", "slot_b"):
        rid = crit.get(slot)
        row = reg.get(rid)
        if row is None:
            print(f"  {slot:22} {c('unknown model ' + repr(rid), RED)}")
            ok = False
            continue
        why = _reachable(row, keys)
        print(f"  {slot:22} {row.get('label', rid)} — {why}")

    # The two bench rules, checked rather than trusted.
    a, b = reg.get(crit.get("slot_a"), {}), reg.get(crit.get("slot_b"), {})
    if "anthropic" in (a.get("family"), b.get("family")):
        print(f"  {c('RULE BROKEN', RED)}: a Claude model holds a critic slot. "
              f"The instance that drafted the prose cannot review it.")
        ok = False
    if a.get("family") and a.get("family") == b.get("family"):
        print(f"  {c('RULE BROKEN', RED)}: both critics are {a['family']} — "
              f"one lineage shares blind spots and will agree for the wrong reasons.")
        ok = False

    print(f"\n{c('keys defined in .env', BOLD)}")
    print(f"  {', '.join(sorted(keys)) if keys else c('none — the drafting side needs none', DIM)}")

    print()
    print(c("ready", GREEN) if ok else c("not ready — see above", AMBER))
    return 0 if ok else 1


def _reachable(row: dict, keys: set[str]) -> str:
    kind = row.get("kind")
    if kind == "subagent":
        return "Claude Code subagent, no key needed"
    if kind == "manual":
        return "you are the transport — paste, no key needed"
    env_var = row.get("key_env")
    if not env_var:
        return f"local endpoint {row.get('base_url', '?')} — must be running"
    return (f"needs ${env_var}" + (" (defined)" if env_var in keys else " — NOT defined"))


def _model_for(env: Env, stage: str, escalations: int) -> str:
    cfg = env.models().get("stages", {}).get(stage)
    if not cfg:
        # A stage with no bench row is a configuration mistake, not a default.
        # Printing "?" here once hid a real name split between the stage
        # contract and models.json.
        return f"NO BENCH ENTRY for {stage!r} in models.json"
    model = cfg.get("model", "unset")
    after = cfg.get("escalate_after_failures")
    if after and escalations >= after and cfg.get("escalate_to"):
        return f"{cfg['escalate_to']} (escalated from {model})"
    return model


def _words(env: Env) -> int:
    total = 0
    d = env.root / "chapters"
    if d.is_dir():
        for p in d.glob("ch*.md"):
            if ".v" not in p.name:          # committed chapters only
                total += len(p.read_text().split())
    return total


# -- argv -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="storyos",
        description="Premise to manuscript, in half-chapter units.")
    p.add_argument("--env", help="environment directory (default: found by walking up)")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="create an environment, which is one book")
    i.add_argument("dir")
    i.add_argument("--title")
    i.add_argument("--chapters", type=int, default=DEFAULT_CHAPTERS)
    i.add_argument("--force", action="store_true",
                   help="rewrite scaffolding; leaves prose, beats, deltas and runs alone")
    i.set_defaults(fn=cmd_init)

    s = sub.add_parser("status", help="what is done, and what to do next")
    s.set_defaults(fn=cmd_status)

    n = sub.add_parser("next", help="the next step, one line")
    n.set_defaults(fn=cmd_next)

    g = sub.add_parser("stage", help="run one free stage on one unit")
    g.add_argument("stage")
    g.add_argument("unit", nargs="?")
    g.set_defaults(fn=cmd_stage)

    d = sub.add_parser("doctor", help="what is reachable, and which bench rules hold")
    d.set_defaults(fn=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (units.BadUnit, KeyError, FileExistsError) as x:
        sys.exit(f"{c('error', RED)}: {x}")


if __name__ == "__main__":
    raise SystemExit(main())
