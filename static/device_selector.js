/* Pure device selector helpers shared by the browser and Node tests. */
"use strict";

function normalizeDeviceId(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const number = Number(value);
  if (!Number.isInteger(number) || number <= 0) {
    return null;
  }
  return number;
}

function getQueryDeviceId(search) {
  if (!search || typeof search !== "string") {
    return null;
  }
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const value = params.get("device_id") || params.get("device");
  return normalizeDeviceId(value);
}

function lastDeviceKey(pageKey) {
  return `dvr-last-device-${pageKey}`;
}

function readLastDeviceId(pageKey) {
  try {
    if (typeof localStorage === "undefined") {
      return null;
    }
    return normalizeDeviceId(localStorage.getItem(lastDeviceKey(pageKey)));
  } catch {
    return null;
  }
}

function writeLastDeviceId(pageKey, deviceId) {
  try {
    if (typeof localStorage === "undefined") {
      return;
    }
    const key = lastDeviceKey(pageKey);
    if (deviceId === null || deviceId === undefined) {
      localStorage.removeItem(key);
      return;
    }
    localStorage.setItem(key, String(deviceId));
  } catch {
    // Private browsing or a full quota must not break the page.
  }
}

function deviceSelectOptions(devices) {
  if (!Array.isArray(devices)) {
    return [];
  }
  return devices.map((device) => {
    const id = normalizeDeviceId(device && device.id);
    return {
      id,
      name: device && typeof device.name === "string" ? device.name : "",
      group: device && typeof device.group === "string" ? device.group : "",
      label:
        device && typeof device.name === "string"
          ? device.name
          : id === null
            ? ""
            : String(id),
    };
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    normalizeDeviceId,
    getQueryDeviceId,
    lastDeviceKey,
    readLastDeviceId,
    writeLastDeviceId,
    deviceSelectOptions,
  };
}
