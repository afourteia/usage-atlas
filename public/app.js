"use strict";
const $ = (selector) => document.querySelector(selector);
const names = { codex: "Codex", claude: "Claude", kimi: "Kimi" };
const logos = { codex: "⌘", claude: "✳", kimi: "K" };
const colors = { codex: "#d9f783", claude: "#e6a184", kimi: "#9cc7f5" };
let snapshot = null,
  filter = "all",
  dailyAccount = "",
  detailAccount = "",
  lastVersion = "",
  online = true;
let toastTimer,
  historyRequest = 0;
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const compact = (n) =>
  new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n);
const percent = (n) =>
  n === null || n === undefined ? "—" : `${Number(n.toFixed(1))}%`;
const now = () => Date.now() / 1000;
const dateTime = (ts) =>
  ts
    ? new Date(ts * 1000).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "Not reported";
function countdown(ts) {
  if (!ts) return "Not reported";
  const seconds = ts - now();
  if (seconds <= 0) return "Awaiting reset";
  const mins = Math.ceil(seconds / 60),
    days = Math.floor(mins / 1440),
    hours = Math.floor((mins % 1440) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${mins % 60}m`;
  return `${mins}m`;
}
function age(ts) {
  if (!ts) return "No reading yet";
  const mins = Math.floor((now() - ts) / 60);
  return mins < 1
    ? "Just checked"
    : mins < 60
      ? `${mins}m ago`
      : `${Math.floor(mins / 60)}h ago`;
}
function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 4000);
}
function showDialog(selector) {
  $(selector).showModal();
}
document
  .querySelectorAll(".close-dialog")
  .forEach((button) =>
    button.addEventListener("click", () => button.closest("dialog").close()),
  );
document.querySelectorAll("dialog").forEach((dialog) =>
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    if (
      event.clientX < r.left ||
      event.clientX > r.right ||
      event.clientY < r.top ||
      event.clientY > r.bottom
    )
      dialog.close();
  }),
);
$("#timezone").textContent =
  `Reset times in ${Intl.DateTimeFormat().resolvedOptions().timeZone}.`;
$("#about-button").addEventListener("click", () => showDialog("#about-dialog"));
document.querySelectorAll("[data-filter]").forEach((button) =>
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((b) => {
      b.classList.toggle("active", b === button);
      b.setAttribute("aria-selected", String(b === button));
    });
    renderCards();
  }),
);
document.querySelector(".tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...document.querySelectorAll("[data-filter]")];
  let i = tabs.indexOf(document.activeElement);
  if (i < 0) return;
  event.preventDefault();
  i =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (i + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
          tabs.length;
  tabs[i].focus();
  tabs[i].click();
});
function allResets() {
  return (snapshot?.accounts || [])
    .flatMap((a) =>
      a.windows
        .filter((w) => w.resetsAt && w.resetsAt > now())
        .map((w) => ({ a, w })),
    )
    .sort((x, y) => x.w.resetsAt - y.w.resetsAt);
}
function quotaMarkup(w) {
  const pct = w.usedPercent;
  const width = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  return `<div class="quota ${pct >= 90 ? "danger" : ""}"><div class="quota-group"><span class="quota-label">${escape(w.label)}</span><span class="quota-percent"><strong>${percent(pct)}</strong> <span>used</span></span></div><div class="bar" role="meter" aria-label="${escape(w.group)} ${escape(w.label)} usage" ${pct == null ? 'aria-valuetext="Not reported"' : `aria-valuenow="${width}" aria-valuemin="0" aria-valuemax="100"`}><div class="bar-fill" style="width:${width}%"></div></div><div class="quota-meta"><span>${w.resetsAt ? "Resets in" : escape(w.resetText || "Reset not reported")}</span><time ${w.resetsAt ? `data-countdown="${w.resetsAt}" title="${escape(dateTime(w.resetsAt))}"` : ""}>${w.resetsAt ? countdown(w.resetsAt) : ""}</time></div></div>`;
}
function groupedWindows(windows) {
  const groups = Map.groupBy
    ? Map.groupBy(windows, (w) => w.group || "All models")
    : windows.reduce((map, w) => {
        const key = w.group || "All models";
        map.set(key, [...(map.get(key) || []), w]);
        return map;
      }, new Map());
  return [...groups]
    .map(
      ([group, rows]) =>
        `<div class="quota-section"><div class="group-title">${escape(group)}</div>${rows.map(quotaMarkup).join("")}</div>`,
    )
    .join("");
}
function renderCards() {
  if (!snapshot) return;
  const accounts = snapshot.accounts.filter(
    (a) => filter === "all" || a.provider === filter,
  );
  $("#cards").innerHTML = accounts.length
    ? accounts
        .map((a, i) => {
          const stale = a.status !== "ok";
          const title = stale
            ? a.status === "pending"
              ? "Connecting"
              : a.status === "stale"
                ? "Stale reading"
                : "Needs attention"
            : age(a.updatedAt);
          return `<article class="card ${a.provider}" style="animation-delay:${i * 40}ms"><div class="card-head"><div class="provider-logo ${a.provider}" aria-hidden="true">${logos[a.provider]}</div><div><h2 class="provider-name">${names[a.provider]}</h2><span class="plan">${escape(a.plan || "Subscription")}</span></div><button class="card-menu" data-detail="${a.id}" aria-label="View ${escape(a.label)} details">↗</button></div><div class="account-name">${escape(a.label)}${a.profileCount > 1 ? `<span class="profile-count">${a.profileCount} profiles · one subscription</span>` : ""}</div><div class="card-body">${a.error ? `<p class="card-error">${escape(a.error)}</p>` : ""}${a.windows.length ? groupedWindows(a.windows) : '<div class="no-quota">Waiting for a quota reading.</div>'}${a.extras?.length ? `<div class="extras">${a.extras.map((e) => `<div class="extra">${escape(e.label)}<strong>${escape(e.value)}</strong></div>`).join("")}</div>` : ""}</div><div class="card-footer"><span class="${stale ? "stale-label" : ""}"><i></i><span ${!stale ? `data-age="${a.updatedAt}"` : ""}>${escape(title)}</span></span><button data-detail="${a.id}">Tokens & details ↗</button></div></article>`;
        })
        .join("")
    : '<div class="empty-card">No accounts found for this provider. Open Accounts to connect one.</div>';
  $("#cards")
    .querySelectorAll("[data-detail]")
    .forEach((b) =>
      b.addEventListener("click", () => openDetail(b.dataset.detail)),
    );
}
function renderOverview() {
  const accounts = snapshot.accounts;
  $("#account-count").innerHTML = `${accounts.length}<small>monitored</small>`;
  $("#provider-count").textContent =
    `${new Set(accounts.map((a) => a.provider)).size} providers · ${accounts.filter((a) => a.status === "ok").length} fresh readings`;
  $("#tab-count").textContent = accounts.length;
  const healthy = accounts
    .filter((a) => a.status === "ok")
    .map((a) => {
      const main = a.windows.filter(
        (w) =>
          ["All models", "Kimi Code", "all models"].includes(w.group) &&
          w.usedPercent !== null,
      );
      return {
        a,
        left: main.length
          ? Math.max(0, 100 - Math.max(...main.map((w) => w.usedPercent)))
          : null,
      };
    })
    .filter((v) => v.left !== null)
    .sort((a, b) => b.left - a.left);
  $("#available").innerHTML = healthy.length
    ? `${percent(healthy[0].left)}<small>left</small>`
    : "—";
  $("#available-name").textContent = healthy.length
    ? `${names[healthy[0].a.provider]} · ${healthy[0].a.label}`
    : "Waiting for a fresh reading";
  tick();
}
function renderResets() {
  const resets = allResets().slice(0, 4);
  $("#reset-list").innerHTML = resets.length
    ? resets
        .map(
          ({ a, w }) =>
            `<div class="reset-item"><span class="reset-dot" style="background:${colors[a.provider]}"></span><div class="reset-info"><strong>${names[a.provider]} · ${escape(w.label)}${!["All models", "all models", "Kimi Code"].includes(w.group) ? " · " + escape(w.group) : ""}</strong><small>${escape(a.label)}${a.status !== "ok" ? " · stale reading" : ""}</small></div><div class="reset-when"><time data-countdown="${w.resetsAt}">${countdown(w.resetsAt)}</time><small>${escape(dateTime(w.resetsAt))}</small></div></div>`,
        )
        .join("")
    : '<p class="detail-footnote">No upcoming reset times have been reported.</p>';
}
function renderDaily() {
  const accounts = snapshot.accounts;
  if (!accounts.some((a) => a.id === dailyAccount))
    dailyAccount = accounts.find((a) => a.daily.length)?.id || accounts[0]?.id;
  $("#activity-account").innerHTML = accounts
    .map(
      (a) =>
        `<option value="${a.id}" ${a.id === dailyAccount ? "selected" : ""}>${names[a.provider]} · ${escape(a.label)}</option>`,
    )
    .join("");
  const account = accounts.find((a) => a.id === dailyAccount);
  if (!account) return;
  $("#daily-source").textContent =
    `${account.dailySource || ""}${account.dailyNote ? ". " + account.dailyNote : ""}${account.status !== "ok" ? " · Stale account reading" : ""}`;
  const map = new Map(account.daily.map((d) => [d.date, d.tokens]));
  const days = Array.from({ length: 14 }, (_, i) => {
    const date = new Date();
    date.setUTCDate(date.getUTCDate() - 13 + i);
    const key = date.toISOString().slice(0, 10);
    return { key, label: date.getUTCDate(), tokens: map.get(key) };
  });
  const available = days.filter((d) => d.tokens !== undefined);
  $("#daily-summary").innerHTML = available.length
    ? `<strong>${compact(available.reduce((n, d) => n + d.tokens, 0))}</strong> tokens reported over the last 14 days`
    : "<strong>—</strong> No daily activity reported";
  if (!available.length) {
    $("#daily-chart").innerHTML =
      '<p class="empty-chart">No daily data for this account.<br>Quota tracking is still available above.</p>';
    return;
  }
  const max = Math.max(1, ...available.map((d) => d.tokens));
  $("#daily-chart").innerHTML = days
    .map(
      (d, i) =>
        `<div class="day" tabindex="0" aria-label="${d.key}: ${d.tokens === undefined ? "not reported" : Math.round(d.tokens).toLocaleString() + " tokens"}"><span class="day-tip">${d.key} · ${d.tokens === undefined ? "Not reported" : compact(d.tokens) + " tokens"}</span><span class="day-bar" style="height:${d.tokens === undefined ? 0 : Math.max(1, (d.tokens / max) * 100)}%;${d.tokens === undefined ? "background:transparent;border-bottom:1px dashed #69725f" : ""}"></span><span class="day-label">${i === 0 ? d.key.slice(5) : d.label}</span></div>`,
    )
    .join("");
}
$("#activity-account").addEventListener("change", (event) => {
  dailyAccount = event.target.value;
  renderDaily();
});
async function openDetail(id) {
  detailAccount = id;
  const a = snapshot.accounts.find((a) => a.id === id);
  if (!a) return;
  $("#detail-content").innerHTML =
    `<h2>${names[a.provider]} <span class="accent">/</span> ${escape(a.plan || "Subscription")}</h2><p class="detail-source">${escape(a.label)}<br>${escape(a.source || "Waiting for provider")} · ${escape(dateTime(a.updatedAt))}</p>${a.status !== "ok" ? `<p class="card-error">${escape(a.error || "This reading is stale.")}</p>` : ""}<h3 class="history-title">Tokens per hour · UTC</h3><div id="detail-heatmap">Loading token records…</div><h3 class="history-title">Quota windows</h3><div class="detail-windows" style="--accent:${colors[a.provider]}">${groupedWindows(a.windows)}</div><h3 class="history-title">Quota history · past 7 days</h3><select id="history-window" class="history-select" aria-label="Quota history window">${a.windows.map((w) => `<option value="${escape(w.id)}">${escape(w.group)} · ${escape(w.label)}</option>`).join("")}</select><div id="history-chart" class="history-chart"><p class="history-empty">Loading history…</p></div><p class="footnote">Hourly averages of percentage used. Blank periods have no samples. A quota reset may lower the next reading.</p><p class="detail-footnote">${escape(a.dailySource || "")}${a.dailyNote ? "<br>" + escape(a.dailyNote) : ""}</p>`;
  if (!$("#detail-dialog").open) showDialog("#detail-dialog");
  $("#history-window").addEventListener("change", loadHistory);
  window.dispatchEvent(
    new CustomEvent("atlas:account-detail", { detail: { accountId: id } }),
  );
  await loadHistory();
}
async function loadHistory() {
  const requestId = ++historyRequest;
  const windowId = $("#history-window")?.value;
  if (!windowId) {
    $("#history-chart").innerHTML =
      '<p class="history-empty">History begins with the first successful reading.</p>';
    return;
  }
  try {
    const response = await fetch(
      `/api/history?account=${encodeURIComponent(detailAccount)}&window=${encodeURIComponent(windowId)}&days=7`,
    );
    if (!response.ok) throw Error();
    const data = await response.json();
    if (requestId !== historyRequest) return;
    const buckets = new Map(data.map((d) => [Math.floor(d.at / 3600), d]));
    const end = Math.floor(now() / 3600);
    const timeline = Array.from({ length: 168 }, (_, i) =>
      buckets.get(end - 167 + i),
    );
    $("#history-chart").innerHTML =
      data.length < 2
        ? '<p class="history-empty">Not enough samples yet. History appears after at least two hourly readings.</p>'
        : timeline
            .map((d) =>
              d
                ? `<div class="history-bar" style="height:${Math.min(100, Math.max(1, d.usedPercent))}%" title="${escape(dateTime(d.at))}: ${percent(d.usedPercent)} used"></div>`
                : '<div class="history-bar history-gap" aria-hidden="true"></div>',
            )
            .join("");
  } catch {
    if (requestId === historyRequest)
      $("#history-chart").innerHTML =
        '<p class="history-empty">History could not be loaded. Try again shortly.</p>';
  }
}
$("#accounts-button").addEventListener("click", async () => {
  showDialog("#accounts-dialog");
  $("#setup-content").textContent = "Loading setup instructions…";
  try {
    const response = await fetch("/api/setup");
    if (!response.ok) throw Error();
    const data = await response.json();
    $("#setup-content").innerHTML =
      data.accounts
        .map(
          (a) =>
            `<div class="setup-item"><h3>${names[a.provider]}</h3><div class="command"><code>${escape(a.command)}</code><button class="copy">Copy</button></div></div>`,
        )
        .join("") +
      `<p class="detail-footnote">Use a different profile name for each new account. For custom locations, add entries to <code>${escape(data.configFile)}</code>.</p><p class="detail-footnote">Claude and Kimi need the probe folder trusted once per profile. On the server, change directory to <code>${escape(data.probeDirectory)}</code>, open that CLI with the same profile environment, and accept trust for this empty folder.</p>`;
    $("#setup-content")
      .querySelectorAll(".copy")
      .forEach((b) =>
        b.addEventListener("click", async () => {
          const text = b.parentElement.querySelector("code").textContent;
          try {
            if (navigator.clipboard && window.isSecureContext)
              await navigator.clipboard.writeText(text);
            else {
              const area = document.createElement("textarea");
              area.value = text;
              area.style.position = "fixed";
              area.style.opacity = "0";
              b.closest("dialog").append(area);
              area.select();
              const ok = document.execCommand("copy");
              area.remove();
              if (!ok) throw Error();
            }
            toast("Command copied. Run it on this machine.");
          } catch {
            toast("Select and copy the command above.");
          }
        }),
      );
  } catch {
    $("#setup-content").textContent =
      "Setup instructions could not be loaded. Check the server connection.";
  }
});
function tick() {
  if (!snapshot) return;
  document.querySelectorAll("[data-age]").forEach((el) => {
    el.textContent = age(Number(el.dataset.age));
  });
  document.querySelectorAll("[data-countdown]").forEach((el) => {
    el.textContent = countdown(Number(el.dataset.countdown));
  });
  const reset = allResets().find(({ a }) => a.status === "ok");
  $("#next-reset").textContent = reset ? countdown(reset.w.resetsAt) : "—";
  $("#reset-name").textContent = reset
    ? `${names[reset.a.provider]} · ${reset.w.group} · ${reset.w.label}`
    : "No fresh reset times";
  $("#collector-label").textContent = !online
    ? "Disconnected"
    : snapshot.polling
      ? "Refreshing"
      : snapshot.accounts.some((a) => a.status !== "ok")
        ? "Needs attention"
        : "Polling active";
  $("#next-poll").textContent = !online
    ? "Reconnecting automatically"
    : snapshot.polling
      ? "Reading provider usage"
      : `Next poll in ${countdown(snapshot.nextPollAt)}`;
  const progress = snapshot.polling
    ? 100
    : Math.min(
        100,
        Math.max(
          0,
          (1 - (snapshot.nextPollAt - now()) / snapshot.pollInterval) * 100,
        ),
      );
  $("#poll-progress").style.width = `${progress}%`;
  $("#refresh").classList.toggle("busy", snapshot.polling);
  $("#refresh").disabled = snapshot.polling;
}
let reading = false;
async function load() {
  if (reading) return;
  reading = true;
  try {
    const response = await fetch("/api/snapshot", {
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw Error();
    const data = await response.json();
    online = true;
    $("#connection").hidden = true;
    snapshot = data;
    const version = JSON.stringify(data.accounts);
    if (version !== lastVersion) {
      lastVersion = version;
      renderCards();
      renderDaily();
      renderResets();
    }
    renderOverview();
  } catch {
    online = false;
    $("#connection").textContent =
      "The collector is unreachable. Displayed readings may be out of date. Reconnecting automatically.";
    $("#connection").hidden = false;
    tick();
  } finally {
    reading = false;
  }
}
$("#refresh").addEventListener("click", async () => {
  $("#refresh").disabled = true;
  try {
    const response = await fetch("/api/refresh", {
      method: "POST",
      headers: { "X-Atlas-Request": "1" },
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw Error();
    const data = await response.json();
    toast(
      data.status === "cooldown"
        ? "Just checked. Refresh is available once a minute."
        : data.status === "already-running"
          ? "A refresh is already running."
          : "Checking your subscriptions…",
    );
    await load();
  } catch {
    toast("Could not reach the collector.");
  } finally {
    $("#refresh").disabled = snapshot?.polling || false;
  }
});
load();
setInterval(load, 10000);
setInterval(tick, 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) load();
});
