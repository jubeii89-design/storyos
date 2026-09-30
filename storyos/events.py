"""Append-only run log. The single channel between the pipeline and any UI.

Every stage writes newline-delimited JSON to `runs/<run_id>/events.jsonl`.
Nothing streams over a socket, nothing lives only in memory. That one decision
buys most of the resumability story:

- The web UI tails the file and pushes over SSE. Refresh is free. Restarting
  the server mid-run loses nothing.
- A terminal fallback (`tail -f`, or a Textual TUI) reads the identical stream
  with no extra code path to keep in sync.
- Resume works by replay: read the log, collect which (chapter, stage) pairs
  emitted `stage_end` with ok=true, and skip them. No separate state file to
  fall out of agreement with reality.
- A crash leaves a complete record up to the crash, which is exactly when you
  most want one.

Writes are line-atomic: one `write()` of one line, then flush. Concurrent
appends from a parent and a child process interleave safely at line
granularity on POSIX for lines under PIPE_BUF, and a truncated final line from
a hard kill is tolerated by the reader rather than fatal.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterator


def new_run_id() -> str:
    """Sortable, human-readable, collision-safe."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


@dataclasses.dataclass(slots=True)
class EventLog:
    path: Path
    run_id: str
    _fh: Any = dataclasses.field(default=None, repr=False)

    @classmethod
    def open(cls, project_dir: str | os.PathLike, run_id: str | None = None) -> "EventLog":
        run_id = run_id or new_run_id()
        path = Path(project_dir) / "runs" / run_id / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        log = cls(path=path, run_id=run_id)
        log._fh = path.open("a", encoding="utf-8")
        return log

    def emit(self, event: str, **fields) -> dict:
        rec = {"ts": round(time.time(), 3), "run": self.run_id, "event": event, **fields}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        self._fh.write(line + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())  # survive a hard kill; a run is slow enough to afford it
        return rec

    # -- the event vocabulary. Keep it small and closed. -------------------

    def run_start(self, project: str, chapters: list[int], plan: dict) -> None:
        self.emit("run_start", project=project, chapters=chapters, plan=plan)

    def stage_start(self, chapter: int, stage: str, model: str, provider: str,
                    prompt_report: dict, part: str = "") -> None:
        """prompt_report is Prompt.report() -- this is what feeds the inspector."""
        self.emit("stage_start", chapter=chapter, part=part, stage=stage, model=model,
                  provider=provider, prompt=prompt_report)

    def stage_end(self, chapter: int, stage: str, ok: bool, *, part: str = "",
                  artifact: str | None = None,
                  input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0,
                  latency_s: float = 0.0, truncated: bool = False,
                  verdict: str | None = None, error: str | None = None) -> None:
        """`verdict` carries a review stage's ship/revise/rewrite outcome.

        Without it, `models.json`'s `escalate_after_failures` has nothing to
        count -- `ok` says the stage ran, not what it concluded, and a review
        that cleanly returns "rewrite" is a successful stage with a bad verdict.
        """
        self.emit("stage_end", chapter=chapter, part=part, stage=stage, ok=ok,
                  artifact=artifact, input_tokens=input_tokens,
                  output_tokens=output_tokens,
                  cost_usd=round(cost_usd, 6), latency_s=round(latency_s, 2),
                  truncated=truncated, verdict=verdict, error=error)

    def finding(self, chapter: int, kind: str, severity: str, message: str,
                span: tuple[int, int] | None = None, quote: str | None = None,
                part: str = "") -> None:
        """A verification hit.

        `span` is a character offset pair into the chapter markdown, so the UI
        can highlight it rather than make you hunt. Checks that cannot produce
        offsets must at least return `quote` so the UI can search for it.
        """
        self.emit("finding", chapter=chapter, part=part, kind=kind, severity=severity,
                  message=message, span=list(span) if span else None, quote=quote)

    def note(self, message: str, **fields) -> None:
        self.emit("note", message=message, **fields)

    def run_end(self, ok: bool, cost_usd: float = 0.0, error: str | None = None) -> None:
        self.emit("run_end", ok=ok, cost_usd=round(cost_usd, 6), error=error)
        self.close()

    def close(self) -> None:
        if self._fh and not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.emit("note", message=f"aborted: {exc_type.__name__}: {exc}", level="error")
        self.close()


# --- reading side: used by the UI, the CLI, and resume --------------------


def read_events(path: str | os.PathLike, *, tolerate_partial: bool = True) -> Iterator[dict]:
    """Yield events. A truncated trailing line from a kill -9 is skipped, not raised."""
    p = Path(path)
    if not p.exists():
        return
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                if tolerate_partial:
                    continue
                raise


def completed_stages(path: str | os.PathLike) -> set[tuple[int, str, str]]:
    """(chapter, part, stage) triples that finished successfully -- the resume key.

    Derived by replaying the log, so there is no second source of truth to
    drift. Re-running a stage that later failed is correct: the last outcome
    for a triple wins.

    `part` is "a", "b", or "" for chapter-level and book-level stages. It is in
    the key because a chapter is drafted in two halves: keyed on chapter alone,
    finishing 01b would mark 01a complete and the second half would overwrite
    the first on the next resume.
    """
    outcome: dict[tuple[int, str, str], bool] = {}
    for ev in read_events(path):
        if ev.get("event") == "stage_end":
            key = (ev["chapter"], ev.get("part", ""), ev["stage"])
            outcome[key] = bool(ev.get("ok"))
    return {k for k, ok in outcome.items() if ok}


def review_failures(path: str | os.PathLike, chapter: int) -> int:
    """How many times this chapter's review returned `rewrite`.

    This is what `models.json`'s `escalate_after_failures` counts. Derived by
    replay rather than stored, so it survives a crash like everything else.
    """
    return sum(
        1 for ev in read_events(path)
        if ev.get("event") == "stage_end"
        and ev.get("stage") == "review"
        and ev.get("chapter") == chapter
        and ev.get("verdict") == "rewrite"
    )


def run_totals(path: str | os.PathLike) -> dict:
    """Cost and token rollup for the queue screen's footer."""
    t = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
         "stages_ok": 0, "stages_failed": 0, "findings": 0, "truncations": 0}
    by_stage: dict[str, float] = {}
    for ev in read_events(path):
        if ev.get("event") == "stage_end":
            t["cost_usd"] += ev.get("cost_usd", 0.0)
            t["input_tokens"] += ev.get("input_tokens", 0)
            t["output_tokens"] += ev.get("output_tokens", 0)
            t["stages_ok" if ev.get("ok") else "stages_failed"] += 1
            t["truncations"] += bool(ev.get("truncated"))
            by_stage[ev["stage"]] = by_stage.get(ev["stage"], 0.0) + ev.get("cost_usd", 0.0)
        elif ev.get("event") == "finding":
            t["findings"] += 1
    t["cost_usd"] = round(t["cost_usd"], 4)
    t["cost_by_stage"] = {k: round(v, 4) for k, v in sorted(by_stage.items())}
    return t
