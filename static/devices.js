"use strict";

const SEARCH_KEY = "dvr-registry-search";
const FILTER_KEY = "dvr-registry-state";
const STATE_VALUES = new Set([
  "all",
  "online",
  "partial",
  "offline",
  "unknown",
]);

const state = {
  groups: [],
  devices: [],
  status: null,
  editingId: null,
  previewCsv: "",
};

const regStatus = document.getElementById("reg-status");
const regStatusText = document.getElementById("reg-status-text");
const statusCounts = document.getElementById("status-counts");
const deviceSearch = document.getElementById("device-search");
const deviceState = document.getElementById("device-state");
const statusRefresh = document.getElementById("status-refresh");
const deviceNote = document.getElementById("device-note");
const deviceGroups = document.getElementById("device-groups");

const deviceForm = document.getElementById("device-form");
const formTitle = document.getElementById("form-title");
const formCancel = document.getElementById("form-cancel");
const deviceName = document.getElementById("device-name");
const deviceGroup = document.getElementById("device-group");
const deviceHost = document.getElementById("device-host");
const deviceHttpPort = document.getElementById("device-http-port");
const deviceRtspPort = document.getElementById("device-rtsp-port");
const deviceUsername = document.getElementById("device-username");
const devicePassword = document.getElementById("device-password");
const deviceClearCreds = document.getElementById("device-clear-creds");
const deviceNotes = document.getElementById("device-notes");
const formNote = document.getElementById("form-note");
const deviceSave = document.getElementById("device-save");

const groupForm = document.getElementById("group-form");
const groupName = document.getElementById("group-name");
const groupNote = document.getElementById("group-note");
const groupList = document.getElementById("group-list");

const defaultForm = document.getElementById("default-form");
const defaultUsername = document.getElementById("default-username");
const defaultPassword = document.getElementById("default-password");
const defaultNote = document.getElementById("default-note");

const csvText = document.getElementById("csv-text");
const csvPreview = document.getElementById("csv-preview");
const csvImport = document.getElementById("csv-import");
const csvNote = document.getElementById("csv-note");
const csvResult = document.getElementById("csv-result");

function setPageStatus(kind, text) {
  regStatus.className = kind ? `status ${kind}` : "status";
  regStatusText.textContent = text;
}

function readStored(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // A private window or a full quota must not break the page.
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const message =
      payload && typeof payload.error === "string"
        ? payload.error
        : `Request failed (HTTP ${response.status}).`;
    throw new Error(message);
  }
  return payload;
}

function sendJson(method, url, body) {
  return requestJson(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function statusMap() {
  const map = new Map();
  const items = Array.isArray(state.status?.devices)
    ? state.status.devices
    : [];
  for (const item of items) {
    if (item && Number.isInteger(item.id)) {
      map.set(item.id, item);
    }
  }
  return map;
}

function formatSeen(value) {
  if (typeof value !== "string" || value === "") {
    return "never";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

function renderCounts() {
  const counts = state.status?.counts;
  if (!counts || !Number.isInteger(counts.total)) {
    statusCounts.textContent = "—";
    statusCounts.className = "badge";
    return;
  }
  statusCounts.textContent = `${counts.online} of ${counts.total} online`;
  statusCounts.className =
    counts.total > 0 && counts.online === counts.total
      ? "badge ok"
      : "badge";
}

function portBadge(label, value) {
  const badge = document.createElement("span");
  if (value === true) {
    badge.className = "badge ok";
    badge.textContent = `${label} up`;
  } else if (value === false) {
    badge.className = "badge warn";
    badge.textContent = `${label} down`;
  } else {
    badge.className = "badge";
    badge.textContent = `${label} ?`;
  }
  return badge;
}

function deviceRow(device, info) {
  const status = info?.state || "unknown";
  const row = document.createElement("div");
  row.className = "device-row";
  row.dataset.deviceId = String(device.id);

  const dot = document.createElement("span");
  dot.className = `device-state ${status}`;
  dot.setAttribute("aria-label", `Status: ${status}`);

  const main = document.createElement("div");
  main.className = "device-main";
  const name = document.createElement("p");
  name.className = "device-name";
  name.textContent = device.name;
  const meta = document.createElement("p");
  meta.className = "device-meta";
  const credentials = device.has_own_credentials
    ? "own credentials"
    : "default credentials";
  meta.textContent =
    `${device.host}:${device.http_port} · RTSP ${device.rtsp_port} · ` +
    credentials;
  const seen = document.createElement("p");
  seen.className = "device-meta";
  seen.textContent = `Last seen ${formatSeen(info?.last_seen)}`;
  main.append(name, meta, seen);
  if (device.notes) {
    const notes = document.createElement("p");
    notes.className = "device-meta";
    notes.textContent = device.notes;
    main.append(notes);
  }

  const flags = document.createElement("div");
  flags.className = "device-flags";
  flags.append(
    portBadge("HTTP", info?.http_ok),
    portBadge("RTSP", info?.rtsp_ok),
  );

  const actions = document.createElement("div");
  actions.className = "device-actions";
  const testButton = document.createElement("button");
  testButton.type = "button";
  testButton.className = "button button-ghost button-small";
  testButton.textContent = "Test connection";
  const editButton = document.createElement("button");
  editButton.type = "button";
  editButton.className = "button button-ghost button-small";
  editButton.textContent = "Edit";
  const deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "button button-danger button-small";
  deleteButton.textContent = "Delete";
  const testNote = document.createElement("p");
  testNote.className = "device-test";

  testButton.addEventListener("click", () => {
    testConnection(device, testButton, testNote);
  });
  editButton.addEventListener("click", () => {
    beginEdit(device);
  });
  deleteButton.addEventListener("click", () => {
    removeDevice(device);
  });
  actions.append(testButton, editButton, deleteButton);

  row.append(dot, main, flags, actions);
  const wrapper = document.createElement("div");
  wrapper.append(row, testNote);
  return wrapper;
}

function renderDevices() {
  const map = statusMap();
  const query = deviceSearch.value.trim().toLowerCase();
  const filter = STATE_VALUES.has(deviceState.value)
    ? deviceState.value
    : "all";
  const grouped = new Map();

  for (const device of state.devices) {
    const info = map.get(device.id);
    const status = info?.state || "unknown";
    if (filter !== "all" && status !== filter) {
      continue;
    }
    const haystack = `${device.name} ${device.host}`.toLowerCase();
    if (query && !haystack.includes(query)) {
      continue;
    }
    const key = device.group || "No group";
    if (!grouped.has(key)) {
      grouped.set(key, []);
    }
    grouped.get(key).push({ device, info });
  }

  deviceGroups.replaceChildren();
  if (state.devices.length === 0) {
    deviceNote.textContent =
      "No devices yet. Add one below or import a CSV.";
    return;
  }
  if (grouped.size === 0) {
    deviceNote.textContent =
      "No devices match the current search and state filter.";
    return;
  }
  deviceNote.textContent = "";
  const visible = [...grouped.values()].reduce(
    (total, entries) => total + entries.length,
    0,
  );
  deviceNote.textContent =
    `Showing ${plural(visible, "device")} of ` +
    `${plural(state.devices.length, "device")}.`;

  const names = [...grouped.keys()].sort((left, right) => {
    if (left === "No group") {
      return 1;
    }
    if (right === "No group") {
      return -1;
    }
    return left.localeCompare(right);
  });

  for (const name of names) {
    const entries = grouped.get(name);
    const section = document.createElement("section");
    section.className = "device-group";
    const heading = document.createElement("h3");
    heading.textContent = `${name} (${entries.length})`;
    section.append(heading);
    for (const entry of entries) {
      section.append(deviceRow(entry.device, entry.info));
    }
    deviceGroups.append(section);
  }
}

function renderGroupSelect() {
  const selected = deviceGroup.value;
  deviceGroup.replaceChildren(new Option("No group", ""));
  for (const group of state.groups) {
    deviceGroup.append(new Option(group.name, String(group.id)));
  }
  deviceGroup.value = selected;
  if (deviceGroup.value !== selected) {
    deviceGroup.value = "";
  }
}

function renderGroupList() {
  groupList.replaceChildren();
  if (state.groups.length === 0) {
    groupNote.textContent = "No groups yet.";
    return;
  }
  groupNote.textContent = `${plural(state.groups.length, "group")}.`;
  for (const group of state.groups) {
    const item = document.createElement("li");
    item.className = "group-item";
    const label = document.createElement("span");
    label.className = "group-item-name";
    label.textContent =
      `${group.name} (${plural(group.device_count, "device")})`;
    const actions = document.createElement("div");
    actions.className = "registry-actions";
    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "button button-ghost button-small";
    rename.textContent = "Rename";
    rename.addEventListener("click", () => renameGroup(group));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button button-danger button-small";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => removeGroup(group));
    actions.append(rename, remove);
    item.append(label, actions);
    groupList.append(item);
  }
}

async function loadGroups() {
  const payload = await requestJson("/api/groups");
  state.groups = Array.isArray(payload?.groups) ? payload.groups : [];
  renderGroupSelect();
  renderGroupList();
}

async function loadDevices() {
  const payload = await requestJson("/api/devices");
  state.devices = Array.isArray(payload?.devices) ? payload.devices : [];
  renderDevices();
}

async function loadStatus() {
  try {
    state.status = await requestJson("/api/status");
    renderCounts();
    renderDevices();
  } catch (error) {
    setPageStatus("error", error.message);
  }
}

function applyDefaults(payload) {
  const username =
    typeof payload?.username === "string" ? payload.username : "";
  defaultUsername.value = username;
  if (payload?.has_password) {
    defaultNote.textContent = username
      ? `Default username: ${username}. A default password is set.`
      : "A default password is set, but no username is.";
  } else if (username) {
    defaultNote.textContent =
      `Default username: ${username}. No default password is set.`;
  } else {
    defaultNote.textContent = "No default credentials are set.";
  }
}

async function loadDefaults() {
  const payload = await requestJson(
    "/api/settings/default-credentials",
  );
  applyDefaults(payload);
}

async function reloadAll(message) {
  await Promise.all([loadGroups(), loadDevices(), loadStatus()]);
  if (message) {
    setPageStatus("ok", message);
  }
}

function resetForm() {
  state.editingId = null;
  formTitle.textContent = "Add device";
  formCancel.hidden = true;
  deviceForm.reset();
  deviceHttpPort.value = "80";
  deviceRtspPort.value = "554";
  deviceGroup.value = "";
  deviceClearCreds.checked = false;
  formNote.className = "section-note registry-wide";
  formNote.textContent =
    "A device with no username and password uses the default credentials.";
}

function beginEdit(device) {
  state.editingId = device.id;
  formTitle.textContent = `Edit ${device.name}`;
  formCancel.hidden = false;
  deviceName.value = device.name;
  deviceHost.value = device.host;
  deviceHttpPort.value = String(device.http_port);
  deviceRtspPort.value = String(device.rtsp_port);
  deviceGroup.value =
    device.group_id === null ? "" : String(device.group_id);
  deviceUsername.value = "";
  devicePassword.value = "";
  deviceClearCreds.checked = false;
  deviceNotes.value = device.notes || "";
  formNote.className = "section-note registry-wide";
  formNote.textContent = device.has_own_credentials
    ? "This device has its own credentials. Leave both fields empty to " +
      "keep them, or tick the box to use the defaults."
    : "This device uses the default credentials. Enter a username and " +
      "password to override them.";
  deviceName.focus();
}

async function testConnection(device, button, note) {
  button.disabled = true;
  button.textContent = "Testing…";
  note.className = "device-test";
  note.textContent = "";
  try {
    const result = await requestJson(
      `/api/devices/${device.id}/test`,
      { method: "POST" },
    );
    if (result?.ok === true) {
      note.className = "device-test ok";
      note.textContent = "Connection ok.";
    } else {
      const reason =
        typeof result?.reason === "string" ? result.reason : "other";
      note.className = "device-test error";
      note.textContent = `Connection failed: ${reason}.`;
    }
  } catch (error) {
    note.className = "device-test error";
    note.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Test connection";
  }
}

async function removeDevice(device) {
  if (!window.confirm(`Delete ${device.name}?`)) {
    return;
  }
  try {
    await requestJson(`/api/devices/${device.id}`, {
      method: "DELETE",
    });
    await reloadAll("Device deleted.");
  } catch (error) {
    setPageStatus("error", error.message);
  }
}

async function renameGroup(group) {
  const value = window.prompt("New group name", group.name);
  if (value === null || value.trim() === group.name) {
    return;
  }
  try {
    await sendJson("PUT", `/api/groups/${group.id}`, {
      name: value,
    });
    await reloadAll("Group renamed.");
  } catch (error) {
    setPageStatus("error", error.message);
  }
}

async function removeGroup(group) {
  if (
    !window.confirm(
      `Delete ${group.name}? Its devices move to "No group".`,
    )
  ) {
    return;
  }
  try {
    await requestJson(`/api/groups/${group.id}`, {
      method: "DELETE",
    });
    await reloadAll("Group deleted.");
  } catch (error) {
    setPageStatus("error", error.message);
  }
}

async function refreshStatusNow() {
  statusRefresh.disabled = true;
  statusRefresh.textContent = "Refreshing…";
  try {
    state.status = await requestJson("/api/status/refresh", {
      method: "POST",
    });
    renderCounts();
    renderDevices();
    setPageStatus("ok", "Status refreshed.");
  } catch (error) {
    if (/already running/i.test(error.message)) {
      setPageStatus(
        "warn",
        "A status check is already running; showing the last result.",
      );
      await loadStatus();
    } else {
      setPageStatus("error", error.message);
    }
  } finally {
    statusRefresh.disabled = false;
    statusRefresh.textContent = "Refresh status";
  }
}

function renderImport(result) {
  const counts = result?.counts || {};
  const rows = Array.isArray(result?.rows) ? result.rows : [];
  if (result?.dry_run) {
    csvNote.className = "section-note";
    csvNote.textContent =
      `Preview: ${counts.ok ?? 0} ready, ` +
      `${plural(counts.error ?? 0, "error")}, ` +
      `${plural(counts.duplicate ?? 0, "duplicate")}. Nothing was written.`;
  } else {
    csvNote.className = "section-note ok";
    csvNote.textContent =
      `Imported ${plural(counts.imported ?? 0, "device")}, ` +
      `${plural(counts.error ?? 0, "error")}, ` +
      `${plural(counts.duplicate ?? 0, "duplicate")}.`;
  }

  csvResult.replaceChildren();
  if (rows.length === 0) {
    return;
  }
  const table = document.createElement("table");
  table.className = "registry-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Row", "Status", "Message"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const item of rows) {
    const row = document.createElement("tr");
    const rowNumber = document.createElement("td");
    rowNumber.textContent = String(item?.row ?? "");
    const statusCell = document.createElement("td");
    const badge = document.createElement("span");
    const status = item?.status;
    if (status === "ok") {
      badge.className = "badge ok";
    } else if (status === "duplicate") {
      badge.className = "badge warn";
    } else {
      badge.className = "badge";
    }
    badge.textContent = typeof status === "string" ? status : "error";
    statusCell.append(badge);
    const message = document.createElement("td");
    message.textContent =
      typeof item?.message === "string" ? item.message : "";
    row.append(rowNumber, statusCell, message);
    body.append(row);
  }
  table.append(head, body);
  csvResult.append(table);
}

async function runImport(dryRun) {
  csvPreview.disabled = true;
  csvImport.disabled = true;
  try {
    const result = await requestJson(
      `/api/devices/import?dry_run=${dryRun ? "true" : "false"}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csv: csvText.value }),
      },
    );
    renderImport(result);
    if (dryRun) {
      state.previewCsv = csvText.value;
      const ready = Number(result?.counts?.ok || 0);
      csvImport.disabled = ready === 0;
    } else {
      state.previewCsv = "";
      csvImport.disabled = true;
      await reloadAll("CSV import finished.");
    }
  } catch (error) {
    csvNote.className = "section-note error";
    csvNote.textContent = error.message;
  } finally {
    csvPreview.disabled = false;
  }
}

async function loadAll() {
  setPageStatus("loading", "Loading devices…");
  try {
    const [groups, devices, defaults] = await Promise.all([
      requestJson("/api/groups"),
      requestJson("/api/devices"),
      requestJson("/api/settings/default-credentials"),
    ]);
    state.groups = Array.isArray(groups?.groups) ? groups.groups : [];
    state.devices = Array.isArray(devices?.devices)
      ? devices.devices
      : [];
    renderGroupSelect();
    renderGroupList();
    renderDevices();
    applyDefaults(defaults);
    await loadStatus();
    setPageStatus(
      "ok",
      `Loaded ${plural(state.devices.length, "device")}.`,
    );
  } catch (error) {
    setPageStatus("error", error.message);
    deviceNote.textContent = error.message;
  }
}

deviceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    name: deviceName.value,
    host: deviceHost.value,
    http_port: Number(deviceHttpPort.value),
    rtsp_port: Number(deviceRtspPort.value),
    notes: deviceNotes.value,
  };
  payload.group_id = deviceGroup.value
    ? Number(deviceGroup.value)
    : null;

  const username = deviceUsername.value.trim();
  const password = devicePassword.value;
  if (state.editingId === null) {
    if (username) {
      payload.username = username;
      payload.password = password;
    } else if (password) {
      formNote.className = "section-note error registry-wide";
      formNote.textContent =
        "Enter a username with the password, or leave both empty " +
        "to use the default credentials.";
      return;
    }
  } else if (deviceClearCreds.checked) {
    payload.username = "";
    payload.password = "";
  } else if (password) {
    payload.password = password;
    if (username) {
      payload.username = username;
    }
  } else if (username) {
    payload.username = username;
  }

  deviceSave.disabled = true;
  deviceSave.textContent = "Saving…";
  try {
    if (state.editingId === null) {
      await sendJson("POST", "/api/devices", payload);
    } else {
      await sendJson(
        "PUT",
        `/api/devices/${state.editingId}`,
        payload,
      );
    }
    resetForm();
    await reloadAll("Device saved.");
  } catch (error) {
    formNote.className = "section-note error registry-wide";
    formNote.textContent = error.message;
  } finally {
    deviceSave.disabled = false;
    deviceSave.textContent = "Save device";
  }
});

formCancel.addEventListener("click", () => {
  resetForm();
});

groupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await sendJson("POST", "/api/groups", {
      name: groupName.value,
    });
    groupName.value = "";
    await reloadAll("Group added.");
  } catch (error) {
    setPageStatus("error", error.message);
  }
});

defaultForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await sendJson("PUT", "/api/settings/default-credentials", {
      username: defaultUsername.value,
      password: defaultPassword.value,
    });
    defaultPassword.value = "";
    await loadDefaults();
    setPageStatus("ok", "Default credentials saved.");
  } catch (error) {
    setPageStatus("error", error.message);
  }
});

csvPreview.addEventListener("click", () => {
  if (!csvText.value.trim()) {
    csvNote.className = "section-note error";
    csvNote.textContent = "Paste a CSV with a header row first.";
    return;
  }
  state.previewCsv = "";
  runImport(true);
});

csvImport.addEventListener("click", () => {
  if (state.previewCsv !== csvText.value) {
    csvNote.className = "section-note error";
    csvNote.textContent = "Preview the current CSV before importing.";
    csvImport.disabled = true;
    return;
  }
  runImport(false);
});

csvText.addEventListener("input", () => {
  if (state.previewCsv && state.previewCsv !== csvText.value) {
    state.previewCsv = "";
    csvImport.disabled = true;
    csvNote.className = "section-note";
    csvNote.textContent = "The CSV changed. Preview it again.";
  }
});

statusRefresh.addEventListener("click", () => {
  refreshStatusNow();
});

deviceSearch.addEventListener("input", () => {
  writeStored(SEARCH_KEY, deviceSearch.value);
  renderDevices();
});

deviceState.addEventListener("change", () => {
  writeStored(FILTER_KEY, deviceState.value);
  renderDevices();
});

const storedFilter = readStored(FILTER_KEY);
if (storedFilter && STATE_VALUES.has(storedFilter)) {
  deviceState.value = storedFilter;
}
const storedSearch = readStored(SEARCH_KEY);
if (storedSearch) {
  deviceSearch.value = storedSearch;
}

loadAll();
setInterval(loadStatus, 30000);
