"""The reconcile stage: compare two blind reviews with arithmetic, not a judge.

No model runs here. A third model asked to adjudicate would reintroduce every
bias the blind pairing removes -- it would prefer the more fluent reviewer, the
one that wrote more, and the one whose style matches its own. Where the two
disagree, the writer decides, which is affordable because this is a one-user
tool.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

from .. import units
from ..state import names as ledger_names
from .reconcile import Status, reconcile, verify_entities
from .schema import parse


def _load(env, chapter: int, slot: str, model: str):
    path = env.review(chapter, slot)
    if not path.is_file():
        raise FileNotFoundError(
            f"{path.name} not found -- critic {slot} has not reviewed chapter "
            f"{chapter:02d} yet.")
    return parse(path.read_text(), reviewer=f"critic_{slot}", model=model)


def run(env, chapter: int | str) -> dict:
    ch = chapter if isinstance(chapter, int) else units.parse(chapter)[0]

    source = env.chapter(ch, 1)
    if not source.is_file():
        raise FileNotFoundError(
            f"{source.name} not found -- reviewers read the seam-repaired "
            f"chapter, never two loose halves.")
    text = source.read_text()

    bench = env.models().get("critics", {})
    registry = {r["id"]: r for r in env.models().get("registry", []) if "id" in r}
    model_a = registry.get(bench.get("slot_a"), {}).get("model", "unknown")
    model_b = registry.get(bench.get("slot_b"), {}).get("model", "unknown")

    a = _load(env, ch, "a", model_a)
    b = _load(env, ch, "b", model_b)

    rec = reconcile(a, b, text)

    # A reviewer that invented a character to object to is caught in Python.
    state = json.loads(env.state.read_text()) if env.state.is_file() else {}
    known = ledger_names(state)
    invented = verify_entities(rec.contested + [x for pair in rec.confirmed for x in pair],
                              state)
    if invented:
        ids = {id(f) for f in invented}
        rec.contested = [f for f in rec.contested if id(f) not in ids]
        rec.confirmed = [p for p in rec.confirmed
                         if id(p[0]) not in ids and id(p[1]) not in ids]
        rec.hallucinated.extend(invented)

    payload = {
        "chapter": units.fmt(ch),
        "source": source.name,
        # Spans are offsets into the text as reviewed. Recording its hash lets
        # the UI tell the writer "this chapter changed since review, spans are
        # stale" instead of highlighting the wrong words after a hand edit.
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "summary": rec.summary(),
        "agreement_rate": round(rec.agreement_rate, 4),
        "verdicts": rec.verdicts,
        "schema_violations": rec.schema_violations,
        "ledger_names_known": len(known),
        "confirmed": [[_row(x) for x in pair] for pair in rec.confirmed],
        "contested": [_row(f) for f in rec.contested],
        "hallucinated": [_row(f) for f in rec.hallucinated],
    }

    out = env.review(ch, "reconciled")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def _row(f) -> dict:
    d = dataclasses.asdict(f)
    d["status"] = str(f.status)
    d["span"] = list(f.span) if f.span else None
    return d


def findings_for_revise(env, chapter: int | str) -> list[dict]:
    """Only the confirmed ones. Contested findings wait for the writer.

    `revise` acts on this list. Letting it act on contested findings would
    make a single reviewer's opinion authoritative, which is the thing the
    blind pairing exists to prevent.
    """
    ch = chapter if isinstance(chapter, int) else units.parse(chapter)[0]
    path = env.review(ch, "reconciled")
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} not found -- reconcile runs first")
    doc = json.loads(path.read_text())

    out = []
    for pair in doc["confirmed"]:
        a = pair[0]
        out.append({"quote": a["quote"], "span": a["span"],
                    "why_it_fails": a["why_it_fails"],
                    "reaches_for": a["reaches_for"],
                    "works_here": a["works_here"],
                    "raised_by": "both reviewers"})
    # Contested findings the writer explicitly accepted.
    for f in doc["contested"]:
        if f.get("accepted"):
            out.append({"quote": f["quote"], "span": f["span"],
                        "why_it_fails": f["why_it_fails"],
                        "reaches_for": f["reaches_for"],
                        "works_here": f["works_here"],
                        "raised_by": f"{f['reviewer']}, accepted by you"})
    return out
