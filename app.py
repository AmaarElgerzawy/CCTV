import asyncio
import logging
import re
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cgi_client import (
    CgiAuthError,
    CgiConfigError,
    CgiError,
    CgiNetworkError,
    DvrCgi,
    day_summary,
)
from dvr_client import (
    CONFIG_NAMES,
    DvrAuthError,
    DvrClient,
    DvrConfigError,
    DvrError,
    DvrLogoutError,
    DvrRpcError,
    DvrSessionExpired,
    mask_sensitive,
)
from registry import (
    REGISTRY_DISABLED_MESSAGE,
    DeviceRegistry,
    RegistryConfigError,
    RegistryError,
    RegistrySettings,
    StatusChecker,
    StatusRoundBusy,
)
from stream_manager import (
    DEFAULT_PLAYBACK_SECONDS,
    PLAYBACK_MIN_SECONDS,
    StreamConfigError,
    StreamError,
    StreamLimitError,
    StreamManager,
    is_stream_name,
    max_channel_from_env,
    public_go2rtc_url,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

logger = logging.getLogger(__name__)


async def _lease_reaper(manager: StreamManager) -> None:
    """Unregister expired leases on a fixed tick until cancelled."""
    while True:
        await asyncio.sleep(manager.lease_check_interval)
        try:
            expired = await asyncio.to_thread(manager.expire_leases)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep ticking on unexpected failures
            logger.warning(
                "Lease expiry check failed: %s", type(exc).__name__
            )
            continue
        if expired:
            logger.info(
                "Stream lease expired: %s", ", ".join(expired)
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the managers, clean leftovers, and clean up on shutdown."""
    manager: StreamManager | None = None
    reaper: asyncio.Task[None] | None = None
    registry: DeviceRegistry | None = None
    checker: StatusChecker | None = None

    try:
        registry_settings = RegistrySettings.from_env(BASE_DIR)
        registry = DeviceRegistry(registry_settings)
        checker = StatusChecker(
            registry,
            interval=registry_settings.status_interval,
            concurrency=registry_settings.status_concurrency,
            timeout=registry_settings.status_timeout,
        )
    except RegistryConfigError as exc:
        logger.warning("Device registry is not configured: %s", exc)
    except Exception as exc:  # defensive: the read-only app keeps working
        logger.error(
            "Device registry could not start: %s", type(exc).__name__
        )

    app.state.registry = registry
    app.state.status_checker = checker

    try:
        manager = StreamManager.from_env()
    except StreamConfigError as exc:
        logger.warning("Live view is not configured: %s", exc)
    except Exception as exc:  # defensive: keep the read-only app available
        logger.error("Live view could not start: %s", type(exc).__name__)

    app.state.stream_manager = manager

    if manager is not None:
        try:
            manager.cleanup_leftovers()
        except Exception as exc:  # defensive
            logger.warning(
                "Live stream startup cleanup failed: %s", type(exc).__name__
            )
        reaper = asyncio.create_task(_lease_reaper(manager))

    if checker is not None:
        checker.start()

    try:
        yield
    finally:
        if checker is not None:
            with suppress(asyncio.CancelledError):
                await checker.stop()
        if reaper is not None:
            reaper.cancel()
            with suppress(asyncio.CancelledError):
                await reaper
        if manager is not None:
            try:
                manager.cleanup_created()
            except Exception as exc:  # defensive
                logger.warning(
                    "Live stream shutdown cleanup failed: %s",
                    type(exc).__name__,
                )
            finally:
                manager.close()


app = FastAPI(title="DVR Web", version="0.7.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@contextmanager
def _open_dvr() -> Iterator[DvrClient]:
    """Open one client, log in, and always log out and close it.

    A logout failure is logged with its safe diagnostic and does not replace
    the error or result from the body.
    """
    client = DvrClient.from_env()
    try:
        client.login()
        yield client
    finally:
        try:
            client.logout()
        except DvrLogoutError as exc:
            logger.warning("DVR logout failed: %s", exc)
        finally:
            client.close()


def _dvr_error(exc: DvrError) -> JSONResponse:
    if isinstance(exc, DvrConfigError):
        logger.error("DVR is not configured: %s", exc)
        return _error(
            503,
            "DVR is not configured. Set DVR_HOST, DVR_USER and DVR_PASS in .env.",
        )
    if isinstance(exc, DvrAuthError):
        logger.warning("DVR login was rejected")
        return _error(502, "The DVR rejected the login.")
    if isinstance(exc, DvrSessionExpired):
        logger.warning("DVR session expired: %s", exc)
        return _error(502, "The DVR session is no longer valid. Try again.")
    if isinstance(exc, DvrRpcError):
        logger.warning("DVR RPC failed: %s", exc)
        return _error(502, "The DVR did not return the requested information.")
    logger.warning("DVR request failed: %s", exc)
    return _error(502, "Could not communicate with the DVR.")


def _read_device(
    reader: Callable[[DvrClient], dict[str, Any]],
) -> JSONResponse:
    """Run one read-only operation on a fresh, always-closed session."""
    try:
        with _open_dvr() as client:
            return JSONResponse(reader(client))
    except DvrError as exc:
        return _dvr_error(exc)
    except Exception as exc:  # pragma: no cover - defensive
        # Log the type only; exception strings can contain upstream data.
        logger.error("Unexpected error while reading DVR data: %s", type(exc).__name__)
        return _error(500, "Unexpected server error.")


_DATE_PARAM = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_START_PARAM = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_DIGITS = re.compile(r"^[0-9]+$")


def _open_cgi() -> DvrCgi:
    """Create one CGI client. Tests replace this factory."""
    return DvrCgi.from_env()


def _server_clock() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def _device_clock(client: DvrCgi) -> tuple[str, str]:
    """Return the DVR clock, falling back to the server date and saying so."""
    try:
        return await client.get_device_time(), "device"
    except CgiConfigError:
        raise
    except CgiError as exc:
        logger.warning("DVR clock unavailable: %s", exc)
        return _server_clock(), "server"


def _day_from_clock(value: str) -> date_cls:
    try:
        return date_cls.fromisoformat(value[:10])
    except ValueError:
        return date_cls.today()


def _cgi_error(exc: CgiError) -> JSONResponse:
    if isinstance(exc, CgiConfigError):
        logger.error("DVR CGI is not configured: %s", exc)
        return _error(
            503,
            "DVR is not configured. Set DVR_HOST, DVR_USER and DVR_PASS in .env.",
        )
    if isinstance(exc, CgiAuthError):
        logger.warning("DVR CGI authentication was rejected")
        return _error(502, "The DVR rejected the CGI login.")
    if isinstance(exc, CgiNetworkError):
        logger.warning("DVR CGI connection failed: %s", exc)
        return _error(502, "Could not read from the DVR over CGI.")
    logger.warning("DVR CGI response was not usable: %s", exc)
    return _error(503, "The DVR returned an unexpected CGI response.")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/devices")
def devices_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "devices.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/device/info")
def device_info() -> JSONResponse:
    return _read_device(
        lambda client: {"info": mask_sensitive(client.get_device_info())}
    )


@app.get("/api/channels")
def channels() -> JSONResponse:
    return _read_device(
        lambda client: {"channels": mask_sensitive(client.get_channels())}
    )


def _live_manager(request: Request) -> StreamManager:
    manager = getattr(request.app.state, "stream_manager", None)
    if manager is None:
        raise StreamConfigError(
            "Live view is not configured. Set DVR_HOST, DVR_USER, DVR_PASS "
            "and the go2rtc settings in .env."
        )
    return manager


@app.get("/api/config")
def live_config() -> JSONResponse:
    """Return only the browser-facing go2rtc URL and the channel cap."""
    try:
        url = public_go2rtc_url()
        max_channel = max_channel_from_env()
    except StreamConfigError as exc:
        logger.warning("Live view is not configured: %s", exc)
        return _error(503, str(exc))
    return JSONResponse(
        {"public_go2rtc_url": url, "max_channel": max_channel}
    )


@app.post("/api/live/{channel}/open")
def open_live(
    channel: int,
    request: Request,
    quality: str = "sub",
) -> JSONResponse:
    """Register one on-demand stream and return only its opaque name."""
    if quality not in {"sub", "main"}:
        return _error(400, "Quality must be sub or main.")

    try:
        manager = _live_manager(request)
        if not 1 <= channel <= manager.max_channel:
            return _error(
                400,
                f"Channel must be between 1 and {manager.max_channel}.",
            )
        stream_name = manager.open(channel, quality)
    except StreamLimitError as exc:
        return _error(429, str(exc))
    except StreamConfigError as exc:
        logger.warning("Live view is not configured: %s", exc)
        return _error(503, str(exc))
    except StreamError as exc:
        logger.warning("Live stream registration failed: %s", exc)
        return _error(502, "Could not start the live stream.")
    except Exception as exc:  # defensive: never expose an upstream response
        logger.error("Unexpected live-stream error: %s", type(exc).__name__)
        return _error(500, "Unexpected server error.")

    return JSONResponse({"stream_name": stream_name})


@app.post("/api/live/renew")
async def renew_live(request: Request) -> JSONResponse:
    """Extend every known, unexpired lease in one batch."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "Body must be a JSON object with a streams list.")

    if not isinstance(body, dict):
        return _error(400, "Body must be a JSON object with a streams list.")
    streams = body.get("streams")
    if not isinstance(streams, list) or not 1 <= len(streams) <= 16:
        return _error(
            400, "streams must be a list of 1 to 16 stream names."
        )
    if any(
        not isinstance(name, str) or not is_stream_name(name)
        for name in streams
    ):
        return _error(400, "Every stream name must use the cctv- format.")

    try:
        renewed, expired = _live_manager(request).renew(streams)
    except StreamConfigError as exc:
        logger.warning("Live view is not configured: %s", exc)
        return _error(503, str(exc))
    except Exception as exc:  # defensive: never expose an upstream response
        logger.error("Unexpected lease-renewal error: %s", type(exc).__name__)
        return _error(500, "Unexpected server error.")

    return JSONResponse({"renewed": renewed, "expired": expired})


@app.post("/api/live/{stream_name}/close")
def close_live(stream_name: str, request: Request) -> JSONResponse:
    """Delete one stream. Safe to call twice."""
    if not is_stream_name(stream_name):
        return _error(400, "Invalid stream name.")

    try:
        _live_manager(request).close_stream(stream_name)
    except StreamConfigError as exc:
        logger.warning("Live view is not configured: %s", exc)
        return _error(503, str(exc))
    except StreamError as exc:
        logger.warning("Live stream removal failed: %s", exc)
        return _error(502, "Could not stop the live stream.")
    except Exception as exc:  # defensive: never expose an upstream response
        logger.error("Unexpected live-stream error: %s", type(exc).__name__)
        return _error(500, "Unexpected server error.")

    return JSONResponse({"closed": True})


@app.post("/api/playback/open")
async def open_playback(request: Request) -> JSONResponse:
    """Register one read-only recorded-playback stream by its start time."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "Body must be a JSON object.")

    if not isinstance(body, dict):
        return _error(400, "Body must be a JSON object.")

    channel = body.get("channel")
    start = body.get("start")
    seconds = body.get("seconds", DEFAULT_PLAYBACK_SECONDS)

    if not isinstance(channel, int) or isinstance(channel, bool):
        return _error(400, "channel must be a channel number.")
    if not isinstance(start, str) or not _START_PARAM.fullmatch(start):
        return _error(400, "start must use YYYY-MM-DD HH:MM:SS.")
    try:
        start_time = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return _error(400, "start is not a valid date and time.")
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        return _error(400, "seconds must be a whole number.")

    try:
        manager = _live_manager(request)
    except StreamConfigError as exc:
        logger.warning("Playback is not configured: %s", exc)
        return _error(503, str(exc))

    if not 1 <= channel <= manager.max_channel:
        return _error(
            400, f"Channel must be between 1 and {manager.max_channel}."
        )
    if not PLAYBACK_MIN_SECONDS <= seconds <= manager.max_playback_seconds:
        return _error(
            400,
            f"seconds must be between {PLAYBACK_MIN_SECONDS} and "
            f"{manager.max_playback_seconds}.",
        )

    try:
        async with _open_cgi() as client:
            device_time, source = await _device_clock(client)
    except CgiError as exc:
        return _cgi_error(exc)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Unexpected CGI clock error: %s", type(exc).__name__)
        return _error(500, "Unexpected server error.")

    try:
        device_now = datetime.strptime(device_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return _error(503, "The DVR returned an unusable clock.")
    if start_time > device_now:
        note = " (server clock used)" if source == "server" else ""
        return _error(400, f"start is later than the DVR time{note}.")

    try:
        stream_name = await asyncio.to_thread(
            manager.open_playback, channel, start_time, seconds
        )
    except StreamLimitError as exc:
        return _error(429, str(exc))
    except StreamConfigError as exc:
        logger.warning("Playback is not configured: %s", exc)
        return _error(503, str(exc))
    except StreamError as exc:
        logger.warning("Playback stream registration failed: %s", exc)
        return _error(502, "Could not start playback.")
    except Exception as exc:  # defensive: never expose an upstream response
        logger.error("Unexpected playback error: %s", type(exc).__name__)
        return _error(500, "Unexpected server error.")

    return JSONResponse({"stream_name": stream_name})


@app.get("/api/recordings/time")
async def recordings_time() -> JSONResponse:
    """Return the DVR clock, or the server date when the clock read fails."""
    try:
        async with _open_cgi() as client:
            device_time, source = await _device_clock(client)
    except CgiError as exc:
        return _cgi_error(exc)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Unexpected CGI clock error: %s", type(exc).__name__)
        return _error(500, "Unexpected server error.")
    return JSONResponse({"device_time": device_time, "source": source})


@app.get("/api/recordings")
async def recordings(
    channel: str | None = None,
    date: str | None = None,
    stream: str = "main",
) -> JSONResponse:
    """Read one channel/stream/day of recordings through the DVR CGI API."""
    if channel is None or not _DIGITS.fullmatch(channel):
        return _error(400, "channel must be a channel number.")
    if date is None or not _DATE_PARAM.fullmatch(date):
        return _error(400, "date must use YYYY-MM-DD.")
    try:
        requested = date_cls.fromisoformat(date)
    except ValueError:
        return _error(400, "date is not a valid calendar date.")
    if stream not in {"main", "sub"}:
        return _error(400, "stream must be main or sub.")

    number = int(channel)
    try:
        max_channel = max_channel_from_env()
    except StreamConfigError as exc:
        logger.warning("DVR channel cap is not configured: %s", exc)
        return _error(503, str(exc))
    if not 1 <= number <= max_channel:
        return _error(
            400, f"channel must be between 1 and {max_channel}."
        )

    try:
        async with _open_cgi() as client:
            device_time, source = await _device_clock(client)
            device_day = _day_from_clock(device_time)
            if requested > device_day:
                return _error(400, "date is later than the DVR date.")
            if (device_day - requested).days > 90:
                return _error(
                    400, "date is more than 90 days before the DVR date."
                )
            result = await client.find_files(number, date, stream)
    except CgiError as exc:
        return _cgi_error(exc)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Unexpected CGI recordings error: %s", type(exc).__name__)
        return _error(500, "Unexpected server error.")

    context = {
        "device_time": device_time,
        "device_time_source": source,
        "date": date,
        "channel": number,
        "stream": stream,
    }
    if not result["available"]:
        return JSONResponse(
            {"available": False, "reason": result["reason"], **context}
        )

    summary = day_summary(result["files"], requested)
    return JSONResponse(
        {
            "available": True,
            **context,
            "files": result["files"],
            "coverage_seconds": summary["coverage_seconds"],
            "coverage_percent": summary["coverage_percent"],
            "last_end": summary["last_end"],
            "gaps": summary["gaps"],
            "truncated": bool(result.get("truncated", False)),
        }
    )


@app.get("/api/storage")
async def storage() -> JSONResponse:
    """Return storage health; a failed call is reported as unknown."""
    try:
        async with _open_cgi() as client:
            result = await client.get_storage_health()
    except CgiConfigError as exc:
        return _cgi_error(exc)
    except CgiError as exc:
        logger.warning("Storage health unavailable: %s", exc)
        return JSONResponse(
            {"health": "unknown", "disks": 0, "errors": 0}
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(
            "Unexpected storage-health error: %s", type(exc).__name__
        )
        return JSONResponse(
            {"health": "unknown", "disks": 0, "errors": 0}
        )
    return JSONResponse(result)


@app.get("/api/config/{name}")
def config(name: str) -> JSONResponse:
    if name not in CONFIG_NAMES:
        return _error(
            400,
            "Unsupported config name. Allowed: " + ", ".join(CONFIG_NAMES) + ".",
        )

    def read(client: DvrClient) -> dict[str, Any]:
        return {"config": mask_sensitive(client.get_config(name))}

    return _read_device(read)


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


# -- device registry (Phase 5a) ------------------------------------------


def _registry(request: Request) -> DeviceRegistry:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise RegistryConfigError(REGISTRY_DISABLED_MESSAGE)
    return registry


def _status_checker(request: Request) -> StatusChecker:
    checker = getattr(request.app.state, "status_checker", None)
    if checker is None:
        raise RegistryConfigError(REGISTRY_DISABLED_MESSAGE)
    return checker


def _registry_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, RegistryConfigError):
        return _error(503, REGISTRY_DISABLED_MESSAGE)
    if isinstance(exc, RegistryError):
        return _error(exc.status_code, str(exc))
    logger.error("Unexpected registry error: %s", type(exc).__name__)
    return _error(500, "Unexpected server error.")


async def _json_body(request: Request) -> dict[str, Any]:
    media = request.headers.get("content-type", "").split(";", 1)[0]
    if media.strip().lower() != "application/json":
        raise RegistryError(
            "Content-Type must be application/json.", 415
        )
    try:
        body = await request.json()
    except Exception:
        raise RegistryError("Body must be a JSON object.", 400) from None
    if not isinstance(body, dict):
        raise RegistryError("Body must be a JSON object.", 400)
    return body


@app.get("/api/groups")
async def list_groups(request: Request) -> JSONResponse:
    try:
        groups = await asyncio.to_thread(_registry(request).list_groups)
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"groups": groups})


@app.post("/api/groups")
async def create_group(request: Request) -> JSONResponse:
    try:
        body = await _json_body(request)
        group = await asyncio.to_thread(
            _registry(request).create_group, body.get("name")
        )
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"group": group}, status_code=201)


@app.put("/api/groups/{group_id}")
async def update_group(group_id: int, request: Request) -> JSONResponse:
    try:
        body = await _json_body(request)
        group = await asyncio.to_thread(
            _registry(request).update_group, group_id, body.get("name")
        )
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"group": group})


@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: int, request: Request) -> JSONResponse:
    try:
        await asyncio.to_thread(_registry(request).delete_group, group_id)
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"deleted": True})


@app.get("/api/devices")
async def list_devices(
    request: Request,
    group_id: str | None = None,
    q: str | None = None,
) -> JSONResponse:
    number: int | None = None
    ungrouped = False
    if group_id is not None and group_id.strip():
        value = group_id.strip()
        if value.lower() in {"none", "null"}:
            ungrouped = True
        elif value.isdigit():
            number = int(value)
        else:
            return _error(400, "group_id must be a group number or 'none'.")
    if q is not None and len(q) > 100:
        return _error(400, "q must be at most 100 characters.")
    try:
        devices = await asyncio.to_thread(
            _registry(request).list_devices,
            group_id=number,
            ungrouped=ungrouped,
            query=q,
        )
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"devices": devices})


@app.post("/api/devices")
async def create_device(request: Request) -> JSONResponse:
    try:
        body = await _json_body(request)
        device = await asyncio.to_thread(
            _registry(request).create_device, body
        )
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"device": device}, status_code=201)


@app.put("/api/devices/{device_id}")
async def update_device(device_id: int, request: Request) -> JSONResponse:
    try:
        body = await _json_body(request)
        device = await asyncio.to_thread(
            _registry(request).update_device, device_id, body
        )
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"device": device})


@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: int, request: Request) -> JSONResponse:
    try:
        await asyncio.to_thread(_registry(request).delete_device, device_id)
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"deleted": True})


@app.get("/api/settings/default-credentials")
async def get_default_credentials(request: Request) -> JSONResponse:
    try:
        result = await asyncio.to_thread(
            _registry(request).get_default_credentials
        )
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse(result)


@app.put("/api/settings/default-credentials")
async def set_default_credentials(request: Request) -> JSONResponse:
    try:
        body = await _json_body(request)
        result = await asyncio.to_thread(
            _registry(request).set_default_credentials,
            body.get("username"),
            body.get("password"),
        )
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse(result)


@app.post("/api/devices/import")
async def import_devices(
    request: Request, dry_run: str = "true"
) -> JSONResponse:
    mode = dry_run.strip().lower()
    if mode not in {"true", "false"}:
        return _error(400, "dry_run must be true or false.")
    try:
        body = await _json_body(request)
        text = body.get("csv")
        if not isinstance(text, str):
            raise RegistryError("csv must be a string.")
        result = await asyncio.to_thread(
            _registry(request).import_csv,
            text,
            dry_run=mode == "true",
        )
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse(result)


@app.post("/api/devices/{device_id}/test")
async def test_device(device_id: int, request: Request) -> JSONResponse:
    try:
        result = await asyncio.to_thread(
            _registry(request).test_device, device_id
        )
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse(result)


@app.get("/api/status")
async def registry_status(request: Request) -> JSONResponse:
    try:
        result = await _status_checker(request).snapshot()
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse(result)


@app.post("/api/status/refresh")
async def refresh_status(request: Request) -> JSONResponse:
    try:
        result = await _status_checker(request).refresh()
    except StatusRoundBusy:
        return _error(429, "A status check is already running.")
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse(result)
