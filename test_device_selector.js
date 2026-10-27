"use strict";

const {
  normalizeDeviceId,
  getQueryDeviceId,
  readLastDeviceId,
  writeLastDeviceId,
  deviceSelectOptions,
} = require("./static/device_selector.js");

const assert = require("assert");

assert.strictEqual(normalizeDeviceId("5"), 5);
assert.strictEqual(normalizeDeviceId(0), null);
assert.strictEqual(normalizeDeviceId("abc"), null);
assert.strictEqual(getQueryDeviceId("?device=2"), 2);
assert.strictEqual(getQueryDeviceId("?device_id=3"), 3);
assert.strictEqual(getQueryDeviceId("?other=1"), null);
const options = deviceSelectOptions([{ id: 1, name: "DVR", group: "A" }]);
assert.strictEqual(options[0].id, 1);
assert.strictEqual(options[0].name, "DVR");
console.log("ok");
