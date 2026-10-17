# The model registry

Every model the system can reach is one row in `models.json`. Nothing about a
model is hardcoded in a prompt or a stage — which model drafts, which two
critics read, and which renderer draws the cover are all data the writer edits
in the UI.

## Keys live in the environment, never in this file

A registry row names an environment variable. It never holds the value.

```json
{ "key_env": "OPENAI_API_KEY" }
```

`models.json` sits in the project folder, is edited through the UI, and will end
up in a backup or a screenshot. A key pasted into it is a leaked key. The
pre-commit hook at `.githooks/pre-commit` runs `tools/scan_secrets.py` and will
block the commit, but the leak has already happened by then — the fix is to
never put it there.

A row whose `key_env` is unset in the environment shows as unavailable in the UI
rather than failing at call time, so a missing key is visible before a run
starts instead of forty minutes into one.

## Registry row

```json
{
  "id": "openai-frontier",
  "label": "ChatGPT",
  "family": "openai",
  "kind": "openai_compatible",
  "base_url": "https://api.openai.com/v1",
  "model": "<exact model id>",
  "key_env": "OPENAI_API_KEY",
  "price_in": null,
  "price_out": null,
  "roles": ["critic"]
}
```

`kind` is one of `subagent` (a Claude Code subagent, no key, billed to the
subscription), `openai_compatible` (anything speaking that wire format —
OpenAI, Ollama, most local servers, most gateways), `anthropic_api`,
`local_diffusion`, or `manual`.

### `kind: "manual"` — the consumer subscription, used legitimately

A consumer chat subscription includes no API access. ChatGPT Plus and Claude Pro
are both sold separately from their APIs, and neither one's payment gives you a
key. Driving a consumer web session with a script violates the terms and breaks
every time the vendor ships a UI change, so the system does not do it.

A `manual` row instead makes the human the transport. The UI renders the review
prompt with a copy button and opens the vendor's page in a second window; the
writer pastes it in, pastes the JSON reply back into a validating textarea, and
the run continues. Nothing about the review's integrity depends on how the text
arrived — `verify_quotes`, `verify_entities` and `schema_violations` all run on a
pasted response exactly as they do on an API one, and a pasted response that
dodges the quota or invents a quote is rejected the same way.

At `slot_b_policy: "on_contest"` this is about ten paste cycles across a
40-chapter novel. Two minutes each. It is the cheapest genuinely-foreign second
reader available, and its only real cost is that the run is no longer unattended.

`family` is load-bearing, not decoration. The critic bench refuses two slots
from the same family, because two models from one lineage share blind spots and
will agree with each other for reasons that have nothing to do with the prose.

`price_in` and `price_out` are per million tokens and are **entered by the
writer**, not shipped. Vendor pricing changes faster than any file in this repo,
and a stale number in a cost display is worse than a blank one. The UI shows
`unpriced` until they are filled and skips that model in the cost footer.

## Critic bench

Two slots. The rules are enforced in code, not left to the UI:

- Neither slot may be `family: "anthropic"`. The instance that drafted the prose
  cannot review it, and neither can its siblings — self-preference bias is the
  best-documented failure mode in LLM-as-judge setups.
- The two slots may not share a `family`.
- Both slots must have `"critic"` in `roles`.

Default pair, both free:

```json
"critics": {
  "slot_a": "ollama-qwen",
  "slot_b": "ollama-mistral",
  "slot_b_policy": "always"
}
```

`slot_b_policy` is `always` or `on_contest`. Set it to `on_contest` when slot B
costs money: slot A and the free Python gate run on every chapter, and slot B is
spent only on chapters where they disagree or where the gate flagged heavily.
That is realistically 8–12 chapters in 40, which is where the uncertainty
actually is — a chapter where a local model and a deterministic checker agree
does not need a third opinion.

Local models are not as sharp as a frontier reader, and that matters less here
than it would elsewhere, because the review's honesty is structural rather than
requested. A fixed quota of three weakest paragraphs, no rating scale, and every
claim carrying a quote that `verify_quotes` string-matches against the chapter —
a 14B model can pick three paragraphs and quote them accurately. Its worst
failure mode is inventing evidence, and that is exactly what the quote check
catches.

## Per-stage assignment

```json
"stages": {
  "brainstorm":    { "model": "claude-opus-5-5" },
  "outline":       { "model": "claude-opus-5" },
  "beats":         { "model": "claude-opus-5" },
  "draft":         { "model": "claude-sonnet-5-5",
                     "escalate_to": "claude-opus-5",
                     "escalate_after_failures": 2 },
  "delta_extract": { "model": "claude-haiku-4-5" },
  "seam":          { "model": "claude-opus-5" },
  "revise":      { "model": "claude-opus-5" }
}
```

Escalate on failure, never by default. Drafting is 80 of the ~320 model calls
and it is also where model quality shows most, so a flat downgrade there buys
limit headroom at the price of the actual book. Instead the drafter runs on
Sonnet and a chapter that fails review twice is redrafted on Opus. `seam` and
`revise` stay on Opus at full price because they are 40 calls each against a
600-word window — the highest craft sensitivity per token anywhere in the
pipeline.

`escalate_after_failures` counts `review` stages that returned a `rewrite`
verdict for that chapter, read from `events.jsonl`. The counter is derived by
replay, not stored, so it survives a crash.

## Cover renderers

```json
"cover_renderers": [
  { "id": "typographic",  "kind": "none",             "cost": "free" },
  { "id": "local-flux",   "kind": "local_diffusion",  "cost": "free",
    "endpoint": "http://127.0.0.1:8188", "vram_gb": 12 },
  { "id": "nano-banana",  "kind": "openai_compatible", "cost": "per image" }
]
```

`typographic` renders HTML and CSS through headless Chromium and needs no model
at all. It is genre-correct for literary and thriller and wrong for fantasy and
romance, which expect illustration.

`local_diffusion` posts to a ComfyUI instance on the writer's own machine. Free
and unlimited once the card is there, which means two hundred candidates cost
nothing — strictly better than a metered service if the VRAM exists.

A cover is one image, so the keyed renderers are a few dollars once per book.
This is the one place in the system where paying is obviously correct, and the
dropdown exists because the tradeoff is per-book rather than per-call.

## Availability, shown before a run

`storyos.models.probe()` checks each enabled row once at UI start: environment
variable present, endpoint reachable, model name accepted. Results are cached
for the session and shown as a dot next to each dropdown entry. A local Ollama
that is not running and a missing key look identical from inside a stage forty
minutes into a drain, so both are caught at the door instead.
