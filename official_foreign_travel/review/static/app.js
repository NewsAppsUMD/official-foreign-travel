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

async function renderList() {
  const reports = await fetchReports();
  const filterSelect = document.getElementById("status-filter");
  filterSelect.onchange = () => renderRows(reports, filterSelect.value);
  renderRows(reports, "");
}

function renderRows(reports, statusFilter) {
  const filtered = statusFilter ? reports.filter((r) => r.status === statusFilter) : reports;
  const reviewed = reports.filter((r) => r.status !== "unreviewed").length;
  document.getElementById("progress").textContent = `${reviewed}/${reports.length} reviewed`;

  const body = document.getElementById("reports-body");
  body.innerHTML = "";
  filtered.forEach((r) => {
    const tr = document.createElement("tr");
    const link = `/report.html?id=${encodeURIComponent(r.report_id)}`;
    tr.innerHTML = `
      <td><a href="${link}">${r.report_id}</a></td>
      <td>${escapeHtml(r.sponsor)}</td>
      <td>${escapeHtml(r.source_file)}</td>
      <td>${escapeHtml(r.flags.join(", "))}</td>
      <td>${r.traveler_count}</td>
      <td>${r.status}</td>
    `;
    body.appendChild(tr);
  });
}

function getReportIdFromUrl() {
  return new URLSearchParams(window.location.search).get("id");
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

function fieldRow(path, value, nullable = false) {
  const row = document.createElement("label");
  row.className = "field-row";
  const label = document.createElement("span");
  label.textContent = path;
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
  row.appendChild(label);
  row.appendChild(input);
  return row;
}

function renderForm(report, existingEdits) {
  const pane = document.getElementById("form-pane");
  pane.innerHTML = "";
  pane.appendChild(fieldRow("sponsor.type", report.sponsor.type));
  pane.appendChild(fieldRow("sponsor.name", report.sponsor.name));
  pane.appendChild(fieldRow("sponsor.code", report.sponsor.code, true));
  if (report.period) {
    pane.appendChild(fieldRow("period.start", report.period.start, true));
    pane.appendChild(fieldRow("period.end", report.period.end, true));
  }

  report.travelers.forEach((traveler, ti) => {
    const heading = document.createElement("h3");
    heading.textContent = `Traveler ${ti + 1}`;
    pane.appendChild(heading);
    pane.appendChild(fieldRow(`travelers[${ti}].name`, traveler.name));
    pane.appendChild(fieldRow(`travelers[${ti}].honorific`, traveler.honorific, true));
    pane.appendChild(fieldRow(`travelers[${ti}].bioguide_id`, traveler.bioguide_id, true));

    traveler.segments.forEach((segment, si) => {
      const segHeading = document.createElement("h4");
      segHeading.textContent = `Segment ${si + 1} (click to highlight source)`;
      segHeading.onclick = () => highlightLines(segment.source_lines);
      pane.appendChild(segHeading);

      const prefix = `travelers[${ti}].segments[${si}]`;
      pane.appendChild(fieldRow(`${prefix}.arrival_date`, segment.arrival_date, true));
      pane.appendChild(fieldRow(`${prefix}.departure_date`, segment.departure_date, true));
      pane.appendChild(fieldRow(`${prefix}.country_raw`, segment.country_raw));

      ["per_diem", "transportation", "other", "total"].forEach((category) => {
        ["foreign_currency", "us_dollar"].forEach((currency) => {
          const cell = segment.costs[category][currency];
          pane.appendChild(
            fieldRow(`${prefix}.costs.${category}.${currency}.amount`, cell.amount, true)
          );
        });
      });
    });
  });

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
  renderForm(data.report, data.correction.edits || {});

  document.getElementById("save-btn").onclick = () => saveCorrection(reportId, "edited");
  document.getElementById("confirm-btn").onclick = () => saveCorrection(reportId, "confirmed_ok");
  await setupPrevNext(reportId);
}
