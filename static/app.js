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
const liveConnect = document.getElementById("live-connect");
const liveDisconnect = document.getElementById("live-disconnect");
const liveQuality = document.getElementById("live-quality");
const liveLarge = document.getElementById("live-large");
const liveLargeTitle = document.getElementById("live-large-title");
const liveLargeFrame = document.getElementById("live-large-frame");
const liveLargeClose = document.getElementById("live-large-close");

const recChannel = document.getElementById("rec-channel");
const recDate = document.getElementById("rec-date");
const recStream = document.getElementById("rec-stream");
const recLoad = document.getElementById("rec-load");
const recHealth = document.getElementById("rec-health");
const recStatus = document.getElementById("rec-status");
const recSummary = document.getElementById("rec-summary");
const recTimelineWrap = document.getElementById("rec-timeline-wrap");
const recTimeline = document.getElementById("rec-timeline");
const recCursor = document.getElementById("rec-cursor");
const recSelected = document.getElementById("rec-selected");
const recFilesDetails = document.getElementById("rec-files-details");
const recFiles = document.getElementById("rec-files");

const CALL_METADATA_KEYS = new Set(["succeeded_calls", "failed_calls"]);
const MAX_TABLE_ROWS = 400;
const LIVE_FALLBACK_CHANNELS = 4;
const LIVE_RENEW_INTERVAL_MS = 10000;
const LIVE_START_GAP_MS = 400;
const LIVE_INCLUDE_KEY_PREFIX = "dvr-live-include-";
const DEFAULT_MAX_CHANNEL = 8;
const DAY_SECONDS = 86400;
const MAX_RECORDING_ROWS = 400;

const liveState = {
  publicUrl: null,
  maxChannel: DEFAULT_MAX_CHANNEL,
  tiles: new Map(),
  large: null,
  renewTimer: null,
  connecting: false,
  disconnecting: false,
};

const recordingsState = {
  loading: false,
  requestId: 0,
  date: null,
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

function cardText(value) {
  if (value === null || value === undefined) {
    return "Not reported";
  }
  if (typeof value === "object") {
    if (!Array.isArray(value)) {
      const version = value.Version;
      if (typeof version === "string" && version.trim() !== "") {
        return version;
      }
      for (const candidate of Object.values(value)) {
        if (typeof candidate === "string" && candidate.trim() !== "") {
          return candidate;
        }
        if (typeof candidate === "number") {
          return String(candidate);
        }
      }
    }
    return JSON.stringify(value);
  }
  const text = String(value);
  return text.trim() === "" ? "Not reported" : text;
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
    element.textContent = cardText(info[key]);
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
  syncLiveTiles(typeof count === "number" ? count : null);
  syncRecordingChannels(typeof count === "number" ? count : null);

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
    syncLiveTiles(null);
    syncRecordingChannels(null);
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

function desiredChannelCount(count) {
  const cap =
    Number.isInteger(liveState.maxChannel) && liveState.maxChannel > 0
      ? liveState.maxChannel
      : DEFAULT_MAX_CHANNEL;
  if (!Number.isInteger(count) || count < 1) {
    return Math.min(LIVE_FALLBACK_CHANNELS, cap);
  }
  return Math.min(count, cap);
}

function syncLiveTiles(count) {
  const hasActive = [...liveState.tiles.values()].some((tile) => tile.name);
  if (hasActive) {
    return;
  }
  const desired = desiredChannelCount(count);
  if (liveState.tiles.size === desired) {
    return;
  }
  createLiveTiles(desired);
}

function syncRecordingChannels(count) {
  const desired = desiredChannelCount(count);
  const report = count === null || count === undefined ? "" : String(count);
  const previous = Number.parseInt(recChannel.value, 10);
  if (
    recChannel.options.length === desired &&
    recChannel.dataset.count === report
  ) {
    return;
  }

  const fragment = document.createDocumentFragment();
  for (let channel = 1; channel <= desired; channel += 1) {
    const option = document.createElement("option");
    option.value = String(channel);
    option.textContent = `Channel ${channel}`;
    fragment.append(option);
  }
  recChannel.replaceChildren(fragment);
  recChannel.dataset.count = report;
  if (Number.isInteger(previous) && previous >= 1 && previous <= desired) {
    recChannel.value = String(previous);
  }
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function secondsIntoDay(text, day) {
  const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(
    String(text || ""),
  );
  if (!match) {
    return null;
  }
  const datePart = `${match[1]}-${match[2]}-${match[3]}`;
  if (datePart < day) {
    return 0;
  }
  if (datePart > day) {
    return DAY_SECONDS;
  }
  return (
    Number(match[4]) * 3600 + Number(match[5]) * 60 + Number(match[6])
  );
}

function formatDaySeconds(seconds) {
  const value = Math.min(Math.max(0, Math.round(seconds)), DAY_SECONDS - 1);
  return `${pad2(Math.floor(value / 3600))}:${pad2(
    Math.floor((value % 3600) / 60),
  )}:${pad2(value % 60)}`;
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) {
    return "—";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function clearRecordings() {
  recordingsState.date = null;
  recSummary.hidden = true;
  recSummary.textContent = "";
  recSummary.className = "section-note";
  recTimelineWrap.hidden = true;
  recFilesDetails.hidden = true;
  recFilesDetails.open = false;
  recFiles.replaceChildren();
  for (const bar of recTimeline.querySelectorAll(".rec-bar")) {
    bar.remove();
  }
  recCursor.hidden = true;
  recSelected.textContent = "Click the timeline to mark a time.";
}

function renderTimeline(files, day) {
  for (const bar of recTimeline.querySelectorAll(".rec-bar")) {
    bar.remove();
  }
  const fragment = document.createDocumentFragment();
  for (const file of files) {
    const start = secondsIntoDay(file?.start, day);
    const end = secondsIntoDay(file?.end, day);
    if (start === null || end === null || end <= start) {
      continue;
    }
    const left = Math.min(
      100,
      Math.max(0, (start / DAY_SECONDS) * 100),
    );
    const width = Math.min(
      100 - left,
      Math.max(0.12, ((end - start) / DAY_SECONDS) * 100),
    );
    const bar = document.createElement("span");
    bar.className = "rec-bar";
    bar.style.left = `${left}%`;
    bar.style.width = `${width}%`;
    bar.title = `${formatValue(file.start)} → ${formatValue(
      file.end,
    )} · ${formatBytes(file.length_bytes)}`;
    fragment.append(bar);
  }
  recTimeline.insertBefore(fragment, recCursor);
}

function renderRecordingFiles(files) {
  if (files.length === 0) {
    recFiles.replaceChildren(
      emptyNode("No recording files were reported for this day."),
    );
    return;
  }

  const table = document.createElement("table");
  table.className = "config-table rec-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Start", "End", "Type", "Flags", "Size"]) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);

  const body = document.createElement("tbody");
  for (const file of files.slice(0, MAX_RECORDING_ROWS)) {
    const row = document.createElement("tr");
    const values = [
      formatValue(file?.start),
      formatValue(file?.end),
      formatValue(file?.type),
      Array.isArray(file?.flags) && file.flags.length > 0
        ? file.flags.join(", ")
        : "—",
      formatBytes(file?.length_bytes),
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body);

  const fragment = document.createDocumentFragment();
  fragment.append(table);
  if (files.length > MAX_RECORDING_ROWS) {
    fragment.append(
      emptyNode(
        `Showing the first ${MAX_RECORDING_ROWS} of ${files.length} files.`,
      ),
    );
  }
  recFiles.replaceChildren(fragment);
}

function renderRecordings(payload, channel, date, stream) {
  const label = `${stream === "main" ? "Main" : "Sub"} channel ${channel} on ${date}`;
  const sourceNote =
    payload?.device_time_source === "server"
      ? " The DVR clock could not be read, so dates use the server clock."
      : "";

  if (!payload || payload.available !== true) {
    clearRecordings();
    const reason =
      typeof payload?.reason === "string" && payload.reason.trim() !== ""
        ? payload.reason.trim()
        : "The DVR did not return recordings.";
    const ending = /[.!?]$/.test(reason) ? "" : ".";
    setSectionNote(
      recStatus,
      "warn",
      `Recordings are unavailable on this device: ${reason}${ending}${sourceNote}`,
    );
    return;
  }

  const files = Array.isArray(payload.files) ? payload.files : [];
  const gaps = Array.isArray(payload.gaps) ? payload.gaps : [];
  const truncated = payload.truncated === true;
  const coverage = Number.isFinite(payload.coverage_percent)
    ? payload.coverage_percent
    : 0;
  const lastEnd =
    typeof payload.last_end === "string" ? payload.last_end : null;

  recordingsState.date = date;
  renderTimeline(files, date);
  recTimelineWrap.hidden = false;
  recCursor.hidden = true;
  recSelected.textContent = "Click the timeline to mark a time.";
  renderRecordingFiles(files);
  recFilesDetails.hidden = false;
  recFilesDetails.open = false;

  recSummary.textContent = `${label}: ${coverage.toFixed(
    1,
  )}% of the day covered · last recording ${
    lastEnd ? `ends ${lastEnd}` : "not reported"
  } · ${gaps.length} gap${gaps.length === 1 ? "" : "s"} of 5 minutes or more.`;
  recSummary.className = "section-note";
  recSummary.hidden = false;

  const parts = [
    `${label}: ${files.length} file${files.length === 1 ? "" : "s"} loaded.`,
  ];
  if (truncated) {
    parts.push(
      "The list was truncated after 30 pages (3000 files); " +
        "the summary may be incomplete.",
    );
  }
  if (sourceNote) {
    parts.push(sourceNote.trim());
  }
  setSectionNote(
    recStatus,
    truncated || sourceNote ? "warn" : files.length > 0 ? "ok" : "warn",
    parts.join(" "),
  );
}

async function loadRecordings() {
  if (recordingsState.loading) {
    return;
  }
  const channel = Number.parseInt(recChannel.value, 10);
  const date = recDate.value;
  const stream = recStream.value === "sub" ? "sub" : "main";

  if (!Number.isInteger(channel)) {
    setSectionNote(recStatus, "error", "Choose a channel before loading.");
    return;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    setSectionNote(recStatus, "error", "Choose a date before loading.");
    return;
  }

  recordingsState.loading = true;
  recordingsState.requestId += 1;
  const requestId = recordingsState.requestId;
  recLoad.disabled = true;
  recLoad.textContent = "Loading…";
  setSectionNote(
    recStatus,
    "loading",
    `Loading ${stream === "main" ? "main" : "sub"} recordings…`,
  );

  try {
    const params = new URLSearchParams({
      channel: String(channel),
      date,
      stream,
    });
    const payload = await fetchJson(`/api/recordings?${params.toString()}`);
    if (requestId === recordingsState.requestId) {
      renderRecordings(payload, channel, date, stream);
    }
  } catch (error) {
    if (requestId === recordingsState.requestId) {
      clearRecordings();
      setSectionNote(
        recStatus,
        "error",
        error.message || "Could not load recordings.",
      );
    }
  } finally {
    if (requestId === recordingsState.requestId) {
      recordingsState.loading = false;
      recLoad.disabled = false;
      recLoad.textContent = "Load";
    }
  }
}

async function loadRecordingsTime() {
  if (recDate.value) {
    return;
  }
  try {
    const payload = await fetchJson("/api/recordings/time");
    const value =
      typeof payload?.device_time === "string"
        ? payload.device_time.slice(0, 10)
        : "";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      throw new Error("The server did not return the DVR date.");
    }
    recDate.value = value;
    if (payload?.source === "server") {
      setSectionNote(
        recStatus,
        "warn",
        "The DVR clock could not be read; the date comes from the server clock.",
      );
    } else {
      setSectionNote(
        recStatus,
        "ok",
        `Date set to ${value}. Press Load to list recordings.`,
      );
    }
  } catch {
    const now = new Date();
    recDate.value = `${now.getFullYear()}-${pad2(
      now.getMonth() + 1,
    )}-${pad2(now.getDate())}`;
    setSectionNote(
      recStatus,
      "warn",
      "The DVR date could not be read; choose a date and press Load.",
    );
  }
}

async function loadStorageHealth() {
  recHealth.className = "badge";
  recHealth.textContent = "Storage…";
  recHealth.title = "Reading storage health.";
  try {
    const payload = await fetchJson("/api/storage");
    const partitions = Number.isInteger(payload?.partitions)
      ? payload.partitions
      : 0;
    const errors = Number.isInteger(payload?.errors) ? payload.errors : 0;
    if (payload?.health === "ok") {
      recHealth.className = "badge ok";
      recHealth.textContent = "Storage ok";
    } else if (payload?.health === "problem") {
      recHealth.className = "badge warn";
      recHealth.textContent = "Storage problem";
    } else {
      recHealth.className = "badge";
      recHealth.textContent = "Storage unknown";
    }
    recHealth.title = `${partitions} partition${
      partitions === 1 ? "" : "s"
    }, ${errors} reported error${errors === 1 ? "" : "s"}.`;
  } catch {
    recHealth.className = "badge";
    recHealth.textContent = "Storage unknown";
    recHealth.title = "Storage health could not be read.";
  }
}

function markTimelineTime(event) {
  const rect = recTimeline.getBoundingClientRect();
  if (rect.width <= 0) {
    return;
  }
  const ratio = Math.min(
    1,
    Math.max(0, (event.clientX - rect.left) / rect.width),
  );
  const seconds = Math.min(
    DAY_SECONDS - 1,
    Math.round(ratio * DAY_SECONDS),
  );
  recCursor.hidden = false;
  recCursor.style.left = `${(ratio * 100).toFixed(3)}%`;
  recSelected.textContent = `Marked ${formatDaySeconds(
    seconds,
  )} on ${recordingsState.date || recDate.value}. Playback comes in a later phase.`;
}

function readInclude(channel) {
  try {
    const stored = localStorage.getItem(`${LIVE_INCLUDE_KEY_PREFIX}${channel}`);
    return stored === null ? true : stored === "1";
  } catch {
    return true;
  }
}

function writeInclude(channel, included) {
  try {
    localStorage.setItem(
      `${LIVE_INCLUDE_KEY_PREFIX}${channel}`,
      included ? "1" : "0",
    );
  } catch {
    // Private browsing or a full quota must not break the page.
  }
}

function createLiveTiles(count) {
  const fragment = document.createDocumentFragment();
  liveState.tiles.clear();

  for (let channel = 1; channel <= count; channel += 1) {
    const tile = document.createElement("article");
    tile.className = "live-tile";
    tile.dataset.channel = String(channel);

    const head = document.createElement("div");
    head.className = "live-tile-head";

    const title = document.createElement("h3");
    title.textContent = `Channel ${channel}`;

    const includeLabel = document.createElement("label");
    includeLabel.className = "live-include";
    includeLabel.title = "Include this channel in Connect";

    const include = document.createElement("input");
    include.type = "checkbox";
    include.checked = readInclude(channel);
    include.addEventListener("change", () =>
      writeInclude(channel, include.checked),
    );
    includeLabel.append(include, document.createTextNode("Include"));

    const badge = document.createElement("span");
    badge.className = "live-badge";
    badge.textContent = "Idle";
    head.append(title, includeLabel, badge);

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
      include,
      frame,
      badge,
      start,
      stop,
      large,
      status,
    });
  }

  liveGrid.replaceChildren(fragment);
  updateLiveButtons();
}

function setTileStatus(channel, state, message) {
  const tile = liveState.tiles.get(channel);
  if (!tile) {
    return;
  }
  tile.status.className = `section-note ${state}`;
  tile.status.textContent = message;
}

function playingNames() {
  const names = new Set();
  for (const tile of liveState.tiles.values()) {
    if (tile.name) {
      names.add(tile.name);
    }
  }
  if (liveState.large) {
    names.add(liveState.large.name);
  }
  return names;
}

function updateLiveButtons() {
  const busy = liveState.connecting || liveState.disconnecting;
  const anyPlaying = playingNames().size > 0;

  for (const tile of liveState.tiles.values()) {
    const active = Boolean(tile.name);
    tile.start.disabled = active || !liveState.publicUrl || busy;
    tile.stop.disabled = !active || busy;
    tile.large.disabled = !liveState.publicUrl || busy;
  }

  liveLargeClose.disabled = !liveState.large || busy;
  liveConnect.disabled = !liveState.publicUrl || busy;
  liveDisconnect.disabled = !anyPlaying || busy;
  liveQuality.disabled = busy;

  if (anyPlaying) {
    ensureRenewTimer();
  } else {
    stopRenewTimer();
  }
}

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
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

async function startTile(channel, quality = "sub", gap = false) {
  const tile = liveState.tiles.get(channel);
  if (!tile || tile.name) {
    return false;
  }
  if (!liveState.publicUrl) {
    setTileStatus(channel, "error", "Live view is not configured.");
    return false;
  }

  const selected = quality === "main" ? "main" : "sub";
  tile.start.disabled = true;
  setTileStatus(channel, "loading", `Opening ${selected} stream…`);

  try {
    const payload = await fetchJson(
      `/api/live/${channel}/open?quality=${selected}`,
      { method: "POST" },
    );
    const name = payload?.stream_name;
    if (typeof name !== "string" || name === "") {
      throw new Error("The server did not return a stream name.");
    }

    tile.name = name;
    tile.quality = selected;
    tile.frame.replaceChildren(
      buildLiveFrame(name, `Channel ${channel} ${selected} stream`),
    );
    tile.badge.textContent = "Live";
    tile.badge.className = "live-badge live";
    setTileStatus(
      channel,
      "ok",
      `${selected === "main" ? "Main" : "Sub"} stream playing.`,
    );
    return true;
  } catch (error) {
    tile.frame.replaceChildren(emptyNode("Not started."));
    tile.badge.textContent = "Idle";
    tile.badge.className = "live-badge";
    setTileStatus(
      channel,
      "error",
      error.message || "Could not start the live stream.",
    );
    return false;
  } finally {
    updateLiveButtons();
    if (gap) {
      await sleep(LIVE_START_GAP_MS);
    }
  }
}

async function stopTile(channel) {
  const tile = liveState.tiles.get(channel);
  if (!tile || !tile.name) {
    return false;
  }

  const name = tile.name;
  tile.name = null;
  tile.quality = null;
  tile.stop.disabled = true;
  setTileStatus(channel, "loading", "Stopping…");
  let stopped = false;

  try {
    await closeStream(name);
    stopped = true;
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
  return stopped;
}

function ensureRenewTimer() {
  if (liveState.renewTimer !== null) {
    return;
  }
  if (playingNames().size === 0) {
    return;
  }
  liveState.renewTimer = window.setInterval(
    renewLeases,
    LIVE_RENEW_INTERVAL_MS,
  );
}

function stopRenewTimer() {
  if (liveState.renewTimer === null) {
    return;
  }
  window.clearInterval(liveState.renewTimer);
  liveState.renewTimer = null;
}

function markExpired(name) {
  let handled = false;

  for (const [channel, tile] of liveState.tiles) {
    if (tile.name !== name) {
      continue;
    }
    tile.name = null;
    tile.quality = null;
    tile.frame.replaceChildren(emptyNode("Not started."));
    tile.badge.textContent = "Expired";
    tile.badge.className = "live-badge expired";
    setTileStatus(channel, "warn", "Stream expired, press Start.");
    handled = true;
  }

  if (liveState.large && liveState.large.name === name) {
    clearLargeView();
    handled = true;
  }

  return handled;
}

async function renewLeases() {
  const names = [...playingNames()];
  if (names.length === 0) {
    stopRenewTimer();
    return;
  }

  try {
    const payload = await fetchJson("/api/live/renew", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ streams: names }),
    });
    const expired = Array.isArray(payload?.expired) ? payload.expired : [];
    let expiredCount = 0;
    for (const name of expired) {
      if (typeof name === "string" && markExpired(name)) {
        expiredCount += 1;
      }
    }
    if (expiredCount > 0) {
      setSectionNote(
        liveStatus,
        "warn",
        `${expiredCount} stream${
          expiredCount === 1 ? "" : "s"
        } expired. Press Start to reconnect.`,
      );
    }
  } catch (error) {
    setSectionNote(
      liveStatus,
      "warn",
      "Could not renew live streams; retrying.",
    );
  } finally {
    updateLiveButtons();
  }
}

async function connectAll() {
  if (
    !liveState.publicUrl ||
    liveState.connecting ||
    liveState.disconnecting
  ) {
    return;
  }

  const quality = liveQuality.value === "main" ? "main" : "sub";
  const channels = [...liveState.tiles.entries()]
    .filter(([, tile]) => tile.include.checked && !tile.name)
    .map(([channel]) => channel);

  if (channels.length === 0) {
    setSectionNote(
      liveStatus,
      "warn",
      "No included channels are waiting to start.",
    );
    return;
  }

  liveState.connecting = true;
  updateLiveButtons();
  setSectionNote(
    liveStatus,
    "loading",
    `Connecting ${channels.length} included channel${
      channels.length === 1 ? "" : "s"
    } (${quality})…`,
  );

  let started = 0;
  for (const channel of channels) {
    if (await startTile(channel, quality, true)) {
      started += 1;
    }
  }

  liveState.connecting = false;
  updateLiveButtons();
  setSectionNote(
    liveStatus,
    started === channels.length ? "ok" : "warn",
    `Connected ${started} of ${channels.length} channel${
      channels.length === 1 ? "" : "s"
    }.`,
  );
}

async function disconnectAll() {
  if (liveState.disconnecting) {
    return;
  }

  liveState.disconnecting = true;
  updateLiveButtons();

  let stopped = 0;
  if (liveState.large) {
    if (await closeLarge()) {
      stopped += 1;
    }
  }
  for (const [channel, tile] of liveState.tiles) {
    if (!tile.name) {
      continue;
    }
    if (await stopTile(channel)) {
      stopped += 1;
    }
  }

  liveState.disconnecting = false;
  updateLiveButtons();
  setSectionNote(
    liveStatus,
    stopped > 0 ? "ok" : "warn",
    stopped > 0
      ? `Disconnected ${stopped} stream${stopped === 1 ? "" : "s"}.`
      : "No streams were playing.",
  );
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
    return false;
  }

  let closed = false;
  try {
    await closeStream(large.name);
    closed = true;
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
  return closed;
}

function closeAllOnUnload() {
  stopRenewTimer();
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
    const maxChannel = payload?.max_channel;
    liveState.maxChannel =
      Number.isInteger(maxChannel) && maxChannel > 0
        ? maxChannel
        : DEFAULT_MAX_CHANNEL;
    setSectionNote(
      liveStatus,
      "ok",
      "Press Connect to start the included channels.",
    );
  } catch (error) {
    liveState.publicUrl = null;
    setSectionNote(
      liveStatus,
      "error",
      error.message || "Could not load live-view configuration.",
    );
  }

  syncLiveTiles(null);
  syncRecordingChannels(null);
  updateLiveButtons();
}

async function loadAll() {
  setLoading(true);
  // Sequential requests: one short-lived DVR session at a time, so a DVR that
  // limits concurrent sessions is not asked to hold four logins at once.
  await loadLiveConfig();
  await loadDeviceInfo();
  await loadChannels();
  await loadRecordingsTime();
  await loadStorageHealth();
  await loadConfig("Network", networkStatus, networkTable, networkRaw);
  await loadConfig("Encode", encodeStatus, encodeTable, encodeRaw);
  setLoading(false);
}

refreshButton.addEventListener("click", loadAll);
liveConnect.addEventListener("click", connectAll);
liveDisconnect.addEventListener("click", disconnectAll);
liveLargeClose.addEventListener("click", closeLarge);
recLoad.addEventListener("click", loadRecordings);
recTimeline.addEventListener("click", markTimelineTime);
window.addEventListener("pagehide", closeAllOnUnload);
syncRecordingChannels(null);
updateLiveButtons();
loadAll();
