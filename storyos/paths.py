"""The environment: one directory, one book, everything inside it.

    ~/TheLongOrbit/            the environment -- and the book
      .storyos                 marker + config; this file is what makes it one
      .env                     the only place a key exists
      models.json              the bench
      prompts/ reference/ templates/
      library/                 craft knowledge, readable and pinnable
      premise.md dossier.md genre.json style.json outline.json
      beats/ chapters/ deltas/ checks/ review/ cover/ export/ runs/
      state.json manuscript.md      derived, rebuildable, gitignored

There is no project list and no slug. The directory you are standing in is the
book you are writing, so `storyos draft 07a` needs no argument naming which book
and there is no way to write a half into the wrong one. A second book is a
second directory.

Two rules shape every path below.

**One writer per file.** A file written by two stages cannot be resumed. If
`seam` and `revise` both wrote `chapters/ch07.md`, a crash after `revise`
followed by a resume would revise the revision and overwrite the only copy of
the pre-revision text, leaving `seam_verify`'s paragraph hashes matching
nothing. So each stage writes its own numbered output and `chapter_commit`
alone writes the name you type.

**Derived files are never authoritative.** `state.json` and `manuscript.md` are
folds of `deltas/` and `chapters/`. Appending to a manuscript from forty stages
duplicates a chapter the first time one is re-run; rebuilding it from the
chapters on disk cannot.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from pathlib import Path

from . import units

MARKER = ".storyos"
ENV_VAR = "STORYOS_ENV"


class EnvNotFound(Exception):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class Env:
    """One environment, which is one book."""

    root: Path
    config: dict

    @classmethod
    def find(cls, explicit: str | os.PathLike | None = None) -> Env:
        """Locate the environment: explicit argument, then $STORYOS_ENV, then
        by walking up from the working directory looking for the marker.

        The walk is last because it is the convenient one, and convenience
        should not silently beat a stated intention.
        """
        for cand in cls._candidates(explicit):
            marker = cand / MARKER
            if marker.is_file():
                return cls(cand.resolve(), json.loads(marker.read_text() or "{}"))

        tried = ", ".join(str(c) for c in cls._candidates(explicit)[:4])
        raise EnvNotFound(
            f"no {MARKER} here or above. Looked in: {tried}. "
            f"Run `storyos init <dir>` to make one."
        )

    @staticmethod
    def _candidates(explicit) -> list[Path]:
        if explicit:
            return [Path(explicit).expanduser()]
        if os.environ.get(ENV_VAR):
            return [Path(os.environ[ENV_VAR]).expanduser()]
        here = Path.cwd().resolve()
        return [here, *here.parents]

    @property
    def title(self) -> str:
        return self.config.get("title") or self.root.name

    # -- configuration ----------------------------------------------------
    @property
    def marker(self) -> Path: return self.root / MARKER
    @property
    def dotenv(self) -> Path: return self.root / ".env"
    @property
    def models_path(self) -> Path: return self.root / "models.json"
    @property
    def prompts_dir(self) -> Path: return self.root / "prompts"
    @property
    def genres(self) -> Path: return self.root / "reference" / "genres.md"
    @property
    def templates(self) -> Path: return self.root / "templates"
    @property
    def docx_reference(self) -> Path: return self.templates / "manuscript.docx"

    def models(self) -> dict:
        return json.loads(self.models_path.read_text())

    def key_names(self) -> list[str]:
        """Which key names `.env` defines. Names only -- no caller can log a
        value by accident, because no caller is ever handed one."""
        if not self.dotenv.is_file():
            return []
        names = []
        for line in self.dotenv.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                names.append(line.split("=", 1)[0].strip())
        return names

    # -- the library ------------------------------------------------------
    @property
    def library(self) -> Path: return self.root / "library"
    @property
    def library_notes(self) -> Path: return self.library / "notes"
    @property
    def library_index(self) -> Path: return self.library / "index.sqlite3"
    @property
    def pins(self) -> Path: return self.root / "library.pins.json"

    # -- the book ---------------------------------------------------------
    @property
    def premise(self) -> Path: return self.root / "premise.md"
    @property
    def brainstorm(self) -> Path: return self.root / "brainstorm.md"
    @property
    def dossier(self) -> Path: return self.root / "dossier.md"
    @property
    def genre(self) -> Path: return self.root / "genre.json"
    @property
    def style(self) -> Path: return self.root / "style.json"
    @property
    def outline(self) -> Path: return self.root / "outline.json"
    @property
    def comps_dir(self) -> Path: return self.root / "comps"

    # derived -- rebuilt from deltas/ and chapters/, safe to delete
    @property
    def state(self) -> Path: return self.root / "state.json"
    @property
    def manuscript(self) -> Path: return self.root / "manuscript.md"

    # -- per unit ---------------------------------------------------------
    def beats(self, unit: str | int) -> Path:
        return self.root / "beats" / f"ch{_id(unit)}.json"

    def part(self, unit: str) -> Path:
        """A drafted half. Rejects a whole-chapter id rather than guessing."""
        chapter, half = units.parse(unit)
        if not half:
            raise units.BadUnit(f"{unit!r} is a whole chapter; halves are 07a and 07b")
        return self.root / "chapters" / "parts" / f"ch{units.fmt(chapter, half)}.md"

    def delta(self, unit: str | int) -> Path:
        return self.root / "deltas" / f"ch{_id(unit)}.json"

    def words_check(self, unit: str | int) -> Path:
        return self.root / "checks" / f"ch{_id(unit)}.words.json"

    # -- per chapter ------------------------------------------------------
    def joined(self, chapter: str | int) -> Path:
        return self.root / "chapters" / "joined" / f"ch{_ch(chapter)}.md"

    def seam_sidecar(self, chapter: str | int) -> Path:
        """Seam offset, window bounds and per-paragraph hashes. Written by
        `join`, read by `seam_verify` to prove nothing outside the window moved.
        """
        return self.root / "chapters" / "joined" / f"ch{_ch(chapter)}.seam.json"

    def seam_check(self, chapter: str | int) -> Path:
        return self.root / "checks" / f"ch{_ch(chapter)}.seam.json"

    def chapter(self, chapter: str | int, version: int | None = None) -> Path:
        """`version=None` is the committed chapter -- the one you open to read.

        Each numbered version has exactly one writing stage: v1 is `seam`, v2 is
        `revise`. The unnumbered name is written only by `chapter_commit`, so a
        resumed `revise` re-reads v1 and rewrites v2 rather than revising its
        own output and destroying the only pre-revision copy.
        """
        ch = _ch(chapter)
        name = f"ch{ch}.md" if version is None else f"ch{ch}.v{version}.md"
        return self.root / "chapters" / name

    def chapter_versions(self, chapter: str | int) -> list[int]:
        ch = _ch(chapter)
        rx = re.compile(rf"ch{ch}\.v(\d+)\.md$")
        return sorted(
            int(m.group(1))
            for p in (self.root / "chapters").glob(f"ch{ch}.v*.md")
            if (m := rx.match(p.name))
        )

    def review(self, chapter: str | int, slot: str) -> Path:
        if slot not in ("a", "b", "reconciled"):
            raise ValueError(f"review slot must be a, b or reconciled, got {slot!r}")
        return self.root / "review" / f"ch{_ch(chapter)}.{slot}.json"

    # -- runs -------------------------------------------------------------
    @property
    def runs_dir(self) -> Path: return self.root / "runs"

    def run_dir(self, run_id: str) -> Path: return self.runs_dir / run_id

    def events(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    def prompt_pack(self, run_id: str, unit: str | int) -> Path:
        return self.run_dir(run_id) / "prompts" / f"ch{_id(unit)}.json"

    def latest_run(self) -> str | None:
        """Newest run holding an event log. Run ids sort chronologically by
        construction, so this is a sort rather than a stat of every directory."""
        if not self.runs_dir.is_dir():
            return None
        runs = [d.name for d in self.runs_dir.iterdir() if (d / "events.jsonl").is_file()]
        return max(runs) if runs else None

    @property
    def cover_dir(self) -> Path: return self.root / "cover"
    @property
    def export_dir(self) -> Path: return self.root / "export"


def _id(unit: str | int) -> str:
    """`7`, `"07"` and `"07a"` each spell themselves. Parsing lives in `units`
    so there is only ever one implementation of this grammar."""
    if isinstance(unit, int):
        return units.fmt(unit)
    return units.fmt(*units.parse(unit))


def _ch(chapter: str | int) -> str:
    n = chapter if isinstance(chapter, int) else units.parse(chapter)[0]
    return units.fmt(n)
