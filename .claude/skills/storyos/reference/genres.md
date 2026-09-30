# Genre contracts

A genre is not a set of props. It is a promise about what kind of satisfaction
the ending will deliver, and readers abandon books that break the promise far
more readily than books that are merely written badly. So a genre is stored as a
contract with obligations that can be checked, not as an adjective.

`genre.json` is written at stage 2 and read by `outline`, `beats`, `draft` and
`review`. Every obligation below becomes a line item the reviewers see, which is
why they are written as testable statements rather than atmosphere.

## How to use this file

Pick one primary genre. Pick at most one secondary. A book that is a mystery and
a romance has two sets of obligations and must satisfy both — that is a harder
book, not a richer one, and at 40 chapters it is achievable. Three genres is a
book that satisfies nobody, and the outline stage should refuse it.

The `scale` field matters as much as the genre. A 40-chapter novel at 130,000
words supports one central dramatic question, two or three subplots, and a cast
of six to nine speaking characters. More than that is not epic, it is thin.

## Mystery / detective

**The promise.** A question is posed that has a determinate answer, and the
reader is given a fair chance to reach it before the detective does.

**Obligations.**
- The solution is derivable from information on the page before it is revealed.
- Every clue that matters appears at least twice: once where it is missable, once
  where it is retrievable.
- The culprit appears in the first quarter and has a line of dialogue there.
- The detective's failure of understanding is characterological, not
  informational — they miss it because of who they are.

**Breaks the contract.** A solution resting on information withheld from the
reader. A confession that replaces deduction. A culprit introduced after the
midpoint. Coincidence doing the work of inference.

**Pacing signature.** Revelation every third chapter, each one reframing rather
than adding. The false solution lands around chapter 28 and must be genuinely
persuasive, or the real one has nothing to overturn.

## Thriller

**The promise.** The threat is active, escalating, and aimed at someone the
reader cares about. Time is running out and the reader can feel the clock.

**Obligations.**
- The antagonist is pursuing something, not waiting to be found.
- Every chapter ends with the protagonist's position worse than it began, or
  better in a way that costs.
- The protagonist's competence is established before it is tested.
- The stakes are specific and personal. "The city" is not stakes; a named person
  in the city is.

**Breaks the contract.** A passive protagonist who is acted upon for more than
two consecutive chapters. A threat that pauses for the plot's convenience. An
escape by capability not previously established.

**Pacing signature.** Scene lengths shorten as the book proceeds. Chapter 38 is
half the length of chapter 4. This is measurable and the style card should carry
it as a target.

## Literary

**The promise.** The sentences are worth reading for themselves, and the book's
meaning is produced by its form as much as its events.

**Obligations.**
- The prose has a discernible and consistent signature, measurable in sentence
  length variance, clause structure and diction register.
- Interiority is specific rather than declared. Not "she felt afraid" but the
  thought fear actually produced.
- The central image or motif recurs with altered meaning at least three times.
- The ending resolves a question of understanding, not a question of plot.

**Breaks the contract.** Beauty detached from function — description that does not
characterise the observer. Interiority that summarises rather than enacts. A plot
resolution substituting for a thematic one.

**Pacing signature.** Uneven by design. The style card's sentence-length variance
target should be high; a literary novel with uniform sentence rhythm is a thriller
with better vocabulary.

## Science fiction

**The promise.** One premise is changed about the world, and the book follows the
consequences honestly.

**Obligations.**
- The central novum is introduced by chapter 3 and its rules never change.
- Consequences are second-order. The invention changes the society, not just the
  plot.
- Exposition arrives through use, not explanation. A character operating the
  technology teaches more than a character describing it.
- The human problem would not be solvable by the technology, or the book has no
  tension.

**Breaks the contract.** Rules that bend to rescue the protagonist. A novum with
no social consequence. Two pages of a character explaining physics to someone who
should already know it.

**Pacing signature.** Front-loaded worldbuilding is the failure mode. World facts
should be distributed across the first fifteen chapters, and the ledger check can
enforce a cap per chapter.

## Fantasy

**The promise.** A world operating on different rules, and a cost structure that
makes power meaningful.

**Obligations.**
- Magic has a price, and the price is paid on the page at least twice.
- The limits are established before the limits matter.
- The world has economics, not just geography. Somebody grows the food.
- The protagonist's power does not scale faster than the threat.

**Breaks the contract.** A new capability arriving exactly when needed. A cost
that is described but never felt. Nine hundred years of history delivered in
chapter 1.

**Pacing signature.** The cast wants to grow past what 130,000 words can hold.
Six to nine speaking characters; the outline stage should refuse a tenth.

## Romance

**The promise.** Two people will end up together, and the reader will believe it.

**Obligations.**
- Both characters have an interior life independent of the relationship.
- The obstacle is internal. External-only obstacles produce a story about
  circumstances, not people.
- Each has something the other needs and cannot admit needing.
- Every scene they share moves the relationship, in either direction.

**Breaks the contract.** An obstacle removable by one honest conversation that
nobody has for no reason. A character whose only function is to be loved. A
reconciliation not earned by a change in understanding.

**Pacing signature.** Alternating proximity. The gap between them should narrow
and widen on a roughly six-chapter cycle, never monotonically.

## Horror

**The promise.** Dread will build, and the thing feared will not be entirely
explained.

**Obligations.**
- The ordinary is established before it is violated, at sufficient length that
  the violation registers.
- The threat's rules are partially knowable. Wholly unknowable is not frightening,
  it is arbitrary.
- The protagonist's reasonable choices make things worse.
- Something is left unexplained at the end, deliberately.

**Breaks the contract.** Full explanation, which converts horror into science
fiction. A monster that violates its own established pattern. Characters behaving
stupidly to sustain the plot.

**Pacing signature.** Long approach, compressed climax. The first ten chapters
should be measurably slower than genre average; the checker should not flag that
as a pacing fault when the genre is horror.

## Historical

**The promise.** The past will be rendered accurately enough that the reader
trusts it, and the story will be one only that period could produce.

**Obligations.**
- The central conflict depends on the period's specific constraints.
- Material detail is load-bearing — what things cost, how long travel took, who
  was permitted to speak to whom.
- Characters hold period-plausible beliefs, including uncomfortable ones.
- Anachronism in diction is tracked. The style card should carry a
  vocabulary-era constraint.

**Breaks the contract.** A modern sensibility in period costume. Research
displayed rather than deployed. A conflict that would work identically in any
century.

**Pacing signature.** Normal, but the `delta_extract` stage carries a heavier
load, since the ledger must track material facts a contemporary reader would
notice being wrong.

## The `genre.json` shape

```json
{
  "primary": "thriller",
  "secondary": null,
  "scale": { "chapters": 40, "words": 130000, "speaking_cast_max": 8,
             "subplots_max": 3 },
  "obligations": [
    { "id": "escalation", "statement": "Every chapter ends with the protagonist's position worse, or better at a cost.", "checkable": "per_chapter" },
    { "id": "active_antagonist", "statement": "The antagonist pursues an aim of their own in every act.", "checkable": "per_act" }
  ],
  "breaks": ["passive protagonist beyond two chapters", "threat pauses for plot convenience"],
  "pacing": { "scene_length_trend": "decreasing", "final_quarter_ratio": 0.55 }
}
```

`checkable` is one of `per_chapter`, `per_act`, `per_book`. The review stage shows
the writer only the obligations whose scope matches what was just drafted, so a
`per_book` obligation does not generate forty false alarms.

An obligation with no `checkable` scope is a mood, and the genre stage should not
write it.
