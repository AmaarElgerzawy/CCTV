const refreshButton = document.getElementById("refresh");
const statusBox = document.getElementById("status");
const statusText = document.getElementById("status-text");
const detailsList = document.getElementById("details");
const fieldCount = document.getElementById("field-count");
const rawOutput = document.getElementById("raw");
const updatedLabel = document.getElementById("updated");
const failedNote = document.getElementById("failed-calls");

const summaryFields = {
  device_type: document.getElementById("device-type"),
  device_class: document.getElementById("device-class"),
  software_version: document.getElementById("software-version"),
  build_date: document.getElementById("build-date"),
};

const channelCount = document.getElementById("channel-count");
const channelsStatus = document.getElementById("channels-status");
const channelNames = document.getElementById("channel-names");
const channelCounts = document.getElementById("channel-counts");
const channelsRaw = document.getElementById("channels-raw");

const networkStatus = document.getElementById("network-status");
const networkTable = document.getElementById("network-table");
const networkRaw = document.getElementById("network-raw");
const encodeStatus = document.getElementById("encode-status");
const encodeTable = document.getElementById("encode-table");
const encodeRaw = document.getElementById("encode-raw");

const liveStatus = document.getElementById("live-status");
const liveGrid = document.getElementById("live-grid");
const liveLarge = document.getElementById("live-large");
const liveLargeTitle = document.getElementById("live-large-title");
const liveLargeFrame = document.getElementById("live-large-frame");
const liveLargeClose = document.getElementById("live-large-close");

const CALL_METADATA_KEYS = new Set(["succeeded_calls", "failed_calls"]);
const MAX_TABLE_ROWS = 400;
const LIVE_CHANNELS = [1, 2, 3, 4];

const liveState = {
  publicUrl: null,
  tiles: new Map(),
  large: null,
};

function formatValue(value) {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function emptyNode(message) {
  return Object.assign(document.createElement("div"), {
    className: "empty",
    textContent: message,
  });
}

function failedCallSummary(failedCalls) {
  return failedCalls
    .map((call) => {
      const method =
        call && typeof call.method === "string"
          ? call.method
          : "unknown method";
      const code =
        call && call.error_code !== null && call.error_code !== undefined
          ? call.error_code
          : "no code";
      return `${method} (code ${code})`;
    })
    .join("; ");
}

function renderDetails(info) {
  const entries = Object.entries(info)
    .filter(([key]) => !CALL_METADATA_KEYS.has(key))
    .sort(([a], [b]) =>
      a.localeCompare(b, undefined, { sensitivity: "base" }),
    );

  fieldCount.textContent = `${entries.length} field${
    entries.length === 1 ? "" : "s"
  }`;

  if (entries.length === 0) {
    detailsList.replaceChildren(
      emptyNode("The DVR returned an empty device info object."),
    );
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const [key, value] of entries) {
    const row = document.createElement("div");
    row.className = "detail-row";

    const term = document.createElement("dt");
    term.textContent = key;

    const description = document.createElement("dd");
    description.textContent = formatValue(value);

    row.append(term, description);
    fragment.append(row);
  }
  detailsList.replaceChildren(fragment);
}

function renderFailedCalls(failedCalls) {
  if (!Array.isArray(failedCalls) || failedCalls.length === 0) {
    failedNote.hidden = true;
    failedNote.textContent = "";
    return;
  }

  failedNote.textContent = `Failed calls: ${failedCallSummary(failedCalls)}.`;
  failedNote.hidden = false;
}

function render(info) {
  for (const [key, element] of Object.entries(summaryFields)) {
    const value = info[key];
    element.textContent =
      value === null || value === undefined || String(value).trim() === ""
        ? "Not reported"
        : String(value);
  }
  renderFailedCalls(info.failed_calls);
  renderDetails(info);
  rawOutput.textContent = JSON.stringify(info, null, 2);
  updatedLabel.textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

function flattenRows(value, prefix = "") {
  const rows = [];

  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      const label = prefix ? `${prefix}[${index}]` : `[${index}]`;
      if (item && typeof item === "object") {
        rows.push(...flattenRows(item, label));
      } else {
        rows.push([label, item]);
      }
    });
    return rows;
  }

  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      const label = prefix ? `${prefix}.${key}` : key;
      if (item && typeof item === "object") {
        rows.push(...flattenRows(item, label));
      } else {
        rows.push([label, item]);
      }
    }
    return rows;
  }

  rows.push([prefix || "value", value]);
  return rows;
}

function rowCount(value) {
  if (value === null || value === undefined) {
    return 0;
  }
  return flattenRows(value).length;
}

function renderTable(container, value, emptyMessage) {
  if (value === null || value === undefined) {
    container.replaceChildren(emptyNode(emptyMessage));
    return;
  }

  const rows = flattenRows(value);
  if (rows.length === 0) {
    container.replaceChildren(emptyNode(emptyMessage));
    return;
  }

  const table = document.createElement("table");
  table.className = "config-table";

  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Field", "Value"]) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);

  const body = document.createElement("tbody");
  for (const [field, item] of rows.slice(0, MAX_TABLE_ROWS)) {
    const row = document.createElement("tr");
    const term = document.createElement("th");
    term.scope = "row";
    term.textContent = field;
    const description = document.createElement("td");
    description.textContent = formatValue(item);
    row.append(term, description);
    body.append(row);
  }

  table.append(head, body);
  const fragment = document.createDocumentFragment();
  fragment.append(table);

  if (rows.length > MAX_TABLE_ROWS) {
    fragment.append(
      emptyNode(
        `Showing the first ${MAX_TABLE_ROWS} of ${rows.length} fields.`,
      ),
    );
  }

  container.replaceChildren(fragment);
}

function setSectionNote(element, state, message) {
  element.className = `section-note ${state}`;
  element.textContent = message;
}

function channelCountDetails(countInfo) {
  if (!countInfo || typeof countInfo !== "object" || Array.isArray(countInfo)) {
    return countInfo;
  }

  const wanted = [
    "CurLocal",
    "CurRemote",
    "CurTotal",
    "MaxLocal",
    "MaxRemote",
    "MaxTotal",
  ];
  const selected = {};
  for (const key of wanted) {
    if (Object.prototype.hasOwnProperty.call(countInfo, key)) {
      selected[key] = countInfo[key];
    }
  }
  return Object.keys(selected).length > 0 ? selected : countInfo;
}

function renderChannels(channels) {
  const count = channels.count;
  channelCount.textContent =
    typeof count === "number"
      ? `${count} channel${count === 1 ? "" : "s"}`
      : "Count not reported";

  const names = Array.isArray(channels.names) ? channels.names : [];
  const failedCalls = Array.isArray(channels.failed_calls)
    ? channels.failed_calls
    : [];

  if (names.length > 0) {
    const list = document.createElement("ul");
    list.className = "name-items";
    for (const name of names) {
      const item = document.createElement("li");
      item.textContent = formatValue(name);
      list.append(item);
    }
    channelNames.replaceChildren(list);
  } else {
    channelNames.replaceChildren(
      emptyNode(
        channels.titles_status === "failed"
          ? "Channel titles could not be read."
          : "The DVR did not report channel titles.",
      ),
    );
  }

  renderTable(
    channelCounts,
    channelCountDetails(channels.count_info),
    "No channel count breakdown was reported.",
  );
  channelsRaw.textContent = JSON.stringify(channels, null, 2);

  if (failedCalls.length > 0) {
    setSectionNote(
      channelsStatus,
      "warn",
      `Loaded with ${failedCalls.length} failed call${
        failedCalls.length === 1 ? "" : "s"
      }: ${failedCallSummary(failedCalls)}.`,
    );
  } else if (names.length > 0) {
    setSectionNote(
      channelsStatus,
      "ok",
      `${names.length} channel title${names.length === 1 ? "" : "s"} reported.`,
    );
  } else {
    setSectionNote(
      channelsStatus,
      "warn",
      "Channel count loaded; titles were not reported.",
    );
  }
}

function renderConfig(config, statusElement, tableElement, rawElement) {
  const name = typeof config.name === "string" ? config.name : "Config";
  const failedCalls = Array.isArray(config.failed_calls)
    ? config.failed_calls
    : [];

  renderTable(
    tableElement,
    config.table,
    `No ${name} fields were returned.`,
  );
  rawElement.textContent = JSON.stringify(config, null, 2);

  if (failedCalls.length > 0) {
    setSectionNote(
      statusElement,
      "warn",
      `${name} read failed: ${failedCallSummary(failedCalls)}.`,
    );
  } else if (config.table === null || config.table === undefined) {
    setSectionNote(statusElement, "warn", `${name} returned no table.`);
  } else {
    const count = rowCount(config.table);
    setSectionNote(
      statusElement,
      "ok",
      `${name} loaded (${count} field${count === 1 ? "" : "s"}).`,
    );
  }
}

function setStatus(state, message) {
  statusBox.className = `status ${state}`;
  statusText.textContent = message;
}

function setLoading(loading) {
  refreshButton.disabled = loading;
  refreshButton.textContent = loading ? "Refreshing…" : "Refresh";
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
    cache: "no-store",
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // The status code below still gives a useful error.
  }

  if (!response.ok) {
    throw new Error(
      payload?.error || `Request failed (HTTP ${response.status}).`,
    );
  }
  return payload;
}

async function loadDeviceInfo() {
  setStatus("loading", "Loading device information…");

  try {
    const payload = await fetchJson("/api/device/info");
    const info = payload?.info;
    if (!info || typeof info !== "object" || Array.isArray(info)) {
      throw new Error("The server did not return device information.");
    }

    const failedCount = Array.isArray(info.failed_calls)
      ? info.failed_calls.length
      : 0;

    render(info);
    if (failedCount > 0) {
      setStatus(
        "warn",
        `Connected. ${failedCount} device call${
          failedCount === 1 ? "" : "s"
        } failed.`,
      );
    } else {
      setStatus("ok", "Connected. Device information loaded.");
    }
  } catch (error) {
    setStatus("error", error.message || "Could not load device information.");
  }
}

async function loadChannels() {
  setSectionNote(channelsStatus, "loading", "Loading channels…");

  try {
    const payload = await fetchJson("/api/channels");
    const channels = payload?.channels;
    if (!channels || typeof channels !== "object" || Array.isArray(channels)) {
      throw new Error("The server did not return channel information.");
    }
    renderChannels(channels);
  } catch (error) {
    setSectionNote(
      channelsStatus,
      "error",
      error.message || "Could not load channels.",
    );
    channelCount.textContent = "—";
    channelNames.replaceChildren();
    channelCounts.replaceChildren();
    channelsRaw.textContent = "—";
  }
}

async function loadConfig(name, statusElement, tableElement, rawElement) {
  setSectionNote(statusElement, "loading", `Loading ${name}…`);

  try {
    const payload = await fetchJson(`/api/config/${encodeURIComponent(name)}`);
    const config = payload?.config;
    if (!config || typeof config !== "object" || Array.isArray(config)) {
      throw new Error(`The server did not return ${name} configuration.`);
    }
    renderConfig(config, statusElement, tableElement, rawElement);
  } catch (error) {
    setSectionNote(
      statusElement,
      "error",
      error.message || `Could not load ${name} configuration.`,
    );
    tableElement.replaceChildren();
    rawElement.textContent = "—";
  }
}

function createLiveTiles() {
  const fragment = document.createDocumentFragment();

  for (const channel of LIVE_CHANNELS) {
    const tile = document.createElement("article");
    tile.className = "live-tile";
    tile.dataset.channel = String(channel);

    const head = document.createElement("div");
    head.className = "live-tile-head";

    const title = document.createElement("h3");
    title.textContent = `Channel ${channel}`;

    const badge = document.createElement("span");
    badge.className = "live-badge";
    badge.textContent = "Idle";
    head.append(title, badge);

    const frame = document.createElement("div");
    frame.className = "live-frame";
    frame.append(emptyNode("Not started."));

    const actions = document.createElement("div");
    actions.className = "live-actions";

    const start = document.createElement("button");
    start.type = "button";
    start.className = "button button-small";
    start.textContent = "Start";
    start.addEventListener("click", () => startTile(channel));

    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "button button-ghost button-small";
    stop.textContent = "Stop";
    stop.disabled = true;
    stop.addEventListener("click", () => stopTile(channel));

    const large = document.createElement("button");
    large.type = "button";
    large.className = "button button-ghost button-small";
    large.textContent = "Larger view";
    large.addEventListener("click", () => openLarge(channel));

    actions.append(start, stop, large);

    const status = document.createElement("p");
    status.className = "section-note";
    status.setAttribute("role", "status");
    status.textContent = "Idle.";

    tile.append(head, frame, actions, status);
    fragment.append(tile);

    liveState.tiles.set(channel, {
      name: null,
      quality: null,
      frame,
      badge,
      start,
      stop,
      large,
      status,
    });
  }

  liveGrid.replaceChildren(fragment);
}

function setTileStatus(channel, state, message) {
  const tile = liveState.tiles.get(channel);
  if (!tile) {
    return;
  }
  tile.status.className = `section-note ${state}`;
  tile.status.textContent = message;
}

function updateLiveButtons() {
  for (const tile of liveState.tiles.values()) {
    const active = Boolean(tile.name);
    tile.start.disabled = active || !liveState.publicUrl;
    tile.stop.disabled = !active;
    tile.large.disabled = !liveState.publicUrl;
  }
  liveLargeClose.disabled = !liveState.large;
}

function playerUrl(name) {
  return `${liveState.publicUrl}/stream.html?src=${encodeURIComponent(
    name,
  )}&mode=mse`;
}

function buildLiveFrame(name, title) {
  const iframe = document.createElement("iframe");
  iframe.className = "live-iframe";
  iframe.title = title;
  iframe.allow = "autoplay; fullscreen";
  iframe.setAttribute("allowfullscreen", "");
  iframe.src = playerUrl(name);
  return iframe;
}

function sendCloseBeacon(name) {
  const url = `/api/live/${encodeURIComponent(name)}/close`;
  if (navigator.sendBeacon) {
    navigator.sendBeacon(url);
    return;
  }
  fetch(url, { method: "POST", keepalive: true }).catch(() => {});
}

async function closeStream(name) {
  await fetchJson(`/api/live/${encodeURIComponent(name)}/close`, {
    method: "POST",
  });
}

async function startTile(channel) {
  const tile = liveState.tiles.get(channel);
  if (!tile || tile.name) {
    return;
  }
  if (!liveState.publicUrl) {
    setTileStatus(channel, "error", "Live view is not configured.");
    return;
  }

  tile.start.disabled = true;
  setTileStatus(channel, "loading", "Opening sub stream…");

  try {
    const payload = await fetchJson(
      `/api/live/${channel}/open?quality=sub`,
      { method: "POST" },
    );
    const name = payload?.stream_name;
    if (typeof name !== "string" || name === "") {
      throw new Error("The server did not return a stream name.");
    }

    tile.name = name;
    tile.quality = "sub";
    tile.frame.replaceChildren(
      buildLiveFrame(name, `Channel ${channel} sub stream`),
    );
    tile.badge.textContent = "Live";
    tile.badge.className = "live-badge live";
    setTileStatus(channel, "ok", "Sub stream playing.");
  } catch (error) {
    tile.frame.replaceChildren(emptyNode("Not started."));
    tile.badge.textContent = "Idle";
    tile.badge.className = "live-badge";
    setTileStatus(
      channel,
      "error",
      error.message || "Could not start the live stream.",
    );
  } finally {
    updateLiveButtons();
  }
}

async function stopTile(channel) {
  const tile = liveState.tiles.get(channel);
  if (!tile || !tile.name) {
    return;
  }

  const name = tile.name;
  tile.name = null;
  tile.quality = null;
  tile.stop.disabled = true;
  setTileStatus(channel, "loading", "Stopping…");

  try {
    await closeStream(name);
    setTileStatus(channel, "ok", "Stopped.");
  } catch (error) {
    setTileStatus(
      channel,
      "error",
      error.message || "Could not stop the stream.",
    );
  } finally {
    tile.frame.replaceChildren(emptyNode("Not started."));
    tile.badge.textContent = "Idle";
    tile.badge.className = "live-badge";
    updateLiveButtons();
  }
}

function clearLargeView() {
  liveState.large = null;
  liveLarge.hidden = true;
  liveLargeFrame.replaceChildren();
  updateLiveButtons();
}

async function openLarge(channel) {
  if (!liveState.publicUrl) {
    setSectionNote(liveStatus, "error", "Live view is not configured.");
    return;
  }

  setSectionNote(
    liveStatus,
    "loading",
    `Opening main stream for channel ${channel}…`,
  );

  if (liveState.large) {
    try {
      await closeStream(liveState.large.name);
    } catch (error) {
      setSectionNote(
        liveStatus,
        "error",
        error.message || "Could not close the previous larger view.",
      );
      return;
    }
    clearLargeView();
  }

  try {
    const payload = await fetchJson(
      `/api/live/${channel}/open?quality=main`,
      { method: "POST" },
    );
    const name = payload?.stream_name;
    if (typeof name !== "string" || name === "") {
      throw new Error("The server did not return a stream name.");
    }

    liveState.large = { name, channel };
    liveLargeFrame.replaceChildren(
      buildLiveFrame(name, `Channel ${channel} main stream`),
    );
    liveLargeTitle.textContent = `Channel ${channel} — main stream`;
    liveLarge.hidden = false;
    updateLiveButtons();
    setSectionNote(
      liveStatus,
      "ok",
      `Larger view is playing channel ${channel} main stream.`,
    );
  } catch (error) {
    setSectionNote(
      liveStatus,
      "error",
      error.message || "Could not open the larger view.",
    );
  }
}

async function closeLarge() {
  const large = liveState.large;
  if (!large) {
    return;
  }

  try {
    await closeStream(large.name);
    setSectionNote(liveStatus, "ok", "Larger view closed.");
  } catch (error) {
    setSectionNote(
      liveStatus,
      "error",
      error.message || "Could not close the larger view.",
    );
  } finally {
    clearLargeView();
  }
}

function closeAllOnUnload() {
  const names = new Set();
  for (const tile of liveState.tiles.values()) {
    if (tile.name) {
      names.add(tile.name);
    }
  }
  if (liveState.large) {
    names.add(liveState.large.name);
  }
  for (const name of names) {
    sendCloseBeacon(name);
  }
}

async function loadLiveConfig() {
  setSectionNote(liveStatus, "loading", "Loading live-view configuration…");

  try {
    const payload = await fetchJson("/api/config");
    const url = payload?.public_go2rtc_url;
    if (typeof url !== "string" || !/^https?:\/\//.test(url)) {
      throw new Error("The server did not return a usable go2rtc URL.");
    }
    liveState.publicUrl = url.replace(/\/+$/, "");
    setSectionNote(
      liveStatus,
      "ok",
      "Start a channel to open an on-demand stream.",
    );
  } catch (error) {
    liveState.publicUrl = null;
    setSectionNote(
      liveStatus,
      "error",
      error.message || "Could not load live-view configuration.",
    );
  }

  updateLiveButtons();
}

async function loadAll() {
  setLoading(true);
  // Sequential requests: one short-lived DVR session at a time, so a DVR that
  // limits concurrent sessions is not asked to hold four logins at once.
  await loadLiveConfig();
  await loadDeviceInfo();
  await loadChannels();
  await loadConfig("Network", networkStatus, networkTable, networkRaw);
  await loadConfig("Encode", encodeStatus, encodeTable, encodeRaw);
  setLoading(false);
}

refreshButton.addEventListener("click", loadAll);
liveLargeClose.addEventListener("click", closeLarge);
window.addEventListener("pagehide", closeAllOnUnload);
createLiveTiles();
updateLiveButtons();
loadAll();
