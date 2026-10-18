"""One search field over prose, library, findings and the ledger.

SQLite with FTS5, which ships inside Python. The index is derived and
disposable: it is rebuilt from the files, never the other way round, so losing
it costs a second and losing the files costs the book.

Rows are re-indexed only when a file's hash changes, because the alternative --
reindexing everything on every keystroke -- turns a search box into a fan.
"""

from __future__ import annotations

import hashlib
import json
import re
import contextlib
import sqlite3
from pathlib import Path

from . import library, units

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc (
  path TEXT PRIMARY KEY, kind TEXT, unit TEXT, title TEXT,
  sha TEXT, words INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS ft USING fts5(
  body, path UNINDEXED, kind UNINDEXED, unit UNINDEXED, title UNINDEXED,
  tokenize = "unicode61 remove_diacritics 2"
);
"""

KINDS = ("prose", "half", "library", "finding", "ledger", "note")


def _db(env) -> sqlite3.Connection:
    env.library.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(env.library_index)
    con.executescript(SCHEMA)
    return con


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _put(con, path: str, kind: str, unit: str, title: str, body: str) -> bool:
    """Index one document. Returns True if it actually changed."""
    sha = _sha(body)
    row = con.execute("SELECT sha FROM doc WHERE path = ?", (path,)).fetchone()
    if row and row[0] == sha:
        return False
    con.execute("DELETE FROM ft WHERE path = ?", (path,))
    con.execute("INSERT INTO ft (body, path, kind, unit, title) VALUES (?,?,?,?,?)",
                (body, path, kind, unit, title))
    con.execute(
        "INSERT INTO doc (path, kind, unit, title, sha, words) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, unit=excluded.unit, "
        "title=excluded.title, sha=excluded.sha, words=excluded.words",
        (path, kind, unit, title, sha, len(body.split())))
    return True


def _reindex(con, env, *, full: bool = False) -> dict:
    """Bring the index up to date. Cheap when nothing changed."""
    if full:
        con.executescript("DELETE FROM ft; DELETE FROM doc;")

    seen: set[str] = set()
    changed = 0

    def put(path: Path | str, kind: str, unit: str, title: str, body: str) -> None:
        nonlocal changed
        rel = str(path) if isinstance(path, str) else str(Path(path).relative_to(env.root))
        seen.add(rel)
        if body.strip() and _put(con, rel, kind, unit, title, body):
            changed += 1

    chapters = env.root / "chapters"
    if chapters.is_dir():
        for p in sorted(chapters.glob("ch*.md")):
            if ".v" in p.name:
                continue
            try:
                ch = units.parse(p.stem.removeprefix("ch"))[0]
            except units.BadUnit:
                continue
            put(p, "prose", units.fmt(ch), f"Chapter {ch}", p.read_text(errors="replace"))
        for p in sorted((chapters / "parts").glob("ch*.md")) if (chapters / "parts").is_dir() else []:
            stem = p.stem.removeprefix("ch")
            put(p, "half", stem, f"Half {stem}", p.read_text(errors="replace"))

    for note in library.notes(env):
        raw = (env.root / note["path"]).read_text(errors="replace")
        put(note["path"], "library", "", note["title"], library.body_of(raw))

    review = env.root / "review"
    if review.is_dir():
        for p in sorted(review.glob("*.reconciled.json")):
            try:
                doc = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            lines = []
            for bucket in ("confirmed", "contested", "hallucinated"):
                for item in doc.get(bucket, []):
                    for f in (item if isinstance(item, list) else [item]):
                        lines.append(
                            f"[{bucket}] {f.get('quote','')} — "
                            f"{f.get('why_it_fails','')} {f.get('reaches_for','')}")
            put(p, "finding", doc.get("chapter", ""),
                f"Review of chapter {doc.get('chapter','')}", "\n".join(lines))

    if env.state.is_file():
        try:
            state = json.loads(env.state.read_text())
        except json.JSONDecodeError:
            state = {}
        for section, rows in state.items():
            for row in rows if isinstance(rows, list) else []:
                name = row.get("name", "")
                if not name:
                    continue
                body = " ".join(f"{k}: {v}" for k, v in row.items()
                                if isinstance(v, str))
                put(f"state.json#{section}/{name}", "ledger", "",
                    f"{section[:-1] if section.endswith('s') else section}: {name}", body)

    # Anything indexed before but gone from disk.
    stale = [r[0] for r in con.execute("SELECT path FROM doc").fetchall()
             if r[0] not in seen]
    for path in stale:
        con.execute("DELETE FROM ft WHERE path = ?", (path,))
        con.execute("DELETE FROM doc WHERE path = ?", (path,))

    con.commit()
    total = con.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
    words = con.execute("SELECT COALESCE(SUM(words),0) FROM doc").fetchone()[0]
    return {"documents": total, "words": words, "updated": changed,
            "removed": len(stale)}


# FTS5 treats these as operators; a writer searching for a character's
# possessive or a quoted phrase should not get a syntax error.
_SPECIAL = re.compile(r'["*()^:]|(?<![A-Za-z])(AND|OR|NOT|NEAR)(?![A-Za-z])')


def _as_query(text: str) -> str:
    """Turn what the writer typed into an FTS5 MATCH expression.

    Quoted phrases are honoured; everything else is ANDed. Bare operators are
    escaped rather than interpreted, because someone searching for the word
    `not` means the word.
    """
    phrases = re.findall(r'"([^"]+)"', text)
    rest = re.sub(r'"[^"]*"', " ", text)
    terms = [t for t in _SPECIAL.sub(" ", rest).split() if len(t) > 1]
    parts = [f'"{p}"' for p in phrases if p.strip()]
    parts += [f'"{t}"' for t in terms]
    return " AND ".join(parts)


def _query(con, env, text: str, *, kinds: tuple[str, ...] = (), limit: int = 60) -> dict:
    text = (text or "").strip()
    if len(text) < 2:
        return {"query": text, "results": [], "note": "type at least 2 characters"}

    match = _as_query(text)
    if not match:
        return {"query": text, "results": [], "note": "nothing searchable in that"}

    sql = ("SELECT path, kind, unit, title, "
           "snippet(ft, 0, '\x02', '\x03', '…', 14) AS snip, bm25(ft) AS rank "
           "FROM ft WHERE ft MATCH ?")
    args: list = [match]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)

    try:
        rows = con.execute(sql, args).fetchall()
    except sqlite3.OperationalError as x:
        return {"query": text, "results": [], "note": f"bad search: {x}"}

    out = [{"path": p, "kind": k, "unit": u, "title": t,
            "snippet": s.replace("\x02", "«").replace("\x03", "»"),
            "rank": round(r, 3)}
           for p, k, u, t, s, r in rows]
    counts = {}
    for r in out:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {"query": text, "match": match, "results": out,
            "total": len(out), "by_kind": counts}


def stats(env) -> dict:
    if not env.library_index.is_file():
        return {"indexed": False, "documents": 0}
    con = _db(env)
    rows = con.execute("SELECT kind, COUNT(*), COALESCE(SUM(words),0) "
                       "FROM doc GROUP BY kind").fetchall()
    con.close()
    return {"indexed": True,
            "documents": sum(r[1] for r in rows),
            "words": sum(r[2] for r in rows),
            "by_kind": {r[0]: r[1] for r in rows}}


def reindex(env, *, full: bool = False) -> dict:
    """Bring the index up to date, closing the handle on every path.

    reindex walks the whole book, so one malformed review file used to leave
    the connection open and the index locked for the rest of the session.
    """
    with contextlib.closing(_db(env)) as con:
        return _reindex(con, env, full=full)


def query(env, text: str, *, kinds: tuple[str, ...] = (), limit: int = 60) -> dict:
    with contextlib.closing(_db(env)) as con:
        return _query(con, env, text, kinds=kinds, limit=limit)
