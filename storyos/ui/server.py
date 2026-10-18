"""The local UI: a reading, judging and approval surface over files on disk.

The page cannot call a model, and this server does not pretend otherwise. Model
work happens as Claude Code turns in a terminal under the writer's
subscription; the page appends to `queue.jsonl` and the terminal drains it.
Anyone offering a browser page that "just calls the model" is selling a server
with an API key in it.

So what is left for the page is most of the work and all of the judgement:
reading prose, seeing which segment of a prompt caused a bad half, deciding
contested findings, pinning craft notes, and watching a run. None of that needs
a model.

Standard library only. `http.server` is adequate for one user on localhost and
has no install step, which matters for a tool that must still run in five years.
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import queue as q
from .. import stages, units
from ..events import completed_all, read_events, review_failures, run_totals
from ..paths import Env, EnvNotFound

HERE = Path(__file__).resolve().parent
PORT = 8730

# Subdirectories the file endpoint may serve. Anything else is refused even if
# it resolves inside the environment -- `.env` lives at the root and must never
# be readable over HTTP, not even to the one user, because a browser extension
# or a stray page can read localhost.
READABLE = ("chapters", "beats", "review", "checks", "deltas", "library",
            "prompts", "comps", "reference", "runs", "export", "cover")
READABLE_FILES = ("premise.md", "brainstorm.md", "dossier.md", "outline.json",
                  "style.json", "genre.json", "state.json", "manuscript.md",
                  "models.json", "library.pins.json")


class Handler(BaseHTTPRequestHandler):
    env: Env = None            # set by serve()
    protocol_version = "HTTP/1.1"
    server_version = "storyos"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):
        pass                   # the terminal is the writer's, not a web log

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload, default=str).encode(),
                   "application/json; charset=utf-8")

    def _err(self, code: int, why: str, **extra) -> None:
        self._json({"error": why, **extra}, code)

    # -- routing ----------------------------------------------------------
    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        path, args = url.path, urllib.parse.parse_qs(url.query)
        try:
            self._route_get(path, args)
        except (FileNotFoundError, EnvNotFound) as x:
            self._err(404, str(x))
        except (ValueError, units.BadUnit, KeyError) as x:
            self._err(400, str(x))

    def _route_get(self, path: str, args: dict) -> None:
        one = lambda k, d=None: (args.get(k) or [d])[0]

        if path in ("/", "/index.html"):
            page = (HERE / "app.html").read_bytes()
            return self._send(200, page, "text/html; charset=utf-8")

        if path == "/api/env":
            return self._json(self._env_payload())

        if path == "/api/plan":
            return self._json(self._plan_payload())

        if path == "/api/queue":
            return self._json({"items": q.fold(self.env),
                               "next_open": q.next_open(self.env)})

        if path == "/api/unit":
            return self._json(self._unit_payload(one("id")))

        if path == "/api/chapter":
            return self._json(self._chapter_payload(one("n")))

        if path == "/api/file":
            return self._file(one("path", ""))

        if path == "/api/prompt":
            pack = self.env.prompt_pack(one("run", ""), one("unit", ""))
            if not pack.is_file():
                raise FileNotFoundError(f"no prompt pack for {one('unit')}")
            return self._json(json.loads(pack.read_text()))

        if path == "/api/inspector":
            return self._json(self._inspector_payload())

        if path == "/api/events":
            run = one("run") or self.env.latest_run()
            if not run:
                return self._json({"run": None, "events": []})
            evs = list(read_events(self.env.events(run)))
            return self._json({"run": run, "events": evs[-int(one("tail", 60)):],
                               "totals": run_totals(self.env.events(run))})

        if path == "/api/search":
            from .. import search as S
            S.reindex(self.env)
            kinds = tuple(k for k in one("kind", "").split(",") if k)
            return self._json(S.query(self.env, one("q", ""), kinds=kinds))

        if path == "/api/library":
            from .. import library
            from .. import search as S
            return self._json({"notes": library.notes(self.env),
                               "pins": library.pins(self.env),
                               "stale": library.stale_pins(self.env),
                               "index": S.stats(self.env)})

        if path == "/api/library/note":
            from .. import library
            rel = one("path", "")
            p2 = (self.env.root / rel).resolve()
            if not p2.is_relative_to(self.env.library.resolve()) or not p2.is_file():
                raise FileNotFoundError(rel)
            raw = p2.read_text(errors="replace")
            return self._json({"path": rel, "meta": library._read_front(raw),
                               "body": library.body_of(raw)})

        if path == "/api/google/status":
            from ..sources import google
            st = google.status(self.env)
            st["setup_text"] = google.SETUP_TEXT if not st["ready_to_connect"] else ""
            return self._json(st)

        if path == "/api/google/search":
            from ..sources import google
            try:
                return self._json({"files": google.search(self.env, one("q", ""))})
            except google.NotConnected as x:
                return self._err(401, str(x))

        self._err(404, f"no route {path}")

    def do_POST(self) -> None:
        url = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._err(400, "body is not JSON")

        if url.path == "/api/queue":
            return self._enqueue(body)
        if url.path == "/api/decide":
            return self._decide(body)
        if url.path == "/api/google/connect":
            return self._google_connect()
        if url.path == "/api/library/add":
            return self._library_add(body)
        if url.path == "/api/library/pin":
            return self._library_pin(body)
        self._err(404, f"no route {url.path}")

    def _google_connect(self) -> None:
        """Start the sign-in. The consent page opens in the writer's browser;
        this returns immediately and the page polls status until it flips."""
        from ..sources import google
        try:
            google.client_config(self.env)
        except google.SetupNeeded as x:
            return self._err(428, "setup needed", setup_text=str(x))

        state = {"done": False, "error": ""}

        def run():
            try:
                google.connect(self.env, open_browser=True)
                state["done"] = True
            except Exception as x:          # surfaced through /api/google/status
                state["error"] = str(x)

        threading.Thread(target=run, daemon=True).start()
        self._json({"started": True,
                    "note": "a Google consent page is opening in your browser"})

    def _library_add(self, body: dict) -> None:
        from .. import library
        from ..sources import files, google
        src = (body.get("source") or "").strip()
        if not src:
            return self._err(400, "source is required")

        original = ""
        try:
            if "docs.google.com" in src or body.get("google"):
                doc = google.fetch(self.env, src)
                if pdf := google.fetch_pdf(self.env, src):
                    original = library.keep_original(self.env, doc["name"], pdf, ".pdf")
                path = library.add(self.env, doc["text"], title=doc["name"],
                                   source="google-docs", source_id=doc["id"],
                                   modified=doc.get("modified", ""),
                                   fmt=doc["exported_as"].rsplit("/", 1)[-1],
                                   original=original)
            elif files.is_url(src):
                md, title, fmt, pdf = files.fetch_url_full(src)
                if pdf:
                    original = library.keep_original(self.env, title, pdf, ".pdf")
                path = library.add(self.env, md, title=title, source="web",
                                   source_id=src, fmt=fmt, original=original)
            else:
                return self._err(400, "the page can add a URL or a Google Doc; "
                                      "local files go through `storyos library add`")
        except google.NotConnected as x:
            return self._err(401, str(x))
        except (files.Unsupported, ValueError, RuntimeError) as x:
            return self._err(400, str(x))

        from .. import search as S
        S.reindex(self.env)
        self._json({"added": str(path.relative_to(self.env.root)),
                    "original": original})

    def _library_pin(self, body: dict) -> None:
        from .. import library
        try:
            rec = library.pin(self.env, body.get("note", ""),
                              body.get("excerpt", ""), body.get("why", ""),
                              chapters=body.get("chapters") or [])
        except (FileNotFoundError, ValueError) as x:
            return self._err(400, str(x))
        self._json({"pinned": rec})

    # -- payloads ---------------------------------------------------------
    def _env_payload(self) -> dict:
        e = self.env
        models = e.models()
        reg = {r["id"]: r for r in models.get("registry", []) if "id" in r}
        crit = models.get("critics", {})
        slots = {}
        for slot in ("slot_a", "slot_b"):
            row = reg.get(crit.get(slot), {})
            slots[slot] = {"id": crit.get(slot), "label": row.get("label"),
                           "family": row.get("family"), "kind": row.get("kind")}
        fam = [slots[s]["family"] for s in ("slot_a", "slot_b")]
        return {
            "title": e.title, "root": str(e.root),
            "chapters": e.config.get("chapters", 40),
            "half_words": e.config.get("half_words", [1500, 1750]),
            "keys": e.key_names(),
            "critics": slots,
            "critic_rules_ok": "anthropic" not in fam and fam[0] != fam[1],
            "stages": models.get("stages", {}),
            "cover_renderer": models.get("cover_renderer"),
            "has": {n: (e.root / n).exists() for n in
                    ("premise.md", "style.json", "outline.json", "genre.json",
                     "dossier.md", "brainstorm.md")},
            "style_measured": self._style_measured(),
        }

    def _style_measured(self) -> bool | None:
        if not self.env.style.is_file():
            return None
        try:
            return bool(json.loads(self.env.style.read_text())
                        .get("source", {}).get("measured"))
        except (json.JSONDecodeError, AttributeError):
            return None

    def _plan_payload(self) -> dict:
        e = self.env
        chapters = e.config.get("chapters", 40)
        done = completed_all(e.root)
        queued = {(i["chapter"], i["part"], i["stage"])
                  for i in q.fold(e) if i["status"] in ("open", "claim")}

        rows = []
        for key in stages.plan(chapters):
            st = stages.get(key[2])
            rows.append({
                "chapter": key[0], "part": key[1], "stage": key[2],
                "label": stages.label(key), "scope": st.scope,
                "model": st.model, "human": st.human, "writes": st.writes,
                "done": key in done, "queued": key in queued,
            })

        nxt = stages.next_step(chapters, done)
        committed = sum(1 for c in range(1, chapters + 1)
                        if (c, "", "chapter_commit") in done)
        halves = sum(1 for c in range(1, chapters + 1) for p in units.PARTS
                     if (c, p, "draft") in done)

        return {
            "rows": rows,
            "next": ({"chapter": nxt[0], "part": nxt[1], "stage": nxt[2],
                      "label": stages.label(nxt),
                      "model": stages.get(nxt[2]).model,
                      "human": stages.get(nxt[2]).human,
                      "command": self._command_for(nxt)} if nxt else None),
            "progress": {"committed": committed, "chapters": chapters,
                         "halves": halves, "words": self._words()},
            "escalations": {c: review_failures(e.root, c)
                            for c in range(1, chapters + 1)
                            if review_failures(e.root, c)},
            "latest_run": e.latest_run(),
        }

    def _command_for(self, key) -> str:
        chapter, part, stage = key
        st = stages.get(stage)
        arg = units.fmt(chapter, part) if chapter else ""
        if st.human:
            return f"$EDITOR {self.env.premise.name}"
        if st.model:
            return f"/storyos {stage} {arg}".rstrip()
        return f"storyos stage {stage} {arg}".rstrip()

    def _words(self) -> int:
        total = 0
        d = self.env.root / "chapters"
        if d.is_dir():
            for p in d.glob("ch*.md"):
                if ".v" not in p.name:
                    total += len(p.read_text(errors="replace").split())
        return total

    def _unit_payload(self, uid: str | None) -> dict:
        if not uid:
            raise ValueError("id is required, e.g. ?id=07b")
        chapter, part = units.parse(uid)
        if not part:
            raise units.BadUnit(f"{uid!r} is a chapter; use /api/chapter?n=")

        e = self.env
        prose = e.part(uid)
        beats = e.beats(uid)
        check = e.words_check(uid)
        return {
            "unit": units.fmt(chapter, part),
            "predecessor": units.predecessor(units.fmt(chapter, part)),
            "sibling": units.sibling(units.fmt(chapter, part)),
            "text": prose.read_text(errors="replace") if prose.is_file() else None,
            "words": len(prose.read_text(errors="replace").split()) if prose.is_file() else 0,
            "mtime": prose.stat().st_mtime if prose.is_file() else None,
            "beats": json.loads(beats.read_text()) if beats.is_file() else None,
            "length_check": json.loads(check.read_text()) if check.is_file() else None,
            "band": e.config.get("half_words", [1500, 1750]),
        }

    def _chapter_payload(self, n: str | None) -> dict:
        if not n:
            raise ValueError("n is required, e.g. ?n=07")
        ch = units.parse(n)[0]
        e = self.env

        def load(p):
            return json.loads(p.read_text()) if p.is_file() else None

        committed = e.chapter(ch)
        return {
            "chapter": units.fmt(ch),
            "versions": e.chapter_versions(ch),
            "committed": committed.read_text(errors="replace") if committed.is_file() else None,
            "v1": e.chapter(ch, 1).read_text(errors="replace") if e.chapter(ch, 1).is_file() else None,
            "joined": e.joined(ch).read_text(errors="replace") if e.joined(ch).is_file() else None,
            "seam": load(e.seam_sidecar(ch)),
            "seam_check": load(e.seam_check(ch)),
            "reconciled": load(e.review(ch, "reconciled")),
            "escalations": review_failures(e.root, ch),
        }

    def _inspector_payload(self) -> dict:
        """Aggregates across every run, with suggestions from arithmetic alone.

        No model is consulted. Each suggestion below is a count or a mean over
        `events.jsonl`, which is the only way a suggestion about the writer's
        own habits can be trustworthy -- a model asked to characterise them
        would produce something plausible and unfalsifiable.
        """
        e = self.env
        reports: list[dict] = []
        redrafts: dict[tuple, int] = {}

        for run in sorted((e.root / "runs").iterdir()) if (e.root / "runs").is_dir() else []:
            log = run / "events.jsonl"
            if not log.is_file():
                continue
            for ev in read_events(log):
                if ev.get("event") == "stage_start" and ev.get("prompt"):
                    reports.append({**ev["prompt"], "_stage": ev.get("stage"),
                                    "_unit": f"{ev.get('chapter')}{ev.get('part','')}"})
                if ev.get("event") == "stage_end" and ev.get("stage") == "draft":
                    key = (ev.get("chapter"), ev.get("part", ""))
                    redrafts[key] = redrafts.get(key, 0) + 1

        return {
            "calls": len(reports),
            "drop_rates": _drop_rates(reports),
            "verbatim_vs_redrafts": _verbatim_bins(reports, redrafts),
            "suggestions": _suggestions(reports, redrafts),
        }

    def _file(self, rel: str) -> None:
        if not rel:
            raise ValueError("path is required")
        p = (self.env.root / rel).resolve()
        if not p.is_relative_to(self.env.root.resolve()):
            return self._err(403, "outside the environment")
        head = Path(rel).parts[0] if Path(rel).parts else ""
        if head not in READABLE and rel not in READABLE_FILES:
            return self._err(403, f"{head or rel!r} is not served over HTTP")
        if not p.is_file():
            raise FileNotFoundError(rel)
        ctype = mimetypes.guess_type(p.name)[0] or "text/plain; charset=utf-8"
        self._send(200, p.read_bytes(), ctype)

    # -- the two writes ---------------------------------------------------
    def _enqueue(self, body: dict) -> None:
        stage = body.get("stage")
        if not stage:
            return self._err(400, "stage is required")
        try:
            rec = q.enqueue(self.env, body.get("unit"), stage,
                            force=bool(body.get("force")),
                            reason=body.get("reason", ""), by="ui")
        except q.AlreadyComplete as x:
            return self._err(409, "complete", why="complete",
                             key=list(x.key), hint="send force to redraft it")
        except q.Blocked as x:
            return self._err(409, "blocked", why="blocked",
                             key=list(x.key),
                             blocked_by=[stages.label(k) for k in x.blocked])
        except KeyError as x:
            return self._err(400, str(x))
        self._json({"queued": rec})

    def _decide(self, body: dict) -> None:
        """Accept or dismiss a contested finding. The writer is the tiebreaker.

        Writes back into the reconciled file rather than a new one, because
        `revise` reads exactly one list and a second source of truth about
        which findings are live is how a dismissed finding gets applied anyway.
        """
        ch = units.parse(str(body.get("chapter", "")))[0]
        idx = body.get("index")
        accepted = bool(body.get("accepted"))
        path = self.env.review(ch, "reconciled")
        if not path.is_file():
            raise FileNotFoundError(f"chapter {ch:02d} has no reconciliation yet")

        doc = json.loads(path.read_text())
        if not isinstance(idx, int) or not 0 <= idx < len(doc.get("contested", [])):
            return self._err(400, f"index out of range: {idx!r}")
        doc["contested"][idx]["accepted"] = accepted
        doc["contested"][idx]["decided_at"] = round(time.time(), 2)
        path.write_text(json.dumps(doc, indent=2) + "\n")
        self._json({"chapter": units.fmt(ch), "index": idx, "accepted": accepted})

    # Search lives in storyos/search.py (SQLite FTS5) and is reached through
    # the /api/search route above. There is deliberately no second
    # implementation here: two searches that disagree about what matches is
    # the same defect class that cost this project its quote verification.


# -- inspector arithmetic -------------------------------------------------

def _drop_rates(reports: list[dict]) -> list[dict]:
    """How often each segment label gets dropped to fit the budget.

    A label dropped often is either too low-priority or the budget is too
    tight, and either way the writer should know before it eats a chapter.
    """
    seen: dict[str, int] = {}
    dropped: dict[str, int] = {}
    for r in reports:
        for s in r.get("segments", []):
            seen[s["label"]] = seen.get(s["label"], 0) + 1
        for s in r.get("dropped", []):
            label = s["label"] if isinstance(s, dict) else str(s)
            seen[label] = seen.get(label, 0) + 1
            dropped[label] = dropped.get(label, 0) + 1
    out = [{"label": k, "calls": seen[k], "dropped": dropped.get(k, 0),
            "rate": round(dropped.get(k, 0) / seen[k], 3)}
           for k in seen]
    return sorted(out, key=lambda r: -r["rate"])


def _verbatim_bins(reports: list[dict], redrafts: dict) -> list[dict]:
    """Mean redrafts against the share of the prompt that was verbatim prose.

    The question this answers is the one the whole context design rests on:
    does giving the model more of the actual previous prose make a half better
    or worse? It is answerable from the log and from nothing else.
    """
    bins: dict[int, list[int]] = {}
    for r in reports:
        if r.get("_stage") != "draft":
            continue
        total = r.get("total_tokens") or 0
        verbatim = (r.get("by_fidelity") or {}).get("verbatim", 0)
        if not total:
            continue
        b = int(min(0.9, verbatim / total) * 10)
        key = r.get("_unit")
        n = max(0, redrafts.get(_unit_key(key), 1) - 1)
        bins.setdefault(b, []).append(n)
    return [{"verbatim_share": f"{b/10:.1f}-{(b+1)/10:.1f}",
             "calls": len(v),
             "mean_redrafts": round(sum(v) / len(v), 2)}
            for b, v in sorted(bins.items())]


def _unit_key(blob):
    if not blob:
        return (None, "")
    s = str(blob)
    part = s[-1] if s and s[-1] in ("a", "b") else ""
    try:
        return (int(s[:-1] if part else s), part)
    except ValueError:
        return (None, part)


def _suggestions(reports: list[dict], redrafts: dict) -> list[dict]:
    out = []

    for row in _drop_rates(reports):
        if row["rate"] >= 0.30 and row["calls"] >= 10:
            out.append({
                "kind": "priority",
                "text": f"“{row['label']}” was dropped from "
                        f"{row['dropped']} of {row['calls']} prompts "
                        f"({row['rate']:.0%}). Either raise its priority or cut "
                        f"tail_words -- at that rate it is not really in your "
                        f"context, it is in your config.",
            })

    bins = [b for b in _verbatim_bins(reports, redrafts) if b["calls"] >= 4]
    if len(bins) >= 2:
        best = min(bins, key=lambda b: b["mean_redrafts"])
        worst = max(bins, key=lambda b: b["mean_redrafts"])
        if worst["mean_redrafts"] - best["mean_redrafts"] >= 0.25:
            out.append({
                "kind": "verbatim",
                "text": f"Halves drafted with {best['verbatim_share']} of the "
                        f"prompt as verbatim prose needed "
                        f"{best['mean_redrafts']} redrafts on average; at "
                        f"{worst['verbatim_share']} they needed "
                        f"{worst['mean_redrafts']}. Measured over "
                        f"{sum(b['calls'] for b in bins)} drafts.",
            })

    total_redrafts = sum(max(0, n - 1) for n in redrafts.values())
    if redrafts and total_redrafts / len(redrafts) >= 0.5:
        out.append({
            "kind": "redrafts",
            "text": f"{total_redrafts} redrafts across {len(redrafts)} halves. "
                    f"Worth looking at what the beats for those halves asked "
                    f"for: a redraft is often a half that was given too little "
                    f"to dramatise.",
        })

    if not out:
        out.append({"kind": "none",
                    "text": "Not enough runs yet to say anything that is not a "
                            "guess. These suggestions are arithmetic over your "
                            "own event log, so they appear once there is a log "
                            "to count."})
    return out


def serve(env_dir: str | None = None, port: int = PORT, open_browser: bool = False):
    env = Env.find(env_dir)
    Handler.env = env
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    print(f"Story OS — {env.title}")
    print(f"  {env.root}")
    print(f"  http://127.0.0.1:{port}")
    print(f"\n  The page reads and writes files. It never calls a model:")
    print(f"  queue work in the page, then drain it from a terminal.")
    print(f"\n  ctrl-c to stop")

    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), _open(port)),
                         daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


def _open(port: int) -> None:
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    import sys
    serve(sys.argv[1] if len(sys.argv) > 1 else None)
