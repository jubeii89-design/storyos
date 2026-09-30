"""The environment: one directory that holds everything.

    ~/writing/                 the environment -- nothing lives outside it
      .storyos                 marker + config; this file is what makes it one
      models.json              the bench, shared by every book
      library/                 knowledge shared across books
      projects/<slug>/         one book

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

import copy
import dataclasses
import json
import os
import re
from pathlib import Path

from . import units

MARKER = ".storyos"
ENV_VAR = "STORYOS_ENV"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$")


class EnvNotFound(Exception):
    pass


class ProjectNotFound(Exception):
    pass


class BadSlug(ValueError):
    pass


def check_slug(slug: str) -> str:
    """Lowercase, hyphenated, filesystem- and URL-safe. Rejected, never coerced.

    Silently rewriting a slug means the directory is not the name the writer
    typed, and every later `storyos <slug>` misses.
    """
    if not isinstance(slug, str) or not SLUG.match(slug):
        raise BadSlug(f"bad slug {slug!r}: want lowercase letters, digits and hyphens")
    return slug


@dataclasses.dataclass(frozen=True, slots=True)
class Env:
    root: Path
    config: dict

    @classmethod
    def find(cls, explicit: str | os.PathLike | None = None) -> Env:
        """Locate the environment: explicit argument, then $STORYOS_ENV, then
        by walking up from the working directory looking for the marker.

        The walk is last because it is the convenient one and convenience should
        not silently win over a stated intention.
        """
        for cand in cls._candidates(explicit):
            if (cand / MARKER).is_file():
                return cls(cand.resolve(), json.loads((cand / MARKER).read_text() or "{}"))

        tried = ", ".join(str(c) for c in cls._candidates(explicit))
        raise EnvNotFound(f"no {MARKER} found. Looked in: {tried}. "
                          f"Run `storyos init <dir>` to make one.")

    @staticmethod
    def _candidates(explicit) -> list[Path]:
        if explicit:
            return [Path(explicit).expanduser()]
        if os.environ.get(ENV_VAR):
            return [Path(os.environ[ENV_VAR]).expanduser()]
        here = Path.cwd().resolve()
        return [here, *here.parents]

    # -- environment-level paths, shared by every book --------------------
    @property
    def marker(self) -> Path: return self.root / MARKER
    @property
    def dotenv(self) -> Path: return self.root / ".env"
    @property
    def models_path(self) -> Path: return self.root / "models.json"
    @property
    def rules_dir(self) -> Path: return self.root / "prompts"
    @property
    def genres(self) -> Path: return self.root / "reference" / "genres.md"
    @property
    def templates(self) -> Path: return self.root / "templates"
    @property
    def docx_reference(self) -> Path: return self.templates / "manuscript.docx"
    @property
    def library(self) -> Path: return self.root / "library"
    @property
    def library_notes(self) -> Path: return self.library / "notes"
    @property
    def library_index(self) -> Path: return self.library / "index.sqlite3"
    @property
    def projects_dir(self) -> Path: return self.root / "projects"

    def projects(self) -> list[str]:
        if not self.projects_dir.is_dir():
            return []
        return sorted(p.name for p in self.projects_dir.iterdir()
                      if (p / "project.json").is_file())

    def project(self, slug: str) -> Project:
        check_slug(slug)
        root = self.projects_dir / slug
        if not (root / "project.json").is_file():
            known = ", ".join(self.projects()) or "none yet"
            raise ProjectNotFound(f"no project {slug!r} in {self.root}. Have: {known}")
        return Project(self, slug, root)

    def key_names(self) -> list[str]:
        """Which key names `.env` defines. Names only -- values are never read
        here, so no caller can log one by accident."""
        if not self.dotenv.is_file():
            return []
        out = []
        for line in self.dotenv.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                out.append(line.split("=", 1)[0].strip())
        return out


def merge_models(base: dict, override: dict) -> dict:
    """Merge a project's `models.json` over the environment's, per section.

    `registry` merges by row `id`, because it is a catalogue of what is
    reachable rather than a choice -- a key added once at environment level
    must reach every book, and a project withdraws a row with
    `"enabled": false` instead of deleting it.

    Every other section replaces wholesale. Deep per-key merging looks
    friendlier and is worse: a project setting `stages.draft = {"model": X}`
    would silently inherit the environment's `escalate_to`, producing a
    configuration that neither file states and that nothing can remove.
    """
    out = copy.deepcopy(base)

    for section, value in override.items():
        if section == "registry" and isinstance(value, list):
            rows = {r["id"]: r for r in out.get("registry", []) if "id" in r}
            for row in value:
                if "id" not in row:
                    raise ValueError(f"registry row without an id: {row!r}")
                rows[row["id"]] = {**rows.get(row["id"], {}), **row}
            out["registry"] = list(rows.values())
        else:
            out[section] = copy.deepcopy(value)

    return out


@dataclasses.dataclass(frozen=True, slots=True)
class Project:
    env: Env
    slug: str
    root: Path

    # -- book level -------------------------------------------------------
    @property
    def manifest(self) -> Path: return self.root / "project.json"
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
    def pins(self) -> Path: return self.root / "library.pins.json"
    @property
    def models_override(self) -> Path: return self.root / "models.json"
    @property
    def comps_dir(self) -> Path: return self.root / "comps"

    # derived -- rebuilt from the files below, never edited by hand
    @property
    def state(self) -> Path: return self.root / "state.json"
    @property
    def manuscript(self) -> Path: return self.root / "manuscript.md"

    def models(self) -> dict:
        base = json.loads(self.env.models_path.read_text())
        if self.models_override.is_file():
            return merge_models(base, json.loads(self.models_override.read_text()))
        return base

    # -- per unit ---------------------------------------------------------
    def beats(self, unit: str | int) -> Path:
        return self.root / "beats" / f"ch{self._id(unit)}.json"

    def part(self, unit: str) -> Path:
        """A drafted half. Rejects a whole-chapter id rather than guessing."""
        chapter, half = units.parse(unit)
        if not half:
            raise units.BadUnit(f"{unit!r} is a whole chapter; halves are 07a and 07b")
        return self.root / "chapters" / "parts" / f"ch{units.fmt(chapter, half)}.md"

    def delta(self, unit: str | int) -> Path:
        return self.root / "deltas" / f"ch{self._id(unit)}.json"

    def words_check(self, unit: str) -> Path:
        return self.root / "checks" / f"ch{self._id(unit)}.words.json"

    # -- per chapter ------------------------------------------------------
    def joined(self, chapter: str | int) -> Path:
        return self.root / "chapters" / "joined" / f"ch{self._ch(chapter)}.md"

    def seam_sidecar(self, chapter: str | int) -> Path:
        """Seam offset, window bounds and per-paragraph hashes. Written by
        `join`, read by `seam_verify` to prove nothing outside the window moved."""
        return self.root / "chapters" / "joined" / f"ch{self._ch(chapter)}.seam.json"

    def seam_check(self, chapter: str | int) -> Path:
        return self.root / "checks" / f"ch{self._ch(chapter)}.seam.json"

    def chapter(self, chapter: str | int, version: int | None = None) -> Path:
        """`version=None` is the committed chapter -- the one you open to read.

        A numbered version has exactly one writing stage: v1 is `seam`, v2 is
        `revise`. The unnumbered name is written only by `chapter_commit`, so a
        resumed `revise` re-reads v1 and rewrites v2 instead of revising its
        own output and destroying the only pre-revision copy.
        """
        ch = self._ch(chapter)
        name = f"ch{ch}.md" if version is None else f"ch{ch}.v{version}.md"
        return self.root / "chapters" / name

    def chapter_versions(self, chapter: str | int) -> list[int]:
        ch = self._ch(chapter)
        found = []
        for p in (self.root / "chapters").glob(f"ch{ch}.v*.md"):
            m = re.match(rf"ch{ch}\.v(\d+)\.md$", p.name)
            if m:
                found.append(int(m.group(1)))
        return sorted(found)

    def review(self, chapter: str | int, slot: str) -> Path:
        if slot not in ("a", "b", "reconciled"):
            raise ValueError(f"review slot must be a, b or reconciled, got {slot!r}")
        return self.root / "review" / f"ch{self._ch(chapter)}.{slot}.json"

    # -- runs -------------------------------------------------------------
    @property
    def runs_dir(self) -> Path: return self.root / "runs"

    def run_dir(self, run_id: str) -> Path: return self.runs_dir / run_id

    def events(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    def prompt_pack(self, run_id: str, unit: str) -> Path:
        return self.run_dir(run_id) / "prompts" / f"ch{self._id(unit)}.json"

    def latest_run(self) -> str | None:
        """Newest run holding an event log. Run ids sort chronologically by
        construction, so this is a sort and not a stat of every directory."""
        if not self.runs_dir.is_dir():
            return None
        runs = [d.name for d in self.runs_dir.iterdir()
                if (d / "events.jsonl").is_file()]
        return max(runs) if runs else None

    @property
    def cover_dir(self) -> Path: return self.root / "cover"
    @property
    def export_dir(self) -> Path: return self.root / "export"

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _id(unit: str | int) -> str:
        """`7` and `"07"` and `"07a"` all spell themselves; parsing lives in
        `units` so there is only ever one implementation of this grammar."""
        if isinstance(unit, int):
            return units.fmt(unit)
        return units.fmt(*units.parse(unit))

    @staticmethod
    def _ch(chapter: str | int) -> str:
        n = chapter if isinstance(chapter, int) else units.parse(chapter)[0]
        return units.fmt(n)
