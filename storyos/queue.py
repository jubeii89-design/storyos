"""What the UI asks for, and what the terminal picks up.

The browser page cannot call a model. So it appends a request here, and a
Claude Code turn drains it. Append-only with the same discipline as
`events.jsonl`: status is folded from the records, never edited in place, so a
crash mid-drain leaves a readable queue rather than a half-written one.

The failure worth engineering against is two turns draining at once -- a manual
`/storyos draft 07b` and a `/loop 3m /storyos drain` both claiming the same
item and both writing `chapters/parts/ch07b.md`. The second would overwrite
prose that already has a successful `stage_end`, breaking one-writer-per-file
in the one place it matters most. So a claim is an atomic file create: whoever
loses the race appends `skip` and moves on.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from . import stages, units
from .events import completed_all

STALE_LOCK_SECONDS = 30 * 60


def new_id() -> str:
    return "q-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def _append(env, record: dict) -> dict:
    env.queue.parent.mkdir(parents=True, exist_ok=True)
    with env.queue.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return record


def read(env) -> list[dict]:
    if not env.queue.is_file():
        return []
    out = []
    for line in env.queue.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a torn trailing line from a kill mid-write
    return out


def fold(env) -> list[dict]:
    """One row per queued item, carrying its latest status."""
    items: dict[str, dict] = {}
    for rec in read(env):
        qid = rec.get("id")
        if not qid:
            continue
        if rec.get("event") == "enqueue":
            items[qid] = {**rec, "status": "open", "note": ""}
        elif qid in items:
            items[qid]["status"] = rec.get("event", "open")
            items[qid]["note"] = rec.get("note", "")
            items[qid]["run"] = rec.get("run", items[qid].get("run"))
    return sorted(items.values(), key=lambda r: r.get("ts", 0))


def enqueue(env, unit: str | None, stage: str, *, force: bool = False,
            reason: str = "", by: str = "ui") -> dict:
    """Ask for one step. Refuses complete or blocked work unless forced."""
    st = stages.get(stage)
    chapter, part = (units.parse(unit) if unit else (0, ""))
    key = (chapter, part, stage)

    done = completed_all(env.root)
    # Two different overrides, deliberately not one flag. `force` means
    # "ignore dependencies"; `redraft` means "redo work that is finished".
    # Conflating them lets an item forced past a dependency check later
    # overwrite prose that completed in the meantime -- the exact
    # one-writer-per-file violation this module exists to prevent.
    redraft = key in done
    if redraft and not force:
        raise AlreadyComplete(key)

    if not force:
        plan = stages.plan(env.config.get("chapters", 40))
        if key in plan:
            blocked = [k for k in plan[:plan.index(key)] if k not in done]
            if blocked:
                raise Blocked(key, blocked[:4])

    return _append(env, {
        "id": new_id(), "ts": round(time.time(), 2), "event": "enqueue",
        "by": by, "unit": units.fmt(chapter, part) if chapter else "book",
        "chapter": chapter, "part": part, "stage": stage,
        "reason": reason, "forced": bool(force), "redraft": bool(redraft),
    })


def claim(env, qid: str, *, run: str = "") -> bool:
    """Take an item for this turn. Atomic -- only one caller can win.

    Re-checks completion at claim time, because the terminal may have advanced
    since the item was queued and the queue is a request, not a fact.
    """
    item = next((i for i in fold(env) if i["id"] == qid), None)
    if item is None:
        return False
    if item["status"] != "open":
        return False

    key = (item["chapter"], item["part"], item["stage"])
    # Only an explicit redraft may overwrite finished work. An item queued
    # while incomplete that finished before it was claimed is skipped, not
    # redone: otherwise two turns racing produce a second draft over the first.
    if key in completed_all(env.root) and not item.get("redraft"):
        _append(env, {"id": qid, "ts": round(time.time(), 2), "event": "skip",
                      "note": "completed_before_claim", "run": run})
        return False

    env.locks_dir.mkdir(parents=True, exist_ok=True)
    lock = env.locks_dir / f"{qid}.lock"

    if lock.exists() and time.time() - lock.stat().st_mtime > STALE_LOCK_SECONDS:
        lock.unlink(missing_ok=True)      # a turn that died holding it

    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        _append(env, {"id": qid, "ts": round(time.time(), 2), "event": "skip",
                      "note": "claimed_elsewhere", "run": run})
        return False

    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps({"pid": os.getpid(), "run": run,
                             "at": round(time.time(), 2)}))

    _append(env, {"id": qid, "ts": round(time.time(), 2), "event": "claim",
                  "run": run})
    return True


def finish(env, qid: str, *, ok: bool = True, note: str = "", run: str = "") -> None:
    _append(env, {"id": qid, "ts": round(time.time(), 2),
                  "event": "done" if ok else "skip", "note": note, "run": run})
    (env.locks_dir / f"{qid}.lock").unlink(missing_ok=True)


def next_open(env) -> dict | None:
    return next((i for i in fold(env) if i["status"] == "open"), None)


class AlreadyComplete(Exception):
    def __init__(self, key):
        self.key = key
        super().__init__(f"{stages.label(key)} is already complete")


class Blocked(Exception):
    def __init__(self, key, blocked):
        self.key, self.blocked = key, blocked
        super().__init__(
            f"{stages.label(key)} is blocked by: "
            + ", ".join(stages.label(k) for k in blocked))
