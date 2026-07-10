# A Socio-Technical Audit of the Official Foreign Travel Parser

*July 2026*

This document is a critical retrospective on the v3 rebuild of this repository: the
parser, the review tooling, the reference data, and the process that produced them. It
is written for someone deciding whether to trust this system, extend it, or copy its
approach for a similar document-parsing project. It is deliberately more judgmental
than the other documentation. The short version: the system is in much better shape
than what it replaced, its core design commitments are sound and mutually reinforcing,
and several of its worst bugs were caught only because the process was built to catch
them — but it also carries real debts, a few of which are load-bearing.

## What this system is, socially

The technical artifact is a parser that turns ~30 years of fixed-width Congressional
Record text into structured JSON. The social artifact is a **chain of custody for
journalistic claims**: a reporter who publishes "Rep. X spent $Y on travel to Z" needs
to be able to trace that number back to a specific line of a specific government
publication, and to know whether a human or an algorithm made every judgment call along
the way.

Almost every design decision below is downstream of that requirement. Where the system
is good, it is good because it took provenance seriously. Where it is weak, the
weakness is usually a place where convenience quietly ate provenance.

The assumed user is specific and narrow: a single technically-comfortable person
(comfortable with `uv`, a terminal, and reading a flag census) working locally, with
the time to hand-review a queue of parser failures. Nothing about the system supports
teams, concurrent reviewers, or non-technical users, and that is by explicit design —
but it should be understood as a real constraint, not an oversight. If this project
ever acquires a second simultaneous reviewer, the corrections overlay's
last-writer-wins semantics will silently discard someone's work.

## The founding decision: deterministic first, LLM never in the happy path

The rebuild began with an explicit question — "would an LLM help parse this data?" —
and the answer shaped everything: **no, not as the primary parser**. The data is
tabular and fixed-width; deterministic parsing is reproducible, free at corpus scale,
auditable, and structurally incapable of hallucinating a dollar amount. Every accuracy
problem in the old parser turned out to be deterministically fixable.

The LLM got exactly one job: a narrow, off-by-default fallback (`--llm-fallback`) for
tables that already failed deterministic parsing, whose output must pass the same
arithmetic invariants as everything else or be flagged `LLM_UNVERIFIED` for a human.
Reports repaired this way are permanently tagged `parse_method: "llm"`.

This was the right call, and the evidence is quantitative: the deterministic rebuild
alone recovered ~7,400 records the old parser silently dropped (12% of the corpus) and
added cost extraction the old parser never had — no model required. The LLM fallback
matters for roughly 60 tables out of ~3,300.

**Critical note:** the discipline held, but partly by luck of the data. Had the corpus
been messier, the pressure to widen the LLM's role would have been real, and the
codebase has no structural mechanism preventing that beyond convention. The invariant
re-validation of LLM output is the one genuine guardrail, and it is a good one — the
model never gets a free pass on arithmetic.

## "Never drop, always flag" — the load-bearing philosophy

The old parser silently dropped anything it couldn't parse: rows without a magic
delimiter, trips that crossed year boundaries, dates its validator disliked. The new
parser's central rule is that **nothing is ever discarded** — an unparseable cost cell,
a garbled date, an unmatched name all produce a kept record with a flag
(`UNPARSEABLE_COST_CELL`, `DEPARTURE_BEFORE_ARRIVAL`, `MEMBER_UNMATCHED`, ~20 others).

This rule is what makes the rest of the system possible. The review UI is a flag-queue
browser. The LLM fallback's trigger criteria are flag predicates. The corpus-regression
test asserts per-year record counts never fall below the old parser's output — a
contract only expressible because records can't vanish silently.

**Where it went wrong, instructively:** flags were originally append-only, including in
`validate_report`. When the corrections workflow arrived, a human fixing an arithmetic
error found that the `ROW_SUM_MISMATCH` flag *survived the fix* — the report claimed a
problem that no longer existed, forever. The fix (making validation idempotent: clear
your own flags, recompute) was cheap, but the bug class is worth naming: **append-only
provenance and re-entrant pipelines conflict**, and the conflict only surfaces when a
human enters the loop. No amount of parser-only testing would have found it.

**Where it still hurts:** flag inflation. 2,374 of 3,070 visible reports carry at least
one flag — 77% of the corpus is "flagged for review." The largest contributor is
`MEMBER_UNMATCHED`, which fires for every bare staff name (correctly unmatched — staff
aren't in the Bioguide) as well as for genuinely broken Member names. A review queue
where everything is flagged prioritizes nothing. The flag-type filter in the UI is the
current mitigation, but the honest assessment is that the flag taxonomy conflates
"something is wrong" with "something is noteworthy," and a `STAFF_UNMATCHED` /
`MEMBER_UNMATCHED` split (or severity tiers) is overdue.

## Layout detection: earned complexity

The old parser used one hardcoded set of column offsets for a corpus containing
fourteen distinct table layouts across 25 years. The replacement detects columns
per-table by cross-checking header label positions against space-gutter patterns in the
actual data rows, with a confidence score and a fingerprint.

Two details deserve note as *earned* rather than speculative complexity. First, the
gutter detector originally accepted a single space as a column boundary and promptly
split "Germany, Rwanda" down the middle; requiring two-plus spaces took accuracy from
93.5% to 99.3%. Second, low-confidence layouts don't fail — they flag
(`LAYOUT_LOW_CONFIDENCE`), which routes them to the LLM fallback or the human queue.
The detector is allowed to be unsure, which is why it can afford to be aggressive.

The lesson generalizes: **measure before building** (the rebuild started with a
per-year coverage census of the old parser, not with code), and **give every component
an "I don't know" output** so uncertainty becomes routing information instead of either
a crash or a silent wrong answer.

## The reference-data lesson: nobody owns a snapshot

`members.csv` and `committees.csv` — the exact-match lookups for traveler names and
sponsoring committees — were inherited as frozen, hand-assembled snapshots. An audit
found `committees.csv` was missing every renamed historical committee (International
Relations, National Security, Government Reform...), leaving **36% of
committee-sponsored reports with no matched code**, and `members.csv`'s
one-spelling-per-person format left half of all Member names unmatched over
middle-initial differences as trivial as "Charles Rangel" vs. "CHARLES B. RANGEL."

The fix was to make the files *generated* (`oft-generate-reference-data`, from
unitedstates/congress-legislators data) rather than curated, expanding each person and
committee into every name form the reports actually print: accents folded, initials
collapsed, nicknames and diminutives both directions, surnames alone, suffixes with and
without commas. Committee-code misses fell from 36% to 8%; unmatched Members from 51%
to below 10% before fuzzy matching even runs.

The safety mechanism that made aggressive alias generation acceptable is the single
best idea in the matching layer: **any generated key claimed by two different people is
dropped entirely rather than resolved**. A wrong exact match is worse than no match
(which just flags for review), so ambiguity is voided, never guessed. "HON. KING"
matches no one; "HON. JACKSON-LEE" can only mean Sheila Jackson Lee. This turned what
would have been a risky heuristic pile into something with a provable no-false-positive
property — the review queue absorbs all residual uncertainty.

Two judgments here. Favorable: the generator prints its ambiguity drops and collisions,
so the curation debt is visible rather than buried. Unfavorable: the snapshot rot went
unnoticed for years because nothing exercised the files against ground truth — the
lesson is that **reference data needs either a regeneration pipeline or an owner, and a
repo full of tests had neither for its most consequential inputs**.

## The fuzzy matcher: a cautionary tale in three acts

Act one: the codebase shipped with a sophisticated date-indexed fuzzy name matcher —
DP-scored, permutation-searched, temporally indexed — behind an opt-in flag. Act two:
when finally run against the full corpus, it did not finish in 35 minutes and was
killed. Profiling found it recomputing identical string scores inside a 120-permutation
brute-force loop: 2 million scoring calls for ten queries. Act three: three
behavior-preserving changes (hoist the loop-invariant scores, memoize the pure
functions, skip permutations of components that can't score) made it **~87× faster with
byte-identical output** — the full corpus now takes 19 seconds.

The indictment is not the slow code; it's that *the feature had plausibly never been
run at scale by anyone*. It existed, it was documented, it was tested at unit
granularity — and it was unusable for its actual purpose. Unit tests validate logic;
only a corpus-scale run validates a tool. The equivalence-checking method used for the
optimization (capture results on representative queries before touching anything, diff
byte-for-byte after) is worth stealing for any refactor of scoring code.

A second, quieter lesson from the same component: the matcher's date-awareness turned
out to resolve most "ambiguous" names for free — the two Donald Paynes and two Duncan
Hunters never served simultaneously, so trip dates disambiguate them. Only one pair in
the whole corpus (the two Mike Rogerses, overlapping 2003–2015) needed the
committee-based disambiguation table that was built for it — ten hand-curated CSV rows.
**Sizing the solution to the measured problem** (ten rows, not an external historical
committee-membership dataset) was correct; the table's weakness is that its
justification lives in a commit message rather than in the file, which cannot carry
comments. If it grows, it needs provenance columns.

## The review UI: constraints as features, and one bad week of bugs

The reviewer chose `http.server` over Flask/FastAPI — zero new dependencies, one
process, bound to localhost only. For a single-operator tool this was right, and the
constraint had teeth: no framework meant no framework's cache headers, CSRF story, or
input validation, and each of those gaps eventually had to be closed by hand (more
below).

The corrections model is the most consequential design in the tool: human edits live in
a **separate overlay file keyed by stable report IDs**, never in the parser's output.
The parser's output stays regenerable; the human's work survives every re-parse;
`oft-parse --apply-corrections` merges the two and tags the results
(`MANUALLY_CORRECTED` / `HUMAN_CONFIRMED`). This is the provenance chain done right —
the machine's answer and the human's answer are never confused.

But the overlay's semantics contain a documented-yet-sharp edge: saving a report
resubmits **every field as an edit**, not a diff. The design doc chose this for
simplicity ("the whole form is always resubmitted"), and the consequence is that a
reviewed report is frozen at review time — if a future parser improvement would have
fixed a field the reviewer never touched, applying old corrections silently reverts it.
For a corpus that is re-parsed as the parser improves, this trades away exactly the
regenerability the overlay exists to protect. It is the design decision in this system
most likely to bite someone in a year, and it will bite silently.

The UI's implementation history is also where the process earned its keep, because the
per-task code reviews caught four genuine data-loss bugs before any real review work
was done:

1. **Blank-field coercion.** Blank cost cells are `null` in the data; HTML inputs
   render `null` and `""` identically; the form sent `""` back; Pydantic rejects `""`
   for a `Decimal`; and `apply_corrections` — correctly treating a bad correction as
   skip-and-log — **silently discarded the reviewer's entire edit set** for any report
   with a blank cost cell, which is most of them. The UI said "Saved." The save was
   real; the eventual apply was a no-op logged in a different process on a different
   day. This is the most instructive failure in the project: three components each
   behaving reasonably composing into silent data loss, discoverable only by tracing a
   value across the web/JSON/Pydantic type boundary. The first fix (track which fields
   started null) was itself incomplete — clearing a field that started *with* a value
   re-broke it — and only a second review pass got it right (nullability by field type,
   not by initial value).
2. **The lost-update race.** Two concurrent saves did unlocked read-modify-write on the
   overlay file; one reviewer's tab could silently erase the other's entry. A
   `threading.Lock` plus write-to-temp-and-rename fixed both the race and the
   torn-file-on-crash case. "Single user" turned out not to mean "single writer" — two
   tabs suffice.
3. **Typo'd edit paths vanishing.** An edit addressed to `sponsor.nam` didn't error; it
   added a junk key that Pydantic silently ignored, and the report was tagged
   `MANUALLY_CORRECTED` anyway. False provenance is worse than no provenance.
4. **Browser caching.** The server sent no `Cache-Control` headers, so browsers
   heuristically cached API responses *across server restarts* — after a re-parse, the
   UI showed stale data while the file on disk was correct. This one escaped every
   review and was found by the actual user in actual use, which is its own lesson: the
   reviews were rigorous about data flow and blind to HTTP semantics, because nobody's
   checklist included "what does a browser do with an unadorned 200."

The frontend has no automated tests (no JS test framework, by design — vanilla JS, no
build step). Manual browser verification was performed at each stage and caught real
issues, but this is the least protected surface in the codebase, and regressions there
will be found by users.

## Process: the audit of the audit

The build used a written-plan → per-task implementer → spec-compliance review →
code-quality review loop, with adversarial reviewers instructed to reproduce bugs
before asserting them. Judged by results, it worked: every Critical bug listed above
was found by a review pass, several via live reproduction scripts, and the two-stage
split mattered (spec compliance caught scope drift; quality review caught the bugs spec
compliance is blind to). A final whole-feature review then caught cross-cutting issues
the per-task lens structurally missed — inconsistent error handling between the two
CLIs, and a promised-but-unbuilt list-view feature.

Failures of process, because there were several:

- **CI was red on master and nobody knew.** The ruff step had been failing (233
  errors) on every push; the mypy step runs with `continue-on-error: true` and its ~25
  errors are permanent background noise. A CI that is allowed to be red trains everyone
  to ignore it — the ruff failure was only noticed when a *new* failure needed
  diagnosing. Tolerated-red is indistinguishable from no-CI.
- **"Supports Python 3.9" was a claim, not a fact.** All development ran on 3.10+; a
  `X | None` annotation crashed *every import* on 3.9, and it shipped to master and
  failed publicly in CI. The package had declared 3.9 support in `pyproject.toml` the
  whole time. If you claim a version, run the tests on that version before pushing —
  this now costs 30 seconds with `uv run --python 3.9`.
- **A bare `git commit` swept up unrelated staged deletions** mid-session, requiring a
  soft reset. Mechanical, recoverable, and the origin of a standing rule (always commit
  with explicit pathspecs during long mixed sessions) that should have been the rule
  from the start.
- **Predictions to the user outran verification twice**: "the review queue will
  shrink" (it shrank by ~4 reports out of 2,374 — technically true, practically
  misleading, because flag inflation was the real queue driver) and the browser-cache
  incident above, where correct-on-disk was confidently asserted while the user's
  screen said otherwise. Both were resolved quickly, but both were avoidable with one
  more measurement before speaking.

## The residual 1.8%, and why it should stay

After all matching work, 226 of 12,378 honorific-prefixed travelers remain without a
Bioguide ID. Roughly 100 of those rows are the House Sergeant at Arms, the Attending
Physician, the Clerk, and the Parliamentarian — people the reports style "Hon." who are
*not Members*, and whom the system correctly refuses to match. Most of the rest are
OCR-grade typos below the confidence threshold and genuinely ambiguous fragments.

This number is presented as a success, and it mostly is — the figure was 51% before the
work began. But the framing matters: the system's goal was never 0% unmatched; it was
**0% wrongly matched**, with everything uncertain routed to a human. On the evidence
available (every probed rejection was a correct rejection), it achieves that. The
honest caveat is that no systematic false-positive audit of the *matched* 98.2% has
been done — spot checks only. That audit is the single highest-value piece of
verification work remaining.

## Standing debts, ranked by how much they'll hurt

1. **Full-form correction snapshots** silently reverting future parser improvements on
   reviewed reports (design decision, needs revisiting before heavy review use).
2. **Flag inflation** making the review queue a poor prioritization tool (taxonomy
   change, cheap).
3. **No false-positive audit** of accepted bioguide matches (verification work).
4. **mypy `continue-on-error`** normalizing decay — the same mechanism that let ruff
   rot (either fix the 25 errors or stop pretending to run mypy).
5. **Untested frontend** (accepted risk, but it grows with every UI feature).
6. **`member_disambiguation.csv` without provenance columns** (fine at 10 rows, not at
   100).
7. **The `llm` extra requires Python 3.10** while the package claims 3.9 — handled
   with environment markers, but a support-matrix asterisk that will confuse someone.

## What to steal from this project

If you are building something similar, the exportable ideas, in order of value:

1. **Flags, not drops.** Every component gets an "unsure" output that routes to a
   human instead of discarding or guessing.
2. **Ambiguity is voided, never resolved by coin flip** — a lookup that could mean two
   people matches neither.
3. **Human corrections live in an overlay keyed by stable IDs**, never in regenerable
   output — but store *diffs*, not snapshots (learn from this project's mistake).
4. **Measure the old system before replacing it**, and freeze that measurement as a
   regression floor in the test suite.
5. **LLMs repair, deterministic code parses** — and LLM output re-passes the same
   validators as everything else, no exceptions.
6. **Reference data must be generated or owned**; a snapshot with neither is a slow
   leak.
7. **Run the tool, not just the tests** — at full scale, on the claimed platforms, in
   an actual browser, before believing your own documentation.
