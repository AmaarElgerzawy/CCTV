# DVR Web — Phase 2

A small, read-only web app for a Dahua DVR on the local network. It logs in to
the DVR, reads device identity, video input channels, and selected configuration,
and shows them in the browser.

All DVR API behavior is based strictly on [`api_spec.md`](api_spec.md) and the
current `har_summary.txt`. Points that the sources do not document are marked as
TODO in the code and listed at the end of this file. The app does not guess
write methods and never sends a set/write/reboot/format call.

## Project layout

| File | Purpose |
| --- | --- |
| `dvr_client.py` | `DvrClient`: one instance per DVR session; `login()`, `keepalive()`, `get_device_info()`, `get_channels()`, `get_config(name)`, `logout()`, context-manager support |
| `app.py` | FastAPI app: `/`, `/health`, `/api/device/info`, `/api/channels`, `/api/config/{name}` |
| `static/` | Plain HTML/CSS/JS frontend (no build step) |
| `test_dvr.py` | Manual login → device/channels/config reads → logout smoke test |
| `.env.example` | Placeholder configuration to copy to `.env` |
| `api_spec.md` | DVR API notes this implementation is based on |

## Requirements

- Python 3.11 or newer
- Network access to the DVR's web interface
- DVR web credentials with read access

## Setup

PowerShell (Windows):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
test -f .env || cp .env.example .env
${EDITOR:-nano} .env
```

This will not overwrite an existing `.env`.

Put the real values in `.env`:

```dotenv
DVR_HOST=192.168.1.100
DVR_USER=admin
DVR_PASS=your-password
```

`DVR_HOST` may include a scheme and port, for example
`http://192.168.1.100:80`. If no scheme is given, `http://` is used.
`python-dotenv` loads `.env` at runtime. The app never sends the username,
password, or session id to the browser, and never logs them.

## Run

```powershell
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>.

Clicking **Refresh** calls the backend; the browser never talks to the DVR
directly. Each API request creates its own short-lived DVR session and logs out
in a `finally` block.

## Test the client

```powershell
python test_dvr.py
python test_dvr.py --verify-logout
```

Expected output starts with `Login: OK` and then contains:

- `Device info:` with the four device fields plus
  `succeeded_calls`/`failed_calls`.
- `Channels:` with `count`, `count_info`, `names`, `titles_status`, and
  per-call metadata.
- `Encode config:` and `Network config:` with the masked `table` (or a
  `failed_calls` entry when that DVR rejects the read).
- `Logout: server-side logout succeeded`.

There is exactly one login flow per run and no automatic retry. Credentials,
session ids, and any `get2DCode` value are never printed. Config fields whose
names contain `password`, `passwd`, `pwd`, `key`, `secret`, or `token` are
replaced with `***` before they are printed or returned by the API.

With `--verify-logout`, the client logs out and then makes exactly one
`global.keepAlive` request with the old session. It reports whether that session
was rejected (closed), was still accepted, or could not be determined. No retry
is made.

You can also check the web API directly:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/device/info
Invoke-RestMethod http://127.0.0.1:8000/api/channels
Invoke-RestMethod http://127.0.0.1:8000/api/config/Encode
```

## API

### `GET /health`

```json
{"status": "ok"}
```

This is a liveness check only; it does not contact the DVR.

### `GET /api/device/info`

Opens a new `DvrClient`, logs in, calls the four device-identity methods, and
calls `global.logout` in a `finally` block. The local session is cleared even if
the logout call fails; a logout failure is logged with a safe diagnostic and
does not include response values. Each device call is isolated so one failure
does not stop the others. No session state is shared between requests.

```json
{
  "info": {
    "device_type": "reported value or null",
    "device_class": "reported value or null",
    "software_version": "reported value or null",
    "build_date": "reported value or null",
    "succeeded_calls": ["magicBox.getDeviceType"],
    "failed_calls": [
      {"method": "magicBox.getSoftwareVersion", "error_code": 0}
    ]
  }
}
```

### `GET /api/channels`

Reads `LogicDeviceManager.getVideoInputChannels` for the count and
`LogicDeviceManager.getVideoChannelsInfo` for titles. The second call is
documented by `api_spec.md` but was not present in the current capture, so a
failure is isolated and reported rather than failing the count.

```json
{
  "channels": {
    "count": 4,
    "count_info": {
      "CurLocal": 4,
      "CurTotal": 4,
      "MaxLocal": 4,
      "MaxTotal": 4
    },
    "names": ["reported channel title"],
    "titles_status": "ok",
    "succeeded_calls": [
      "LogicDeviceManager.getVideoInputChannels",
      "LogicDeviceManager.getVideoChannelsInfo"
    ],
    "failed_calls": []
  }
}
```

`titles_status` is `ok`, `empty`, or `failed`. `count` comes from
`params.channelCountInfo.CurTotal`; the full count object is returned in
`count_info`. Failed entries contain only the method name and error code.

### `GET /api/config/{name}`

Reads one `configManager.getConfig` table for an allow-listed name. Anything
else returns HTTP `400` without contacting the DVR.

Allowed names: `Encode`, `Network`, `VideoStandard`, `Locales`.

`WorkGroupMode` is deliberately excluded because it returned an error in the
capture. `Encode` and `Network` are marked TODO(spec): `Encode` is named by
`api_spec.md`, while `Network` is an unconfirmed read name suggested by
`api_spec.md`; neither appears in the current `har_summary.txt`.

```json
{
  "config": {
    "name": "Encode",
    "table": {
      "MainFormat": [{"Name": "reported value"}],
      "Password": "***"
    },
    "succeeded_calls": ["configManager.getConfig"],
    "failed_calls": []
  }
}
```

Every field whose name contains `password`, `passwd`, `pwd`, `key`, `secret`, or
`token` is replaced with `***` before the response is returned. A rejected config
read is reported in `failed_calls` with the method name and error code; session
and transport failures return HTTP `502`.

Errors are returned as `{"error": "message"}` with status `503` for missing
configuration, `502` for DVR communication, login, or session failures, and `400`
for an unlisted config name. Messages never include the password, session id, or
raw DVR payload.

## Security notes

- Credentials live only in `.env`, which is ignored by `.gitignore`. Only
  `.env.example` belongs in git.
- The DVR session id is kept in memory inside one `DvrClient` instance and is
  never logged or returned by an endpoint.
- Each API request creates and closes its own client, which keeps the door open
  for the later multi-device, on-demand architecture.
- The endpoints and context manager call `logout()` in a `finally` block, so the
  local session is cleared even when an earlier call fails.
- Config fields are masked by key name at the API boundary. This is a safety
  heuristic: a secret stored under an innocuous key name would not be caught.
- This phase is read-only except for session logout. No configuration write/set,
  reboot, factory-reset, or format call exists in the code.

## Evidence from the sources

### Channel count

- `har_summary.txt:7-8` — `POST /RPC2 rpc=LogicDeviceManager.getVideoInputChannels`
  with response key `params.channelCountInfo`.
- `api_spec.md:73` — same call and response key.
- The capture shows the request with `params: null` and `channelCountInfo.CurTotal`
  as the total count.

### Channel titles

- `api_spec.md:74` — `LogicDeviceManager.getVideoChannelsInfo` with response key
  `params.Output`.
- This call is **not** present in the current `har_summary.txt` or the raw
  capture, so its request shape and `Output` shape are TODO(spec).

### Config reads

- `har_summary.txt:13-14` — `configManager.getConfig name=VideoStandard` with
  `params.table`.
- `har_summary.txt:51-52` — `configManager.getConfig name=Locales` with
  `params.table`.
- `api_spec.md:89` — `configManager.getConfig name=Encode` with `params.table`.
  This call is **not** present in the current `har_summary.txt`.
- No `configManager.getConfig name=Network` appears in either source.
  `api_spec.md:174` and `api_spec.md:238` explicitly mark the full Network config
  read as uncertain; `api_spec.md:246` suggests trying `name=Network` as a
  read-only test, which is what the allowlist entry does.

### Calls skipped because they failed in the capture

The current raw capture contains an `error` key for these calls. Error codes are
included because they are non-sensitive diagnostics.

| Call | Error code |
| --- | --- |
| `configManager.getConfig name=WorkGroupMode` | `285278249` |
| `SecurityApp.getSecCap` | `285278249` |
| `POS.getCaps` | `268894210` |
| `StorageDeviceManager.getDeviceInfos` | `285278249` |
| `CloudUpgrader.getAutoCheck` | `285278249` |
| `Security.needLoginByUShield` (`/OutsideCmd`) | no code recorded |

`WorkGroupMode` is excluded from the config allowlist. The other calls are not
used by this phase.

## Not confirmed by the sources (marked TODO in code)

1. **Login request construction.** The sources confirm the endpoint, method, and
   response fields (`params.realm`, `params.random`, `params.encryption`,
   `session`, `error`, `result`), but not the request-side credential fields,
   `clientType`/`authorityType`/`passwordType` values, or the password digest
   algorithm. `dvr_client.py` uses the standard two-step Dahua challenge:
   step 1 sends an empty password and returns realm/random/session; step 2 sends
   the uppercase MD5 challenge with `authorityType`/`passwordType` set to
   `Default`. There is exactly one attempt per step and no automatic retry.
2. **Logout request body.** The capture confirms `POST /RPC2 rpc=global.logout`
   and response keys `id`, `result`, `session`, but not the request body.
   `logout()` sends the current session in the top-level `session` field, a
   fresh `id`, and `params: null` as the smallest assumption. It adds no other
   fields. Success means `result` is `true`; the local session is always cleared.
   A failed logout raises a safe `DvrLogoutError` with method, HTTP status, error
   code/message, and result type only. The optional verification uses one
   `global.keepAlive` request with the old session and never retries.
3. **Device call request shapes.** `getProductDefinition` sends
   `params.name = "BuildDate"`. Neither source shows request parameters for
   `getDeviceType`, `getDeviceClass`, or `getSoftwareVersion`; the client sends
   an empty `params` object as the smallest assumption. `get2DCode` is
   intentionally not called because its value should not be printed or logged.
4. **Channel count request shape.** The capture shows `params: null`, which the
   client sends. No other fields are added.
5. **Channel titles.** `getVideoChannelsInfo` is documented only by
   `api_spec.md`, not by the current capture. Its request is sent with an empty
   `params` object, and `params.Output` is parsed with a best-effort name/title
   extractor. If the shape differs, the call fails or returns no names; the count
   is unaffected. The parser never invents a name.
6. **Encode and Network config request shapes.** The capture records `name` and
   `onlyLocal` request keys for the configs it did contain, but not their values.
   The client sends only `params.name`, the smallest assumption. The response
   `params.table` shape is not documented, so the UI renders it generically.
7. **Network config exists.** There is no confirmed `configManager.getConfig
   name=Network` call. The allowlist entry is a read-only probe based on
   `api_spec.md:246`. If the DVR rejects it, `/api/config/Network` returns a
   `failed_calls` entry with the method and code, and the UI shows that instead
   of a table.
8. **Masking scope.** Masking is by key name only, recursively, and replaces the
   whole value with `***`. It may over-mask fields such as
   `KeyFrameInterval`, and it cannot detect a secret stored under an unrelated
   key name.
9. **Session-expiry detection.** No invalid-session error code is documented.
   The client treats a failed response that omits the otherwise-common
   `session` field as an expired session. This check is independent of whether
   an `error` key is present, and is marked TODO.
10. **RPC success rules.** The client treats a call as successful when `result`
    is boolean `true` and, for a data-returning method, `params` is present. A
    benign `error` object does not fail a call whose `result` is `true`;
    `api_spec.md` does not document this behavior. A call fails when `result` is
    `false` or missing, when the HTTP status is not 2xx, or when `params` is
    missing for a data-returning method.
11. **Transport details.** The sources do not state the HTTP/HTTPS scheme, port,
    or TLS behavior. `DVR_HOST` defaults to `http://` and TLS verification is on
    for `https://`.
12. **Object-style factory calls.** The capture shows several
    `*.factory.instance` calls but no request bodies, no `object` field, and no
    factory call around the reads used here. No object helper is used.

## Out of scope for Phase 2

Multiple devices, live view/RTSP, playback, configuration writes,
reboot/factory-reset/format operations, user management, scheduled keepalive,
and authentication for the web app itself.
