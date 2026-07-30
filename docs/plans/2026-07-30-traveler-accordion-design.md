# Review UI: collapsible travelers + compact cost grid

**Date:** 2026-07-30
**Status:** Approved

## Problem

The report detail page's form pane renders every traveler fully expanded as a flat
list of label+input rows — 3 traveler fields plus ~11 rows per segment (3 field rows
and 8 cost rows). A report with a dozen travelers is one giant scroll, and the long
`travelers[0].segments[0].costs.per_diem.us_dollar.amount` labels waste most of each
row's width. Reviewers want more verticality and the ability to expand/collapse
individual travelers.

## Constraint that shapes the design

`collectEdits()` ([app.js](../../official_foreign_travel/review/static/app.js)) gathers
edits by querying every `[data-path]` input in the DOM, and saved edits are restored by
querying the same attribute. Any collapse mechanism must therefore **hide** content
(`display: none`), never remove it from the DOM — otherwise edits inside collapsed
travelers would be silently dropped on save.

## Design

### Traveler accordion

Each traveler renders as a section with a clickable toggle header (a styled div, not a
native `<details>`, for styling control and consistent behavior). The header shows:

- disclosure arrow (▸ collapsed / ▾ expanded)
- traveler name, falling back to `(unnamed)`
- bioguide match indicator (matched ID or blank)
- segment count
- a badge with the count of flagged segments (e.g. `2 ⚑`) so collapsed travelers that
  hide problems are visible at a glance

Clicking the header toggles the section body. Collapsed bodies are hidden with
`display: none`; all inputs remain in the DOM.

**Expand all / Collapse all** buttons sit at the top of the form pane.

### Default expand state

A traveler starts **expanded** when any of these hold:

- any of its segments has flags
- any of its cost cells has `military_air: true`
- its name or bioguide_id is blank
- a saved correction (`existingEdits`) touches any path under this traveler — a saved
  edit must never load hidden

Otherwise it starts **collapsed**.

### Compact cost grid

Within a segment, the 8 cost field-rows become one small table:

|                | Foreign currency | U.S. dollar |
|----------------|------------------|-------------|
| Per diem       | input            | input       |
| Transportation | input            | input       |
| Other          | input            | input       |
| Total          | input            | input       |

Each cell is the same `<input data-path="travelers[i].segments[j].costs.<cat>.<cur>.amount"
data-nullable="true">` as today — only the visual arrangement and labels change, so
save/corrections round-trip identically.

### Shortened labels

Non-cost segment fields (arrival/departure dates, country) keep the field-row layout
but display short labels (`arrival_date`, `country_raw`) instead of the full path. The
full path remains in `data-path` and is exposed as a `title` tooltip on the label.
Traveler-level fields (name, honorific, bioguide_id) get the same treatment.

## Out of scope

- Raw pane, list page, server, and corrections format: unchanged.
- Click-to-highlight-source on segment headings: unchanged.
- Master-detail drill-in (considered as Option C, rejected: changes review flow and
  save logic the most).

## Testing

The Python suite doesn't cover static JS, so verification is manual via the browser:

1. Load `2025q1feb18-003` (2 travelers, flags on both sides) and a large
   multi-traveler report.
2. Confirm default expand/collapse states match the rules above.
3. Toggle travelers; confirm expand/collapse-all works.
4. Edit a field, collapse its traveler, save — confirm the edit persists.
5. Reload a report with saved corrections — confirm the corrected input is populated
   and its traveler is expanded.
6. Confirm segment click-to-highlight still works and no console errors.
