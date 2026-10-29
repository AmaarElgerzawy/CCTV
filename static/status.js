"use strict";

const statusEl = document.getElementById("status");
const statusText = document.getElementById("status-text");
const sumOk = document.getElementById("sum-ok");
const sumStale = document.getElementById("sum-stale");
const sumProblems = document.getElementById("sum-problems");
const sumOffline = document.getElementById("sum-offline");
const sumUnknown = document.getElementById("sum-unknown");
const healthList = document.getElementById("health-list");
const healthNote = document.getElementById("health-note");
const healthSearch = document.getElementById("health-search");
const healthFilter = document.getElementById("health-filter");
const healthGroup = document.getElementById("health-group");
const checkAllBtn = document.getElementById("check-all");

function setStatus(kind, text) {
  statusEl.className = kind ? `status ${kind}` : "status";
  statusText.textContent = text;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) {
    const message = payload && typeof payload.error === "string" ? payload.error : `Request failed (HTTP ${response.status}).`;
    throw new Error(message);
  }
  return payload;
}

function timeAgo(iso) {
  if (!iso) return "unknown";
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 60) return Math.round(diff) + "s ago";
    if (diff < 3600) return Math.round(diff/60) + "m ago";
    if (diff < 86400) return Math.round(diff/3600) + "h ago";
    return Math.round(diff/86400) + "d ago";
  } catch { return iso; }
}

function channelCell(ch) {
  const c = document.createElement("div");
  c.className = "channel-cell";
  const st = ch.state || "unknown";
  c.classList.add(`ch-${st}`);
  let label = "";
  if (st === "unused") label = "unused";
  else if (st === "none") label = "none in lookback";
  else if (st === "unavailable") label = "unavailable";
  else if (st === "stale") {
    if (ch.last_end) {
      try {
        const ref = new Date();
        // approximate by showing last_end relative? just show "stale"
        label = "stale";
      } catch { label = "stale"; }
    } else label = "stale";
  } else if (st === "ok") {
    if (ch.last_end) label = "ok";
    else label = "ok";
  }
  c.textContent = `Ch${ch.channel}: ${label}`;
  if (ch.approximate) {
    const m = document.createElement("span");
    m.className = "approx";
    m.textContent = "~";
    c.appendChild(m);
  }
  return c;
}

async function load() {
  setStatus("loading", "Loading health…");
  try {
    const data = await requestJson("/api/health");
    const summary = data.summary || {};
    sumOk.textContent = summary.ok ?? 0;
    sumStale.textContent = summary.stale ?? 0;
    sumProblems.textContent = summary.problems ?? 0;
    sumOffline.textContent = summary.offline ?? 0;
    sumUnknown.textContent = summary.unknown ?? 0;
    window._last = data;
    render(data);
    const round = data.round || {};
    if (round.running) {
      const n = round.checked || 0;
      const m = round.total || 0;
      setStatus("warn", `Check running: ${n} of ${m}`);
      if (checkAllBtn) { checkAllBtn.disabled = true; checkAllBtn.textContent = "Checking…"; }
    } else {
      if (checkAllBtn) { checkAllBtn.disabled = false; checkAllBtn.textContent = "Check all (slow)"; }
      setStatus("ok", "Loaded");
    }
  } catch (e) {
    setStatus("error", e.message);
    healthList.replaceChildren();
    healthNote.textContent = e.message;
    if (checkAllBtn) { checkAllBtn.disabled = false; checkAllBtn.textContent = "Check all (slow)"; }
  }
}

function render(data) {
  const items = Array.isArray(data.devices) ? data.devices : [];
  const q = healthSearch.value.trim().toLowerCase();
  const filter = healthFilter.value;
  const grouped = healthGroup.checked;
  let filtered = items.filter(it => {
    const d = it.device || {};
    const h = it.health || {};
    if (q) {
      const hay = `${d.name||""} ${d.host||""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (filter === "problems") {
      const st = h.state;
      if (!(st && st !== "ok")) return false;
    }
    return true;
  });
  filtered.sort((a,b) => {
    const sa = (a.health||{}).state||"unknown";
    const sb = (b.health||{}).state||"unknown";
    const rank = s => (s==="ok"||s==="unknown"?1:(s==="offline"?0: -1));
    if (rank(sa) !== rank(sb)) return rank(sa)-rank(sb);
    return 0;
  });
  healthList.replaceChildren();
  if (filtered.length === 0) {
    // check if there are any devices at all
    if (!items || items.length === 0) {
      healthNote.textContent = "No devices registered yet";
    } else {
      healthNote.textContent = "No devices match filters.";
    }
    return;
  }
  healthNote.textContent = "";
  if (grouped) {
    const map = new Map();
    for (const it of filtered) {
      const key = (it.device||{}).group || "No group";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(it);
    }
    const names = [...map.keys()].sort((a,b)=>a.localeCompare(b));
    for (const name of names) {
      const sec = document.createElement("section");
      sec.className = "device-group";
      const h = document.createElement("h3");
      h.textContent = name;
      sec.append(h);
      for (const it of map.get(name)) sec.append(row(it));
      healthList.append(sec);
    }
  } else {
    for (const it of filtered) healthList.append(row(it));
  }
}

function row(it) {
  const d = it.device || {};
  const h = it.health || {};
  const wrap = document.createElement("div");
  wrap.className = "health-row";
  const top = document.createElement("div");
  top.className = "health-top";
  const left = document.createElement("div");
  const name = document.createElement("a");
  name.href = `/?device=${encodeURIComponent(d.id)}`;
  name.textContent = d.name || "—";
  const meta = document.createElement("span");
  meta.className = "muted";
  const checked = h.checked_at ? timeAgo(h.checked_at) : "never checked";
  meta.textContent = ` ${d.host||""}:${d.http_port||""} · checked ${checked}`;
  left.append(name, document.createElement("br"), meta);
  const badges = document.createElement("div");
  const bs = document.createElement("span");
  bs.className = "badge";
  bs.textContent = h.state || "unknown";
  const bstor = document.createElement("span");
  bstor.className = "badge";
  bstor.textContent = h.storage_state || "storage?";
  badges.append(bs, bstor);
  const act = document.createElement("div");
  const chk = document.createElement("button");
  chk.className = "button button-ghost button-small";
  chk.textContent = "Check now";
  chk.onclick = async () => {
    chk.disabled = true;
    try {
      await requestJson(`/api/devices/${d.id}/health/check`, {method:"POST"});
      await load();
    } catch(e) {
      setStatus("error", e.message);
    } finally {
      chk.disabled = false;
    }
  };
  act.append(chk);
  top.append(left, badges, act);
  const chs = document.createElement("div");
  chs.className = "channel-grid";
  const carr = Array.isArray(h.channels) ? h.channels : [];
  for (const c of carr) chs.append(channelCell(c));
  wrap.append(top, chs);
  return wrap;
}

checkAllBtn.onclick = async () => {
  checkAllBtn.disabled = true;
  try {
    await requestJson("/api/health/check-all", {method:"POST"});
    setStatus("ok", "Check all started");
    setTimeout(load, 500);
  } catch(e) {
    if (e.message && (e.message.includes("already") || e.message.includes("running"))) setStatus("warn", e.message);
    else setStatus("error", e.message);
    checkAllBtn.disabled = false;
  }
};
healthSearch.oninput = () => { if (window._last) render(window._last); };
healthFilter.onchange = () => { if (window._last) render(window._last); };
healthGroup.onchange = () => { if (window._last) render(window._last); };

load();
setInterval(load, 30000);
