# DVR Web — Phase 1

A small, read-only web app for a Dahua DVR on the local network. It logs in to
the DVR, reads device type, device class, software version, and build date, and
shows them in the browser.

All DVR API behavior is based strictly on [`api_spec.md`](api_spec.md). Points
that the spec does not document are marked as TODO in the code and listed at the
end of this file.

## Project layout

| File | Purpose |
| --- | --- |
| `dvr_client.py` | `DvrClient`: one instance per DVR session; `login()`, `keepalive()`, `logout()`, `get_device_info()`, context-manager support |
| `app.py` | FastAPI app: `/`, `/health`, `/api/device/info` |
| `static/` | Plain HTML/CSS/JS frontend (no build step) |
| `test_dvr.py` | Manual login → read → logout smoke test |
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
directly.

## Test the client

```powershell
python test_dvr.py
python test_dvr.py --verify-logout
```

Expected output is `Login: OK`, a `Device info:` JSON object containing the four
device fields plus `succeeded_calls`/`failed_calls`, and
`Logout: server-side logout succeeded`. The four values are non-sensitive;
credentials, session ids, and any `get2DCode` value are never printed. A failed
call is isolated so the other three still run, and failure entries contain only
the method name and error code.

With `--verify-logout`, the client logs out and then makes exactly one
`global.keepAlive` request with the old session. It reports whether that session
was rejected (closed), was still accepted, or could not be determined. No retry
is made.

You can also check the web API directly:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/device/info
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

Errors are returned as `{"error": "message"}` with status `503` for missing
configuration and `502` for DVR communication or login failures. A failed
device-identity call does not fail the request; it is reported in
`info.failed_calls` with the method name and error code. The message never
includes the password, session id, or raw DVR payload.

## Security notes

- Credentials live only in `.env`, which is ignored by `.gitignore`. Only
  `.env.example` belongs in git.
- The DVR session id is kept in memory inside one `DvrClient` instance and is
  never logged or returned by an endpoint.
- Each `/api/device/info` request creates and closes its own client, which keeps
  the door open for the later multi-device, on-demand architecture.
- The endpoint and context manager call `logout()` in a `finally` block, so the
  local session is cleared even when an earlier call fails.
- This phase is read-only except for session logout. No configuration write/set
  calls exist in the code.

## Confirmed by `api_spec.md` and `har_summary.txt`

- JSON-RPC login: `POST /RPC2_Login` with `rpc=global.login`.
- Post-login calls: `POST /RPC2`, including the `session` value in the request
  and a `session` value in responses.
- A successful login response contains a top-level `session`.
- Logout: `POST /RPC2` with `rpc=global.logout`, returning response keys
  `id`, `result`, `session`. The capture does not record the request body.
- Keepalive: `global.keepAlive`, with `params.timeout` in the response.
- Device type: `magicBox.getDeviceType`, with the result in `params.type`.
- Device class: `magicBox.getDeviceClass`, with the result in `params.type`.
- Software version: `magicBox.getSoftwareVersion`, with the result in
  `params.version`.
- Build date: `magicBox.getProductDefinition` with `params.name` = `BuildDate`,
  with the result in `params.definition`.

## Not confirmed by the spec (marked TODO in code)

1. **Login request construction.** The spec confirms the endpoint, method, and
   response fields (`params.realm`, `params.random`, `params.encryption`,
   `session`, `error`, `result`), but not the request-side credential fields,
   `clientType`/`authorityType`/`passwordType` values, or the password digest
   algorithm. `dvr_client.py` uses the standard two-step Dahua challenge:
   step 1 sends an empty password and returns realm/random/session; step 2 sends
   the uppercase MD5 challenge with `authorityType`/`passwordType` set to
   `Default`. The spec also lists `params.authorization` and `params.encryption`
   as login fields, but does not say whether they are request or response fields;
   this flow sends neither. There is exactly one attempt per step and no
   automatic retry. See the `TODO(spec)` comments in `DvrClient.login()` and
   `_hash_password()`.
2. **Logout request body.** The capture confirms `POST /RPC2 rpc=global.logout`
   and response keys `id`, `result`, `session`, but not the request body.
   `logout()` sends the current session in the top-level `session` field, a
   fresh `id`, and `params: null` as the smallest assumption. It adds no other
   fields. Success means `result` is `true`; the local session is always cleared.
   A failed logout raises a safe `DvrLogoutError` with method, HTTP status, error
   code/message, and result type only. The optional verification uses one
   `global.keepAlive` request with the old session and never retries.
3. **Device call request shapes.** `api_spec.md` and `har_summary.txt` show
   `magicBox.getProductDefinition name=BuildDate`, so that call sends
   `params.name = "BuildDate"`. Neither source shows request parameters for
   `getDeviceType`, `getDeviceClass`, or `getSoftwareVersion`; the client sends
   an empty `params` object as the smallest assumption. `get2DCode` exists but is
   intentionally not called in this phase because its value should not be
   printed or logged.
4. **Pre-login encryption/init calls.** `/OutsideCmd` calls such as
   `Security.getEncryptInfo` exist in the spec, but whether they are required for
   login and how encryption is negotiated is not documented. They are not used.
5. **Session-expiry detection.** No invalid-session error code is documented.
   The client treats a failed response that omits the otherwise-common
   `session` field as an expired session. This check is independent of whether
   an `error` key is present, and is marked TODO.
6. **RPC success rules.** The client treats a call as successful when `result`
   is boolean `true` and, for a data-returning method, `params` is present. A
   benign `error` object (including the observed message "Unknown error! error
   code was not set in service!") does not fail a call whose `result` is `true`;
   `api_spec.md` does not document this behavior. A call fails when `result` is
   `false` or missing, when the HTTP status is not 2xx, or when `params` is
   missing for a data-returning method.
7. **Transport details.** The spec does not state the HTTP/HTTPS scheme, port,
   or TLS behavior. `DVR_HOST` defaults to `http://` and TLS verification is on
   for `https://`.
8. **Object-style factory calls.** `har_summary.txt` shows several
   `*.factory.instance` calls and one `mediaFileFind.destroy`, but no request
   bodies, no `object` field, and no factory call around the four
   device-identity calls. No object helper is used. The unconfirmed
   request-side names are the top-level `object` field, the response field that
   carries the object id (only a `result` key is visible), and destroy's request
   fields.

## Out of scope for Phase 1

Multiple devices, live view/RTSP, playback, configuration reads beyond system
info, configuration writes, user management, scheduled keepalive, and
authentication for the web app itself.
