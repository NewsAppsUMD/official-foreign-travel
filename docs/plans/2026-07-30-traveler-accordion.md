# Traveler Accordion + Compact Cost Grid Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make travelers in the review form pane collapsible (flagged-expanded by default) and replace the 8 verbose cost rows per segment with a compact 4×2 grid.

**Architecture:** All changes live in the review app's two static files — `app.js` (render logic) and `app.css` (styles). Collapse hides content with CSS (`display: none`) so every `[data-path]` input stays in the DOM; `collectEdits()`, `blankRequiredFields()`, and saved-edit restoration need zero changes because they query the DOM by attribute. Design doc: `docs/plans/2026-07-30-traveler-accordion-design.md`.

**Tech Stack:** Vanilla JS + CSS, no build step. The Python server (`review/server.py`) reads static files from disk on every request with `Cache-Control: no-store`, so changes appear on browser reload — no server restart needed.

**Testing:** The project has no JS test harness, and the Python suite doesn't execute app.js. Verification is browser-based per task, against the running server at `http://127.0.0.1:8765` (start with `uv run oft-review report_text/ output.json` if not running). Good test reports: `2025q1feb18-003` (2 travelers: one clean-but-military-air, one flagged) and `2025q1feb18-005` (Energy & Commerce, ~10 travelers, mix of members and staffers).

**One deliberate refinement over the design doc:** the design said a traveler starts expanded when "its name or bioguide_id is blank." A blank bioguide alone is the *normal* state for staffers (most travelers in many reports), which would leave nearly everything expanded and defeat the accordion. Implemented rule: expand when the name is blank, or when the traveler has an honorific (e.g. "Hon.") **and** no bioguide — i.e. someone who looks like a member but didn't match. Flag this to the user at review time.

---

### Task 1: `fieldRow` display labels with full-path tooltips

**Files:**
- Modify: `official_foreign_travel/review/static/app.js` (the `fieldRow` function, currently ~line 206)

**Step 1: Add an optional `label` parameter to `fieldRow`**

Replace the current `fieldRow` function with:

```js
function fieldRow(path, value, nullable = false, label = null) {
  const row = document.createElement("label");
  row.className = "field-row";
  const labelEl = document.createElement("span");
  labelEl.textContent = label ?? path;
  labelEl.title = path;
  const input = document.createElement("input");
  input.dataset.path = path;
  if (nullable) {
    // Optional[Decimal]/Optional[date]/Optional[str] fields (cost amounts,
    // arrival/departure dates, honorific, bioguide_id, sponsor.code) round-trip
    // through this input as "" whether they started blank or were cleared by
    // the reviewer -- but apply_corrections rejects "" for non-str fields.
    // Mark these so collectEdits sends null instead, for either case.
    input.dataset.nullable = "true";
  }
  input.value = value ?? "";
  row.appendChild(labelEl);
  row.appendChild(input);
  return row;
}
```

Note the local variable rename (`label` → `labelEl`) to free the name for the parameter. The `title = path` tooltip applies to every row, including the sponsor/period rows that keep their full-path labels.

**Step 2: Verify in browser**

Reload `http://127.0.0.1:8765/report.html?id=2025q1feb18-003`. Expected: page renders identically to before (all callers still pass no label), and hovering any field label shows the full path as a tooltip. Check the console for errors.

**Step 3: Commit**

```bash
git add official_foreign_travel/review/static/app.js
git commit -m "Review UI: fieldRow supports short display labels with full-path tooltips"
```

---

### Task 2: Compact cost grid per segment

**Files:**
- Modify: `official_foreign_travel/review/static/app.js` (add `costGrid` above `renderForm`; replace the cost-rows loop inside `renderForm`)
- Modify: `official_foreign_travel/review/static/app.css` (append grid styles)

**Step 1: Add the `costGrid` helper**

Insert above `renderForm` in app.js:

```js
const COST_CATEGORIES = [
  ["per_diem", "Per diem"],
  ["transportation", "Transportation"],
  ["other", "Other"],
  ["total", "Total"],
];
const COST_CURRENCIES = [
  ["foreign_currency", "Foreign currency"],
  ["us_dollar", "U.S. dollar"],
];

function costGrid(prefix, costs) {
  const table = document.createElement("table");
  table.className = "cost-grid";
  const headRow = table.createTHead().insertRow();
  headRow.appendChild(document.createElement("th"));
  COST_CURRENCIES.forEach(([, label]) => {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  });
  const body = table.createTBody();
  COST_CATEGORIES.forEach(([category, label]) => {
    const row = body.insertRow();
    const rowHead = document.createElement("th");
    rowHead.scope = "row";
    rowHead.textContent = label;
    row.appendChild(rowHead);
    COST_CURRENCIES.forEach(([currency]) => {
      const path = `${prefix}.costs.${category}.${currency}.amount`;
      const input = document.createElement("input");
      input.dataset.path = path;
      input.dataset.nullable = "true";
      input.title = path;
      input.value = costs[category][currency].amount ?? "";
      row.insertCell().appendChild(input);
    });
  });
  return table;
}
```

The inputs carry the exact same `data-path` / `data-nullable` attributes as the field rows they replace, so `collectEdits`, `blankRequiredFields`, and saved-edit restoration are untouched.

**Step 2: Use it in `renderForm`**

Inside `renderForm`'s segment loop, replace this block:

```js
      ["per_diem", "transportation", "other", "total"].forEach((category) => {
        ["foreign_currency", "us_dollar"].forEach((currency) => {
          const cell = segment.costs[category][currency];
          pane.appendChild(
            fieldRow(`${prefix}.costs.${category}.${currency}.amount`, cell.amount, true)
          );
        });
      });
```

with:

```js
      pane.appendChild(costGrid(prefix, segment.costs));
```

Also update `segmentHasMilitaryAirCost` (~line 151) to reuse the new constants instead of its inline arrays:

```js
function segmentHasMilitaryAirCost(segment) {
  return COST_CATEGORIES.some(([category]) =>
    COST_CURRENCIES.some(([currency]) => segment.costs[category][currency].military_air)
  );
}
```

(`COST_CATEGORIES` must therefore be defined above `segmentHasMilitaryAirCost` — put the two constants near the top of the detail-page section, just above `segmentHasMilitaryAirCost`.)

**Step 3: Add grid styles**

Append to app.css. The overrides matter: the global `table` rule sets `width: 100%; margin-top: 1rem; background: var(--surface)` and the global `th` rule sets `position: sticky` + uppercase — all wrong inside the form pane.

```css
.cost-grid { width: auto; margin: 0.25rem 0 0.75rem; background: none; }
.cost-grid th, .cost-grid td { border: none; padding: 0.12rem 0.35rem; }
.cost-grid thead th {
  position: static;
  background: none;
  text-transform: none;
  letter-spacing: 0;
  color: var(--muted);
  font-size: 0.7rem;
  font-weight: 500;
  text-align: left;
}
.cost-grid tbody th { text-align: left; font-weight: 400; color: var(--muted); font-size: 0.75rem; }
.cost-grid input {
  width: 8.5rem;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 0.8rem;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.2rem 0.4rem;
}
.cost-grid input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
```

**Step 4: Verify in browser**

Reload `report.html?id=2025q1feb18-003`. Expected:
- Each segment shows a small 4-row × 2-column grid instead of 8 label rows; hovering an input shows its full path.
- Edit a cost value, click **Save**, reload — the edited value is restored into the right grid cell (round-trip through corrections.json works).
- Console: no errors.

Undo the test edit: re-enter the original value and Save again (or delete the report's entry from `corrections.json`).

**Step 5: Commit**

```bash
git add official_foreign_travel/review/static/app.js official_foreign_travel/review/static/app.css
git commit -m "Review UI: compact 4x2 cost grid per segment"
```

---

### Task 3: Traveler accordion with flagged-expanded defaults

**Files:**
- Modify: `official_foreign_travel/review/static/app.js` (add `travelerStartsExpanded`; restructure `renderForm`)
- Modify: `official_foreign_travel/review/static/app.css` (append accordion styles)

**Step 1: Add the default-state helper**

Insert above `renderForm`:

```js
function travelerStartsExpanded(traveler, ti, existingEdits) {
  const hasFlaggedSegment = traveler.segments.some(
    (s) => (s.flags && s.flags.length) || segmentHasMilitaryAirCost(s)
  );
  // A blank bioguide alone is normal (staffers); only treat it as a problem
  // when the honorific says this should be a member who therefore ought to
  // have matched.
  const identityIncomplete =
    !traveler.name || (Boolean(traveler.honorific) && !traveler.bioguide_id);
  // A saved correction must never load hidden.
  const hasSavedEdit = Object.keys(existingEdits).some((path) =>
    path.startsWith(`travelers[${ti}].`)
  );
  return hasFlaggedSegment || identityIncomplete || hasSavedEdit;
}
```

**Step 2: Restructure `renderForm`**

Replace the whole `renderForm` function with:

```js
function renderForm(report, existingEdits) {
  const pane = document.getElementById("form-pane");
  pane.innerHTML = "";

  const controls = document.createElement("div");
  controls.className = "form-controls";
  const expandAll = document.createElement("button");
  expandAll.type = "button";
  expandAll.textContent = "Expand all";
  const collapseAll = document.createElement("button");
  collapseAll.type = "button";
  collapseAll.textContent = "Collapse all";
  controls.appendChild(expandAll);
  controls.appendChild(collapseAll);
  pane.appendChild(controls);

  pane.appendChild(fieldRow("sponsor.type", report.sponsor.type));
  pane.appendChild(fieldRow("sponsor.name", report.sponsor.name));
  pane.appendChild(fieldRow("sponsor.code", report.sponsor.code, true));
  if (report.period) {
    pane.appendChild(fieldRow("period.start", report.period.start, true));
    pane.appendChild(fieldRow("period.end", report.period.end, true));
  }

  report.travelers.forEach((traveler, ti) => {
    const section = document.createElement("section");
    section.className = "traveler-section";

    const header = document.createElement("div");
    header.className = "traveler-header";
    const arrow = document.createElement("span");
    arrow.className = "traveler-arrow";
    const name = document.createElement("span");
    name.className = "traveler-name";
    name.textContent = traveler.name || "(unnamed)";
    const meta = document.createElement("span");
    meta.className = "traveler-meta";
    const segmentCount = traveler.segments.length;
    const metaParts = [
      `Traveler ${ti + 1}`,
      `${segmentCount} segment${segmentCount === 1 ? "" : "s"}`,
    ];
    if (traveler.bioguide_id) metaParts.push(traveler.bioguide_id);
    meta.textContent = metaParts.join(" · ");
    header.appendChild(arrow);
    header.appendChild(name);
    header.appendChild(meta);
    const flaggedCount = traveler.segments.filter(
      (s) => (s.flags && s.flags.length) || segmentHasMilitaryAirCost(s)
    ).length;
    if (flaggedCount) {
      const flagCount = document.createElement("span");
      flagCount.className = "badge flag-badge";
      flagCount.textContent = `${flaggedCount} ⚑`;
      header.appendChild(flagCount);
    }

    const body = document.createElement("div");
    body.className = "traveler-body";
    body.appendChild(fieldRow(`travelers[${ti}].name`, traveler.name, false, "name"));
    body.appendChild(fieldRow(`travelers[${ti}].honorific`, traveler.honorific, true, "honorific"));
    body.appendChild(
      fieldRow(`travelers[${ti}].bioguide_id`, traveler.bioguide_id, true, "bioguide_id")
    );

    traveler.segments.forEach((segment, si) => {
      const segHeading = document.createElement("h4");
      segHeading.textContent = `Segment ${si + 1} (click to highlight source)`;
      segHeading.onclick = () => highlightLines(segment.source_lines);
      body.appendChild(segHeading);
      const displayFlags = segment.flags ? [...segment.flags] : [];
      // MILITARY_AIR_LABEL_ROW already conveys this; only add the synthetic
      // badge for the inline-footnote case, which sets the cell's
      // military_air bool without any corresponding entry in `flags`.
      if (segmentHasMilitaryAirCost(segment) && !displayFlags.includes("MILITARY_AIR_LABEL_ROW")) {
        displayFlags.push("MILITARY_AIR");
      }
      if (displayFlags.length) {
        body.appendChild(flagBadges(displayFlags, "segment-flags"));
      }

      const prefix = `travelers[${ti}].segments[${si}]`;
      body.appendChild(fieldRow(`${prefix}.arrival_date`, segment.arrival_date, true, "arrival_date"));
      body.appendChild(
        fieldRow(`${prefix}.departure_date`, segment.departure_date, true, "departure_date")
      );
      body.appendChild(fieldRow(`${prefix}.country_raw`, segment.country_raw, false, "country_raw"));
      body.appendChild(costGrid(prefix, segment.costs));
    });

    section.appendChild(header);
    section.appendChild(body);
    if (!travelerStartsExpanded(traveler, ti, existingEdits)) {
      section.classList.add("collapsed");
    }
    header.onclick = () => section.classList.toggle("collapsed");
    pane.appendChild(section);
  });

  expandAll.onclick = () =>
    pane.querySelectorAll(".traveler-section").forEach((s) => s.classList.remove("collapsed"));
  collapseAll.onclick = () =>
    pane.querySelectorAll(".traveler-section").forEach((s) => s.classList.add("collapsed"));

  Object.entries(existingEdits).forEach(([path, value]) => {
    const input = pane.querySelector(`[data-path="${CSS.escape(path)}"]`);
    if (input) input.value = value ?? "";
  });
}
```

Key invariants to preserve exactly: the segment-heading click-to-highlight, the MILITARY_AIR synthetic-badge logic, and the trailing existing-edits restoration loop.

**Step 3: Add accordion styles**

Append to app.css:

```css
.form-controls { display: flex; gap: 0.4rem; margin-bottom: 0.6rem; }
.form-controls button { font-size: 0.75rem; padding: 0.2rem 0.6rem; }

.traveler-section { border: 1px solid var(--border); border-radius: 6px; margin: 0.6rem 0; }
.traveler-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.45rem 0.6rem;
  cursor: pointer;
  user-select: none;
  background: #eef1f5;
  border-radius: 5px 5px 0 0;
}
.traveler-section.collapsed .traveler-header { border-radius: 5px; }
.traveler-header:hover { background: #e2e7ee; }
.traveler-arrow::before { content: "▾"; font-size: 0.7rem; color: var(--muted); }
.traveler-section.collapsed .traveler-arrow::before { content: "▸"; }
.traveler-name { font-weight: 600; font-size: 0.9rem; }
.traveler-meta { color: var(--muted); font-size: 0.75rem; }
.traveler-body { padding: 0.5rem 0.6rem; }
.traveler-section.collapsed .traveler-body { display: none; }
```

The old `#form-pane h3` rules become dead (travelers no longer render an h3) but are harmless; leave them.

**Step 4: Verify in browser**

Reload `report.html?id=2025q1feb18-003`. Expected:
- Both travelers render as bordered sections with header rows (arrow, name, "Traveler N · M segments", bioguide when present, flag-count badge when flagged).
- Both start expanded here (Lopez has military-air, Omar has a flag).
- Clicking a header collapses/expands; Expand all / Collapse all work.
- Check a big report (e.g. `2025q1feb18-005`): staffers with clean segments start collapsed; flagged/military-air travelers start expanded.

**Step 5: Commit**

```bash
git add official_foreign_travel/review/static/app.js official_foreign_travel/review/static/app.css
git commit -m "Review UI: collapsible traveler sections, flagged expanded by default"
```

---

### Task 4: End-to-end verification (design doc checklist)

**Files:** none (verification only)

Run through the design doc's test list in the browser:

1. **Collapsed edits persist:** open a report, expand a collapsed traveler, edit its `honorific`, collapse the traveler again, click **Save**. Reload — expected: the traveler is now auto-expanded (saved edit rule) and the input shows the edited value. Then clear the edit (restore original value, Save).
2. **Confirm OK unaffected:** click **Confirm OK** on a report; expected `Saved` status and no console errors (confirm sends empty edits regardless of collapse state).
3. **Blank-required guard still works:** blank out a `name` field, click **Save**; expected the "cannot save with these fields blank" error, not a save.
4. **Segment highlight:** click a segment heading inside an expanded traveler; expected the raw pane highlights and scrolls to the source lines.
5. **Console and layout sweep:** check reports with an unnamed traveler (search the list for `SEGMENT_WITHOUT_TRAVELER_NAME`) — expected header shows `(unnamed)` and starts expanded.

**Step 2: Run the Python suite (server/corrections untouched, but cheap insurance)**

```bash
source .venv/bin/activate && python3 -m pytest tests/test_review_server.py tests/test_review_corrections.py -q --no-cov
```

Expected: all pass.

**Step 3: Final commit if any fixups were needed; otherwise done.**
