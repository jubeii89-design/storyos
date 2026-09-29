# STORY-OS v001: prose production pipeline, foundation layer (claude, 2026-09-29)

User request: fix the Story OS — leaked credentials, a blueprint resting on an
alpha SDK, all-prose-in-every-prompt context handling, regex-parsed pseudo-XML —
then design a personal UI, with an honest verdict on building it in Unreal.

**Deliverable so far:** the four load-bearing modules, each self-tested. No
pipeline runner yet, no UI yet. Nothing here calls a paid API.

## Files

| File | What |
|---|---|
| `.gitignore` | Blocks `.env`, `*.key`, `secrets/`, and `projects/*/runs/`. `.env.example` is explicitly un-ignored. |
| `.env.example` | Documents all five keys (Anthropic, z.ai, OpenRouter, Ollama, Notion) with no values. Copy to `.env`. |
| `.githooks/pre-commit` | Runs the secret scanner; a commit carrying a credential fails. Wired via `git config core.hooksPath .githooks`. |
| `tools/scan_secrets.py` | 12 vendor key patterns + a generic assigned-secret rule. Masks findings so its own output is safe to paste. `# pragma: allowlist` opts a line out. Exit 1 on any hit. |
| `storyos/providers/base.py` | `Provider` ABC, `ModelSpec` with per-tier pricing, `Usage`, `Completion`. Retry with exponential backoff on transient errors; cost and truncation computed per call. Vendor SDKs are never imported outside a provider subclass. |
| `storyos/context.py` | Prompt assembly as labeled `Segment`s with fidelity levels and token accounting. `fit()` drops lowest-priority segments to meet a budget. `verbatim_tail()` takes whole paragraphs only. `build_prose_prompt()` is the tiered-fidelity prose prompt. |
| `storyos/events.py` | Append-only `runs/<id>/events.jsonl`. `completed_stages()` derives the resume key by replay; `run_totals()` rolls up cost per stage. fsync per line. |

Self-test each module: `python3 tools/scan_secrets.py .` — the others have smoke
tests in the session transcript, not yet extracted into `tests/`. See Limits.

## Decisions

- **Secrets come only from the environment.** They were previously in a shared
  Google Doc; two Anthropic keys and a Notion token must be treated as public
  and rotated. Moving a leaked key to a new home is not a fix.
- **The blueprint's stack is real; the dependency is still wrong.** `pip install
  google-antigravity` does exist (Google LLC, Apache 2.0) — I was wrong to call
  it fabricated. But it is **v0.1.20, Development Status: Alpha**, with a
  compiled per-platform runtime binary. An alpha SDK must not be the layer your
  writing tool cannot start without. Story OS runs on plain Python; Antigravity
  may be added later as an optional dispatcher, behind the provider interface.
- **Cost is not the reason to trim context.** A full 40-chapter draft resending
  the growing prefix is ~3.2M input tokens: $4.48 on GLM-5.2, $12.80 on Opus
  5.5, less with caching. The real cost is **attention dilution** — at 160k
  tokens of prior prose, chapter 7's facts and chapter 39's cadence compete for
  the same budget. Hence tiered fidelity: verbatim tail for voice, structured
  ledger rows for facts.
- **Dropping a whole segment beats truncating one.** Half a character ledger is
  worse than none, because the model fills the gap confidently and wrongly.
- **JSON for state, markdown headings for prompts, no pseudo-XML.** The
  blueprint's `<world_bible>` tags were regex-parsed, which breaks the first
  time dialogue contains `<`. Models follow `## Heading` at least as well and
  nothing has to parse it back.
- **The event log is the only channel to any UI.** Progress on disk means
  refresh is free, resume is replay, and a crash leaves a complete record.
  There is no second source of truth to drift.
- **Files are authoritative over any cache.** A chapter edited by hand outside
  the pipeline must win; sidecar `content_sha256` detects it and marks
  downstream findings stale rather than deleting them.
- **UI: FastAPI + Jinja + HTMX + SSE on `127.0.0.1:8730`.** Four dependencies,
  no build step. Browser for review, your own editor for writing, linked by
  `vscode://file/<abs>:<line>`. The screen that justifies the project is the
  Prompt Inspector — segment token shares plus a run-to-run prompt diff —
  because every bad chapter is a bad context.
- **Not Unreal, for the UI.** UE 5.8 has no first-party editable rich-text
  widget (`RichTextBlock` is display-only, `EditableTextBox` is plain text),
  UMG does not inherit OS-native text behaviour, clipboard needs a paid
  marketplace plugin, IME is documented-poor, and the install is ~114 GB. No
  documented document-editing app has been built in it. A text tool consumes
  none of what Unreal is good at.
- **Unreal is right for previz, downstream.** Seam: Story OS emits
  `projects/<slug>/previz/shot-plan.json` in the schema the Blender pipeline
  already uses; Unreal consumes it via the Remote Control API (HTTP :30010),
  which avoids the editor-only-Python problem entirely. Screen direction and
  the 180-degree line stay in Python and are validated deterministically
  against exported camera transforms. Unreal renders; it never owns state.

## Limits

- **No runner, no stages, no UI.** Outline/Beats/Prose/Verify are designed and
  their contracts fixed, but unimplemented. `build_prose_prompt()` has no
  caller yet.
- **No provider subclass exists.** `base.py` defines the interface; the
  Anthropic and OpenAI-compatible implementations are not written, so nothing
  can reach a model. Zero API calls have been made.
- **Token counts are estimates**, ~3.6 chars/token biased pessimistic. Good
  enough for budget decisions, wrong for billing. `Prompt.recount()` accepts a
  real tokenizer; none is wired.
- **No `tests/` directory.** Every module was smoke-tested in-session and the
  outputs checked by hand, but those tests are not committed and will rot.
  This is the largest gap.
- **The style fingerprint is designed, not built.** `style.json`, the extractor
  that measures your own prose, and the verifier that checks drift against it
  are the highest-value unbuilt piece.
- **Cross-chapter repetition detection is unbuilt** — the check that catches the
  same image reused in chapters 4, 17 and 31, which is the failure a per-chapter
  AI-ism blocklist cannot see.
- **The `fit()` drop order is untested against a real 40-chapter run.** It
  currently sheds the verbatim tail first under pressure, which preserves plot
  at the cost of voice. That trade may be backwards for prose; verify before
  trusting it on a long book.
- **Credential exposure is unresolved.** The scanner prevents the next leak. It
  does nothing about the keys already sitting in Drive since 2026-08-05.
