import { buildHeatmap, intensity } from "./heatmap-data.mjs";

const $ = (selector) => document.querySelector(selector);
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const compact = (value) =>
  new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
const format = (value) => value.toLocaleString();
const providers = { codex: "Codex", claude: "Claude", kimi: "Kimi" };
let data = null,
  selection = "all",
  days = 14,
  detailAccount = null,
  loading = false,
  failed = false;
let version = "";

function heatmapMarkup(account, id) {
  const source = account || data;
  const hours = source.buckets || source.hours;
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const chart = buildHeatmap(hours, days);
  const label = account
    ? `${providers[account.provider]} · ${account.label}`
    : "All accounts";
  const cells = chart.rows.flatMap((row) => row.cells);
  const firstFocus = Math.max(
    0,
    cells.findLastIndex((cell) => !cell.future && cell.tokens > 0),
  );
  const hasData = hours.length > 0;
  const age = data.indexedAt
    ? Math.max(0, Math.floor((Date.now() / 1000 - data.indexedAt) / 60))
    : null;
  let status = failed
    ? "Connection lost; showing the last token scan."
    : data.error || "";
  if (data.scanning)
    status = "Scanning CLI logs. Counts will update when the scan finishes.";
  else if (age !== null && age > 10)
    status ||= `Token scan is ${age} minutes old.`;
  const partial = account
    ? account.status === "partial"
    : data.accounts.some((a) => a.status === "partial");
  if (partial) status += " Some log files could not be read completely.";
  const missing = account
    ? account.status === "empty"
    : data.accounts.some((a) => a.status === "empty");
  if (missing)
    status += account
      ? " No supported token records for this profile."
      : " Some accounts have no local token records.";
  const scale = `<div class="heatmap-scale"><span>0 recorded</span>${[0, 1, 2, 3, 4, 5].map((level) => `<i class="heatmap-swatch heat-${level}"></i>`).join("")}<span>${compact(chart.peak)} / hour</span></div>`;
  const header = `<div class="heatmap-summary"><span><strong>${compact(chart.sum)}</strong> recorded tokens</span><span><strong>${chart.activeHours}</strong> active hours</span><span><strong>${compact(chart.records)}</strong> response records</span></div>`;
  const grid = `<div class="heatmap-grid" role="grid" aria-label="${escape(label)} token usage by day and hour, ${escape(timezone)}" aria-rowcount="${days + 1}" aria-colcount="25"><div class="heatmap-axis" role="row"><span role="columnheader" class="heatmap-date">Hour</span>${Array.from({ length: 24 }, (_, hour) => `<span role="columnheader" class="heatmap-hour ${hour % 3 === 0 ? "major" : ""}">${String(hour).padStart(2, "0")}</span>`).join("")}</div>${chart.rows
    .map(
      (row, index) =>
        `<div class="heatmap-row" role="row"><span class="heatmap-date" role="rowheader">${row.date.slice(5)}</span>${row.cells
          .map((cell, hour) => {
            const text = `${cell.date} ${String(cell.hour).padStart(2, "0")}:00–${String((cell.hour + 1) % 24).padStart(2, "0")}:00 ${timezone} · ${cell.future ? "Future hour" : `${format(cell.tokens)} tokens recorded · ${format(cell.records)} responses${cell.current ? " · Hour in progress" : ""}`}`;
            return `<button type="button" role="gridcell" class="heatmap-cell heat-${intensity(cell.tokens, chart.peak)} ${cell.future ? "future" : ""} ${cell.current ? "current" : ""}" tabindex="${index * 24 + hour === firstFocus ? "0" : "-1"}" data-index="${index * 24 + hour}" data-detail="${escape(text)}" aria-label="${escape(text)}" aria-selected="false" ${cell.future ? 'aria-disabled="true"' : ""}></button>`;
          })
          .join("")}</div>`,
    )
    .join("")}</div>`;
  return `${header}${!hasData ? '<p class="heatmap-empty">No local token records yet.</p>' : ""}${grid}<div class="heatmap-meta">${scale}<span>${age === null ? "Not indexed yet" : `Indexed ${age < 1 ? "just now" : `${age}m ago`}`}</span></div><output class="heatmap-inspector" id="${id}-inspector" aria-live="polite">Select an hour for its token count. Arrow keys move between cells.</output><p class="heatmap-note">CLI logs on this machine · ${escape(timezone)} · cached input included</p>${status ? `<p class="heatmap-warning" role="status">${escape(status.trim())}</p>` : ""}`;
}

function bindGrid(root) {
  const cells = [...root.querySelectorAll(".heatmap-cell")];
  const inspector = root.querySelector(".heatmap-inspector");
  function inspect(cell, select = false) {
    inspector.textContent = cell.dataset.detail;
    if (select) {
      cells.forEach((other) => {
        other.tabIndex = other === cell ? 0 : -1;
        other.setAttribute("aria-selected", String(other === cell));
      });
    }
  }
  cells.forEach((cell, index) => {
    cell.addEventListener("pointerenter", () => inspect(cell));
    cell.addEventListener("focus", () => inspect(cell));
    cell.addEventListener("click", () => inspect(cell, true));
    cell.addEventListener("keydown", (event) => {
      const offsets = {
        ArrowLeft: -1,
        ArrowRight: 1,
        ArrowUp: -24,
        ArrowDown: 24,
      };
      let next = index + (offsets[event.key] || 0);
      if (event.key === "Home")
        next = event.ctrlKey ? 0 : Math.floor(index / 24) * 24;
      else if (event.key === "End")
        next = event.ctrlKey
          ? cells.length - 1
          : Math.floor(index / 24) * 24 + 23;
      else if (!(event.key in offsets)) return;
      event.preventDefault();
      next = Math.max(0, Math.min(cells.length - 1, next));
      inspect(cells[next], true);
      cells[next].focus();
    });
  });
}

function render() {
  if (!data) return;
  const chooser = $("#heatmap-account");
  if (!data.accounts.some((a) => a.id === selection)) selection = "all";
  chooser.innerHTML =
    '<option value="all">All accounts</option>' +
    data.accounts
      .map(
        (a) =>
          `<option value="${escape(a.id)}" ${a.id === selection ? "selected" : ""}>${providers[a.provider]} · ${escape(a.label)}</option>`,
      )
      .join("");
  chooser.value = selection;
  const selected = data.accounts.find((a) => a.id === selection);
  $("#heatmap-content").innerHTML = heatmapMarkup(selected, "global");
  bindGrid($("#heatmap-content"));
  renderDetail();
}

function renderDetail() {
  const root = $("#detail-heatmap");
  if (!root) return;
  if (!data) {
    root.textContent = "Loading token records…";
    return;
  }
  const account = data.accounts.find((a) => a.id === detailAccount);
  if (!account) {
    root.textContent = "No indexed token records for this account.";
    return;
  }
  root.innerHTML = heatmapMarkup(account, "detail");
  bindGrid(root);
}

async function load() {
  if (loading) return;
  loading = true;
  try {
    const response = await fetch("/api/tokens", {
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw Error();
    const next = await response.json();
    const key = JSON.stringify([
      Intl.DateTimeFormat().resolvedOptions().timeZone,
      new Date().toDateString(),
      new Date().getHours(),
      next.indexedAt,
      next.scanning,
      next.error,
      next.accounts.map((a) => [a.id, a.label, a.status]),
    ]);
    const changed = key !== version || failed;
    data = next;
    failed = false;
    version = key;
    if (changed) render();
  } catch {
    failed = true;
    if (data) render();
    else
      $("#heatmap-content").innerHTML =
        '<p class="heatmap-warning" role="status">Token data could not be loaded. Retrying automatically.</p>';
  } finally {
    loading = false;
  }
}

$("#heatmap-account").addEventListener("change", (event) => {
  selection = event.target.value;
  render();
});
$("#heatmap-days").addEventListener("change", (event) => {
  days = Number(event.target.value);
  render();
});
window.addEventListener("atlas:account-detail", (event) => {
  detailAccount = event.detail.accountId;
  renderDetail();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) load();
});
load();
setInterval(load, 15000);
