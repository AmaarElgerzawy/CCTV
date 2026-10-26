# DVR Web — Phase 3a

A small, read-only web app for a Dahua DVR on the local network. It logs in to
the DVR, reads device identity, video input channels, and selected configuration,
and shows them in the browser. Phase 3a adds an on-demand live view through
go2rtc.

All DVR API behavior is based strictly on [`api_spec.md`](api_spec.md) and the
current `har_summary.txt`. Points that the sources do not document are marked as
TODO in the code and listed at the end of this file. The app does not guess
write methods and never sends a set/write/reboot/format call.

Live video is separate from the DVR JSON-RPC session: the live path uses RTSP
through go2rtc and does not call `DvrClient` or add any DVR RPC calls.

## Project layout

| File | Purpose |
| --- | --- |
| `dvr_client.py` | `DvrClient`: one instance per DVR session; `login()`, `keepalive()`, `get_device_info()`, `get_channels()`, `get_config(name)`, `logout()`, context-manager support |
| `stream_manager.py` | On-demand go2rtc stream registration, limits, and cleanup; builds the RTSP URL server-side |
| `app.py` | FastAPI app: `/`, `/health`, `/api/device/info`, `/api/channels`, `/api/config` (live config), `/api/config/{name}` (DVR config), `/api/live/{channel}/open`, `/api/live/{stream_name}/close` |
| `static/` | Plain HTML/CSS/JS frontend (no build step), including the Live view |
| `test_dvr.py` | Manual login → device/channels/config reads → logout smoke test |
| `.env.example` | Placeholder configuration to copy to `.env` |
| `api_spec.md` | DVR API notes this implementation is based on |

## Requirements

- Python 3.11 or newer
- Network access to the DVR's web interface and RTSP service
- DVR web credentials with read access
- A running go2rtc instance with access to `ffmpeg` for H.264 transcoding
- On the Ubuntu server, a go2rtc API reachable at `GO2RTC_URL`
  (default `http://127.0.0.1:1984`)


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

Phase 3a adds these live-view settings (all safe defaults, no credentials):

```dotenv
GO2RTC_URL=http://127.0.0.1:1984
PUBLIC_GO2RTC_URL=http://127.0.0.1:1984
STREAM_TRANSCODE_H264=true
MAX_LIVE_STREAMS=4
MAX_MAIN_STREAMS=1
```

- `GO2RTC_URL` is what the backend calls. It should normally stay on loopback.
- `PUBLIC_GO2RTC_URL` is what the browser uses for the player iframe. Through the
  SSH tunnel it is also `http://127.0.0.1:1984`.
- `STREAM_TRANSCODE_H264=true` makes the go2rtc source
  `ffmpeg:<rtsp-url>#video=h264`. Leave it true for this DVR: all of its streams
  are H.265, which the browser cannot play.
- `MAX_LIVE_STREAMS` is the total number of registered live streams.
- `MAX_MAIN_STREAMS` is the number of main-quality streams allowed at once.

`DVR_HOST` may include a scheme and port, for example
`http://192.168.1.100:80`. If no scheme is given, `http://` is used. The RTSP
port is fixed at `554` because that is the measured DVR behaviour; the web port
in `DVR_HOST` is not reused for RTSP. `python-dotenv` loads `.env` at runtime.
The app never sends the username, password, RTSP URL, or session id to the
browser, and never logs them.

## Run

### Windows development

```powershell
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>.

Clicking **Refresh** calls the backend; the browser never talks to the DVR
directly. Each data request creates its own short-lived DVR session and logs out
in a `finally` block. go2rtc is not run on Windows; the live section is tested
on the Ubuntu server.

### Ubuntu server

go2rtc must already be running. This app never downloads, installs, starts, or
stops go2rtc or ffmpeg.

```bash
cd /path/to/dvr-web
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

Both uvicorn and go2rtc should listen on loopback. For testing from Windows,
open an SSH tunnel:

```bash
ssh -L 1984:127.0.0.1:1984 -L 8000:127.0.0.1:8000 USER@SERVER
```

Then open <http://127.0.0.1:8000> on Windows. The browser loads the web app
through port `8000` and the go2rtc player through port `1984`, both over the
tunnel.

**Exposure note:** this phase exposes go2rtc to the browser through the SSH
tunnel only. Do not bind go2rtc or uvicorn to a public interface. A later phase
will proxy go2rtc behind authentication.

## Live view behaviour

- One tile per channel (1-4). Only channel 2 currently has a camera, so no
  channel is hardcoded.
- **Start** opens the sub stream (`subtype=1`, 352x288) in an iframe using
  go2rtc's `stream.html?src=<name>&mode=mse`.
- **Larger view** opens the main stream (`subtype=0`) in a full-width player.
- **Stop**, **Close larger view**, and the page `pagehide` handler call the close
  endpoint. The page uses `navigator.sendBeacon` when it is unloading.
- Streams are registered only while watched. The backend builds the RTSP URL
  server-side as
  `rtsp://USER:PASS@HOST:554/cam/realmonitor?channel=N&subtype=S`
  (`S=1` sub, `S=0` main), URL-encodes the username and password, and sends the
  result only to go2rtc.
- The browser receives only an opaque stream name such as `cctv-<random>`; it
  never receives the RTSP URL or a go2rtc API response.
- `MAX_LIVE_STREAMS` and `MAX_MAIN_STREAMS` are enforced under a lock. A limit
  returns HTTP `429` with a clear message, shown in the Live section.
- A transcoded main stream uses about one CPU core; the server has two. go2rtc
  stops pulling and transcoding when nobody is watching.

### go2rtc persistence and cleanup

go2rtc writes streams added through its API into its own configuration file,
including the RTSP source and therefore the DVR credentials. The backend
therefore:

1. Uses only opaque names with the fixed prefix `cctv-`.
2. Deletes each stream immediately on close.
3. On startup, lists go2rtc streams and deletes every leftover whose name starts
   with `cctv-`. Only response keys are inspected; values are never logged.
4. On shutdown, deletes every stream it created. A close that failed earlier is
   retried from the in-memory created set.

If the process is killed without a graceful shutdown, leftovers remain in
go2rtc's config until the next startup cleanup. Protect the go2rtc config file
with appropriate permissions; it can contain DVR credentials. Do not commit it
or paste it into issue reports. Startup cleanup assumes this app is the only
consumer of the `cctv-` prefix.

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

## Test live view on the Ubuntu server

1. Push from Windows, pull on Ubuntu, and confirm go2rtc is already running:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:1984/api/streams
   ```

   This prints only the HTTP status. Do not paste the response body; go2rtc
   stream entries can contain RTSP credentials.

2. Start the app on Ubuntu (loopback only):

   ```bash
   cd /path/to/dvr-web
   source .venv/bin/activate
   uvicorn app:app --host 127.0.0.1 --port 8000
   ```

3. Open the tunnel from Windows and keep it running:

   ```bash
   ssh -L 1984:127.0.0.1:1984 -L 8000:127.0.0.1:8000 USER@SERVER
   ```

4. In the Windows browser open <http://127.0.0.1:8000>. The Live section should
   show four tiles. `GET /api/config` should return only
   `{"public_go2rtc_url": "http://127.0.0.1:1984"}`.

5. Click **Start** on channel 2 (the only channel with a camera). The iframe
   should show the sub stream through go2rtc `mode=mse`. Click **Larger view** on
   channel 2; the main stream should appear in the full-width player. Click
   **Close larger view**, then **Stop**.

6. Verify cleanup with a names-only command. This prints only keys, never
   stream sources:

   ```bash
   curl -s http://127.0.0.1:1984/api/streams \
     | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)))'
   ```

   After Stop and Close, no name starting with `cctv-` should remain.

7. Test the 429 message through the API. Open one main stream:

   ```bash
   curl -s -X POST 'http://127.0.0.1:8000/api/live/2/open?quality=main'
   ```

   The response contains only `{"stream_name":"cctv-..."}`. Open a second main
   stream:

   ```bash
   curl -s -X POST 'http://127.0.0.1:8000/api/live/2/open?quality=main'
   ```

   This should return HTTP `429` with the main-stream limit message. To test the
   total limit, start all four sub tiles in the UI and then click **Larger
   view**; the Live section should show a clear `429` message. Close the first
   main stream with the name from step 7:

   ```bash
   curl -s -X POST 'http://127.0.0.1:8000/api/live/<stream_name>/close'
   ```

8. Test startup cleanup. Start a stream and confirm a `cctv-` name exists with
   the names-only command. Stop uvicorn with `Ctrl+C`; graceful shutdown should
   delete it. To simulate a crash, start a stream again and run
   `kill -9 <uvicorn-pid>` instead, then start uvicorn again. Startup cleanup
   should delete the leftover `cctv-` name. Verify with the names-only command.

9. Test page unload. Start a channel, then reload or close the tab. The
   `pagehide` handler sends a `sendBeacon` close for every active stream; verify
   that no `cctv-` name remains.

You can also check the web API directly:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/config
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

### `GET /api/config`

Returns only the browser-facing go2rtc URL used by the Live view:

```json
{"public_go2rtc_url": "http://127.0.0.1:1984"}
```

This is distinct from `GET /api/config/{name}`, which reads DVR configuration.
It returns no stream sources, credentials, or go2rtc API response.

### `POST /api/live/{channel}/open?quality=sub|main`

Registers one on-demand go2rtc stream. `channel` must be `1`-`4`; `quality`
defaults to `sub`. The response contains only the opaque stream name:

```json
{"stream_name": "cctv-<random>"}
```

The RTSP URL is built server-side and sent only to go2rtc. It is never returned
or logged. `quality=main` maps to RTSP `subtype=0`; `quality=sub` maps to
`subtype=1`.

Status codes:

- `400` — channel outside 1-4, or quality not `sub`/`main`.
- `429` — `MAX_LIVE_STREAMS` or `MAX_MAIN_STREAMS` reached; the message says
  which limit was hit.
- `502` — go2rtc could not be reached or rejected the registration.
- `503` — live view is not configured.

### `POST /api/live/{stream_name}/close`

Deletes one stream. The response is `{"closed": true}`. It is safe to call
twice: an unknown or already-closed name is treated as closed and is not
forwarded to go2rtc. A malformed name returns `400`; a go2rtc deletion failure
returns `502`.

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
- The RTSP URL, DVR username/password, and go2rtc API responses stay on the
  backend. The browser receives only an opaque `cctv-` stream name and the
  configured `PUBLIC_GO2RTC_URL`.
- go2rtc persists API-added streams, including their RTSP source with
  credentials, in its own config. The cleanup rules above remove this app's
  streams; protect the go2rtc config file and never commit or paste it.
- go2rtc is exposed to the browser only through the SSH tunnel in this phase. It
  is not proxied or authenticated by this app yet; do not expose it publicly.

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
13. **go2rtc list response for startup cleanup.** The cleanup assumes
    `GET {GO2RTC_URL}/api/streams` returns a JSON object keyed by stream name.
    That is the documented go2rtc shape but was not verified in this
    environment; a non-object response is ignored safely.
14. **go2rtc status codes.** Registration accepts any `2xx`; deletion treats
    `404` as already gone and accepts any other `2xx`. Exact responses were not
    confirmed against a live go2rtc.
15. **RTSP port.** The RTSP URL uses port `554` exactly as measured. The port in
    `DVR_HOST` is treated as the web port and is not reused for RTSP.
16. **Transcoding disabled.** With `STREAM_TRANSCODE_H264=false`, the raw RTSP
    source is sent to go2rtc. This DVR's H.265 streams are expected to be
    unplayable in the browser; that path was not tested.
17. **Browser MSE support.** The player uses go2rtc `mode=mse` because WebRTC
    needs UDP ports that the SSH tunnel does not carry. Actual playback was not
    tested from this Windows machine.
18. **CPU and persistence behaviour.** The one-core main-stream estimate and
    go2rtc's config persistence come from the measured facts supplied with this
    phase, not from a live run here. The stream limits are count-based, not
    CPU-aware.
19. **No live DVR/go2rtc contact in this phase.** All verification used mock
    HTTP transports. The real RTSP/go2rtc path is unconfirmed until the Ubuntu
    test below is run.

## Out of scope for Phase 3a

Multiple devices, playback, configuration writes,
reboot/factory-reset/format operations, user management, scheduled keepalive,
recording, PTZ, and authentication or proxying for go2rtc. The go2rtc player is
reached through the SSH tunnel; a later phase will put it behind authenticated
proxying.
