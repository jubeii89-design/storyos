# What leaves this machine

Story OS is local software that reaches out when you ask it to. Your book lives
in a directory on your disk and is never uploaded anywhere by this program.

Every connection below is *inbound-fetching* — pulling something in that you
asked for. None of them send your manuscript out.

## Connections it makes

| When | Where | Carries | Why |
|---|---|---|---|
| `storyos connect google`, once | `accounts.google.com`, `oauth2.googleapis.com` | your consent, then a token exchange | signing you in |
| importing a Doc | `www.googleapis.com/drive/v3` | the file id you named, your token | reading that document |
| adding a URL | the host you pasted | an ordinary page request | fetching and archiving it |

That is the complete list. There is no telemetry, no update check, no crash
reporting, no analytics, and no account of any kind with us — there is no "us".

## Connections it does not make

**The model is not called from here.** Drafting, the seam repair and line edits
run as Claude Code turns in your terminal, under your own subscription. This
program never holds an Anthropic key and never sends prose to Anthropic.

**Critics are yours to choose.** A local Ollama model never leaves the machine.
A paste critic means *you* carry the text to a browser, which is the only way a
consumer chat subscription can legitimately be used. Only an API critic you
explicitly configure and enable sends a chapter anywhere, and it sends one
chapter, when you run that stage.

**No webfonts.** The UI uses faces already installed on your machine. Loading a
font from a CDN would tell that CDN, every time, that you opened your notebook.

## Listening sockets

Both bind `127.0.0.1`, never `0.0.0.0`, so nothing is reachable from your
network:

- `storyos ui` — the page, default port 8730
- during Google sign-in — a one-shot loopback redirect on a random port, which
  closes as soon as the code arrives

## Credentials

Keys live in `.env` and the Google token in `.google-token.json`, both at
owner-only permissions, both gitignored, and both caught by
`tools/scan_secrets.py` if they are ever staged. `models.json` names an
environment variable and never holds a value, because it is edited in the UI and
ends up in screenshots.

## Working entirely offline

Everything except the three rows in the table above works with no network:
drafting, the checks, the seam, reconciliation, search, export to DOCX, EPUB and
PDF. A library note already imported is yours; the PDF kept beside it is why a
dead link does not cost you the source.
