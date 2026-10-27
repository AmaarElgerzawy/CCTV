# DVR Web — Phase 5b

A small, read-only web app for Dahua DVRs on the local network. It logs in to
a DVR, reads device identity, video input channels, and selected configuration,
and shows them in the browser. Phase 3b adds lease-based live streaming through
go2rtc. Phase 4a adds a read-only recordings list, a 24-hour timeline, and
storage health through the DVR HTTP CGI API. Phase 4b adds recorded playback
through go2rtc from a clip or a timeline time, still read-only on the DVR.
Phase 5a adds a local device registry: groups, up to hundreds of recorders,
encrypted per-device credentials with a default fallback, CSV import, and a
TCP-only online-status checker. Phase 5b links the dashboard (Live, Recordings,
Settings) to a registry-selected device with per-device limits, device-aware
stream management, and lazy loading for configuration sections.

All DVR API behavior is based strictly on [`api_spec.md`](api_spec.md) and the
current `har_summary.txt`. Points that the sources do not document are marked as
TODO in the code and listed at the end of this file. The app does not guess
write methods and never sends a set/write/reboot/format call.

Live video is separate from the DVR JSON-RPC session: the live path uses RTSP
through go2rtc and does not call `DvrClient` or add any DVR RPC calls.
Recorded playback also goes through go2rtc with a separate playback RTSP URL.
Recordings and storage use the DVR HTTP CGI API with Digest authentication and
do not use the RPC session either. No media is downloaded.

## Project layout

| File | Purpose |
| --- | --- |
| `dvr_client.py` | `DvrClient`: one instance per DVR session; `login()`, `keepalive()`, `get_device_info()`, `get_channels()`, `get_config(name)`, `logout()`, context-manager support |
| `cgi_client.py` | Read-only DVR CGI client: device clock, `findFile`/`findNextFile` paging, `close`/`destroy` cleanup, storage health, recording cache, and per-device lock |
| `stream_manager.py` | On-demand go2rtc live and recorded-playback streams, leases, limits, and cleanup; builds the RTSP URLs server-side |
| `registry.py` | Phase 5a device registry: settings, SQLite storage, Fernet credentials, groups/devices, CSV import, first-run migration, connection test, and the TCP status checker |
| `app.py` | FastAPI app: `/`, `/devices`, `/health`, `/api/device/info`, `/api/channels`, `/api/config` (live config), `/api/config/{name}` (DVR config), `/api/live/{channel}/open`, `/api/live/renew`, `/api/live/{stream_name}/close`, `/api/playback/open`, `/api/recordings`, `/api/recordings/time`, `/api/storage`, the registry API (`/api/devices/*`), plus the lease reaper and status checker; Phase 5b adds device resolution, per-device locks/caches, per-device stream limits, and `POST /api/devices/{id}/streams/close-all` |
| `static/` | Plain HTML/CSS/JS frontend (no build step), including Live, Recordings, playback, and the Devices page; Phase 5b adds device selector/header, per-device lazy loading, and per-device Include keys |
| `static/playback_clips.js` | Pure clip-navigation helpers (`daySeconds`, `clipStarts`, `adjacentClipStart`) shared by the browser and a Node test |
| `static/device_selector.js` | Pure device selector helpers (shared by browser and Node test) |
| `static/devices.html`, `static/devices.js` | Phase 5a registry page: counters, search/state filter, grouped devices, CRUD, default credentials, CSV preview/import, status refresh; Phase 5b adds an "Open" button linking to `/?device=<id>` |
| `test_dvr.py` | Manual login → device/channels/config reads → logout smoke test |
| `test_registry.py` | Offline unittest suite for Phase 5a (mock transports, fake sockets, temp SQLite) |
| `test_phase5b.py` | Offline tests for Phase 5b device resolution and per-device stream limits |
| `test_phase5b_regression.py` | Regression tests for device_id path through recordings/time/storage with mocks |
| `test_device_selector.js` | Node tests for pure device selector helpers |
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

Phase 4a adds these recording settings:

```dotenv
RESPONSE_CHANNEL_OFFSET=-1
RECORDINGS_CACHE_SECONDS=60
```

Phase 4b adds these playback settings:

```dotenv
MAX_PLAYBACK_STREAMS=2
PLAYBACK_MAX_SECONDS=3600
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
- `RESPONSE_CHANNEL_OFFSET` is added to the requested channel number when
  filtering `findNextFile` items. The measured device reports the requested
  channel one number lower, so the default is `-1`. This is TODO(spec): it was
  measured on one device only.
- `RECORDINGS_CACHE_SECONDS` is how long one channel/date/stream finder result is
  kept in memory. `0` disables the cache.
- `MAX_PLAYBACK_STREAMS` is the number of recorded-playback streams allowed at
  once. Playback has its own counter and does not count against
  `MAX_LIVE_STREAMS` or `MAX_MAIN_STREAMS`.
- `PLAYBACK_MAX_SECONDS` is the longest accepted playback request. The browser
  asks for 600 seconds; the accepted range is 30 to this value.

Phase 5a adds these registry settings:

```dotenv
APP_SECRET_KEY=replace-with-a-generated-fernet-key
DATA_DIR=./data
STATUS_INTERVAL_SECONDS=60
STATUS_CONCURRENCY=10
STATUS_TIMEOUT_SECONDS=2
ALLOW_PUBLIC_HOSTS=false
```

Phase 5b adds these settings (safe defaults):

```dotenv
DEVICE_INFO_CACHE_SECONDS=60
MAX_STREAMS_PER_DEVICE=4
MAX_MAIN_PER_DEVICE=1
```

Generate the Fernet key once, then paste only the value:

```powershell
python -c "from cryptography.fernet import Fernet as F; print(F.generate_key().decode())"
```

```bash
python3 -c "from cryptography.fernet import Fernet as F; print(F.generate_key().decode())"
```

- `APP_SECRET_KEY` encrypts the stored DVR passwords. If it is missing or not a
  valid Fernet key, every registry endpoint returns `503` with
  "Device registry is not configured. Set a valid APP_SECRET_KEY in .env." and
  the rest of the app (Live, Recordings, Settings) keeps working. Changing the key
  makes the stored passwords unreadable; they must be entered again.
- `DATA_DIR` holds `dvr.db`. A relative path is resolved from the app folder,
  so `./data` is `<repo>/data/dvr.db`. On first use the directory and the SQLite
  file are created; on POSIX the file is chmod `600` (best effort, ignored on
  Windows). `data/` is in `.gitignore`; never commit or paste the database.
- `STATUS_INTERVAL_SECONDS` is the delay between background status rounds
  (default `60`). Invalid or non-positive values fall back to the default.
- `STATUS_CONCURRENCY` is the maximum number of devices checked at once
  (default `10`). Each device uses at most one socket at a time.
- `STATUS_TIMEOUT_SECONDS` is the per-port TCP connect timeout (default `2`).
- `ALLOW_PUBLIC_HOSTS=false` rejects public IP literals as device hosts. Set it
  to `true` only if the registry really contains recorders reachable over the
  internet.
- `cryptography` is installed from `requirements.txt` and is only used for the
  registry's credential encryption.

No registry password can be read back: the API returns only
`has_own_credentials` / `has_password`, and no response, log line, error message,
CSV result, or export contains one.

`DVR_HOST` may include a scheme and port, for example
`http://192.168.1.100:80`. If no scheme is given, `http://` is used. The RTSP
port is fixed at `554` because that is the measured DVR behaviour; the web port
in `DVR_HOST` is not reused for RTSP. Do not put a username or password in
`DVR_HOST`; credentials belong in `DVR_USER` and `DVR_PASS`, and a URL with
userinfo is rejected. `python-dotenv` loads `.env` at runtime. The app never
sends the username, password, RTSP URL, session id, or DVR file path to the
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
  10 seconds with all playing stream names (including the larger view and
  recorded playback). The timer stops when nothing is playing and on `pagehide`,
  which also sends the close beacons.
- If renewal reports a stream as expired, its tile is marked stopped with
  "Stream expired, press Start". The other tiles are left alone. A playback
  stream that expires shows "Playback ended".
- `MAX_LIVE_STREAMS` and `MAX_MAIN_STREAMS` are enforced under a lock. A limit
  returns HTTP `429` with a clear message. A main-stream limit says
  "Main-stream limit reached (N). Stop another main stream or switch Grid
  quality to Sub."
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
the renew endpoint; that is not an HTTP error. The browser turns a live-stream
expiry into "Stream expired, press Start" and a playback expiry into
"Playback ended". Playback streams use the same `cctv-` prefix, lease code, and
reaper as live streams.

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

## Recordings, playback, and storage (Phases 4a-4b)

The **Recordings** section sits directly after Live. It has a channel select
(from the reported channel count, capped by `MAX_CHANNEL`), a date input, a
Main/Sub select, and a **Load** button. Nothing is loaded automatically: the
page only reads the device date and storage health on load/refresh; recordings
are read when **Load** is pressed.

- The date input is filled from `GET /api/recordings/time`, which reads the DVR
  clock. If the clock read fails, the server date is used and the section says
  so.
- `Main` maps to the DVR's `VideoStream=Main`; `Sub` maps to `Extra1`.
- Items are filtered to the requested channel with
  `item.Channel == channel + RESPONSE_CHANNEL_OFFSET`, then sorted by start.
- The 24-hour timeline draws one bar per file at its position in the day. Bars
  are colored by flag: **Event** is amber, **Timing** is the accent color, and
  other files are gray; a small legend matches the colors. Empty space is a gap.
- Clicking the timeline marks a time and enables **Play from here**; it does not
  start playback by itself.
- Coverage is the merged, day-clamped recording time divided by 86,400
  seconds. Overlapping or adjacent files are merged. Only gaps of 5 minutes or
  more are listed (including a leading or trailing gap when files exist).
- The summary line shows coverage percent, the last recording end, and the gap
  count. The file table is inside a closed **Files** `<details>`; at most 400
  rows are rendered, with a note when the backend returned more. Each row has a
  **Play** button that starts playback at that clip's start.
- The storage badge shows `ok`, `problem`, or `unknown`. A recorder that
  overwrites old data can report `UsedBytes == TotalBytes`; that is not treated
  as a problem. `problem` means a disk state other than `Success` or a
  detail `IsError=true`. A failed storage call is `unknown`.
- The badge tooltip reports the number of listed disks. The API field is
  `disks` because it counts `list.info[]` entries, which is what the device
  reports, not necessarily the number of physical partitions.
- If the DVR answers `findFile` with `Error / Bad Request!`, the section shows
  "Recordings are unavailable on this device" with that reason. It is not a
  server error.
- If a finder reaches 30 pages of 100 files (3,000 files), the response is
  marked `truncated` and the section warns that the summary may be incomplete.

### Playback behaviour (Phase 4b)

Playback is available only after recordings for one channel and date are loaded.
The page knows the loaded channel, the DVR date, and the file list.

- **Play** on a file row plays from that clip's start. A timeline click sets the
  chosen time and **Play from here** plays from it. **Previous clip** and
  **Next clip** jump to the previous or next clip start computed from the loaded
  file list; while playback is active they also start that clip immediately.
- **Stop** closes the stream, clears the viewer, and shows "Playback ended".
- One viewer sits under the timeline and uses the public go2rtc player at
  `stream.html?src=<stream_name>&mode=mse`. The status line reads
  "Playing from HH:MM:SS. The DVR may start a few seconds earlier and skips gaps
  between clips."
- The lease is renewed every 10 seconds through the existing
  `POST /api/live/renew` batch, together with any live streams. The stream is
  closed on Stop, when a new playback starts, when the recordings list is
  reloaded or cleared, and on `pagehide`.
- If the lease expires, the viewer is cleared and the line shows
  "Playback ended". There is no pause, speed, or seek control.
- The backend builds the playback RTSP URL server-side as
  `rtsp://USER:PASS@HOST:554/cam/playback?channel=N&starttime=YYYY_MM_DD_HH_MM_SS&endtime=YYYY_MM_DD_HH_MM_SS`
  and sends it only to go2rtc. Times are the DVR's own local clock, the same
  clock as the recordings list, so no timezone conversion is applied. The
  measured device may start a few seconds before the requested start, skips
  gaps between clips, and ends at the end of the last clip.
- Playback always uses the main stream, even when the clip list was loaded with
  `Sub`; a note says so. Recordings are H.265, so the go2rtc source is
  `ffmpeg:<rtsp-url>#video=h264` and the browser plays in MSE mode.
- `MAX_PLAYBACK_STREAMS` limits playback separately from live streams. A limit
  returns `429` and the playback status line shows the safe message.
- TODO(spec): the playback URL's `channel` parameter was confirmed on one device
  with channel `1` only. The UI passes the 1-based channel number. A playback
  URL for the `Extra1`/sub stream is not known and is not attempted.

Backend behavior:

- Each request creates one CGI client with `httpx.DigestAuth`, a 10-second
  timeout, and redirects disabled. Credentials come from `DVR_HOST`,
  `DVR_USER`, and `DVR_PASS`.
- A module-level lock serializes finder sessions per DVR host, so two requests
  never run two finders on the same device at once.
- `factory.create` returns the finder object. `findFile` queries the requested
  day from `00:00:00` to `23:59:59`. `findNextFile&count=100` is repeated until
  `found < 100`, with a hard cap of 30 pages.
- `close` and `destroy` always run for the same object in a `finally` block,
  including on bad-request and transport failures.
- Finder results are cached in memory per `(host, channel, date, stream)` for
  `RECORDINGS_CACHE_SECONDS`. Unavailable results are cached too.
- The DVR's internal `FilePath` field is discarded while parsing. It is never
  returned, rendered, logged, or included in an error.
- Read-only: this phase never calls a set/write/delete/format/reboot/download
  action and never downloads media.

## Device registry (Phase 5a)

The registry is a local inventory of recorders. It is independent of the
single-device `DVR_*` settings: Live, Recordings, and Settings still use those,
and the registry never changes them.

**Binding requirement.** The app has no user login. Until an authentication
phase exists, bind uvicorn to `127.0.0.1` only (the examples in this file do).
Anyone who can reach the port can read device names/hosts, change the registry,
and trigger a connection test with the stored credentials. Do not bind to
`0.0.0.0`, do not reverse-proxy it, and do not expose it through a public SSH
tunnel. The `/devices` page is served at `/static/devices.html` as well.

### Storage

- SQLite at `DATA_DIR/dvr.db`, opened with `sqlite3` from `asyncio.to_thread`,
  foreign keys on, one short transaction per operation.
- `groups(id, name UNIQUE COLLATE NOCASE)`. Groups are flat; there is no nesting.
- `devices(id, name UNIQUE COLLATE NOCASE, group_id, host, http_port,
  rtsp_port, username, password_enc, notes, created_at, last_seen)`.
  `group_id` is optional; deleting a group moves its devices to "No group"
  (`ON DELETE SET NULL`).
- `settings(key, value)`: the default username in clear text, the default
  password encrypted, and the first-run migration marker.
- A unique `(host, http_port)` index rejects the same web endpoint twice. Two
  devices may share a host with different HTTP ports.
- `last_seen` is an ISO-8601 UTC timestamp, persisted whenever at least one of
  the two ports answered. An offline round does not clear it.

### Credentials

- One default username/password pair plus an optional per-device override. A
  device with no username uses the default; an override always wins.
- Overrides are written together: a username requires a password and a password
  requires a username. On update, omitting both keeps the current override, and
  sending `""` for both removes it (the device falls back to the defaults).
- Passwords are encrypted with Fernet (`APP_SECRET_KEY`) before they reach the
  database. The default username is stored in clear text, as specified.
- Passwords are write-only. The API returns `has_own_credentials` and
  `has_password` only. No password appears in a response, a log line, an error
  message, a CSV result, or an export.
- If a stored value cannot be decrypted (for example after changing
  `APP_SECRET_KEY`), the device falls back to the defaults and a warning is
  logged without the ciphertext or the key.

### Validation

- Names (device and group): 1–64 characters, no control characters,
  case-insensitive uniqueness.
- Host: a hostname or IPv4 literal. An IPv4 literal must be private, loopback,
  or link-local unless `ALLOW_PUBLIC_HOSTS=true`. Public literals such as
  `8.8.8.8` are rejected. A hostname is accepted as written (lowercased) and is
  not resolved at validation time.
- Ports: integers `1`–`65535`; the defaults are HTTP `80` and RTSP `554`.
- Notes: up to 1,000 characters; username: up to 64; password: up to 512.
- Duplicate `(host, http_port)` and duplicate names are rejected with `409`.

### CSV import

Header row required, case-insensitive, exactly these columns (order free):

```csv
name,group,host,http_port,rtsp_port,username,password
Lobby 1,Floor 1,192.168.1.50,80,554,,
Roof,Floor 2,192.168.1.51,8080,10554,admin,override-password
```

- Only `name` and `host` are required. Empty ports use the defaults. `group` is
  created when missing. Empty username and password mean "use the defaults".
- Limits: 500 data rows and 256 KB of UTF-8. Header problems and limit
  violations reject the whole request with `400`; a bad data row is reported as
  a row result.
- `dry_run=true` (the default) validates and returns per-row
  `{row, status: "ok"|"error"|"duplicate", message}` plus
  `counts.rows/ok/error/duplicate/imported`, and writes nothing. `row` is the
  CSV line number, so the header is line 1.
- `dry_run=false` validates first and writes only the valid rows in one
  transaction, creating missing groups. Invalid and duplicate rows are skipped.
- A password in the CSV is encrypted immediately and never echoed back, not
  even in a row message.

### Online status

- A background `asyncio` task runs one round immediately at startup and then
  every `STATUS_INTERVAL_SECONDS`; it is cancelled on shutdown.
- A round opens a plain TCP connection to the HTTP port and then to the RTSP
  port with `STATUS_TIMEOUT_SECONDS`, at most `STATUS_CONCURRENCY` devices at a
  time. It sends no HTTP request, performs no login, and reads no DVR data.
- State: `online` when both ports answer, `partial` when one does, `offline`
  when neither does, and `unknown` until the first round. `last_seen` is the last
  time a port answered (persisted); `last_checked` is the time of the last round.
- `GET /api/status` only reads the cached round and SQLite; it never starts
  network work. New devices appear as `unknown` in the next round or after a
  manual refresh.
- `POST /api/status/refresh` runs one round and returns when it finishes; a
  second request while a round is running gets `429`.
- The `/devices` page polls `GET /api/status` every 30 seconds and offers a
  **Refresh status** button. The counter is "N of M online", where `partial`
  does not count as online.

### First-run migration

If the `devices` table is empty and `DVR_HOST`, `DVR_USER`, and `DVR_PASS` are
all set, the app creates one device named "Default device" from them (password
encrypted) and records that the migration ran. It runs once; the values are
never logged. If the three variables are not set, nothing is written and the
migration is retried on a later start.

### Frontend

`/devices` reuses `styles.css` and plain JS. It provides the counter, search,
state filter, group sections with a status dot and HTTP/RTSP flags, Add/Edit
(with the password empty and labelled "Leave empty to keep"), Delete, per-device
**Test connection**, group create/rename/delete, the default-credentials form,
CSV **Preview** then **Import**, and **Refresh status**. Server strings are
inserted with `textContent` only. The search text and state filter are kept in
`localStorage` inside `try`/`catch`, so blocked storage cannot break the page.

### Connection test

`POST /api/devices/{id}/test` logs in with the effective credentials using
`DvrClient` (5-second timeout) and logs out in a `finally` block. It is the only
registry action that talks to a DVR, and it makes no read/RPC call beyond
`global.login`/`global.logout`. The result is `{"ok": true}` or
`{"ok": false, "reason": "unreachable"|"authentication failed"|"other"}` with no
stack trace and no credentials.

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

Phase 4a was additionally checked with a separate mock-transport suite that is
not shipped in the project. It covered multi-page `findNextFile` parsing, the
100-item boundary, 30-page truncation, `close`/`destroy` on success, bad-request
and page-error paths, channel-offset filtering, Main/Extra1 separation, coverage
and gap math across midnight, cache hit/expiry, per-device lock serialization,
parameter/date validation, `available: false` handling, and the absence of
credentials and `FilePath` from responses and logs.

Phase 4b was checked the same way with a separate mock-transport suite plus a
small Node test. The Python suite covered the playback URL (username/password
quoting, underscore time format, channel, 64/3600/600-second windows, midnight
rollover, transcode on/off), input validation, the playback-only limit (`429`)
and its separation from live counters, the main-limit message, lease expiry and
cleanup of playback names, startup leftover cleanup, safe failures and logs, the
server-clock fallback, storage `disks`, and regression checks for live and
recordings. It asserted that no RTSP URL, password, host, or go2rtc response
appears in a response or log. The Node test covered `daySeconds`, `clipStarts`,
and `adjacentClipStart`, including previous/next from a clip start, between
clips, before the first, after the last, empty lists, invalid input, and day
clamping.

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

## Test recordings and storage on the Ubuntu server

Run these after the live test, with uvicorn on loopback and the SSH tunnel open.
Edit `.env` with your own editor; never paste its contents into a shell history,
chat, or issue.

1. Set `DVR_HOST` to the DVR that has recordings, keep `DVR_USER` and
   `DVR_PASS`, leave `RESPONSE_CHANNEL_OFFSET=-1`, and leave
   `RECORDINGS_CACHE_SECONDS=60`. Restart uvicorn so the new settings are read.

2. Open <http://127.0.0.1:8000>. The Recordings section is below Live. The date
   input should show the DVR's current date. The storage badge should show
   **Storage ok**; compare it with the storage/HDD page in Smart PSS. A recorder
   that overwrites old data showing `UsedBytes == TotalBytes` is still `ok`.

3. Select the channel with the camera (for example channel 2), keep the date on
   a recent day that has recordings, choose **Main**, and press **Load**. The
   summary, timeline, and closed **Files** details should appear. Nothing should
   have loaded before Load was pressed.

4. In Smart PSS, search the same channel and date for Main-stream recordings.
   Compare the first start time and the last end time with the summary and the
   first/last rows under **Files**. Compare gaps of 5 minutes or more with the
   Smart PSS timeline. If the channel is off by one, verify
   `RESPONSE_CHANNEL_OFFSET` on this device before changing it.

5. Switch to **Sub**, press **Load**, and repeat the comparison for the
   `Extra1` files.

6. Click the timeline at a few positions. The red marker and the chosen-time
   text should change and **Play from here** should become enabled. Clicking the
   timeline must not start any media request by itself. Colors in the timeline
   should match the legend: Event, Timing, or Other.

7. Press **Load** again within `RECORDINGS_CACHE_SECONDS` (60 seconds by
   default). The same result should return from the in-memory cache. To compare
   uncached behavior, set `RECORDINGS_CACHE_SECONDS=0`, restart uvicorn, and
   load again.

8. Test the "unavailable" path. Stop uvicorn, set `DVR_HOST` to the DVR that
   answers `findFile` with `Error / Bad Request!`, restart, and press **Load**
   for a recent date. The section should show
   "Recordings are unavailable on this device: Error / Bad Request!" and the
   HTTP response should be `200`, not `502`. Restore the working DVR afterward.

9. Optional truncation test: use a day with more than 3,000 matching files.
   The response should have `"truncated": true` and the section should warn that
   the list was capped after 30 pages. The first 3,000 items are still used for
   the timeline and summary.

10. With the browser network panel open, inspect the `/api/recordings` JSON.
    Confirm there is no `FilePath` key and no internal DVR path value. Do not
    paste the response body into a report.

## Test playback on the Ubuntu server

Run these after the recordings test, with uvicorn on loopback, the SSH tunnel
open, and go2rtc already running. Edit `.env` with your own editor. Use stream
names only in notes; never paste a response body, a go2rtc entry, or `.env`.

1. Keep `MAX_PLAYBACK_STREAMS=2`, `PLAYBACK_MAX_SECONDS=3600`, and the rest of
   the Phase 4b defaults. Restart uvicorn so the settings are read.

2. Load recordings for one channel and date, open the **Files** details, and
   press **Play** on a clip. The viewer should appear under the timeline. The
   status line should read
   `Playing from HH:MM:SS. The DVR may start a few seconds earlier and skips gaps between clips.`
   where `HH:MM:SS` is the clip start. Confirm it actually plays video, not a
   black frame. TODO(spec): use channel 1 for the first test; other channels
   are not confirmed for the playback URL.

3. In the browser network panel, confirm `POST /api/playback/open` returned only
   `{"stream_name": "cctv-…"}`. Confirm the response contains no RTSP URL,
   username, password, DVR host, `FilePath`, or go2rtc response body. Do not
   copy the stream name into a public report.

4. While it plays, watch the network panel for about 20 seconds. A
   `POST /api/live/renew` request should include the `cctv-` playback name every
   10 seconds and report it under `renewed`.

5. Click the timeline at another time and press **Play from here**. The old
   playback stream should close and a new one should start. Confirm the old
   `cctv-` name disappears from go2rtc (using names only), for example:

   ```bash
   curl -s http://127.0.0.1:1984/api/streams | python3 -c "import json,sys; print(' '.join(sorted(json.load(sys.stdin))))"
   ```

   This prints stream names only. Never print the full go2rtc response, which
   can contain RTSP credentials.

6. Press **Previous clip** and **Next clip**. Each should choose the adjacent
   clip start and, while playback is active, restart playback there. The first
   clip should disable **Previous clip** and the last clip should disable
   **Next clip**.

7. Press **Stop**. The viewer should clear and the status line should read
   `Playback ended`. The playback name should disappear from go2rtc within a
   moment.

8. Start another playback, then close the browser tab without pressing Stop.
   The `pagehide` beacon should close the stream. If it does not, wait
   `LEASE_TIMEOUT_SECONDS` (30 seconds by default) and confirm the reaper
   removed the name. The app must still be running for the reaper to act.

9. Start three playbacks in different tabs or windows (or with the endpoint
   directly). With `MAX_PLAYBACK_STREAMS=2`, the third should return HTTP `429`
   with the playback-limit message. Close one and retry; it should succeed.
   Live streams must still be startable while two playbacks are active, up to
   their own limits.

10. Review the go2rtc stream list after each step. Only this app's `cctv-`
    names should be present, and every one should disappear after Stop, expiry,
    tab close, or server shutdown. If any remain, the shutdown/startup cleanup
    should remove them on the next start; report the count and whether the
    cleanup ran, never the stream sources.

## Test the registry

### Offline tests (no DVR contact)

```powershell
.\.venv\Scripts\python.exe -m unittest test_registry -v
node --check static/devices.js
```

```bash
.venv/bin/python -m unittest test_registry -v
node --check static/devices.js
```

The suite uses a temporary `DATA_DIR`, an httpx mock transport for the
connection test, fake TCP connectors for the status checker, and a fake Fernet
key. It covers:

- settings defaults, invalid numbers, and a missing/invalid `APP_SECRET_KEY`
  (`503` with the fixed message while `/` and `/devices` still load);
- Fernet round trip, the database file not containing a plaintext password, a
  changed key falling back to the defaults, and no password in logs or any JSON
  response;
- validation (private/loopback/link-local/public IPs, hostnames, ports, names,
  duplicates, groups);
- default vs override credentials, update semantics (omit to keep, `""` to
  clear, username-only changes), and a missing username/password pair;
- CSV dry run vs real import, row numbering, bad ports, duplicates inside the
  file and against the database, the 500-row and 256 KB limits, and new groups;
- first-run migration once, skipped when devices exist, and skipped without
  credentials;
- the three connection-test outcomes with a real client over a mock transport,
  including one logout on success and no password in the result;
- status rounds: concurrency cap, per-port timeout, online/partial/offline,
  `last_seen` persistence across a restart and across an offline round, snapshot
  without network work, one round at a time, cancellation, and a failed round
  not killing the checker;
- the app lifespan starting and cancelling the checker on shutdown;
- `415` for a non-JSON content type, `400` for a non-object body, and `429` for
  a concurrent refresh.

### On the Ubuntu server

All steps assume the app is bound to loopback and reached through the existing
SSH tunnel (`-L 8000:127.0.0.1:8000`). Use placeholder credentials in commands;
do not paste a real password into a ticket or chat.

1. Generate the key once and add the Phase 5a block to `.env` (see Setup). Never
   print `.env`; check only that the key is non-empty:
   `grep -c '^APP_SECRET_KEY=' .env` should print `1`.
2. Restart uvicorn, then confirm the database and its permissions:

   ```bash
   ls -l data/dvr.db            # expect -rw------- on Linux
   curl -sS http://127.0.0.1:8000/api/status
   ```

   The first response should be `{"counts": {"total": 0, ...}, ...}` when the
   registry is empty. A `503` means the key is missing or invalid; the rest of
   the app should still load at <http://127.0.0.1:8000>.
3. Create a group and a device, and confirm the response has no password:

   ```bash
   curl -sS -X POST http://127.0.0.1:8000/api/groups \
     -H 'Content-Type: application/json' -d '{"name":"Floor 1"}'
   curl -sS -X POST http://127.0.0.1:8000/api/devices \
     -H 'Content-Type: application/json' \
     -d '{"name":"Lobby","group_id":1,"host":"192.168.1.50"}'
   curl -sS http://127.0.0.1:8000/api/devices
   ```

   The device should show `"has_own_credentials": false` and no password field.
4. Save the default credentials and confirm only `has_password` comes back:

   ```bash
   curl -sS -X PUT http://127.0.0.1:8000/api/settings/default-credentials \
     -H 'Content-Type: application/json' \
     -d '{"username":"admin","password":"<dvr-password>"}'
   curl -sS http://127.0.0.1:8000/api/settings/default-credentials
   ```

5. Test the recorder. Expect `{"ok": true}` for a reachable device with correct
   credentials. A wrong password must return `authentication failed`, a powered
   off device `unreachable`, and anything else `other`; none may include a
   password or a stack trace:

   ```bash
   curl -sS -X POST http://127.0.0.1:8000/api/devices/1/test
   ```

6. Check encryption at rest without printing the password. The count must be
   `0` and the command prints nothing else:

   ```bash
   grep -c '<dvr-password>' data/dvr.db
   ```

7. Preview and import a CSV. Start with two rows and one deliberately bad host,
   confirm the dry run reports `ok`/`error` and writes nothing, then import:

   ```bash
   curl -sS -X POST 'http://127.0.0.1:8000/api/devices/import?dry_run=true' \
     -H 'Content-Type: application/json' \
     -d '{"csv":"name,group,host,http_port,rtsp_port,username,password\nCam 1,Floor 1,192.168.1.60,,,,\nBad,,999.1.1.1,,,,"}'
   curl -sS -X POST 'http://127.0.0.1:8000/api/devices/import?dry_run=false' \
     -H 'Content-Type: application/json' \
     -d '{"csv":"name,group,host,http_port,rtsp_port,username,password\nCam 1,Floor 1,192.168.1.60,,,,"}'
   ```

8. Refresh the status and compare with reality:

   ```bash
   curl -sS -X POST http://127.0.0.1:8000/api/status/refresh
   curl -sS http://127.0.0.1:8000/api/status
   ```

   Power off one recorder and refresh again: its state should become `offline`
   while `last_seen` stays at the previous value after the page reloads. Check
   that the background round appears every `STATUS_INTERVAL_SECONDS` in the
   page (the browser polls every 30 seconds). A second refresh during a round
   must return `429`.
9. Open <http://127.0.0.1:8000/devices> through the tunnel and exercise the page:
   the counter, search, state filter, group sections, Add/Edit/Delete,
   **Test connection**, the default-credentials form, CSV **Preview** then
   **Import**, **Refresh status**, and the header link back to the dashboard.
   Edit a device without typing a password and confirm it still reports
   "own credentials".
10. For the 48-device inventory, import the real CSV in dry-run mode first and
    check the row count/duplicates. A round is bounded by
    `ceil(devices / STATUS_CONCURRENCY)` batches of two short connects each, so
    the worst case with the defaults is about 20 seconds for 48 devices; normal
    LAN connects finish much faster. Raise `STATUS_TIMEOUT_SECONDS` only after
    measuring a real slow device.

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

### `POST /api/playback/open`

Registers one recorded-playback stream and starts its lease. The body is:

```json
{
  "channel": 2,
  "start": "2026-10-27 11:00:00",
  "seconds": 600
}
```

- `channel` must be between `1` and `MAX_CHANNEL`.
- `start` must use `YYYY-MM-DD HH:MM:SS`, must be a real date and time, and must
  not be later than the DVR clock. If the DVR clock cannot be read, the server
  clock is used and the rejection says so.
- `seconds` defaults to `600` and must be between `30` and
  `PLAYBACK_MAX_SECONDS`.

The response contains only the opaque stream name:

```json
{"stream_name": "cctv-<random>"}
```

The playback RTSP URL is built server-side and sent only to go2rtc. It is never
returned or logged. Times are DVR-local and are not timezone-converted.
Playback uses the main stream. `MAX_PLAYBACK_STREAMS` applies to playback only;
live counters are unchanged.

Status codes:

- `400` — bad JSON or body shape, channel outside `1..MAX_CHANNEL`, malformed or
  impossible start, `seconds` outside the allowed range, or a start later than
  the DVR clock.
- `429` — `MAX_PLAYBACK_STREAMS` reached.
- `502` — DVR clock read, network, or go2rtc failure; the message is short and
  safe.
- `503` — playback is not configured, or the DVR returned an unusable clock.

TODO(spec): the playback `channel` parameter was confirmed on one device with
channel `1` only. A sub/extra-stream playback URL is not known.

### `GET /api/recordings/time`

Reads the DVR clock through the CGI API. The date input uses this so "today"
means the DVR's day, not the server's:

```json
{
  "device_time": "2026-10-27 12:34:56",
  "source": "device"
}
```

If the clock read fails after authentication, the server clock is used and
`source` is `"server"`; the UI shows that fallback. A configuration problem
returns `503`; a network or authentication failure returns `502`.

### `GET /api/recordings?channel=<n>&date=YYYY-MM-DD&stream=main|sub`

Reads one channel/stream/day through the DVR CGI API. `channel` must be between
`1` and `MAX_CHANNEL`; `stream` defaults to `main`. All times are the DVR's naive
local time; no timezone conversion is done.

Successful response:

```json
{
  "available": true,
  "device_time": "2026-10-27 12:34:56",
  "device_time_source": "device",
  "date": "2026-10-27",
  "channel": 2,
  "stream": "main",
  "files": [
    {
      "start": "2026-10-27 00:00:00",
      "end": "2026-10-27 01:00:00",
      "type": "dav",
      "flags": ["Timing"],
      "length_bytes": 123456
    }
  ],
  "coverage_seconds": 3600,
  "coverage_percent": 4.2,
  "last_end": "2026-10-27 01:00:00",
  "gaps": [
    {
      "start": "2026-10-27 01:00:00",
      "end": "2026-10-27 23:59:59"
    }
  ],
  "truncated": false
}
```

`files` is filtered to the requested channel and `VideoStream` (`main` → `Main`,
`sub` → `Extra1`), sorted by start, and contains no `FilePath`. Coverage merges
and clamps intervals to the day; gaps shorter than 5 minutes are omitted.
`truncated` becomes `true` after 30 pages of 100 files.

A DVR that rejects `findFile` returns `200`:

```json
{
  "available": false,
  "reason": "Error / Bad Request!",
  "device_time": "2026-10-27 12:34:56",
  "device_time_source": "device",
  "date": "2026-10-27",
  "channel": 2,
  "stream": "main"
}
```

Status codes:

- `400` — missing or non-numeric channel, channel outside `1..MAX_CHANNEL`,
  malformed or impossible date, unsupported stream, a date later than the DVR
  date, or a date more than 90 days before the DVR date.
- `200` — recordings listed, or `available: false` for a rejected finder.
- `502` — network or authentication failure.
- `503` — missing CGI configuration or an unusable CGI response.

One finder session runs per DVR host at a time. Results are cached for
`RECORDINGS_CACHE_SECONDS` keyed by host, channel, date, and stream.

### `GET /api/storage`

Reads the CGI storage device information and returns only a small health summary:

```json
{"health": "ok", "disks": 4, "errors": 0}
```

- `ok` — every reported disk is `State=Success` and no detail `IsError=true`.
- `problem` — a disk has another state, or a detail reports an error.
- `unknown` — no disks were reported or the storage call failed.

`disks` counts `list.info[]` entries. It was renamed from `partitions` because
the measured device reports four entries while having four disks; the health
logic is unchanged.

`UsedBytes == TotalBytes` is deliberately ignored: a recorder that overwrites old
data can report that with healthy storage. A failed call is still HTTP `200`
with `health: "unknown"`; only a configuration problem returns `503`.

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

### Registry API (Phase 5a)

All registry endpoints live under `/api` and are disabled as a group when
`APP_SECRET_KEY` is missing or invalid: they return `503` with
`{"error": "Device registry is not configured. Set a valid APP_SECRET_KEY in .env."}`.
The dashboard, Live, Recordings, and Settings endpoints keep working in that
state.

Request bodies must be sent as `application/json`; any other content type gets
`415`, and a body that is not a JSON object gets `400`. Validation failures,
unknown `group_id`, and bad query values get `400`. Missing records get `404`,
duplicate names or `(host, http_port)` pairs get `409`, and a second status
refresh during a running round gets `429`. Every error body is `{"error": ...}`.
No response contains a password, a ciphertext, the encryption key, or a stack
trace.

#### `GET /api/groups`

```json
{"groups": [{"id": 1, "name": "Floor 1", "device_count": 3}]}
```

Groups are flat and sorted by name. `device_count` counts devices in the group.

#### `POST /api/groups`

Body: `{"name": "Floor 1"}`. Returns `201` with `{"group": {...}}` where the
group has `id`, `name`, and `device_count` (`0`).

#### `PUT /api/groups/{id}`

Body: `{"name": "Floor 2"}`. Returns `200` with the renamed group. Names are
1–64 characters and unique case-insensitively.

#### `DELETE /api/groups/{id}`

Returns `{"deleted": true}`. Devices in the group are moved to "No group"; they
are not deleted.

#### `GET /api/devices?group_id=<n|none>&q=<text>`

Returns `{"devices": [...]}`. Without filters it returns every device. `group_id`
filters to one group, `group_id=none` returns devices without a group, and `q`
(case-insensitive, up to 100 characters) matches the name, host, notes, or group
name. Each device is:

```json
{
  "id": 1,
  "name": "Lobby",
  "group_id": 1,
  "group": "Floor 1",
  "host": "192.168.1.50",
  "http_port": 80,
  "rtsp_port": 554,
  "has_own_credentials": true,
  "notes": "front door"
}
```

`has_own_credentials` says whether a per-device username/password override is
stored; the values themselves are never returned. A device without an override
uses the default credentials.

#### `POST /api/devices`

Returns `201` with `{"device": {...}}` in the same shape as the list. Body:

```json
{
  "name": "Lobby",
  "group_id": 1,
  "host": "192.168.1.50",
  "http_port": 80,
  "rtsp_port": 554,
  "notes": "front door",
  "username": "admin",
  "password": "<dvr-password>"
}
```

Only `name` and `host` are required. `group_id` is optional (`null` or omitted
means no group; an unknown id is `400`). `http_port` defaults to `80` and
`rtsp_port` to `554`; both must be integers 1–65535. `username` and `password`
must be supplied together, or both omitted, to create an override. `notes` is
optional (up to 1,000 characters).

#### `PUT /api/devices/{id}`

Partial update with any of the create fields. Omitted fields keep their current
value. Omitting `password` keeps the stored override; sending `"username": ""`
and `"password": ""` together removes the override and falls back to the
defaults. Sending a new username without a password (or the reverse) on a device
that has no override is `400`. Returns `200` with the updated device.

#### `DELETE /api/devices/{id}`

Returns `{"deleted": true}`. The record and its encrypted override are removed.
The status list is built from the database, so the device disappears from
`GET /api/status` immediately; its cached result is discarded on the next round.

#### `GET /api/settings/default-credentials`

```json
{"username": "admin", "has_password": true}
```

The username is returned in clear text (it is stored that way); the password is
never returned.

#### `PUT /api/settings/default-credentials`

Body: `{"username": "admin", "password": "<dvr-password>"}`. Both are required
to set a pair. Returns `200` with the same shape as the `GET`. The password is
encrypted before storage and cannot be read back through the API.

#### `POST /api/devices/import?dry_run=true|false`

Body: `{"csv": "name,group,...\n..."}`. `dry_run` defaults to `true`; any value
other than `true` or `false` (case-insensitive) is `400`. The CSV format and
limits are described in
[CSV import](#csv-import). The response is:

```json
{
  "dry_run": true,
  "counts": {"rows": 2, "ok": 1, "error": 1, "duplicate": 0, "imported": 0},
  "rows": [
    {"row": 2, "status": "ok", "message": "Ready to add."},
    {"row": 3, "status": "error", "message": "host is not a valid IPv4 address."}
  ]
}
```

`row` is the CSV line number (the header is line 1). A dry run writes nothing
and imports nothing. A real import (`dry_run=false`) writes only `ok` rows in
one transaction and reports `counts.imported`; invalid and duplicate rows are
skipped. Header errors and size/row limits reject the whole request with `400`.
No row message contains a password.

#### `POST /api/devices/{id}/test`

Logs in and out once with the device's effective credentials and returns `200`:

```json
{"ok": true}
```

or

```json
{"ok": false, "reason": "unreachable"}
```

`reason` is `unreachable` (no answer / timeout), `authentication failed` (the
DVR rejected the credentials, or the device has no usable credentials), or
`other` (an unexpected response, such as a non-JSON page). An unknown device is
`404`. This is the only registry action that contacts a DVR; it makes no read
call beyond `global.login`/`global.logout`.

#### `GET /api/status`

Returns the cached status round. It never opens a socket:

```json
{
  "counts": {
    "total": 3, "online": 1, "partial": 1, "offline": 1, "unknown": 0
  },
  "checked_at": "2026-10-27T04:57:04+00:00",
  "devices": [
    {
      "id": 1,
      "name": "Lobby",
      "group": "Floor 1",
      "host": "192.168.1.50",
      "http_port": 80,
      "rtsp_port": 554,
      "http_ok": true,
      "rtsp_ok": false,
      "state": "partial",
      "last_checked": "2026-10-27T04:57:02+00:00",
      "last_seen": "2026-10-27T04:57:02+00:00"
    }
  ]
}
```

`state` is `online` (both ports), `partial` (one port), `offline` (neither), or
`unknown` before the first round. `checked_at` is the time the response was
produced, so before the first round all devices are still `unknown`.
`last_seen` is the last time a port answered and persists across restarts;
`last_checked` is the last round's time for that device and is `null` before the
first round.

#### `POST /api/status/refresh`

Runs one status round and returns the same shape when it finishes. A second
request while a round is still running returns `429`. This is the only registry
endpoint besides the connection test that performs network work.

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
- The CGI client uses `httpx.DigestAuth`, a 10-second timeout, and no redirects.
  Logs contain fixed action names, status codes, and safe messages only; request
  URLs, credentials, response bodies, and `FilePath` values are never logged.
- The recordings parser drops the DVR's internal `FilePath` before any result is
  cached or returned. Finder errors return a short first line with URLs and
  `FilePath` text hidden; `FilePath` never reaches the browser.
- `close` and `destroy` always run for a created finder object in a `finally`
  block. The finder is capped at 30 pages; no media is downloaded.
- The storage endpoint returns only `health`, disk count, and error count.
  It does not return serial numbers, model strings, or raw storage records.
- Playback uses the same opaque `cctv-` names and cleanup rules as live. The
  playback RTSP URL with the requested start/end times and DVR credentials is
  built server-side and sent only to go2rtc. The browser receives only the
  stream name. Request times are non-sensitive, but the full URL is still never
  returned or logged.
- `/api/playback/open` returns no DVR clock value, file path, or go2rtc
  response. A failed registration is logged with the exception type and a short
  safe message.
- The recordings cache holds file times, types, flags, and sizes only. It is
  in memory, keyed by host/channel/date/stream, and expires after
  `RECORDINGS_CACHE_SECONDS`.
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

Phase 5a registry:

- **The app has no user login.** Anyone who can reach the port can read the
  device inventory, change it, and trigger a connection test with the stored
  credentials. Bind uvicorn to `127.0.0.1` until an authentication phase exists;
  do not bind `0.0.0.0`, reverse-proxy it, or publish it through a public tunnel.
- DVR passwords for registry devices are encrypted with Fernet before they reach
  SQLite. The only plaintext copy is the process environment / `.env`, which is
  ignored by `.gitignore`. Never commit `data/`, paste `dvr.db`, or log
  `APP_SECRET_KEY`.
- The default username is stored in clear text by design; only the password is
  encrypted. Treat the database as sensitive even so.
- Passwords are write-only at the API boundary: only `has_own_credentials` and
  `has_password` are returned. No password, ciphertext, or key appears in a
  response, log line, error message, CSV preview/result, or export. Request
  bodies are not logged.
- Credential decryption errors are logged by exception type only and fall back
  to the default credentials. Changing `APP_SECRET_KEY` makes every stored
  password unreadable; re-enter them.
- The status checker opens plain TCP connections only. It sends no HTTP request
  and performs no login, so a port being open is the only signal.
- The connection test reuses `DvrClient.login()`/`logout()`. Its result is one of
  three fixed reasons; exception text, passwords, and stack traces never reach
  the browser.
- On POSIX, `data/dvr.db` is chmod `600` after creation (best effort; the
  failure is ignored on Windows). The directory itself is created with the
  process umask, so keep `DATA_DIR` on a volume only the service account can
  read.
- CSV import accepts passwords in the request body and encrypts them
  immediately. The body is not stored, echoed, or included in row results.
- Registry devices are inventory records only in this phase. They are not
  connected to Live, Recordings, or Settings, and the checker does not read
  device identity, channels, or footage.

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
19. **No live DVR/go2rtc/CGI contact in this phase.** All verification used mock
    HTTP transports. The real RTSP/go2rtc path and the real `findFile`/storage
    paths are unconfirmed until the server test steps above are run.
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
26. **Real CGI finder behaviour.** The paging loop, `found=<k>` semantics, the
    100-item boundary, the 30-page truncation, and the `close`/`destroy`
    responses were tested only with mock transports. Some devices reject
    `findFile` with `Error / Bad Request!` even though login works; the cause is
    unknown and is reported as `available: false`.
27. **Channel offset on other devices.** `RESPONSE_CHANNEL_OFFSET=-1` was
    measured on one device. Confirm the mapping on each recorder before changing
    the setting; a wrong value can show an empty day.
28. **DVR clock and time zones.** Times are treated as naive DVR-local values.
    Timezone changes, daylight-saving transitions, and a drifting DVR clock are
    not converted or corrected. The server-date fallback is labelled in the API
    and UI but may be a different day near midnight.
29. **Storage info shape.** Health parsing assumes
    `list.info[N].State=Success` and `list.info[N].Detail[M].IsError=...`.
    Other firmware may use different keys, return no disk entries, or expose a
    different error flag. `UsedBytes` and `TotalBytes` are intentionally ignored.
30. **CGI authentication details.** Digest authentication, 401/403 handling, and
    redirect rejection were tested with mocks only. A device that uses a
    non-standard login page or redirect may not behave as expected.
31. **Cache and lock scope.** The finder lock and cache are per process. Running
    multiple uvicorn workers would give each worker its own lock and cache, so
    run one worker for this phase.
32. **Truncation count.** The 3,000-file cap is 30 pages × 100. If a device
    returns duplicate items or fewer items than `found`, the actual number of
    usable files can differ.
33. **Midnight-crossing files.** The parser keeps files that cross the day
    boundary and clamps coverage/gaps to `00:00:00`-`23:59:59`. Real devices may
    return different start/end conventions; this was tested with mocks only.
34. **Unavailable reason text.** The reason is the first response line, capped at
    60 characters, with URLs and `FilePath` text hidden. The exact wording from
    other devices is unknown.
35. **Real playback path.** The playback RTSP URL was measured externally, but
    this app's URL construction, go2rtc registration, MSE playback, and lease
    lifecycle were tested only with mock transports and Node here. The server
    steps above are the real test. In particular, whether go2rtc starts playback
    at the requested time, how long ffmpeg takes to produce the first frame, and
    real CPU use for two concurrent playbacks are unconfirmed.
36. **Playback channel parameter.** Only channel `1` was measured for
    `/cam/playback`. Other channels are TODO(spec); the UI passes the 1-based
    channel number unchanged. If playback fails on another channel, test channel
    1 first before changing code.
37. **Sub / Extra1 playback.** A playback URL for the sub or extra stream was
    not measured. Playback always uses the main stream, even when the clip list
    was loaded with `Sub`, at the cost of more transcoding CPU.
38. **Playback timing and gaps.** The measured device starts a few seconds
    before `starttime`, skips gaps between clips, and ends at the last clip end.
    The exact early-start amount, behavior when `starttime` falls in a large gap,
    and behavior when the requested window has no recording are unconfirmed.
    There is no fallback to find the nearest clip server-side.
39. **Playback end detection.** The app shows "Playback ended" on Stop, lease
    expiry, or a failed renewal. It does not detect the natural end of the
    requested window; the iframe may stay open after the DVR has no more video.
    The browser may show a black frame instead. A long `PLAYBACK_MAX_SECONDS`
    does not extend the lease.
40. **Playback navigation while a clip list is truncated.** Previous/next use
    only the files loaded in the browser (up to 3,000). They cannot cross a day
    boundary or include files omitted by truncation. The helpers clamp
    out-of-day times and were tested in Node, not against real DVR data.
41. **Playback limits and clocks.** `MAX_PLAYBACK_STREAMS` is count-based and
    independent of CPU. The server-clock fallback compares against the server's
    naive local time; if the server and DVR are in different time zones, the
    400 check can reject or accept the wrong request near the clock boundary.
42. **`disks` count meaning.** The count is the number of `list.info[]` entries,
    renamed from `partitions` on the measured device. Firmware that reports one
    entry per volume or per physical disk may show a different number; health is
    still based on the same state/error fields.

Phase 5a registry:

43. **TCP status versus real service health.** `online`/`partial` reflect a TCP
    connect to the configured HTTP and RTSP ports only. A recorder whose web
    service is broken but whose port is open still counts, and a firewall that
    accepts then drops may change the state. This was tested with fake sockets
    and one real device at startup; compare with the actual recorders during the
    server test.
44. **Real inventory scale.** CSV import and the 500-row/256 KB limits were
    tested with generated rows and a 3-row demo, and the 30-second page polling
    was not observed with 48 devices. The default round for 48 devices is
    estimated at up to about 20 seconds, but real LAN timing is unconfirmed.
45. **Fernet key rotation.** There is no rotation tool. Changing the key
    invalidates stored passwords (they fall back to the defaults with a warning)
    and the defaults must be re-entered. This path was tested only by swapping
    keys in a temp database, not on the server.
46. **File permissions on Linux.** The chmod `600` call is best effort and was
    not exercised on the Ubuntu server in this session; confirm with
    `ls -l data/dvr.db` there.
47. **Registry UI verification.** The page was exercised in a local browser
    against a demo server with fake hosts (add/edit/delete, groups, CSV
    preview/import, status refresh, search/filter, localStorage restore, header
    links, and the 503 banner). It was not tested with real recorders, a real
    48-device list, a blocked `localStorage`, or a mobile device.
48. **Connection-test classification on real firmware.** The three outcomes were
    tested with httpx mock transports plus a refused loopback port. How a real
    Dahua answers a wrong password, an HTTP error page, or a slow challenge was
    not observed here. The classification uses only safe exception text, so an
    unusual message may land in `other`.
49. **Refresh serialization.** `429` for a concurrent refresh and the background
    task's cancellation were tested with fakes. Browser timing between the
    30-second poll and a running round on the server is unconfirmed.
50. **First-run migration against the real `.env`.** Migration was tested with
    patched `DVR_*` values. On a first start with the real `.env`, confirm that
    exactly one "Default device" appears and that the password is not in
    `data/dvr.db` in clear text.
51. **Single process only.** The registry and status checker use per-process
    state. Running multiple uvicorn workers would run duplicate rounds and could
    race on SQLite writes; use one worker in this phase.

## Testing

- Unit/static checks: `python -W error::RuntimeWarning -m unittest discover -p "test_*.py"`
- Pyflakes (all .py): `python -m pyflakes *.py` (or use test_static_checks)
- JS syntax: `node -c static/app.js && node -c static/devices.js && node -c static/device_selector.js`
- JS unit: `node test_device_selector.js`
- Dev deps: `pip install -r requirements-dev.txt`

## Out of scope for Phase 5a

Linking a registry device to Live, Recordings, or Settings; per-device
credentials for those pages; application authentication or user accounts;
multi-worker/HA deployment; registry export/import beyond the CSV described
above; credential rotation tooling; nested groups; network discovery or
scanning; scheduled tasks other than the status round; recording download or
export; configuration writes; reboot/factory-reset/format; PTZ; playback
pause/speed/seek; and authenticated proxying for go2rtc.

