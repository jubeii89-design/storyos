---
name: storyos
description: Drive a novel from premise to manuscript in half-chapter units. Use when the user asks to brainstorm a story, build a dossier, outline chapters, draft or continue prose, join two half-chapters, run the two-reviewer proofread, design a cover, or export DOCX/PDF. Also use when the user names a project slug and a stage, or says "draft the next half", "drain the queue", or "/storyos".
---

# Story OS

A writing partner that takes a premise to a finished manuscript. No API keys, no
server, no per-token billing: every model call is a Claude Code turn or a
subagent spawn under the user's subscription.

The browser UI (`python -m storyos.ui`) is the reading, editing and approval
surface. It never calls a model — it reads and writes files in the project
directory. You are the engine. The two halves meet at
`projects/<slug>/queue.jsonl`.

## The half-chapter rule

Prose is **never** drafted a whole chapter at a time. Each chapter is two halves
of 1,500–1,750 words (6–7 manuscript pages at 250 words/page). Draft `07a`, then
`07b`, then join them into `07`.

This is not a budget decision. A single 3,500-word generation flattens its own
pacing: the back half compresses toward summary because the model is managing an
ending it can see coming. At 1,700 words the prose stays at scene density
throughout. Two halves also give a mid-chapter checkpoint, so a bad chapter costs
one unit to rewrite instead of two.

The cost of the rule is **the seam**, and it is real. Independently drafted halves
re-establish setting, repeat gestures, restate information, and give half A a
false ending. Never join with concatenation. The join is its own stage with its
own checks.

## Stages

Read `reference/stages.md` for the full file contract. Summary:

| Stage | Model? | Writes |
|---|---|---|
| `brainstorm` | yes, conversational | `brainstorm.md` |
| `dossier` | yes | `dossier.md` |
| `cast`, `world` | yes | `characters/*.json`, `world/*.json` |
| `outline` | yes | `outline.json` (40 chapters) |
| `beats` | yes | `beats/<ch><half>.json` |
| `draft` | yes | `halves/<ch><half>.md` |
| `gate0` | **no — Python** | findings in `events.jsonl` |
| `join` | yes, narrow | `chapters/<ch>.md` |
| `review` | yes, two models | reconciliation in `events.jsonl` |
| `lineedit` | yes | `chapters/<ch>.md` (revised) |
| `cover` | delegated skill | `cover/front.pdf`, `cover/back.pdf` |
| `export` | **no — pandoc** | `export/manuscript.docx`, `.pdf` |

Every stage appends to `events.jsonl` via `storyos.events`. Never write a stage
result without emitting `stage_start` and `stage_end` — resume is derived by
replaying that log, so an unlogged stage will silently redo or skip.

## Drafting a half

1. Build the prompt with `storyos.context.build_prose_prompt`. Never with an
   f-string, so the UI's Prompt Inspector can show which segment caused a bad
   half.
2. For half **b**, half **a** goes in **verbatim and whole** — all 1,700 words,
   highest `VERBATIM` priority. This is the one place full prose is worth its
   tokens; a summarised half A produces a visible discontinuity.
3. Half b's rules segment must carry: you are continuing mid-scene; do not
   re-establish the setting; do not resolve anything; the final sentence of half
   A is your immediate antecedent.
4. Half a's rules segment must carry: you are writing the first half of a scene
   that continues; do not land an ending.

## Joining

Run `python -m storyos.checks.seam halves/<ch>a.md halves/<ch>b.md` first. It is
free and it answers the questions a model would only guess at: repeated n-grams
across the boundary, restated setting, repeated gesture, false ending.

Then edit **only the ±300 words around the join**. Do not touch prose outside
that window, and do not "improve" either half while you are in there — that
silently discards work the reviewers already passed. Write the result to
`chapters/<ch>.md` and record the join offset in `events.jsonl` so the UI can
show where the seam was.

## Proofreading with two models

`models.json` holds the bench; `reference/models.md` is its contract. Two rules
are enforced in code rather than left to the UI:

**No Claude model may hold a critic slot.** The instance that drafted the prose
cannot review it, and neither can its siblings — self-preference is the
best-documented failure mode in LLM-as-judge setups. **The two slots may not
share a `family`,** because two models of one lineage agree for reasons that have
nothing to do with the prose.

The default pair is two local Ollama models of different families, which costs
nothing and runs unlimited. When slot B is a keyed model, set
`"slot_b_policy": "on_contest"` so it is spent only on chapters where slot A and
the free Python gate disagree.

Spawn both in the same block so neither sees the other's output, and give
reviewer B `shuffled_paragraphs()` output to defeat position bias. Reconcile with
`storyos.review.reconcile` — never ask a third model to adjudicate, because a
third judge reintroduces every bias the blind pairing removes. Contested findings
go to the writer, who is the tiebreaker.

### Never ask a reviewer to be honest

It changes tone and not judgment. The honesty is structural:

- A fixed quota of three weakest paragraphs. There is no way to be agreeable when
  the task requires naming a worst.
- No rating scale. Scores collapse to 7/10 and drift up with length.
- Every claim carries a verbatim quote, string-matched by `verify_quotes`. A
  quote that is not in the chapter costs the reviewer its finding.
- Exactly one strongest paragraph, as a calibration probe. A reviewer that cannot
  name a strength is pattern-matching rather than reading.

### Every finding says what the passage was reaching for

Each weakest-paragraph finding carries three fields, not one:

- `why_it_fails` — the defect, in one sentence, tied to the quote.
- `what_it_reaches_for` — the effect the passage was going after.
- `what_works_here` — the part that should survive a revision, or the literal
  string `nothing` if there genuinely is none.

This is not softening. It is what makes `lineedit` usable: a finding that says
only "this is flat" licenses the editor to replace the passage with something
that loses whatever the writer was actually doing. It is also a hallucination
guard, because a reviewer that invented the defect cannot describe the intent
behind it without contradicting itself, and the contradiction is visible.

### Hallucination guards

Four, in order of how much they catch:

1. `verify_quotes` — the quote must literally appear. Discarded findings are
   logged, not silently dropped, so a reviewer that invents repeatedly is visible
   in its own record.
2. Entity check — any proper noun in a finding must exist in `state.json`. A
   reviewer inventing a character to complain about is caught in Python.
3. No outside authority — a reviewer may cite only craft documents that were in
   its context. A finding grounded in a book it was not given is discarded.
4. `para_index` must agree with the quote's located span. A reviewer that quotes
   accurately but points at the wrong paragraph did not read in order.

## Covers

Delegate to the `bookcover` skill. It builds front and back from typography and
CSS rendered through headless Chromium, because no image model is reachable
without a key — so covers are typographic and abstract, which suits literary and
thriller and does not suit fantasy or romance. Tell the user that before they
spend an afternoon on it.

## Draining the queue

The UI appends one JSON object per line to `queue.jsonl`. When asked to drain:
read it, execute the first entry whose `(chapter, half, stage)` is not already
complete per `completed_stages()`, append the result, and stop. One unit per
turn — do not drain the whole queue in one turn, because a failure halfway
through leaves the log ambiguous about what to retry.

For unattended runs the user starts `/loop 3m /storyos drain`.

## Reference

- `reference/stages.md` — the file contract for every stage
- `reference/genres.md` — the genre contracts and what each one obliges
- `reference/craft.md` — the prose standards applied at `lineedit`
