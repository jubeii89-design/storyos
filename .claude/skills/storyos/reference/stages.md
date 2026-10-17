# Stage contract

All paths relative to `projects/<slug>/`. Every stage emits `stage_start` and
`stage_end` to `runs/<run_id>/events.jsonl`. The resume key is the tuple
`(chapter, part, stage)` where `part` is `"a"`, `"b"`, or `""` for chapter-level
and book-level work. Book-level stages use `chapter=0`.

Stages marked **no** must never be given to a model. A model asked what Python
can answer will guess, and its guess will be plausible.

## Book level — `chapter=0`, `part=""`

| # | stage | inputs | output | model |
|---|---|---|---|---|
| 1 | `premise` | human | `premise.md` | no |
| 2 | `genre` | `premise.md`, `reference/genres.md` | `genre.json` — the contract the book has signed | yes |
| 3 | `brainstorm` | `premise.md`, `genre.json` | `brainstorm.md` — conversational, multi-turn | yes |
| 4 | `dossier` | `brainstorm.md` | `dossier.md` | yes |
| 5 | `bible` | `dossier.md` | `state.json` (`#world`, `#characters` seeded) | yes |
| 6 | `style_card` | `comps/*.txt`, your own prose | `style.json` — measured, not described | **no** |
| 7 | `outline` | `dossier.md`, `state.json`, `genre.json` | `outline.json`, 40 entries | yes |

`style.json` holds numbers taken from the user's own writing — sentence length
distribution, dialogue share, paragraph length, adverb rate. A style card written
as adjectives ("lyrical but spare") constrains nothing.

### `state.json` shape

Every section is a **list of flat dicts**, never a nested tree. `#characters` in
a `source` string is a pointer for the inspector, not a JSON path.

```json
{
  "characters": [
    { "name": "Yara Okonkwo", "aliases": ["Yara"], "location": "greenhouse gallery",
      "knows": "the crate is warm", "not_said": "that she recognised the seal",
      "provisional": false, "first_seen": "01a" }
  ],
  "world": [
    { "name": "greenhouse gallery", "fact": "runs the length of the station spine",
      "established": "01a" }
  ],
  "places": []
}
```

Flat because two things read it mechanically: `context.build_prose_prompt` turns
rows into `STRUCTURED` segments, and `review.verify_entities` collects every
`name` and `aliases` value to decide whether a reviewer invented a character. A
nested shape breaks the second silently — it finds no names, matches nothing, and
passes every fabrication. Keep it flat and both stay honest.

## Chapter beats

| # | stage | inputs | output | model |
|---|---|---|---|---|
| 8 | `beats` | `outline.json`, `state.json` | `beats/ch07.json` | yes |
| 9 | `split_beats` | `beats/ch07.json` | `beats/ch07a.json`, `beats/ch07b.json` | **no** |

`split_beats` assigns beats to halves and writes `seam_intent` into both files:
what must be true at the handoff. Both halves read it, so half B knows what state
it inherits without being shown half A's beats.

## Half unit — `part="a"` then `part="b"`

| # | stage | inputs | output | model |
|---|---|---|---|---|
| 10 | `context_pack` | `beats/ch07a.json`, `state.json`, `style.json`, `prompts/rules.md`, `chapters/parts/ch06b.md` | `runs/<run>/prompts/ch07a.json` | **no** |
| 11 | `draft` | the prompt pack | `chapters/parts/ch07a.md` | yes |
| 12 | `length_gate` | the draft | `checks/ch07a.words.json` | **no** |
| 13 | `delta_extract` | the draft | `deltas/ch07a.json`, `provisional: true` | yes, cheap |
| 14 | `state_rebuild` | every `deltas/*.json` | `state.json` (derived) | **no** |

`context_pack` calls `context.build_prose_prompt`. For `part="b"` it passes half
A **whole and verbatim** at top `VERBATIM` priority; for `part="a"` it passes
`verbatim_tail` of the previous chapter's half B.

`length_gate` targets 1,500–1,750 words. Fail the stage and redraft only below
1,350 or above 2,000 — a redraft of a 1,480-word half throws away good prose to
satisfy a round number. Between the bands, warn and continue.

`delta_extract` runs for half A **before** half B drafts. Continuity breaks
inside a chapter more often than across one, and this is the only point where the
fix is cheap: B receives A's facts as `STRUCTURED` rows and A's prose as
`VERBATIM`, so B cannot contradict A. The rows are marked provisional because
`seam` and `revise` may still change the prose they came from; `chapter_commit`
writes a final non-provisional `deltas/ch07.json`, and the fold prefers it.

`state_rebuild` is a fold, not an update — it reads every delta in numeric order
and replays them. That is why a crash mid-chapter is recoverable: the deltas on
disk are each written once by one stage, so the ledger can always be
reconstructed from them, whereas a `state.json` mutated in place can be left in
a state no replay explains.

`deltas/` sits at the top of the project rather than under `state/` for a dull
but real reason: `state.json` beside a `state/` directory breaks tab completion
in the terminal where the writing actually happens.

## Join — `part=""`

| # | stage | inputs | output | model |
|---|---|---|---|---|
| 15 | `join` | both halves | `chapters/joined/ch07.md`, `chapters/joined/ch07.seam.json` | **no** |
| 16 | `seam_check` | both halves | findings in `events.jsonl` | **no** |
| 17 | `seam` | the joined file, `state.json`, `beats/ch07.json#seam_intent` | `chapters/ch07.v1.md` | yes |
| 18 | `seam_verify` | `chapters/ch07.v1.md`, `ch07.seam.json` | `checks/ch07.seam.json` | **no** |

`ch07.seam.json` records the seam character offset, the edit window bounds, and a
hash per paragraph. `seam_check` is `storyos.checks.seam` — repeated n-grams
across the boundary, restated setting, repeated gesture, false ending.

The `seam` stage may edit, **inside the window only** (last 2 paragraphs of A,
first 3 of B): the transition sentence, duplicated stage business, a repeated
re-introduction of a character or setting, time and tense cues, pronoun
antecedents, and the paragraph break itself.

It must not touch a byte outside the window, add or remove or reorder plot
events, change a line of dialogue, introduce a named entity absent from
`state.json`, or move total word count by more than ±150. `seam_verify` enforces
all five by comparing paragraph hashes — the constraints are checked, not
requested, because a prompt that asks a model to restrict its own edits is a
suggestion.

## Review — `part=""`

| # | stage | inputs | output | model |
|---|---|---|---|---|
| 19 | `review_a` / `review_b` | `chapters/ch07.v1.md`, `outline.json`, `state.json` | `review/ch07.a.json`, `.b.json` | yes, two |
| 20 | `reconcile` | both reviews, the chapter | `review/ch07.reconciled.json` | **no** |
| 21 | `revise` | `chapters/ch07.v1.md`, confirmed findings only | `chapters/ch07.v2.md` | yes |
| 22 | `chapter_commit` | the highest existing version | `chapters/ch07.md`, `deltas/ch07.json` | **no** |

Reviewers read one continuous chapter, never two halves — a reviewer shown a
seam will spend all three of its findings on the seam. That is why `seam`
precedes review. Reviewer B receives `shuffled_paragraphs()` output.

`revise` acts on confirmed findings only. Contested findings are the user's
call and wait in the UI.

## Output

| # | stage | inputs | output | model |
|---|---|---|---|---|
| 23 | `cover` | `dossier.md`, `genre.json` | `cover/front.pdf`, `cover/back.pdf` | `bookcover` skill |
| 24 | `export` | `chapters/ch*.md`, `cover/*` | `manuscript.md`, `export/manuscript.docx`, `.pdf` | **no** — pandoc |

`export` runs pandoc with `--reference-doc` against a manuscript-format template
so the DOCX arrives in standard submission shape rather than needing an
afternoon of manual styling.

## One writer per file

Every output above is written by exactly one stage. This is not tidiness; it is
what makes resume correct, and three places in an earlier draft of this contract
broke it:

- **`chapters/ch07.md` was written by both `seam` and `revise`.** A crash after
  `revise`, then a resume, would have re-read the already-revised text, revised
  the revision, and overwritten the only copy of the pre-revision prose — with
  `seam_verify`'s paragraph hashes then matching nothing. Now `seam` writes
  `v1`, `revise` writes `v2`, and only `chapter_commit` writes the name you type.
- **`state.json` was written by both `state_apply` and `chapter_commit`.** A
  crash between half A's apply and half B's leaves a half-applied ledger that
  replay cannot repair, because `completed_stages` correctly reports the stage
  as finished. Now `deltas/ch07a.json` is the only written truth — one writer
  per file — and `state.json` is a fold rebuilt by `storyos.state.rebuild()`.
- **`manuscript.md` had forty appending writers.** Re-running `chapter_commit`
  for chapter 12 after a crash appends chapter 12 twice, and draining out of
  order interleaves chapters. Now `export` concatenates `chapters/ch*.md` in
  numeric order.

The general rule: **a stage may append to `events.jsonl` and write its own
files. Nothing else.** Derived files — `state.json`, `manuscript.md`,
`library/index.sqlite3` — are rebuilt from those, are safe to delete, and are
gitignored.

## Where these files live

One environment directory, which **is** the book. `.storyos` at its root marks it.

```
~/TheLongOrbit/
  .storyos  .env  models.json    the bench, and the only place a key exists
  prompts/ reference/ templates/ rules, genre contracts, the DOCX reference
  library/notes/*.md             craft knowledge, readable and pinnable
  library/index.sqlite3          FTS5 search — derived, gitignored
  premise.md dossier.md genre.json style.json outline.json
  beats/ chapters/ deltas/ checks/ review/ cover/ export/ runs/
  state.json manuscript.md       derived, rebuildable, gitignored
```

There is no project list and no slug. The directory you are standing in is the
book you are writing, so `storyos draft 07a` needs no argument naming which
book, and there is no way to write a half into the wrong one. A second book is a
second directory with its own `.storyos`, its own library and its own bench —
nothing is shared, and nothing can leak between them.

The environment is found by explicit argument, then `$STORYOS_ENV`, then by
walking up from the working directory for the marker. The walk is last because
it is the convenient one, and convenience should not silently beat a stated
intention.

`storyos/paths.py` resolves every path above. Nothing in the system builds a
path by string concatenation, and nothing parses a unit id outside
`storyos/units.py` — two implementations of one grammar is the defect class that
cost this project its quote verification, and it will not be repeated for
something as load-bearing as which chapter a file belongs to.
