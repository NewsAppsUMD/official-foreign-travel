const API = "/api/reports";

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
      <td>${r.sponsor}</td>
      <td>${r.source_file}</td>
      <td>${r.flags.join(", ")}</td>
      <td>${r.traveler_count}</td>
      <td>${r.status}</td>
    `;
    body.appendChild(tr);
  });
}
