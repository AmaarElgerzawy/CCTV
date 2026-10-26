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

const CALL_METADATA_KEYS = new Set(["succeeded_calls", "failed_calls"]);

function formatValue(value) {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
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
      Object.assign(document.createElement("div"), {
        className: "empty",
        textContent: "The DVR returned an empty device info object.",
      }),
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

  const details = failedCalls.map((call) => {
    const method =
      call && typeof call.method === "string" ? call.method : "unknown method";
    const code =
      call && call.error_code !== null && call.error_code !== undefined
        ? call.error_code
        : "no code";
    return `${method} (code ${code})`;
  });

  failedNote.textContent = `Failed calls: ${details.join("; ")}.`;
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

function setStatus(state, message) {
  statusBox.className = `status ${state}`;
  statusText.textContent = message;
}

function setLoading(loading) {
  refreshButton.disabled = loading;
  refreshButton.textContent = loading ? "Refreshing…" : "Refresh";
}

async function loadDeviceInfo() {
  setLoading(true);
  setStatus("loading", "Loading device information…");

  try {
    const response = await fetch("/api/device/info", {
      headers: { Accept: "application/json" },
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
  } finally {
    setLoading(false);
  }
}

refreshButton.addEventListener("click", loadDeviceInfo);
loadDeviceInfo();
