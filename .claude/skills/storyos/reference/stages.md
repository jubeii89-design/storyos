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
| 6 | `style_card` | `comps/*.txt`, the user's own prose | `style.json` — measured, not described | yes |
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
| 13 | `delta_extract` | the draft | `state/deltas/ch07a.json`, `provisional: true` | yes, cheap |
| 14 | `state_apply` | the delta | `state.json` | **no** |

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
`seam` and `revise` may still change the prose they came from;
`chapter_commit` re-extracts from the finished chapter and overwrites them.

## Join — `part=""`

| # | stage | inputs | output | model |
|---|---|---|---|---|
| 15 | `join` | both halves | `chapters/joined/ch07.md`, `chapters/joined/ch07.seam.json` | **no** |
| 16 | `seam_check` | both halves | findings in `events.jsonl` | **no** |
| 17 | `seam` | the joined file, `state.json`, `beats/ch07.json#seam_intent` | `chapters/ch07.md` | yes |
| 18 | `seam_verify` | `chapters/ch07.md`, `ch07.seam.json` | `checks/ch07.seam.json` | **no** |

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
| 19 | `review_a` / `review_b` | `chapters/ch07.md`, `outline.json`, `state.json` | `review/ch07.a.json`, `.b.json` | yes, two |
| 20 | `reconcile` | both reviews, the chapter | `review/ch07.reconciled.json` | **no** |
| 21 | `revise` | the chapter, confirmed findings only | `chapters/ch07.md` v2, `chapters/ch07.v1.md` | yes |
| 22 | `chapter_commit` | `chapters/ch07.md` | `state.json`, `manuscript.md` | **no** |

Reviewers read one continuous chapter, never two halves — a reviewer shown a
seam will spend all three of its findings on the seam. That is why `seam`
precedes review. Reviewer B receives `shuffled_paragraphs()` output.

`revise` acts on confirmed findings only. Contested findings are the user's
call and wait in the UI.

## Output

| # | stage | inputs | output | model |
|---|---|---|---|---|
| 23 | `cover` | `dossier.md`, `genre.json` | `cover/front.pdf`, `cover/back.pdf` | `bookcover` skill |
| 24 | `export` | `manuscript.md`, `cover/*` | `export/manuscript.docx`, `.pdf` | **no** — pandoc |

`export` runs pandoc with `--reference-doc` against a manuscript-format template
so the DOCX arrives in standard submission shape rather than needing an
afternoon of manual styling.
