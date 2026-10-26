# DVR Web — Phase 3b

A small, read-only web app for a Dahua DVR on the local network. It logs in to
the DVR, reads device identity, video input channels, and selected configuration,
and shows them in the browser. Phase 3b adds lease-based stream expiry, a
Connect action that starts every included channel, and UI fixes.

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
| `stream_manager.py` | On-demand go2rtc streams, leases, limits, and cleanup; builds the RTSP URL server-side |
| `app.py` | FastAPI app: `/`, `/health`, `/api/device/info`, `/api/channels`, `/api/config` (live config), `/api/config/{name}` (DVR config), `/api/live/{channel}/open`, `/api/live/renew`, `/api/live/{stream_name}/close`, plus the lease reaper |
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

Phase 3b adds these live-view settings (all safe defaults, no credentials):

```dotenv
GO2RTC_URL=http://127.0.0.1:1984
PUBLIC_GO2RTC_URL=http://127.0.0.1:1984
STREAM_TRANSCODE_H264=true
MAX_CHANNEL=8
MAX_LIVE_STREAMS=6
MAX_MAIN_STREAMS=2
LEASE_TIMEOUT_SECONDS=30
LEASE_CHECK_INTERVAL_SECONDS=5
```

- `GO2RTC_URL` is what the backend calls. It should normally stay on loopback.
- `PUBLIC_GO2RTC_URL` is what the browser uses for the player iframe. Through the
  SSH tunnel it is also `http://127.0.0.1:1984`.
- `STREAM_TRANSCODE_H264=true` makes the go2rtc source
  `ffmpeg:<rtsp-url>#video=h264`. Leave it true for this DVR: all of its streams
  are H.265, which the browser cannot play.
- `MAX_CHANNEL` is the highest accepted channel number. Channels `1` through
  `MAX_CHANNEL` are valid; the frontend also caps its tile count with it.
- `MAX_LIVE_STREAMS` is the total number of registered live streams.
- `MAX_MAIN_STREAMS` is the number of main-quality streams allowed at once.
- `LEASE_TIMEOUT_SECONDS` is how long a stream survives without a renewal.
- `LEASE_CHECK_INTERVAL_SECONDS` is how often the background reaper checks for
  expired leases.

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

- The Live section is directly below the device cards. Tiles are built from the
  channel count returned by `GET /api/channels`, capped at `MAX_CHANNEL`; if the
  count is unavailable, four tiles are shown.
- Nothing starts on page load. **Connect** starts every included channel,
  **Disconnect** stops every playing stream (including the larger view), and the
  per-tile **Start** and **Stop** still work.
- Each tile has an **Include** checkbox, checked by default. The choice is saved
  in `localStorage` per channel number; storage failures fall back to the
  in-memory checkbox state. Exclude channels with no camera so Connect does not
  open them.
- **Grid quality** (`Sub` by default, `Main`) is read when **Connect** is
  pressed; changing it does not restart streams already playing. Per-tile
  **Start** opens the sub stream, and **Larger view** always opens the main
  stream for that channel. Main streams count against `MAX_MAIN_STREAMS`; a
  `429` is shown on that tile and the other channels keep starting.
- Connect starts channels one at a time: open the stream, set the iframe `src`,
  wait about 400 ms, then continue. This avoids several ffmpeg processes
  starting at the same instant.
- Start, Larger view, and Stop use go2rtc's
  `stream.html?src=<name>&mode=mse`. The browser only ever receives the opaque
  `cctv-` name and `PUBLIC_GO2RTC_URL`.
- The backend builds the RTSP URL server-side as
  `rtsp://USER:PASS@HOST:554/cam/realmonitor?channel=N&subtype=S`
  (`S=1` sub, `S=0` main), URL-encodes the username and password, and sends the
  result only to go2rtc.
- While any stream is playing, the page sends `POST /api/live/renew` every
  10 seconds with all playing stream names (including the larger view). The
  timer stops when nothing is playing and on `pagehide`, which also sends the
  close beacons.
- If renewal reports a stream as expired, its tile is marked stopped with
  "Stream expired, press Start". The other tiles are left alone.
- `MAX_LIVE_STREAMS` and `MAX_MAIN_STREAMS` are enforced under a lock. A limit
  returns HTTP `429` with a clear message.
- A transcoded main stream uses about one CPU core; the server has two. go2rtc
  stops pulling and transcoding when nobody is watching.

### Lease expiry

Each registered stream has a last-renewed time from a monotonic clock, which can
be injected in tests. `open` starts the lease. A background asyncio task runs
every `LEASE_CHECK_INTERVAL_SECONDS` and deletes every stream whose lease is
older than `LEASE_TIMEOUT_SECONDS`. The slot is freed before the go2rtc delete;
a failed delete is logged without a source URL and retried during shutdown
cleanup. `close` removes the lease, so a closed stream can never expire later.

A name that is unknown or already past its timeout is listed under `expired` by
the renew endpoint; that is not an HTTP error. The browser turns that into the
"Stream expired, press Start" state.

Leases cover a dead tab, a closed browser, or a dropped SSH tunnel without
relying on the `pagehide` beacon. The app process must still be running for the
reaper to delete an expired stream.

### go2rtc persistence and cleanup

go2rtc writes streams added through its API into its own configuration file,
including the RTSP source and therefore the DVR credentials. The backend
therefore:

1. Uses only opaque names with the fixed prefix `cctv-`.
2. Deletes each stream immediately on close and as soon as its lease expires.
3. On startup, lists go2rtc streams and deletes every leftover whose name starts
   with `cctv-`. Only response keys are inspected; values are never logged.
4. On shutdown, deletes every stream it created. A close or expiry delete that
   failed earlier is retried from the in-memory created set.

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
   be directly below the device cards. `GET /api/config` should return only
   `{"public_go2rtc_url": "http://127.0.0.1:1984", "max_channel": 8}`. The tile
   count should match the DVR channel count, capped at `MAX_CHANNEL`. The
   device-fields fallback and the Encode table should be collapsed.

5. Exclude channels with no camera with their **Include** checkbox, keep channel
   2 included, leave **Grid quality** on **Sub**, and press **Connect**. Expect
   channel 2 to show the sub stream and the status to report one connected
   channel. Then press **Disconnect**; no `cctv-` name should remain.

6. Measure the Connect burst. With the desired channels included, press
   **Connect** and count the ffmpeg processes (names only, no arguments):

   ```bash
   pgrep -c ffmpeg
   ```

   The count should match the number of streams go2rtc is transcoding (plus any
   unrelated ffmpeg). Measure CPU with `top`:

   ```bash
   top -b -n 1 | head -20
   ```

   Do not press `c` in interactive `top`: it shows full command lines, which can
   include the RTSP URL with credentials. Look only at the `%CPU` column for
   `ffmpeg`. One transcoded main stream is expected to use about one core.

7. Check lease renewal while a stream plays. In another terminal, list names
   only:

   ```bash
   curl -s http://127.0.0.1:1984/api/streams \
     | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)))'
   ```

   A `cctv-` name should exist. Wait at least 30 seconds with the tab open; the
   browser renews every 10 seconds, so the name should stay. Optionally call the
   batch endpoint yourself (it returns only names):

   ```bash
   curl -s -X POST http://127.0.0.1:8000/api/live/renew \
     -H 'Content-Type: application/json' \
     -d '{"streams": ["cctv-..."]}'
   ```

8. Test expiry. For a true lease test, kill the SSH tunnel while a stream is
   playing so the tab's close beacon cannot reach the backend (closing the tab
   may close immediately through `sendBeacon`). Leave uvicorn and go2rtc
   running. Wait about
   `LEASE_TIMEOUT_SECONDS + LEASE_CHECK_INTERVAL_SECONDS` (35 seconds with the
   defaults), then run the names-only command again. No name starting with
   `cctv-` should remain, and uvicorn should log the expired stream name.

9. Test the 429 message and Connect isolation. Set **Grid quality** to **Main**
   and press **Connect** with more included channels than `MAX_MAIN_STREAMS`.
   The first two mains should play (default limit 2); later tiles should show the
   clear 429 message and the earlier tiles must keep playing. **Disconnect**
   clears them.

10. Test startup/shutdown cleanup. Start a stream and confirm a `cctv-` name
    exists. Stop uvicorn with `Ctrl+C`; graceful shutdown should delete it. To
    simulate a crash, start a stream again and run `kill -9 <uvicorn-pid>`, then
    start uvicorn again; startup cleanup should delete the leftover. Verify with
    the names-only command.

11. Test page unload. Start a channel, then reload or close the tab. The
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

Returns only the browser-facing go2rtc URL and the channel cap used by the Live
view:

```json
{
  "public_go2rtc_url": "http://127.0.0.1:1984",
  "max_channel": 8
}
```

This is distinct from `GET /api/config/{name}`, which reads DVR configuration.
It returns no stream sources, credentials, or go2rtc API response.

### `POST /api/live/{channel}/open?quality=sub|main`

Registers one on-demand go2rtc stream and starts its lease. `channel` must be
between `1` and `MAX_CHANNEL`; `quality` defaults to `sub`. The response contains
only the opaque stream name:

```json
{"stream_name": "cctv-<random>"}
```

The RTSP URL is built server-side and sent only to go2rtc. It is never returned
or logged. `quality=main` maps to RTSP `subtype=0`; `quality=sub` maps to
`subtype=1`.

Status codes:

- `400` — channel outside `1..MAX_CHANNEL`, or quality not `sub`/`main`.
- `429` — `MAX_LIVE_STREAMS` or `MAX_MAIN_STREAMS` reached; the message says
  which limit was hit.
- `502` — go2rtc could not be reached or rejected the registration.
- `503` — live view is not configured.

### `POST /api/live/renew`

Extends the leases of known streams in one batch. The body must contain 1 to 16
names, each matching the `cctv-` format:

```json
{"streams": ["cctv-<random>", "cctv-<random>"]}
```

The response separates extended names from names that are unknown or already
past their lease. An expired name is not a `404`:

```json
{
  "renewed": ["cctv-<random>"],
  "expired": ["cctv-<random>"]
}
```

Malformed input returns `400`: a non-object body, a `streams` value that is not
a list, a list length outside 1-16, a non-string entry, or an invalid name.
Duplicate names are answered once.

### `POST /api/live/{stream_name}/close`

Deletes one stream and removes its lease. The response is `{"closed": true}`. It
is safe to call twice: an unknown or already-closed name is treated as closed and
is not forwarded to go2rtc. A malformed name returns `400`; a go2rtc deletion
failure returns `502`.

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
- Lease expiry logs stream names and expiry events only. It never logs a source
  URL, a password, or a go2rtc response body.
- The per-channel **Include** preference is stored in the browser's
  `localStorage` as a simple `1`/`0` flag; it contains no credentials or stream
  names.
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
    test steps above are run.
20. **Background-tab timer throttling.** Browsers throttle `setInterval` in
    hidden tabs (Chrome can limit it to about once per minute). With the default
    30-second lease, a hidden tab may lose its streams to expiry even though it
    is still open. Raise `LEASE_TIMEOUT_SECONDS` or keep the tab visible; this
    was not observed in a real browser here.
21. **Lease clock behaviour.** The manager takes an injectable monotonic clock,
    and the mock tests use a fake one. Real suspend/hibernate behaviour of
    `time.monotonic` across platforms was not tested.
22. **Failed expiry deletes.** A failed go2rtc delete frees the slot immediately
    and leaves the name in the created set for shutdown retry. Whether go2rtc
    leaves a partially removed stream behind in that case is not verified.
23. **Connect with channels that have no camera.** go2rtc/ffmpeg retry and CPU
    behaviour for included channels without a camera were not measured. Exclude
    them with the Include checkbox during the server test.
24. **Renew and expiry UI.** The 10-second renewal timer, `pagehide` ordering,
    and per-tile "Stream expired, press Start" state were syntax-checked but not
    run against a live go2rtc.
25. **`pgrep` and `top` values.** Exact ffmpeg counts and CPU percentages depend
    on the server, go2rtc version, and stream quality; the steps describe the
    expected shape, not measured numbers.

## Out of scope for Phase 3b

Multiple devices, playback, configuration writes,
reboot/factory-reset/format operations, user management, scheduled keepalive,
recording, PTZ, and authentication or proxying for go2rtc. The go2rtc player is
reached through the SSH tunnel; a later phase will put it behind authenticated
proxying.
