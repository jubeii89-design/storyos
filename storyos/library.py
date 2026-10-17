"""The library: craft knowledge the system reads so the writer need not recall it.

Everything lands as Markdown in `library/notes/`, whatever it arrived as. One
format downstream means the search, the reader and the pin anchors each have a
single thing to understand, and Markdown keeps the headings that pins attach
to -- which plain-text extraction destroys.

Each note carries a small front-matter block recording where it came from and
when. A library of unattributed text is a library you stop trusting.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
from pathlib import Path

SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str, *, limit: int = 60) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = SLUG.sub("-", s.lower()).strip("-")
    return (s[:limit].rstrip("-") or "note")


def front_matter(meta: dict) -> str:
    lines = ["---"]
    for k in ("title", "source", "source_id", "fetched", "modified", "format", "words"):
        if meta.get(k) not in (None, ""):
            lines.append(f"{k}: {json.dumps(meta[k])}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def add(env, text: str, *, title: str, source: str, source_id: str = "",
        modified: str = "", fmt: str = "markdown", overwrite: bool = False) -> Path:
    """Write one note. Returns its path."""
    env.library_notes.mkdir(parents=True, exist_ok=True)
    body = text.strip() + "\n"
    meta = {"title": title, "source": source, "source_id": source_id,
            "fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "modified": modified, "format": fmt, "words": len(body.split())}

    path = env.library_notes / f"{slugify(title)}.md"
    if path.exists() and not overwrite:
        # Same source re-imported: replace it. A different source that happens
        # to share a title gets a suffix, so one never silently eats the other.
        existing = path.read_text(errors="replace")
        if source_id and f'source_id: {json.dumps(source_id)}' in existing:
            overwrite = True
        else:
            n = 2
            while (alt := env.library_notes /
                   f"{slugify(title)}-{n}.md").exists():
                n += 1
            path = alt

    path.write_text(front_matter(meta) + body)
    return path


def notes(env) -> list[dict]:
    if not env.library_notes.is_dir():
        return []
    out = []
    for p in sorted(env.library_notes.glob("**/*.md")):
        raw = p.read_text(errors="replace")
        meta = _read_front(raw)
        out.append({"path": str(p.relative_to(env.root)), "file": p.name,
                    "title": meta.get("title", p.stem),
                    "source": meta.get("source", "unknown"),
                    "words": meta.get("words") or len(raw.split()),
                    "sha": hashlib.sha256(raw.encode()).hexdigest()[:12]})
    return out


def _read_front(raw: str) -> dict:
    if not raw.startswith("---"):
        return {}
    end = raw.find("\n---", 3)
    if end < 0:
        return {}
    meta = {}
    for line in raw[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            try:
                meta[k.strip()] = json.loads(v.strip())
            except json.JSONDecodeError:
                meta[k.strip()] = v.strip()
    return meta


def body_of(raw: str) -> str:
    """The note without its front matter, for search and for pinning."""
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end >= 0:
            return raw[end + 4:].lstrip("\n")
    return raw


# -- pins -----------------------------------------------------------------

def pins(env) -> list[dict]:
    if not env.pins.is_file():
        return []
    try:
        return json.loads(env.pins.read_text())
    except json.JSONDecodeError:
        return []


def pin(env, note_path: str, excerpt: str, why: str, *,
        chapters: list[int] | None = None) -> dict:
    """Freeze a passage into the book's working context.

    The excerpt is copied, and the note's hash recorded. Editing the shared
    note later therefore cannot silently change what chapter 12 was written
    against -- the pin goes stale and says so, instead of rewriting history.
    """
    p = env.root / note_path
    if not p.is_file():
        raise FileNotFoundError(note_path)
    raw = p.read_text(errors="replace")
    if excerpt.strip() and excerpt.strip() not in raw:
        raise ValueError("that excerpt is not in the note")

    rec = {"id": f"pin-{len(pins(env)) + 1:04d}", "note": note_path,
           "sha256": hashlib.sha256(raw.encode()).hexdigest(),
           "excerpt": excerpt.strip(), "why": why,
           "chapters": chapters or [],
           "pinned_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    all_pins = pins(env) + [rec]
    env.pins.write_text(json.dumps(all_pins, indent=2) + "\n")
    return rec


def stale_pins(env) -> list[dict]:
    """Pins whose note changed after they were made."""
    out = []
    for rec in pins(env):
        p = env.root / rec["note"]
        if not p.is_file():
            out.append({**rec, "why_stale": "the note was deleted"})
            continue
        now = hashlib.sha256(p.read_text(errors="replace").encode()).hexdigest()
        if now != rec.get("sha256"):
            out.append({**rec, "why_stale": "the note was edited after pinning"})
    return out
