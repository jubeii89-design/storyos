"""The terminal is the interface until a real one exists, so output is a feature.

Every command answers one question in a form you can act on without reading
source. `status` in particular has to answer "what now" in one line, because
that is the question asked most often and the one a progress bar cannot answer.

A stage that needs a model is refused here. The Claude Code skill drives those
-- it is where the subscription's model access lives -- and a Python command
that silently did nothing for the free stages would be a trap.
So those print the skill invocation to use instead, and exit nonzero.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import stages, units
from .events import (EventLog, completed_all, new_run_id, review_failures,
                     run_totals)
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
        # Read the threshold rather than assume it. This was hard-coded to 2
        # here while models.json carried escalate_after_failures, so a writer
        # who changed it saw the old number in status and the new one applied.
        after = (env.models().get("stages", {}).get(stage, {})
                 .get("escalate_after_failures", 2))
        model = _model_for(env, stage, esc)
        print(f"        needs a model ({model}) — run it through the skill:")
        print(f"        {c(f'/storyos {stage} ' + (units.fmt(chapter, part) if chapter else ''), BOLD)}")
        if after and esc >= after:
            print(f"        {c(f'escalated: {esc} rewrite verdict(s), threshold {after}', AMBER)}")
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


FREE_STAGES = {
    "style_card":     lambda env, unit, run: _style(env),
    "split_beats":    lambda env, unit, run: _beats(env, unit),
    "context_pack":   lambda env, unit, run: _pack(env, unit, run),
    "length_gate":    lambda env, unit, run: _length(env, unit),
    "join":           lambda env, unit, run: _join(env, unit),
    "seam_check":     lambda env, unit, run: _seam_check(env, unit),
    "seam_verify":    lambda env, unit, run: _seam_verify(env, unit),
    "state_rebuild":  lambda env, unit, run: _state(env),
    "chapter_commit": lambda env, unit, run: _commit(env, unit),
    "reconcile":      lambda env, unit, run: _reconcile(env, unit),
    "export":         lambda env, unit, run: _export(env),
}


def cmd_stage(args) -> int:
    st = stages.get(args.stage)
    env = _env(args)

    if st.human:
        sys.exit(f"{c('yours', AMBER)}: {st.name} is not run by a command. "
                 f"Open and write:\n  {env.premise}")

    if st.model:
        unit = args.unit or ""
        sys.exit(
            f"{c('refused', RED)}: {st.name} needs a model, and models are driven by "
            f"the Claude Code skill rather than this command.\n"
            f"  Run:  /storyos {st.name} {unit}".rstrip()
        )

    fn = FREE_STAGES.get(st.name)
    if fn is None:
        sys.exit(f"{c('not built yet', AMBER)}: {st.name} is free but unimplemented.\n"
                 f"  It will write: {st.writes}")

    # A chapter stage given "07a" parsed the chapter and did the work, but
    # logged the key (7,"a",stage) where the plan expects (7,"",stage). The
    # work happened and stayed invisible, so status asked for it forever and
    # every re-run repeated it. The half direction was already guarded by
    # env.part(); this guards the other one.
    if st.scope == "chapter" and args.unit and units.parse(args.unit)[1]:
        sys.exit(f"{c('error', RED)}: {st.name} works on a whole chapter. "
                 f"Use {units.fmt(units.parse(args.unit)[0])}, not {args.unit}.")
    if st.scope == "book" and args.unit:
        sys.exit(f"{c('error', RED)}: {st.name} is a book-level stage and takes no unit.")

    if st.scope in ("half", "chapter") and not args.unit:
        sys.exit(f"{c('error', RED)}: {st.name} needs a unit, e.g. "
                 f"`storyos stage {st.name} "
                 f"{'07a' if st.scope == 'half' else '07'}`")

    # Every stage must log, or it did not happen. Resume is derived by
    # replaying events.jsonl, so a stage that writes its file without an event
    # is invisible: status never advances and the stage is re-run forever.
    # This is the one place a Python stage touches the log, and it is the
    # runner's job rather than each stage's, so no stage can forget.
    run_id = env.latest_run() or new_run_id()
    log = EventLog.open(env.root, run_id)
    chapter, part = units.parse(args.unit) if args.unit else (0, "")
    started = time.monotonic()
    log.stage_start(chapter, st.name, model="none", provider="python",
                    prompt_report={}, part=part)
    try:
        result = fn(env, args.unit, run_id)
    except BaseException as x:
        # Anything at all, including KeyboardInterrupt. A stage that dies
        # without a stage_end is invisible to resume and will be re-run
        # forever, so the log is closed on every path out of here.
        log.stage_end(chapter, st.name, False, part=part, error=str(x) or type(x).__name__,
                      latency_s=time.monotonic() - started)
        log.close()
        if isinstance(x, (FileNotFoundError, ValueError, units.BadUnit, KeyError)):
            sys.exit(f"{c('failed', RED)}: {x}")
        raise

    # A stage may return a plain list (succeeded) or (lines, ok). A check that
    # reports a violation must exit nonzero: printing "ok" above a violation
    # and returning 0 is how a broken gate gets ignored in a script.
    ok = True
    if isinstance(result, tuple):
        result, ok = result

    log.stage_end(chapter, st.name, ok, part=part,
                  latency_s=round(time.monotonic() - started, 2))
    log.close()

    print(f"{c('ok' if ok else 'FAILED', GREEN if ok else RED)} "
          f"{st.name} {args.unit or ''}".rstrip())
    for line in result:
        print(f"  {line}")
    return 0 if ok else 1


def _style(env):
    from .style import run
    card = run(env, force=True)
    src = card["source"]
    if not src["measured"]:
        return [c("no style card — comps/ is empty", AMBER),
                "Story OS will not invent targets it cannot measure, so drafts",
                "will carry no style constraint at all until you give it prose.",
                f"put 2,000+ words of your own writing in {env.comps_dir}"]
    out = [f"measured {card['words']:,} words from {len(src['files'])} file(s)",
           f"sentences: mean {card['sentence_len']['mean']}, "
           f"stdev {card['sentence_len']['stdev']}, p90 {card['sentence_len']['p90']}",
           f"dialogue {card['dialogue']['pct_of_words']}% of words · "
           f"adverbs {card['habits']['adverbs_per_1k']}/1k · "
           f"semicolons {card['habits']['semicolons_per_1k']}/1k"]
    if not src["sufficient"]:
        out.append(c(src["warning"], AMBER))
    return out


def _beats(env, unit):
    from .beats import run
    r = run(env, unit)
    return [f"half a: {r['a']['beats']} beats (weight {r['a']['weight']})",
            f"half b: {r['b']['beats']} beats (weight {r['b']['weight']})",
            f"handoff: {r['seam_intent']}"]


def _pack(env, unit, run_id):
    from .pack import run
    rep = run(env, unit, run_id)
    lines = [f"{len(rep['segments'])} segments, {rep['total_tokens']:,} tokens"]
    for s in sorted(rep["segments"], key=lambda s: -s["priority"]):
        lines.append(f"  prio {s['priority']:>3}  {s['tokens']:>6,} tok  {s['label'][:54]}")
    if rep.get("dropped"):
        lines.append(c(f"dropped {len(rep['dropped'])} segment(s) to fit", AMBER))
    return lines


def _length(env, unit):
    from .checks.length import run
    r = run(env, unit)
    lines = [f"{r['words']:,} words — {r['verdict']}"]
    for h in r.get("banned_phrases", []):
        lines.append(c(f"  line {h['line']}: \u201c{h['phrase']}\u201d", RED))
        lines.append(f"    …{h['context'][:70]}…")
    if not r["ok"] and "banned" not in r["verdict"]:
        lines.append(f"outside the hard band {r['hard'][0]:,}-{r['hard'][1]:,}; redraft")
    return lines, r["ok"]


def _join(env, unit):
    from .join import run
    s = run(env, unit)
    return [f"seam at paragraph {s['seam_paragraph']} (offset {s['seam_offset']:,})",
            f"editable window: paragraphs {s['window']['first_paragraph']}"
            f"-{s['window']['last_paragraph']}",
            f"{s['words']['total']:,} words, {len(s['paragraphs'])} paragraphs hashed"]


def _seam_check(env, unit):
    """The four defects two independently drafted halves make at their join."""
    from .checks.seam import check_seam
    from . import units as U
    ch = U.parse(unit)[0]
    a, b = env.part(U.fmt(ch, "a")), env.part(U.fmt(ch, "b"))
    missing = [p.name for p in (a, b) if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"both halves must exist: missing {', '.join(missing)}")

    found = check_seam(a.read_text(), b.read_text())
    errors = [f for f in found if f.severity == "error"]
    if not found:
        return ["clean — no repeated phrasing, restated setting, repeated "
                "gesture or false ending at the join"], True

    lines = []
    for f in found:
        tag = c(f.severity.upper(), RED if f.severity == "error" else AMBER)
        lines.append(f"{tag:14} {f.code:17} {f.message}")
        lines.append(f"{'':14} {f.evidence!r}")
    lines.append("")
    lines.append("these are the seam stage's work list; it may edit only the window")
    return lines, not errors


def _seam_verify(env, unit):
    from .join import verify
    v = verify(env, unit)
    if not v:
        return ["clean — the repair stayed inside its window"], True
    return ([f"{len(v)} violation(s) — the repair must be redone"] +
            [f"  {x['kind']} para {x['paragraph']}: {x['detail']}" for x in v]), False


def _state(env):
    from .state import rebuild
    s = rebuild(env)
    return [f"{len(s['characters'])} characters, {len(s['world'])} world rows, "
            f"{len(s['places'])} places",
            f"provisional: {sum(1 for g in s.values() for r in g if r.get('provisional'))}"]


def _reconcile(env, unit):
    from .review.run import run
    d = run(env, unit)
    lines = [d["summary"]]
    for who, bad in d["schema_violations"].items():
        for v in bad:
            lines.append(c(f"{who}: {v}", AMBER))
    for pair in d["confirmed"]:
        lines.append(f"CONFIRMED  \u201c{pair[0]['quote'][:58]}\u2026\u201d")
        lines.append(f"           fails: {pair[0]['why_it_fails'][:64]}")
        lines.append(f"           reaching for: {pair[0]['reaches_for'][:58]}")
    for f in d["contested"]:
        lines.append(f"contested  [{f['reviewer']}] \u201c{f['quote'][:50]}\u2026\u201d")
    for f in d["hallucinated"]:
        lines.append(c(f"discarded  \u201c{f['quote'][:46]}\u2026\u201d "
                       f"({f['match_ratio']:.0%} match)", DIM))
    lines.append("")
    lines.append("confirmed findings go to revise; contested ones are yours to call")
    return lines


def _export(env):
    from .export import run
    r = run(env)
    lines = [f"{r['chapters']} chapter(s), {r['words']:,} words"]
    for fmt, path in r["files"].items():
        lines.append(f"{fmt:5} {path}")
    gone = sorted({k for s in r.get("scrubbed", {}).values()
                   for k in s.get("removed", [])})
    lines.append("")
    lines.append("metadata cleaned: no renderer name, no build timestamp, "
                 "no per-export id")
    if gone:
        lines.append(f"  removed from the PDF: {', '.join(gone)}")
    missing = {"docx","epub","pdf"} - set(r["files"])
    if missing:
        lines.append(c(f"not produced: {', '.join(sorted(missing))}", AMBER))
    return lines


def _commit(env, unit):
    from .commit import run
    r = run(env, unit)
    return [f"committed from {r['from']} — {r['words']:,} words",
            f"manuscript: {r['chapters_in_manuscript']} chapter(s), "
            f"{r['manuscript_words']:,} words",
            f"ledger: {r['ledger_rows']} rows"]


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

    cn = sub.add_parser("connect", help="sign in to an outside service")
    cn.add_argument("service", choices=["google"])
    cn.add_argument("--again", action="store_true", help="sign in again, as someone else")
    cn.add_argument("--forget", action="store_true", help="sign out and delete the token")
    cn.add_argument("--no-browser", action="store_true", help="print the URL instead")
    cn.set_defaults(fn=cmd_connect)

    lb = sub.add_parser("library", help="craft knowledge the system can read and pin")
    lb.add_argument("action", choices=["add", "list", "search", "reindex"])
    lb.add_argument("target", nargs="?", help="a file, a URL, or a Google Doc link")
    lb.add_argument("--google", action="store_true", help="treat target as a Google Doc id")
    lb.add_argument("--full", action="store_true", help="reindex: rebuild from scratch")
    lb.set_defaults(fn=cmd_library)

    u = sub.add_parser("ui", help="serve the local page (reads and writes files only)")
    u.add_argument("--port", type=int, default=8730)
    u.add_argument("--open", action="store_true", help="open a browser")
    u.set_defaults(fn=cmd_ui)

    return p


def cmd_connect(args) -> int:
    from .sources import google
    env = _env(args)
    if args.service != "google":
        sys.exit(f"{c('error', RED)}: unknown service {args.service!r}. Known: google")

    if args.forget:
        gone = google.forget(env)
        print(c("signed out", GREEN) if gone else "was not signed in")
        print("  Story OS no longer holds a token. To revoke its access at "
              "Google's end:\n  https://myaccount.google.com/permissions")
        return 0

    st = google.status(env)
    if st["connected"] and not args.again:
        print(f"{c('already signed in', GREEN)} to Google")
        print(f"  scopes: {', '.join(s.rsplit('/', 1)[-1] for s in st['scopes'])}")
        print(f"  run with --again to sign in as someone else")
        return 0

    try:
        google.connect(env, open_browser=not args.no_browser)
    except google.SetupNeeded as x:
        print(x)
        return 1
    except (RuntimeError, TimeoutError) as x:
        sys.exit(f"{c('failed', RED)}: {x}")

    print(f"\n{c('connected', GREEN)} — Story OS can now read Google Docs you import.")
    print(f"  the token is stored in {env.root / '.google-token.json'} (owner-only)")
    print(f"  import one with:  storyos library add <paste the doc URL>")
    return 0


def cmd_library(args) -> int:
    from . import library
    from .sources import files, google
    env = _env(args)

    if args.action == "list":
        rows = library.notes(env)
        if not rows:
            print("the library is empty")
            print("  add craft knowledge with: storyos library add <file|url|google-doc>")
            return 0
        for r in rows:
            mark = "PDF" if r.get("original", "").endswith(".pdf") else \
                   "doc" if r.get("original") else "  —"
            print(f"  {r['words']:>6,}w  {mark}  {r['source']:<10} {r['title'][:48]}")
        print(f"\n  {len(rows)} note(s) in {env.library_notes}")
        stale = library.stale_pins(env)
        if stale:
            print(c(f"  {len(stale)} pin(s) now stale — the note changed after pinning", AMBER))
        return 0

    if args.action == "search":
        if not args.target:
            sys.exit("usage: storyos library search <text>")
        from . import search as S
        S.reindex(env)
        d = S.query(env, args.target)
        if d.get("note"):
            print(f"  {d['note']}")
            return 0
        for r in d["results"]:
            tag = c(f"[{r['kind']}]", DIM)
            where = f" {r['unit']}" if r["unit"] else ""
            print(f"  {tag} {c(r['title'][:44], BOLD)}{where}")
            print(f"       {r['snippet']}")
        kinds = ", ".join(f"{v} {k}" for k, v in d.get("by_kind", {}).items())
        print(f"\n  {d['total']} result(s)" + (f" — {kinds}" if kinds else ""))
        return 0

    if args.action == "reindex":
        from . import search as S
        d = S.reindex(env, full=args.full if hasattr(args, "full") else False)
        print(f"{c('indexed', GREEN)} {d['documents']} document(s), "
              f"{d['words']:,} words")
        print(f"  {d['updated']} updated, {d['removed']} removed")
        return 0

    # add
    src = args.target
    if not src:
        sys.exit("usage: storyos library add <file path | url | google doc url>")

    # PDF is the standard kept form: fixed pages, checkable quotes, opens
    # anywhere. Where no PDF can be had the Markdown is the original.
    original = ""
    try:
        if "docs.google.com" in src or (args.google and src):
            st = google.status(env)
            if not st["connected"]:
                print(c("not signed in to Google", AMBER))
                print("  Story OS needs your permission to read the document.")
                print("  Run:  storyos connect google")
                return 1
            doc = google.fetch(env, src)
            if pdf := google.fetch_pdf(env, src):
                original = library.keep_original(env, doc["name"], pdf, ".pdf")
            path = library.add(env, doc["text"], title=doc["name"],
                               source="google-docs", source_id=doc["id"],
                               modified=doc.get("modified", ""),
                               fmt=doc["exported_as"].rsplit("/", 1)[-1],
                               original=original)
            fmt = doc["exported_as"]
        elif files.is_url(src):
            md, title, fmt, pdf = files.fetch_url_full(src)
            if pdf:
                original = library.keep_original(env, title, pdf, ".pdf")
            path = library.add(env, md, title=title, source="web",
                               source_id=src, fmt=fmt, original=original)
        else:
            md, title, fmt = files.read(src)
            raw = Path(src).expanduser()
            ext = raw.suffix.lower()
            if ext in (".pdf", ".docx"):
                original = library.keep_original(env, title, raw.read_bytes(), ext)
            # Record the file's name, not where it happened to sit. An absolute
            # path names the machine and the account to anyone the note reaches.
            elif ext in (".html", ".htm"):
                # Render it, so an imported page is kept the way it looked
                # rather than as a file that needs a browser and its assets.
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "p.pdf"
                    if files.html_to_pdf(raw.read_text(errors="replace"), out,
                                         base_url=raw.resolve().as_uri()):
                        original = library.keep_original(env, title,
                                                         out.read_bytes(), ".pdf")
            path = library.add(env, md, title=title, source=fmt,
                               source_id=raw.name, fmt=fmt,
                               original=original)
    except google.NotConnected as x:
        print(c("Google says no", AMBER)); print(f"  {x}")
        print("  Run:  storyos connect google")
        return 1
    except (files.Unsupported, FileNotFoundError, ValueError, RuntimeError) as x:
        sys.exit(f"{c('failed', RED)}: {x}")

    words = len(path.read_text().split())
    print(f"{c('added', GREEN)} {path.name}")
    print(f"  {words:,} words · from {fmt}")
    print(f"  searchable: {path.relative_to(env.root)}")
    if original:
        kind = "PDF kept" if original.endswith(".pdf") else "original kept"
        print(f"  {kind}:  {original}")
    else:
        print(f"  no separate original — the Markdown is the document")
    return 0


def cmd_ui(args) -> int:
    from .ui.server import serve
    env = _env(args)
    serve(str(env.root), port=args.port, open_browser=args.open)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (units.BadUnit, KeyError, FileExistsError) as x:
        sys.exit(f"{c('error', RED)}: {x}")


if __name__ == "__main__":
    raise SystemExit(main())
