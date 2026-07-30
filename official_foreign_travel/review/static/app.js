const API = "/api/reports";

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

async function fetchReports() {
  const res = await fetch(API);
  return res.json();
}

const listSort = { column: null, ascending: true };

// Persist list filters across page loads, so navigating into a detail page
// and back doesn't reset the reviewer's place in the queue.
const FILTER_STORAGE_KEY = "oft-review-list-filters";

function saveListFilters(state) {
  try {
    localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(state));
  } catch (e) {
    // localStorage unavailable (private mode etc.) -- filters just won't persist
  }
}

function loadListFilters() {
  try {
    return JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY)) || {};
  } catch (e) {
    return {};
  }
}

function populateFlagFilter(reports) {
  const select = document.getElementById("flag-filter");
  const flags = Array.from(new Set(reports.flatMap((r) => r.flags))).sort();
  flags.forEach((flag) => {
    const option = document.createElement("option");
    option.value = flag;
    option.textContent = flag;
    select.appendChild(option);
  });
}

async function renderList() {
  const reports = await fetchReports();
  populateFlagFilter(reports);

  const statusSelect = document.getElementById("status-filter");
  const flagSelect = document.getElementById("flag-filter");
  const flaggedOnly = document.getElementById("flagged-only");

  const saved = loadListFilters();
  if (saved.status !== undefined) statusSelect.value = saved.status;
  if (
    saved.flag !== undefined &&
    Array.from(flagSelect.options).some((o) => o.value === saved.flag)
  ) {
    flagSelect.value = saved.flag;
  }
  if (saved.flaggedOnly !== undefined) flaggedOnly.checked = saved.flaggedOnly;
  if (saved.sortColumn) {
    listSort.column = saved.sortColumn;
    listSort.ascending = saved.sortAscending !== false;
  }

  const rerender = () => {
    saveListFilters({
      status: statusSelect.value,
      flag: flagSelect.value,
      flaggedOnly: flaggedOnly.checked,
      sortColumn: listSort.column,
      sortAscending: listSort.ascending,
    });
    renderRows(reports, statusSelect.value, flagSelect.value, flaggedOnly.checked);
  };
  statusSelect.onchange = rerender;
  flagSelect.onchange = rerender;
  flaggedOnly.onchange = rerender;

  document.querySelectorAll("th[data-sort]").forEach((th) => {
    th.onclick = () => {
      const column = th.dataset.sort;
      listSort.ascending = listSort.column === column ? !listSort.ascending : true;
      listSort.column = column;
      rerender();
    };
  });

  rerender();
}

function sortReports(reports, column, ascending) {
  if (!column) return reports;
  const sorted = [...reports].sort((a, b) => {
    const [av, bv] = [a[column], b[column]];
    if (av < bv) return -1;
    if (av > bv) return 1;
    return 0;
  });
  if (!ascending) sorted.reverse();
  return sorted;
}

function updateSortIndicators() {
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    const isActive = th.dataset.sort === listSort.column;
    th.classList.toggle("sorted", isActive);
    th.dataset.sortArrow = isActive ? (listSort.ascending ? "▲" : "▼") : "";
  });
}

function renderRows(reports, statusFilter, flagFilter, flaggedOnly) {
  let filtered = flaggedOnly ? reports.filter((r) => r.flags.length > 0) : reports;
  if (statusFilter) filtered = filtered.filter((r) => r.status === statusFilter);
  if (flagFilter) filtered = filtered.filter((r) => r.flags.includes(flagFilter));
  filtered = sortReports(filtered, listSort.column, listSort.ascending);
  updateSortIndicators();

  // The review queue is the flagged subset, whatever the current view shows.
  const flagged = reports.filter((r) => r.flags.length > 0);
  const reviewed = flagged.filter((r) => r.status !== "unreviewed").length;
  document.getElementById("progress").textContent =
    `${reviewed}/${flagged.length} flagged reviewed · ${reports.length} reports total`;

  const body = document.getElementById("reports-body");
  body.innerHTML = "";
  const knownStatuses = ["unreviewed", "edited", "confirmed_ok"];
  filtered.forEach((r) => {
    const tr = document.createElement("tr");
    const link = `/report.html?id=${encodeURIComponent(r.report_id)}`;
    const statusClass = knownStatuses.includes(r.status) ? r.status : "unreviewed";
    tr.innerHTML = `
      <td><a href="${link}">${escapeHtml(r.report_id)}</a></td>
      <td>${escapeHtml(r.sponsor)}</td>
      <td>${escapeHtml(r.source_file)}</td>
      <td class="flags-cell">${escapeHtml(r.flags.join(", "))}</td>
      <td>${r.traveler_count}</td>
      <td><span class="badge status-${statusClass}">${escapeHtml(r.status)}</span></td>
    `;
    body.appendChild(tr);
  });
}

function getReportIdFromUrl() {
  return new URLSearchParams(window.location.search).get("id");
}

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

function segmentHasMilitaryAirCost(segment) {
  return COST_CATEGORIES.some(([category]) =>
    COST_CURRENCIES.some(([currency]) => segment.costs[category][currency].military_air)
  );
}

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

function flagBadges(flags, extraClass) {
  // Flags repeat (e.g. MEMBER_UNMATCHED once per staff traveler) -- show each
  // distinct flag once with a count instead of a wall of duplicates.
  const wrap = document.createElement("div");
  wrap.className = extraClass ? `flag-badges ${extraClass}` : "flag-badges";
  const counts = {};
  flags.forEach((flag) => {
    counts[flag] = (counts[flag] || 0) + 1;
  });
  Object.entries(counts).forEach(([flag, n]) => {
    const badge = document.createElement("span");
    badge.className = "badge flag-badge";
    badge.textContent = n > 1 ? `${flag} ×${n}` : flag;
    wrap.appendChild(badge);
  });
  if (!flags.length) {
    const badge = document.createElement("span");
    badge.className = "badge no-flags-badge";
    badge.textContent = "no flags";
    wrap.appendChild(badge);
  }
  return wrap;
}

function renderReportHeader(report) {
  const container = document.getElementById("report-header");
  container.innerHTML = "";
  const title = document.createElement("h2");
  title.textContent = report.report_id;
  const sponsor = document.createElement("div");
  sponsor.id = "report-sponsor";
  sponsor.textContent = `${report.sponsor.name} · ${report.source_file}`;
  container.appendChild(title);
  container.appendChild(sponsor);
  container.appendChild(flagBadges(report.flags));
}

let currentRawLines = [];

function highlightLines(range) {
  if (!range || !range.length) return;
  const pre = document.getElementById("raw-pane");
  pre.innerHTML = currentRawLines
    .map((line, i) => (range.includes(i + 1) ? `<mark>${escapeHtml(line)}</mark>` : escapeHtml(line)))
    .join("\n");
  const marked = pre.querySelector("mark");
  if (marked) marked.scrollIntoView({ block: "center" });
}

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

function collectEdits() {
  const edits = {};
  document.querySelectorAll("#form-pane [data-path]").forEach((input) => {
    const blank = input.value === "" && input.dataset.nullable === "true";
    edits[input.dataset.path] = blank ? null : input.value;
  });
  return edits;
}

function blankRequiredFields(edits) {
  // Fields not marked nullable (sponsor.type/name, traveler name, country_raw)
  // are required strings -- Report accepts "" for them (some already-parsed
  // rows legitimately have a blank name/country from illegible source text),
  // so apply_corrections won't reject it either. Left unchecked, clearing one
  // here would save silently with no signal that anything's wrong.
  return Object.entries(edits)
    .filter(([path, value]) => {
      const input = document.querySelector(`[data-path="${CSS.escape(path)}"]`);
      return value === "" && input && input.dataset.nullable !== "true";
    })
    .map(([path]) => path);
}

async function saveCorrection(reportId, status) {
  const edits = status === "confirmed_ok" ? {} : collectEdits();
  if (status === "edited") {
    const blank = blankRequiredFields(edits);
    if (blank.length) {
      document.getElementById("save-status").textContent =
        `Error: cannot save with these fields blank: ${blank.join(", ")}`;
      return;
    }
  }
  const res = await fetch(`/api/reports/${encodeURIComponent(reportId)}/corrections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, edits }),
  });
  document.getElementById("save-status").textContent = res.ok ? "Saved" : "Error saving";
}

async function setupPrevNext(currentId) {
  const reports = await fetchReports();
  const ids = reports.map((r) => r.report_id);
  const idx = ids.indexOf(currentId);
  document.getElementById("prev-btn").disabled = idx <= 0;
  document.getElementById("next-btn").disabled = idx < 0 || idx >= ids.length - 1;
  document.getElementById("prev-btn").onclick = () => {
    if (idx > 0) window.location.href = `/report.html?id=${encodeURIComponent(ids[idx - 1])}`;
  };
  document.getElementById("next-btn").onclick = () => {
    if (idx < ids.length - 1) window.location.href = `/report.html?id=${encodeURIComponent(ids[idx + 1])}`;
  };
}

async function renderDetail() {
  const reportId = getReportIdFromUrl();
  const res = await fetch(`/api/reports/${encodeURIComponent(reportId)}`);
  const data = await res.json();

  currentRawLines = data.raw_lines;
  document.getElementById("raw-pane").textContent = currentRawLines.join("\n");
  renderReportHeader(data.report);
  renderForm(data.report, data.correction.edits || {});

  document.getElementById("save-btn").onclick = () => saveCorrection(reportId, "edited");
  document.getElementById("confirm-btn").onclick = () => saveCorrection(reportId, "confirmed_ok");
  await setupPrevNext(reportId);
}
