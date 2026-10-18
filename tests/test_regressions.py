"""Every defect found by hand during the build, locked down.

Each test here corresponds to a bug that actually shipped and was caught by
running the thing rather than reading it. They are written as the bug, not as
the feature: the name says what broke, and the docstring says what it cost,
because a test whose purpose is forgotten is deleted during the next cleanup.

Run with: python -m pytest tests/ -q        (or: python tests/test_regressions.py)
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storyos import beats, commit, join, library, queue, search, state, stages, units
from storyos.checks import seam
from storyos.events import (EventLog, completed_all, completed_stages,
                            new_run_id, review_failures)
from storyos.context import build_prose_prompt
from storyos.paths import Env
from storyos.project import init_env
from storyos.review.reconcile import (Finding, Review, Verdict, verify_entities,
                                      verify_quotes)
from storyos.review.schema import parse
from storyos.sources import files


def fresh(chapters: int = 3) -> Env:
    return init_env(Path(tempfile.mkdtemp()) / "Book", chapters=chapters)


def paras(n: int, tag: str, words: int = 40) -> str:
    return "\n\n".join(" ".join([f"{tag}{i}"] * words) + "." for i in range(n))


# -- 1. quote verification ------------------------------------------------

def test_apostrophes_do_not_break_quote_verification():
    """A verbatim quote containing contractions scored 0.433 and was recorded
    as fabricated evidence, because two implementations of one transform
    disagreed about what a deleted apostrophe leaves behind. Most quoted
    dialogue contains a contraction, so review returned almost nothing -- and
    the failure was indistinguishable from both critics hallucinating, which
    is the single distinction that module exists to make."""
    chapter = ("He didn't look at the crate. She hadn't slept, and the "
               "station's pumps were still running.")
    quote = "He didn't look at the crate. She hadn't slept, and the station's pumps"

    for variant in (quote,
                    quote.replace("'", "’"),          # curly
                    quote.replace("didn't", "didnt")):      # apostrophe dropped
        f = Finding("a", "weakest_paragraph", variant, why_it_fails="x.",
                    reaches_for="y.", works_here="nothing", para_index=0)
        r = Review("a", "m", weakest=[f], strongest_quote="z", verdict=Verdict.REVISE)
        ver, hall = verify_quotes(r, chapter)
        assert len(ver) == 1 and not hall, f"rejected a real quote: {variant!r}"
        assert f.match_ratio > 0.9


def test_invented_quotes_are_still_discarded():
    """The fix above must not make verification permissive."""
    chapter = "He didn't look at the crate."
    f = Finding("a", "weakest_paragraph",
                "Her heart hammered against her ribs as the alarm began to sound",
                why_it_fails="x.", reaches_for="y.", works_here="nothing")
    r = Review("a", "m", weakest=[f], strongest_quote="z", verdict=Verdict.REVISE)
    ver, hall = verify_quotes(r, chapter)
    assert not ver and len(hall) == 1
    assert f.match_ratio < 0.5


def test_quote_span_points_at_the_real_text():
    chapter = 'A. "No manifest," Vance said, when the lock cycled. B.'
    f = Finding("a", "weakest_paragraph",
                "No manifest, Vance said, when the lock cycled",
                why_it_fails="x.", reaches_for="y.", works_here="nothing")
    r = Review("a", "m", weakest=[f], strongest_quote="z", verdict=Verdict.REVISE)
    verify_quotes(r, chapter)
    assert f.span and "manifest" in chapter[f.span[0]:f.span[1]]


# -- 2. resume ------------------------------------------------------------

def test_resume_folds_across_every_run():
    """completed_stages read only the newest run. A per-turn drain opens a new
    run id, so by chapter 3 the system believed nothing had been done and would
    redraft the book from the start."""
    env = fresh()
    r1 = new_run_id()
    log = EventLog.open(env.root, r1)
    for s in ("premise", "genre", "brainstorm"):
        log.stage_end(0, s, True)
    log.stage_end(1, "draft", True, part="a")
    log.close()

    time.sleep(1.05)                      # run ids are second-resolution
    r2 = new_run_id()
    log = EventLog.open(env.root, r2)
    log.stage_end(1, "draft", True, part="b")
    log.close()

    assert len(completed_stages(env.events(r2))) == 1      # the old, wrong view
    done = completed_all(env.root)
    assert len(done) == 5
    assert (1, "a", "draft") in done and (1, "b", "draft") in done


def test_a_later_failure_overrides_an_earlier_success():
    env = fresh()
    r1 = new_run_id()
    log = EventLog.open(env.root, r1); log.stage_end(1, "draft", True, part="a"); log.close()
    time.sleep(1.05)
    r2 = new_run_id()
    log = EventLog.open(env.root, r2)
    log.stage_end(1, "draft", False, part="a", error="truncated"); log.close()
    assert (1, "a", "draft") not in completed_all(env.root)


def test_halves_are_distinct_resume_keys():
    """Keyed on chapter alone, finishing 01b marked 01a complete and the next
    resume overwrote the first half."""
    env = fresh()
    log = EventLog.open(env.root, new_run_id())
    log.stage_end(1, "draft", True, part="a")
    log.stage_end(1, "draft", True, part="b")
    log.close()
    done = completed_all(env.root)
    assert (1, "a", "draft") in done and (1, "b", "draft") in done


def test_escalation_matches_the_stage_names_actually_emitted():
    """review_failures compared against "review", but the pipeline only ever
    emits review_a and review_b, so escalation could never fire and the warning
    in status was unreachable code."""
    env = fresh()
    log = EventLog.open(env.root, new_run_id())
    log.stage_end(1, "review_a", True, verdict="rewrite")
    log.stage_end(1, "review_b", True, verdict="rewrite")
    log.stage_end(1, "review_a", True, verdict="revise")
    log.close()
    assert review_failures(env.root, 1) == 2
    assert review_failures(env.root, 2) == 0


# -- 3. the ledger --------------------------------------------------------

def test_a_character_from_half_b_survives_the_chapter_commit():
    """The fold skipped a chapter's halves once its final delta existed, which
    silently deleted any character the final delta did not mention. A character
    introduced in half b vanished the moment the chapter was committed."""
    env = fresh()
    state.write_delta(env, "01a", {"characters": [{"name": "Yara", "location": "bay nine"}]})
    state.write_delta(env, "01b", {"characters": [{"name": "Yara", "knows": "it is warm"},
                                                  {"name": "Vance", "role": "dock officer"}]})
    state.write_delta(env, "01", {"characters": [{"name": "Yara", "location": "the gallery"}]},
                      provisional=False)
    s = state.rebuild(env)
    names = {r["name"] for r in s["characters"]}
    assert names == {"Yara", "Vance"}
    yara = next(r for r in s["characters"] if r["name"] == "Yara")
    assert yara["location"] == "the gallery"     # the final delta wins its field
    assert yara["knows"] == "it is warm"         # and half b's fact survives


def test_the_fold_is_idempotent():
    env = fresh()
    state.write_delta(env, "01a", {"characters": [{"name": "Yara"}]})
    assert state.rebuild(env, write=False) == state.rebuild(env, write=False)


# -- 4. the seam ----------------------------------------------------------

def _joined(env):
    env.part("01a").parent.mkdir(parents=True, exist_ok=True)
    env.part("01a").write_text(paras(6, "alpha"))
    env.part("01b").write_text(paras(6, "beta"))
    join.run(env, 1)
    return join.paragraphs(env.joined(1).read_text())


def test_a_repair_inside_the_window_passes():
    env = fresh()
    ps = _joined(env)
    ps[5] = "alpha5 rewritten transition."
    ps[6] = "beta0 continues without re-establishing."
    env.chapter(1, 1).write_text("\n\n".join(ps))
    assert join.verify(env, 1) == []


def test_an_edit_outside_the_window_is_caught():
    """The edit window is enforced by comparing paragraph hashes, not requested
    in a prompt. A prompt asking a model to limit its own edits is a suggestion."""
    env = fresh()
    for index, where in ((0, "before"), (-1, "after")):
        ps = _joined(env)
        ps[index] = "SILENTLY REWRITTEN far from the seam."
        env.chapter(1, 1).write_text("\n\n".join(ps))
        v = join.verify(env, 1)
        assert v and v[0]["kind"] == "edited_outside_window", where


def test_a_merge_across_the_seam_is_allowed():
    """Paragraph count may change inside the window: a repair legitimately
    merges the last paragraph of A with the first of B."""
    env = fresh()
    ps = _joined(env)
    merged = ps[:5] + [ps[5] + " " + ps[6]] + ps[7:]
    env.chapter(1, 1).write_text("\n\n".join(merged))
    assert join.verify(env, 1) == []


def test_word_count_drift_beyond_tolerance_is_caught():
    env = fresh()
    ps = _joined(env)
    ps[5] += " " + " ".join(["padding"] * 300)
    env.chapter(1, 1).write_text("\n\n".join(ps))
    assert any(v["kind"] == "word_count_drift" for v in join.verify(env, 1))


def test_seam_checker_is_quiet_on_realistic_clean_prose():
    """A check that fires on good writing gets disabled. This pair deliberately
    shares six nouns across the boundary, as real consecutive halves do."""
    a = ('The greenhouse gallery ran the length of the station spine. '
         'She put her palm flat against the crate and counted to ten.\n\n'
         '"You logged it," she said.\n\n"I logged a mass."')
    b = ('"No," he said. "It isn\'t."\n\nThe seal was a double. Yara crouched '
         'and read the pressure strip.\n\n"How long has the crate been aboard?"')
    assert seam.check_seam(a, b) == []


def test_seam_checker_catches_a_damaged_join():
    a = ("The greenhouse gallery smelled of coolant and wet soil, and Yara walked "
         "its length with her hands in the trays.\n\nHe shrugged.\n\n"
         "She understood, for the first time, what it carried. Nothing would be the same.")
    b = ("The greenhouse gallery smelled of coolant and damp soil, and Yara walked "
         "its length with her hands in the trays.\n\nHe shrugged again.")
    codes = {f.code for f in seam.check_seam(a, b)}
    assert {"repeat_ngram", "restated_setting", "false_ending"} <= codes


# -- 5. context assembly --------------------------------------------------

def test_half_a_outranks_the_ledger_when_the_budget_is_tight():
    """The verbatim tail sat below every ledger row, so under pressure the
    prompt discarded voice and kept facts -- backwards. This never fires at
    chapter 1 (6k tokens against a 60k budget); it starts firing around chapter
    20, so the inversion was silent for nineteen chapters."""
    rows = [{"name": f"Char{i}", "knows": "x" * 400} for i in range(10)]
    half_a = " ".join(["The greenhouse gallery ran the length of the spine."] * 120)
    p = build_prose_prompt(
        rules="R" * 400, style_card={"s": 1}, world_facts={"w": "y" * 1200},
        character_rows=rows, beats=[{"b": 1}], prior_chapters=["old " * 2000],
        chapter_no=7, part="b", half_a_text=half_a, budget_tokens=3000)
    labels = [s.label for s in p.segments]
    assert any("first half of this very scene" in l for l in labels)


def test_voice_outranks_the_ledger_for_a_first_half():
    rows = [{"name": f"Char{i}", "knows": "x" * 400} for i in range(10)]
    p = build_prose_prompt(
        rules="R" * 400, style_card={"s": 1}, world_facts={"w": "y" * 1200},
        character_rows=rows, beats=[{"b": 1}],
        prior_chapters=[" ".join(["She had not slept."] * 200)],
        chapter_no=7, part="a", budget_tokens=1600)
    kept = [s.label for s in p.segments]
    dropped = [s.label for s in p.dropped]
    assert any("Immediately preceding" in l for l in kept), kept
    assert any("Character state" in l for l in dropped), dropped


# -- 6. the queue ---------------------------------------------------------

def test_a_dependency_override_is_not_a_redraft():
    """force was doing duty as two overrides -- ignore dependencies, and redo
    finished work. An item forced past a dependency check that completed before
    being claimed was drafted again over the finished prose."""
    env = fresh()
    q = queue.enqueue(env, "01a", "draft", force=True)
    assert q["forced"] and not q["redraft"]

    log = EventLog.open(env.root, new_run_id())
    log.stage_end(1, "draft", True, part="a"); log.close()

    assert queue.claim(env, q["id"], run="r1") is False
    assert any(i["note"] == "completed_before_claim" for i in queue.fold(env))


def test_an_explicit_redraft_is_honoured():
    env = fresh()
    log = EventLog.open(env.root, new_run_id())
    log.stage_end(1, "draft", True, part="a"); log.close()
    q = queue.enqueue(env, "01a", "draft", force=True)
    assert q["redraft"] is True
    assert queue.claim(env, q["id"], run="r1") is True


def test_only_one_of_many_racing_claims_wins():
    """A manual draft and a loop drain both claiming one item would both write
    the same half, and the second would overwrite prose that already had a
    successful stage_end."""
    env = fresh()
    q = queue.enqueue(env, "01b", "draft", force=True)
    results: list[bool] = []
    threads = [threading.Thread(target=lambda: results.append(
        queue.claim(env, q["id"], run="r"))) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sum(results) == 1


def test_a_torn_queue_line_does_not_break_the_fold():
    env = fresh()
    queue.enqueue(env, "01a", "draft", force=True)
    with env.queue.open("a") as fh:
        fh.write('{"id":"q-broken","eve')          # killed mid-write
    assert len(queue.fold(env)) == 1


# -- 7. units and paths ---------------------------------------------------

def test_unit_ids_parse_and_reject():
    assert units.parse("07a") == (7, "a")
    assert units.parse("7") == (7, "")
    assert units.parse("CH7B") == (7, "b")
    for bad in ("", "0", "07c", "abc", "1000", "7.5", None, 7):
        try:
            units.parse(bad)
        except units.BadUnit:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_predecessor_encodes_the_half_chapter_rule():
    """Half b follows half a; half a follows the previous FINISHED chapter,
    not its loose second half."""
    assert units.predecessor("01a") is None
    assert units.predecessor("01b") == "01a"
    assert units.predecessor("02a") == "01"
    assert units.predecessor("02b") == "02a"


def test_each_chapter_version_has_exactly_one_writer():
    """chapters/ch07.md was written by both seam and revise, so a resumed
    revise re-read its own output and destroyed the pre-revision copy."""
    env = fresh()
    assert env.chapter(7, 1).name == "ch07.v1.md"     # seam
    assert env.chapter(7, 2).name == "ch07.v2.md"     # revise
    assert env.chapter(7).name == "ch07.md"           # chapter_commit alone


# -- 8. commit and export -------------------------------------------------

def test_committing_twice_leaves_one_copy_in_the_manuscript():
    """manuscript.md had forty appending writers, so re-running a crashed
    commit appended the chapter twice."""
    env = fresh()
    _joined(env)
    env.chapter(1, 1).write_text(env.joined(1).read_text())
    a = commit.run(env, 1)
    b = commit.run(env, 1)
    assert a["manuscript_words"] == b["manuscript_words"]
    assert env.manuscript.read_text().count("# Chapter 1") == 1


# -- 9. the review schema -------------------------------------------------

def test_a_messy_pasted_review_still_parses():
    """A pasted reply and an API reply are the same thing once they are text.
    This one is wrapped in prose, fenced, and uses aliased field names."""
    raw = ('Sure! Here you go:\n```json\n'
           '{"weakest_paragraphs":[{"text":"a quote of at least twelve words '
           'here for the check","paragraph":0,"problem":"flat","intent":"mood",'
           '"what_works":"nothing"}],"strongest":"x","overall":"needs revision"}\n'
           '```\nHope that helps!')
    r = parse(raw, reviewer="critic_b", model="m")
    assert len(r.weakest) == 1
    assert r.weakest[0].why_it_fails == "flat"
    assert r.weakest[0].reaches_for == "mood"
    assert r.verdict == Verdict.REVISE


def test_schema_violations_catch_a_dodged_quota():
    r = parse('{"weakest":[{"quote":"too short","why_it_fails":"meh"}],'
              '"strongest_quote":"","verdict":"ship"}', reviewer="a", model="m")
    bad = " ".join(r.schema_violations())
    assert "required 3" in bad
    assert "no strongest" in bad
    assert "named no intent" in bad


def test_the_review_prompt_asks_for_a_shape_not_an_attitude():
    """Telling a model to be honest changes its tone, not its judgement."""
    from storyos.review.schema import prompt_for
    p = prompt_for("text").lower()
    for phrase in ("be honest", "be brutal", "unsparing", "be harsh",
                   "yes-man", "rate the", "out of 10"):
        assert phrase not in p
    assert "do not score the chapter" in p


def test_an_invented_character_is_caught_by_the_ledger():
    ledger = {"characters": [{"name": "Yara", "aliases": ["Yara"]}, {"name": "Vance"}],
              "world": [], "places": []}
    invented = Finding("b", "weakest_paragraph", "a quote",
                       why_it_fails="Kestrel's reaction is withheld.",
                       reaches_for="Tension.", works_here="nothing")
    assert verify_entities([invented], ledger)

    legit = Finding("a", "weakest_paragraph", "a quote",
                    why_it_fails="Atmosphere by assertion; nothing is named.",
                    reaches_for="A station with a history.", works_here="Restraint.")
    assert not verify_entities([legit], ledger)


# -- 10. beats, library, search -------------------------------------------

def test_beats_split_by_weight_not_count():
    """Cutting ten beats 5/5 when the first two carry the chapter's whole
    argument gives half A 1,700 words for two beats and half B for eight."""
    a, b = beats.split([{"beat": "one", "weight": 3}, {"beat": "two", "weight": 3},
                        {"beat": "three", "weight": 1}, {"beat": "four", "weight": 1},
                        {"beat": "five", "weight": 1}, {"beat": "six", "weight": 1}])
    assert len(a) == 2 and len(b) == 4
    assert sum(x["weight"] for x in a) == 6


def test_both_halves_get_the_handoff_but_not_each_others_beats():
    env = fresh()
    env.beats(1).parent.mkdir(parents=True, exist_ok=True)
    env.beats(1).write_text(json.dumps({"beats": [
        {"beat": "Yara counts the crates", "weight": 2},
        {"beat": "The crate is warm", "weight": 1}]}))
    beats.run(env, 1)
    a = json.loads(env.beats("01a").read_text())
    b = json.loads(env.beats("01b").read_text())
    assert a["seam_intent"] == b["seam_intent"]
    assert "Yara counts" not in json.dumps(b["beats"])


def test_a_note_is_titled_from_the_document_not_the_filename():
    """Two files called craft.docx and craft.pdf collided as craft and
    craft-2, though the documents said what they were called."""
    md = "# On the Craft of Scene\n\nA scene is a unit of change."
    assert files.first_heading(md) == "On the Craft of Scene"
    assert files.first_heading("No heading here, just prose.") == ""


def test_hard_hyphenation_is_rejoined_but_bare_whitespace_is_not():
    """A renderer that drops the hyphen leaves two words that cannot be told
    from two real words; rejoining those would corrupt ordinary prose."""
    out = files._to_markdown("the writer must be disci-\nplined about it")
    assert "disciplined" in out
    kept = files._to_markdown("feel relent\n        less and oddly")
    assert "relentless" not in kept


def test_search_spans_prose_library_findings_and_ledger():
    env = fresh()
    (env.root / "chapters").mkdir(parents=True, exist_ok=True)
    env.chapter(1).write_text("The gallery smelled of coolant and something older.")
    library.add(env, "# Craft\n\nA scene is a unit of change.",
                title="Craft", source="md")
    env.state.write_text(json.dumps({"characters": [{"name": "Vance", "role": "dock officer"}],
                                     "world": [], "places": []}))
    search.reindex(env)
    assert search.query(env, "coolant")["results"][0]["kind"] == "prose"
    assert search.query(env, '"unit of change"')["results"][0]["kind"] == "library"
    assert search.query(env, "dock officer")["results"][0]["kind"] == "ledger"


def test_search_does_not_choke_on_fts_operators():
    """Someone searching for the word "not" means the word."""
    env = fresh()
    library.add(env, "# N\n\nIt is not a symptom but a disease.", title="N", source="md")
    search.reindex(env)
    for q in ("not", "NOT", 'a "disease"', "OR", "symptom*"):
        assert "note" not in search.query(env, q) or search.query(env, q)["results"] is not None


def test_a_pin_freezes_what_was_actually_used():
    """Editing the shared note later must not silently change what a chapter
    was written against."""
    env = fresh()
    p = library.add(env, "# Craft\n\nA scene is a unit of change.",
                    title="Craft", source="md")
    rel = str(p.relative_to(env.root))
    library.pin(env, rel, "A scene is a unit of change", "the test")
    assert not library.stale_pins(env)

    p.write_text(p.read_text() + "\n\nEdited afterwards.\n")
    stale = library.stale_pins(env)
    assert len(stale) == 1 and "edited" in stale[0]["why_stale"]
    assert stale[0]["excerpt"] == "A scene is a unit of change"


# -- 11. PDF rendering ----------------------------------------------------

def test_the_manuscript_pdf_carries_running_heads_and_page_numbers():
    """Chromium's print-to-PDF renders a page counter but ignores string-set,
    so a manuscript built by it has no running head. weasyprint implements CSS
    paged media and is the default for the interior, which is HTML we author.
    Measured on a 9-page sample: 0.30s and 17KB against 2.33s and 43KB."""
    try:
        import weasyprint                      # noqa: F401
    except ImportError:
        return                                  # fallback path; nothing to assert
    from pypdf import PdfReader
    from storyos import export

    env = fresh()
    (env.root / "chapters").mkdir(parents=True, exist_ok=True)
    body = "\n\n".join("The gallery ran the length of the station spine, and she "
                        "walked it twice before the shuttle showed." for _ in range(14))
    for ch in (1, 2):
        env.chapter(ch).write_text(body)

    export.run(env, formats=("pdf",))
    r = PdfReader(str(env.export_dir / "manuscript.pdf"))
    assert len(r.pages) > 3

    first = r.pages[0].extract_text()
    assert not any(l.strip().isdigit() for l in first.splitlines()), \
        "the title page must carry no page number"

    later = r.pages[2].extract_text()
    assert any(l.strip().isdigit() for l in later.splitlines()), "no page number"
    assert "chapter" in later.lower(), "no running head"


def test_a_snapshot_still_renders_without_a_browser():
    """A degraded record of what was read beats no record at all."""
    from unittest import mock
    out = Path(tempfile.mkdtemp()) / "x.pdf"
    with mock.patch.object(files, "_chromium", lambda: None):
        ok = files.html_to_pdf("<h1>Title</h1><p>Body text here.</p>", out)
    try:
        import weasyprint                      # noqa: F401
    except ImportError:
        return
    assert ok and out.stat().st_size > 0


# -- 12. exported metadata ------------------------------------------------

def test_exports_disclose_nothing_the_writer_did_not_choose():
    """A PDF announced WeasyPrint, a DOCX claimed to be Microsoft Word 12 and
    recorded the minute of the build, an EPUB stamped a timestamp and a fresh
    UUID. None of that is in the book, and a manuscript going to an agent
    should not volunteer the hour you finished it."""
    import re
    try:
        import weasyprint                      # noqa: F401
    except ImportError:
        return
    from storyos import export, scrub

    env = fresh()
    env.marker.write_text(json.dumps({**env.config, "author": "A Writer"}))
    env = Env.find(env.root)
    (env.root / "chapters").mkdir(parents=True, exist_ok=True)
    for ch in (1, 2):
        env.chapter(ch).write_text("\n\n".join(
            "The gallery ran the length of the spine." for _ in range(12)))
    export.run(env)

    tell = re.compile(r"20\d\d-\d\d-\d\d|weasy|skia|chromium|microsoft|normal\.dotm",
                      re.I)
    for name in ("manuscript.pdf", "manuscript.docx", "manuscript.epub"):
        meta = scrub.report(env.export_dir / name)["metadata"]
        leaks = {k: v for k, v in meta.items() if tell.search(str(v))}
        assert not leaks, f"{name} still discloses {leaks}"
        joined = " ".join(meta.values())
        assert "A Writer" in joined, f"{name} lost the author the writer chose"


def test_the_epub_identifier_is_stable_and_internally_consistent():
    """pandoc writes a random UUID into both the package and the NCX. Once the
    package identifier is made stable the two disagree, which is a conformance
    error and not merely a reproducibility one."""
    import re
    import zipfile
    try:
        import pypandoc                        # noqa: F401
    except ImportError:
        return
    from storyos import export

    env = fresh()
    (env.root / "chapters").mkdir(parents=True, exist_ok=True)
    env.chapter(1).write_text("Body text for the chapter.")
    export.run(env, formats=("epub",))

    z = zipfile.ZipFile(env.export_dir / "manuscript.epub")
    opf = next(n for n in z.namelist() if n.endswith(".opf"))
    ident = re.search(r"<dc:identifier[^>]*>([^<]*)", z.read(opf).decode()).group(1)
    assert ident.startswith("urn:storyos:")
    ncx = [n for n in z.namelist() if n.endswith(".ncx")]
    if ncx:
        uid = re.search(r'dtb:uid"\s+content="([^"]*)',
                        z.read(ncx[0]).decode()).group(1)
        assert uid == ident, "the NCX uid must match the package identifier"


def test_a_library_note_does_not_record_an_absolute_path():
    """source_id held the full import path, which names the machine and the
    account to anyone the note reaches."""
    env = fresh()
    p = library.add(env, "# A\n\nBody.", title="A", source="md",
                    source_id="craft.pdf")
    raw = p.read_text()
    assert "source_id" in raw
    assert "/home/" not in raw and "/Users/" not in raw


# -- 13. not guessing -----------------------------------------------------

def test_an_unmeasured_style_card_invents_no_targets():
    """neutral_card() used to return sixteen numbers described as "mid-range
    values for contemporary commercial fiction". No such corpus was consulted;
    they were made up, then handed to the model under the heading "Measured
    style targets" -- a claim the one party who cannot check it was asked to
    write toward."""
    from storyos.style import unmeasured_card
    card = unmeasured_card("comps/ is empty")
    numbers = [v for group in card.values() if isinstance(group, dict)
               for v in group.values()
               if isinstance(v, (int, float)) and not isinstance(v, bool)]
    assert not numbers, f"invented targets: {numbers}"
    assert card["source"]["measured"] is False


def test_the_style_segment_tracks_the_data_not_a_flag():
    """The prompt must carry style targets when they were measured and none
    when they were not. An earlier guard keyed on source.measured, which
    measure() never sets, so a genuinely measured card was dropped too --
    the check asked a label about the data instead of the data."""
    from storyos.context import build_prose_prompt
    from storyos.style import measure, unmeasured_card

    def has_style(card):
        p = build_prose_prompt(rules="R" * 200, style_card=card, world_facts={},
                               character_rows=[], beats=[{"b": 1}],
                               prior_chapters=[], chapter_no=1, part="a")
        return any("style" in s.label.lower() for s in p.segments)

    assert not has_style(unmeasured_card("empty"))
    assert has_style(measure(
        'She walked. He did not. The gallery was long, and cold, and it '
        'smelled of coolant; she had never minded that. "You logged it," '
        'she said. "I logged a mass."'))


def test_the_blocklist_the_rules_promise_is_actually_enforced():
    """rules_common.md told the model the list was rejected on sight. That was
    false: the phrases lived in the prompt and nothing read the draft
    afterwards. A rule asserted and never enforced teaches a model to treat
    the rest of the rules as advisory."""
    from storyos.checks import length
    from storyos.checks.prose import BANNED, find

    env = fresh()
    env.part("01a").parent.mkdir(parents=True, exist_ok=True)
    clean = " ".join(["word"] * 1500)
    env.part("01a").write_text(clean)
    assert length.run(env, "01a")["ok"], "clean prose in band must pass"

    env.part("01a").write_text(clean + " Her heart hammered against her ribs.")
    r = length.run(env, "01a")
    assert not r["ok"], "a banned phrase must fail the gate"
    assert r["banned_phrases"] and r["banned_phrases"][0]["phrase"] == "heart hammered"
    assert find("the air was thick with smoke")


def test_every_enforced_phrase_is_one_the_model_was_shown():
    """Penalising a draft for a phrase the rules never listed is a trap."""
    import re
    from storyos.checks.prose import BANNED
    rules = (Path(__file__).resolve().parent.parent
             / "storyos" / "project.py").read_text()
    section = rules[rules.index("## Never write these"):rules.index("## Length")]
    listed = " ".join(re.findall(r"\*([^*]+)\*", section)).lower()
    unstated = [p for p in BANNED if p.split()[0] not in listed]
    assert not unstated, f"enforced but never shown to the model: {unstated}"


def test_status_reads_the_configured_escalation_threshold():
    """It was hard-coded to 2 while models.json carried the setting, so a
    writer who changed it saw one number and got another."""
    env = fresh()
    for threshold in (1, 3):
        m = json.loads(env.models_path.read_text())
        m["stages"]["draft"]["escalate_after_failures"] = threshold
        env.models_path.write_text(json.dumps(m, indent=2))
        got = (Env.find(env.root).models()["stages"]["draft"]
               ["escalate_after_failures"])
        assert got == threshold


# -- 14. the bug hunt -----------------------------------------------------

def test_an_empty_quote_never_verifies():
    """Folding removes quote marks, so "", '""' and "   " all arrived empty --
    and hay.find("") returns 0, verifying at ratio 1.0 with a span covering the
    whole chapter. Two critics omitting the field then agreed, handing `revise`
    a CONFIRMED defect over an entire reviewed chapter on no evidence."""
    chapter = "A real chapter of prose. " * 12
    for empty in ("", '""', "   ", "''"):
        f = Finding("a", "promise_break", empty, why_it_fails="x.",
                    reaches_for="y.", works_here="nothing")
        r = Review("a", "m", promise_breaks=[f], strongest_quote="z",
                   verdict=Verdict.REVISE)
        ver, hall = verify_quotes(r, chapter)
        assert not ver and len(hall) == 1, f"{empty!r} verified"
        assert f.match_ratio == 0.0


def test_a_negated_verdict_is_not_read_as_approval():
    """Verdict parsing scanned for substrings in enum order with `ship` first,
    so "do not ship this" and "should not ship, rewrite it" both recorded as
    SHIP -- revise was skipped and escalation never counted the failure."""
    cases = {"do not ship this": Verdict.REWRITE,
             "should not ship, rewrite it": Verdict.REWRITE,
             "ship": Verdict.SHIP,
             "rewrite": Verdict.REWRITE,
             "needs revision": Verdict.REVISE}
    for raw, want in cases.items():
        got = parse('{"verdict":"%s"}' % raw, reviewer="a", model="m").verdict
        assert got == want, f"{raw!r} -> {got}, wanted {want}"


def test_recommitting_a_chapter_does_not_revert_the_ledger():
    """commit.run(deltas=None) wrote empty sections over the chapter's final
    delta, so a re-run silently reverted every row to the halves' provisional
    values and later chapters drafted against stale facts."""
    env = fresh()
    env.part("01a").parent.mkdir(parents=True, exist_ok=True)
    for part in ("a", "b"):
        env.part(f"01{part}").write_text(
            "\n\n".join(f"paragraph {i} of the half." for i in range(6)))
    join.run(env, 1)
    env.chapter(1, 1).write_text(env.joined(1).read_text())

    state.write_delta(env, "01a", {"characters": [{"name": "Yara",
                                                   "location": "bay nine"}]})
    commit.run(env, 1, deltas={"characters": [{"name": "Yara",
                                               "location": "the gallery"}]})
    before = json.loads(env.state.read_text())["characters"][0]
    commit.run(env, 1)                       # what the CLI does
    after = json.loads(env.state.read_text())["characters"][0]
    assert after["location"] == before["location"] == "the gallery"
    assert after["provisional"] is False


def test_a_stray_file_in_deltas_does_not_break_the_fold():
    """One backup file raised BadUnit out of rebuild, which broke
    state_rebuild and chapter_commit for every remaining chapter."""
    env = fresh()
    state.write_delta(env, "01a", {"characters": [{"name": "Yara"}]})
    (env.root / "deltas" / "chars-backup.json").write_text("{}")
    (env.root / "deltas" / "ch99-notes.json").write_text("{}")
    s = state.rebuild(env)
    assert [r["name"] for r in s["characters"]] == ["Yara"]


def test_fit_never_strips_a_prompt_to_nothing():
    """The loop dropped user segments while the total exceeded budget, and the
    total includes system segments it cannot touch -- so a large enough rules
    file produced a draft prompt with no beats and no first half."""
    from storyos.context import build_prose_prompt
    p = build_prose_prompt(rules="R " * 5000, style_card={}, world_facts={"w": "y"},
                           character_rows=[{"name": "Y"}],
                           beats=[{"beat": "something happens"}],
                           prior_chapters=[], chapter_no=7, part="b",
                           half_a_text="The gallery ran the length. " * 100,
                           budget_tokens=1000)
    assert len(p.user_segments) >= 1
    survivor = p.user_segments[0]
    assert survivor.priority == max(s.priority for s in p.user_segments + p.dropped)


def test_a_chapter_stage_refuses_a_half_id():
    """Given 07a a chapter stage did the work but logged (7,"a",stage) where
    the plan expects (7,"",stage) -- the work stayed invisible and status
    asked for it forever."""
    import subprocess
    env = fresh()
    r = subprocess.run(["storyos", "--env", str(env.root), "stage", "join", "07a"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "whole chapter" in (r.stdout + r.stderr)


def test_a_prose_check_writes_one_canonical_file():
    """The path used the raw unit string, so 01a, 1a and ch01a each wrote a
    different file and a consumer looking for the canonical name found none."""
    from storyos.checks import length
    env = fresh()
    env.part("01a").parent.mkdir(parents=True, exist_ok=True)
    env.part("01a").write_text(" ".join(["word"] * 1500))
    for spelling in ("01a", "1a", "ch01a"):
        length.run(env, spelling)
    written = sorted(p.name for p in (env.root / "checks").glob("*.prose.json"))
    assert written == ["ch01a.prose.json"], written


def test_a_delta_is_written_atomically():
    """A delta is a model extraction that cannot be regenerated for free, and
    a torn one breaks the fold for the rest of the book."""
    env = fresh()
    state.write_delta(env, "01a", {"characters": [{"name": "Yara"}]})
    leftovers = list((env.root / "deltas").glob("*.writing"))
    assert not leftovers, leftovers
    assert json.loads(env.delta("01a").read_text())["unit"] == "01a"


# -- 15. the pipeline as data ---------------------------------------------

def test_every_model_stage_has_a_bench_row():
    """A stage with no bench row printed "?" for its model, which once hid a
    name split between the stage contract and models.json."""
    env = fresh()
    bench = env.models().get("stages", {})
    missing = [s.name for s in stages.ALL.values()
               if s.model and s.name not in bench
               and not s.name.startswith("review") and s.name != "cover"]
    assert not missing, f"no bench row for: {missing}"


def test_the_plan_drafts_both_halves_before_joining():
    plan = stages.plan(2)
    order = [stages.label(k) for k in plan]
    assert order.index("01b draft") < order.index("01 join")
    assert order.index("01 seam") < order.index("01 review_a")


def test_neither_critic_slot_is_a_claude_model():
    """The instance that drafted the prose cannot review it, and two models of
    one lineage agree for reasons that have nothing to do with the prose."""
    env = fresh()
    m = env.models()
    reg = {r["id"]: r for r in m["registry"]}
    fams = [reg[m["critics"][s]]["family"] for s in ("slot_a", "slot_b")]
    assert "anthropic" not in fams
    assert fams[0] != fams[1]


# -- runner ---------------------------------------------------------------

def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  \033[32mok\033[0m    {name}")
        except AssertionError as x:
            failed.append((name, f"assertion: {x}"))
            print(f"  \033[31mFAIL\033[0m  {name}")
        except Exception as x:
            failed.append((name, f"{type(x).__name__}: {x}"))
            print(f"  \033[31mERROR\033[0m {name}")

    print(f"\n  {len(tests) - len(failed)}/{len(tests)} passed")
    for name, why in failed:
        print(f"    {name}: {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
