"""Unit identifiers -- the one grammar every path and every event key shares.

A book is addressed in three sizes, and all three are written the same way so
the writer can type them into a terminal without thinking:

    07      the whole chapter -- the joined, reviewed, finished thing
    07a     its first half, ~1,700 words, drafted on its own
    07b     its second half

`(chapter, part)` is the pair. `part` is "a", "b", or "" for the chapter itself,
which is exactly the shape `events.completed_stages` keys on, so a unit id and
a resume key are two spellings of one fact. Keeping that in a single module is
the point: the half-chapter rule touches paths, prompts, events, checks and the
UI, and a second opinion about whether "7a" is valid would desynchronise them.
"""

from __future__ import annotations

import re

MAX_CHAPTER = 999
PARTS = ("a", "b")

_UNIT = re.compile(r"^(?:ch)?0*([0-9]{1,3})([ab])?$", re.IGNORECASE)


class BadUnit(ValueError):
    """Raised for anything that is not a unit id. Never guessed at."""


def parse(unit: str) -> tuple[int, str]:
    """Return `(chapter, part)`; part is "a", "b", or "" for a whole chapter.

    Accepts `07a`, `7a`, `ch07a`, `CH7A` and the part-less forms of each,
    because all of them get typed. Rejects everything else rather than
    guessing -- a silently mis-parsed unit id writes a half-chapter over the
    wrong chapter, and that is not a failure you notice on the day.
    """
    if not isinstance(unit, str):
        raise BadUnit(f"unit must be a string, got {type(unit).__name__}")

    m = _UNIT.match(unit.strip())
    if not m:
        raise BadUnit(f"not a unit id: {unit!r} (want 07, 07a or 07b)")

    chapter = int(m.group(1))
    if not 1 <= chapter <= MAX_CHAPTER:
        raise BadUnit(f"chapter out of range: {chapter} (want 1-{MAX_CHAPTER})")

    return chapter, (m.group(2) or "").lower()


def fmt(chapter: int, part: str = "") -> str:
    """Canonical spelling: zero-padded to two digits, lowercase part.

    Two digits because a 40-chapter book sorts correctly that way in any file
    listing, and three only when the book is genuinely that long.
    """
    if not 1 <= chapter <= MAX_CHAPTER:
        raise BadUnit(f"chapter out of range: {chapter}")
    part = (part or "").lower()
    if part not in ("", *PARTS):
        raise BadUnit(f"part must be 'a', 'b' or empty, got {part!r}")
    return f"{chapter:02d}{part}"


def halves(chapter: int) -> tuple[str, str]:
    """The two half ids for a chapter, in drafting order."""
    return fmt(chapter, "a"), fmt(chapter, "b")


def sibling(unit: str) -> str:
    """The other half of the same chapter. Raises for a whole-chapter id."""
    chapter, part = parse(unit)
    if not part:
        raise BadUnit(f"{unit!r} is a whole chapter and has no sibling half")
    return fmt(chapter, "b" if part == "a" else "a")


def whole(unit: str) -> str:
    """The chapter a unit belongs to, as a chapter id."""
    chapter, _ = parse(unit)
    return fmt(chapter)


def is_half(unit: str) -> bool:
    return bool(parse(unit)[1])


def predecessor(unit: str) -> str | None:
    """The unit whose prose immediately precedes this one, or None at the start.

    This is what decides which text goes into a draft prompt as VERBATIM, so it
    encodes the half-chapter rule directly: half b follows half a of the same
    chapter, and half a follows the *previous chapter*, which by then is joined
    and reviewed rather than two loose halves.
    """
    chapter, part = parse(unit)
    if part == "b":
        return fmt(chapter, "a")
    if chapter == 1:
        return None
    return fmt(chapter - 1)          # the finished chapter, not its half b


def units(chapters: int) -> list[str]:
    """Every drafting unit for a book, in order: 01a, 01b, 02a, 02b, ..."""
    return [fmt(c, p) for c in range(1, chapters + 1) for p in PARTS]


def resume_key(unit: str, stage: str) -> tuple[int, str, str]:
    """`(chapter, part, stage)` -- what `events.completed_stages` returns."""
    chapter, part = parse(unit)
    return chapter, part, stage
