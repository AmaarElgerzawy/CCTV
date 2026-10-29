import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable

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
    ResolvedDevice,
    StatusChecker,
    StatusRoundBusy,
)
from stream_manager import (
    DEFAULT_PLAYBACK_SECONDS,
    PLAYBACK_MIN_SECONDS,
    StreamConfigError,
    StreamDevice,
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


try:
    _DEVICE_INFO_CACHE_SECONDS = float(os.environ.get("DEVICE_INFO_CACHE_SECONDS", "60") or 60)
except (TypeError, ValueError):
    _DEVICE_INFO_CACHE_SECONDS = 60.0
if _DEVICE_INFO_CACHE_SECONDS < 0:
    _DEVICE_INFO_CACHE_SECONDS = 60.0
_DEVICE_INFO_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_DEVICE_INFO_CACHE_LOCK = asyncio.Lock()

_RPC_LOCKS: dict[int, asyncio.Lock] = {}
_RPC_LOCKS_GUARD = asyncio.Lock()


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
    health_sched_task: asyncio.Task[None] | None = None

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
        health_sched_task = asyncio.create_task(_health_scheduler())
    except Exception:
        health_sched_task = None

    try:
        yield
    finally:
        if health_sched_task is not None:
            health_sched_task.cancel()
            with suppress(asyncio.CancelledError):
                await health_sched_task
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


def _open_dvr(host: str, username: str, password: str) -> DvrClient:
    """Open one client, log in, and always log out and close it."""
    client = DvrClient(host, username, password)
    try:
        client.login()
        return client
    except Exception:
        try:
            client.close()
        except Exception:
            pass
        raise


async def _read_device_resolved(
    request: Request, reader: Callable[[DvrClient], dict[str, Any]]
) -> JSONResponse:
    try:
        device_id = _device_id_from_query(request)
        resolved = await _resolve_device(request, device_id)
        lock = await _rpc_lock(resolved.id)
        async with lock:
            client = await asyncio.to_thread(
                lambda: _open_dvr(resolved.base_url, resolved.username, resolved.password)
            )
            try:
                result = await asyncio.to_thread(reader, client)
                return JSONResponse(result)
            finally:
                try:
                    client.logout()
                except DvrLogoutError as exc:
                    logger.warning("DVR logout failed: %s", type(exc).__name__)
                finally:
                    client.close()
    except RegistryError as exc:
        return _registry_error(exc)
    except DvrError as exc:
        return _dvr_error(exc)
    except Exception as exc:
        log_unexpected(logger, "Unexpected error while reading DVR data", exc)
        return _error(500, "Unexpected server error.")


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
        log_unexpected(logger, "Unexpected error while reading DVR data", exc)
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
        logger.warning("DVR clock unavailable: %s", type(exc).__name__)
        return _server_clock(), "server"


def _clear_device_caches(device_id: int | None = None) -> None:
    if device_id is None:
        _DEVICE_INFO_CACHE.clear()
        return
    keys = [k for k in _DEVICE_INFO_CACHE if k[0] == device_id]
    for k in keys:
        _DEVICE_INFO_CACHE.pop(k, None)


async def _rpc_lock(device_id: int) -> asyncio.Lock:
    async with _RPC_LOCKS_GUARD:
        lock = _RPC_LOCKS.get(device_id)
        if lock is None:
            lock = asyncio.Lock()
            _RPC_LOCKS[device_id] = lock
        return lock



def _stream_device(resolved: ResolvedDevice) -> StreamDevice:
    return StreamDevice(
        device_id=resolved.id,
        host=resolved.host,
        rtsp_port=resolved.rtsp_port,
        username=resolved.username,
        password=resolved.password,
    )


async def _resolve_device(
    request: Request, device_id: int | None
) -> ResolvedDevice:
    registry = _registry(request)
    try:
        return await asyncio.to_thread(registry.resolve_device, device_id)
    except RegistryError:
        raise


def _device_id_from_query(request: Request) -> int | None:
    value = request.query_params.get("device_id") or request.query_params.get(
        "device"
    )
    if value is None or value.strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        raise RegistryError("device_id must be a number.", 400)



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
async def device_info(request: Request) -> JSONResponse:
    try:
        device_id = _device_id_from_query(request)
    except RegistryError as exc:
        return _registry_error(exc)

    now = time.monotonic()
    if device_id is not None:
        cached = _DEVICE_INFO_CACHE.get(device_id)
        if cached is not None and now < cached[0]:
            return JSONResponse(cached[1])

    try:
        registry = _registry(request)
        resolved = await asyncio.to_thread(registry.resolve_device, device_id)
        lock = await _rpc_lock(resolved.id)
        async with lock:
            client = await asyncio.to_thread(
                lambda: _open_dvr(resolved.base_url, resolved.username, resolved.password)
            )
            try:
                info = await asyncio.to_thread(client.get_device_info)
                result = {"info": mask_sensitive(info)}
                if _DEVICE_INFO_CACHE_SECONDS > 0:
                    async with _DEVICE_INFO_CACHE_LOCK:
                        _DEVICE_INFO_CACHE[resolved.id] = (
                            time.monotonic() + _DEVICE_INFO_CACHE_SECONDS,
                            result,
                        )
                return JSONResponse(result)
            finally:
                try:
                    client.logout()
                except DvrLogoutError as exc:
                    logger.warning("DVR logout failed: %s", type(exc).__name__)
                finally:
                    client.close()
    except RegistryError as exc:
        return _registry_error(exc)
    except DvrError as exc:
        return _dvr_error(exc)
    except Exception as exc:
        log_unexpected(logger, "Unexpected error while reading DVR data", exc)
        return _error(500, "Unexpected server error.")


@app.get("/api/channels")
async def channels(request: Request) -> JSONResponse:
    try:
        device_id = _device_id_from_query(request)
    except RegistryError as exc:
        return _registry_error(exc)

    now = time.monotonic()
    if device_id is not None:
        cached = _DEVICE_INFO_CACHE.get(device_id)
        if cached is not None and now < cached[0]:
            pass

    try:
        registry = _registry(request)
        resolved = await asyncio.to_thread(registry.resolve_device, device_id)
        lock = await _rpc_lock(resolved.id)
        async with lock:
            client = await asyncio.to_thread(
                lambda: _open_dvr(resolved.base_url, resolved.username, resolved.password)
            )
            try:
                channels_info = await asyncio.to_thread(client.get_channels)
                result = {"channels": mask_sensitive(channels_info)}
                return JSONResponse(result)
            finally:
                try:
                    client.logout()
                except DvrLogoutError as exc:
                    logger.warning("DVR logout failed: %s", type(exc).__name__)
                finally:
                    client.close()
    except RegistryError as exc:
        return _registry_error(exc)
    except DvrError as exc:
        return _dvr_error(exc)
    except Exception as exc:
        log_unexpected(logger, "Unexpected error while reading DVR data", exc)
        return _error(500, "Unexpected server error.")



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
async def open_live(
    channel: int,
    request: Request,
    quality: str = "sub",
) -> JSONResponse:
    """Register one on-demand stream and return only its opaque name."""
    if quality not in {"sub", "main"}:
        return _error(400, "Quality must be sub or main.")

    try:
        device_id = _device_id_from_query(request)
    except RegistryError as exc:
        return _registry_error(exc)

    try:
        manager = _live_manager(request)
        if not 1 <= channel <= manager.max_channel:
            return _error(
                400,
                f"Channel must be between 1 and {manager.max_channel}.",
            )
        registry = _registry(request)
        resolved = await asyncio.to_thread(registry.resolve_device, device_id)
        stream_name = await asyncio.to_thread(
            manager.open, _stream_device(resolved), channel, quality
        )
    except RegistryError as exc:
        return _registry_error(exc)
    except StreamLimitError as exc:
        return _error(429, str(exc))
    except StreamConfigError as exc:
        logger.warning("Live view is not configured: %s", exc)
        return _error(503, str(exc))
    except StreamError as exc:
        logger.warning("Live stream registration failed: %s", exc)
        return _error(502, "Could not start the live stream.")
    except Exception as exc:  # defensive: never expose an upstream response
        log_unexpected(logger, "Unexpected live-stream error", exc)
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
        log_unexpected(logger, "Unexpected lease-renewal error", exc)
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
        log_unexpected(logger, "Unexpected live-stream error", exc)
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
    device_id = body.get("device_id")

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
    if device_id is None:
        try:
            device_id = _device_id_from_query(request)
        except RegistryError as exc:
            return _registry_error(exc)
    elif isinstance(device_id, bool) or not isinstance(device_id, int):
        try:
            device_id = _device_id_from_query(request)
        except RegistryError as exc:
            return _registry_error(exc)
    else:
        pass

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
        registry = _registry(request)
        resolved = await asyncio.to_thread(registry.resolve_device, device_id)
        async with _open_cgi() as client:
            device_time, source = await _device_clock(client)
    except RegistryError as exc:
        return _registry_error(exc)
    except CgiError as exc:
        return _cgi_error(exc)
    except Exception as exc:  # pragma: no cover - defensive
        log_unexpected(logger, "Unexpected CGI clock error", exc)
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
            manager.open_playback,
            _stream_device(resolved),
            channel,
            start_time,
            seconds,
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
        log_unexpected(logger, "Unexpected playback error", exc)
        return _error(500, "Unexpected server error.")

    return JSONResponse({"stream_name": stream_name})


@app.get("/api/recordings/time")
async def recordings_time(request: Request) -> JSONResponse:
    """Return the DVR clock, or the server date when the clock read fails."""
    try:
        device_id = _device_id_from_query(request)
    except RegistryError as exc:
        return _registry_error(exc)
    device_time = None
    source = "server"
    try:
        registry = _registry(request)
        resolved = await asyncio.to_thread(registry.resolve_device, device_id)
        from cgi_client import DvrCgi

        cgi = DvrCgi(
            resolved.base_url,
            resolved.username,
            resolved.password,
            device_id=resolved.id,
        )
        try:
            device_time, source = await _device_clock(cgi)
        finally:
            await cgi.aclose()
    except RegistryError as exc:
        return _registry_error(exc)
    except CgiError as exc:
        return _cgi_error(exc)
    except Exception as exc:  # pragma: no cover - defensive
        log_unexpected(logger, "Unexpected CGI clock error", exc)
        return _error(500, "Unexpected server error.")
    return JSONResponse({"device_time": device_time, "source": source})


@app.get("/api/recordings")
async def recordings(
    request: Request,
    channel: str | None = None,
    date: str | None = None,
    stream: str = "main",
) -> JSONResponse:
    """Read one channel/stream/day of recordings through the DVR CGI API."""
    try:
        device_id = _device_id_from_query(request)
    except RegistryError as exc:
        return _registry_error(exc)
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

    device_time = None
    source = "device"
    try:
        registry = _registry(request)
        resolved = await asyncio.to_thread(registry.resolve_device, device_id)
        from cgi_client import DvrCgi

        cgi = DvrCgi(
            resolved.base_url,
            resolved.username,
            resolved.password,
            device_id=resolved.id,
        )
        try:
            device_time, source = await _device_clock(cgi)
            device_day = _day_from_clock(device_time)
            if requested > device_day:
                return _error(400, "date is later than the DVR date.")
            if (device_day - requested).days > 90:
                return _error(
                    400, "date is more than 90 days before the DVR date."
                )
            result = await cgi.find_files(number, date, stream)
        finally:
            await cgi.aclose()
    except RegistryError as exc:
        return _registry_error(exc)
    except CgiError as exc:
        return _cgi_error(exc)
    except Exception as exc:  # pragma: no cover - defensive
        log_unexpected(logger, "Unexpected CGI recordings error", exc)
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
async def storage(request: Request) -> JSONResponse:
    """Return storage health; a failed call is reported as unknown."""
    try:
        device_id = _device_id_from_query(request)
    except RegistryError as exc:
        return _registry_error(exc)
    try:
        registry = _registry(request)
        resolved = await asyncio.to_thread(registry.resolve_device, device_id)
        from cgi_client import DvrCgi

        cgi = DvrCgi(
            resolved.base_url,
            resolved.username,
            resolved.password,
            device_id=resolved.id,
        )
        try:
            result = await cgi.get_storage_health()
        finally:
            await cgi.aclose()
    except CgiConfigError as exc:
        return _cgi_error(exc)
    except RegistryError as exc:
        return _registry_error(exc)
    except CgiError as exc:
        logger.warning("Storage health unavailable: %s", type(exc).__name__)
        return JSONResponse(
            {"health": "unknown", "disks": 0, "errors": 0}
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(
        )
        log_unexpected(logger, "Unexpected storage-health error", exc)
        return JSONResponse(
            {"health": "unknown", "disks": 0, "errors": 0}
        )
    return JSONResponse(result)


@app.get("/api/config/{name}")
async def config(name: str, request: Request) -> JSONResponse:
    if name not in CONFIG_NAMES:
        return _error(
            400,
            "Unsupported config name. Allowed: " + ", ".join(CONFIG_NAMES) + ".",
        )
    try:
        device_id = _device_id_from_query(request)
    except RegistryError as exc:
        return _registry_error(exc)
    try:
        registry = _registry(request)
        resolved = await asyncio.to_thread(registry.resolve_device, device_id)
        lock = await _rpc_lock(resolved.id)
        async with lock:
            client = await asyncio.to_thread(
                lambda: _open_dvr(resolved.base_url, resolved.username, resolved.password)
            )
            try:
                result = {"config": mask_sensitive(await asyncio.to_thread(client.get_config, name))}
                return JSONResponse(result)
            finally:
                try:
                    client.logout()
                except DvrLogoutError as exc:
                    logger.warning("DVR logout failed: %s", type(exc).__name__)
                finally:
                    client.close()
    except RegistryError as exc:
        return _registry_error(exc)
    except DvrError as exc:
        return _dvr_error(exc)
    except Exception as exc:
        log_unexpected(logger, "Unexpected error while reading DVR data", exc)
        return _error(500, "Unexpected server error.")


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
    log_unexpected(logger, "Unexpected registry error", exc)
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
        try:
            if any(k in body for k in ("host", "http_port", "rtsp_port", "username", "password")):
                manager = getattr(request.app.state, "stream_manager", None)
                if manager is not None:
                    await asyncio.to_thread(manager.close_all_for_device, device_id)
        except Exception:
            pass
        _clear_device_caches(device_id)
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"device": device})


@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: int, request: Request) -> JSONResponse:
    try:
        await asyncio.to_thread(_registry(request).delete_device, device_id)
        try:
            manager = getattr(request.app.state, "stream_manager", None)
            if manager is not None:
                await asyncio.to_thread(manager.close_all_for_device, device_id)
        except Exception:
            pass
        _clear_device_caches(device_id)
    except Exception as exc:
        return _registry_error(exc)
    return JSONResponse({"deleted": True})


@app.post("/api/devices/{device_id}/streams/close-all")
async def close_device_streams(device_id: int, request: Request) -> JSONResponse:
    try:
        manager = _live_manager(request)
        count = await asyncio.to_thread(manager.close_all_for_device, device_id)
        return JSONResponse({"closed": count})
    except StreamConfigError as exc:
        logger.warning("Live view is not configured: %s", exc)
        return _error(503, str(exc))
    except Exception as exc:
        log_unexpected(logger, "Unexpected close-all error", exc)
        return _error(500, "Unexpected server error.")


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
def log_unexpected(logger, message: str, exc: Exception) -> None:
    exc_type = type(exc).__name__
    if exc_type in ("NameError", "UnboundLocalError"):
        try:
            logger.error("%s %s: %s", message, exc_type, str(exc))
        except Exception:
            logger.error("%s %s", message, exc_type)
        return
    frames = []
    try:
        tb = exc.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            code = frame.f_code
            frames.append(f"{code.co_filename}:{tb.tb_lineno}:{code.co_name}")
            tb = tb.tb_next
        frame_str = " | ".join(frames) if frames else ""
        if frame_str:
            logger.error("%s %s: %s", message, exc_type, frame_str)
        else:
            logger.error("%s %s", message, exc_type)
    except Exception:
        logger.error("%s %s", message, exc_type)


# Phase 6a: health status
try:
    from health_checker import check_device
except Exception:
    check_device = None  # type: ignore

_HEALTH_CHECK_LOCKS: dict[int, asyncio.Lock] = {}
_HEALTH_CHECK_LOCKS_GUARD = asyncio.Lock()
_HEALTH_LAST_CHECK: dict[int, float] = {}
_HEALTH_ROUND_TASK: asyncio.Task | None = None
_HEALTH_ROUND_LOCK = asyncio.Lock()
_HEALTH_ROUND_META = {"running": False, "started_at": None, "finished_at": None, "checked": 0, "total": 0}


async def _health_check_lock(device_id: int) -> asyncio.Lock:
    async with _HEALTH_CHECK_LOCKS_GUARD:
        lock = _HEALTH_CHECK_LOCKS.get(device_id)
        if lock is None:
            lock = asyncio.Lock()
            _HEALTH_CHECK_LOCKS[device_id] = lock
        return lock


def _deep_check_interval_seconds() -> float:
    try:
        v = float(os.environ.get("DEEP_CHECK_INTERVAL_MINUTES", "30"))
    except Exception:
        v = 30.0
    if v < 0:
        v = 30.0
    return v * 60.0


def _deep_check_concurrency() -> int:
    try:
        v = int(os.environ.get("DEEP_CHECK_CONCURRENCY", "3"))
    except Exception:
        v = 3
    if v < 1:
        v = 1
    return v


def _status_cache_getter(device_id: int):
    checker = getattr(app.state, "status_checker", None)
    if checker is None:
        return None
    try:
        snap = checker.snapshot()
        for d in snap.get("devices") or []:
            if d.get("id") == device_id:
                return d
    except Exception:
        pass
    return None


async def _run_health_round() -> None:
    registry = getattr(app.state, "registry", None)
    if registry is None or check_device is None:
        _HEALTH_ROUND_META["running"] = False
        _HEALTH_ROUND_META["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return
    # ensure tcp status is fresh if cache empty
    checker = getattr(app.state, "status_checker", None)
    try:
        if checker is not None and hasattr(checker, "snapshot"):
            snap = checker.snapshot()
            devs_snap = snap.get("devices") if isinstance(snap, dict) else None
            if not devs_snap:
                try:
                    if hasattr(checker, "run_once"):
                        await checker.run_once()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        devices = registry.get_all_devices()
    except Exception:
        devices = []
    to_check = []
    for d in devices:
        did = d.get("id") if isinstance(d, dict) else None
        if did is None:
            continue
        try:
            st = _status_cache_getter(did)
            if st and isinstance(st, dict):
                sst = st.get("state")
                if sst in ("offline",):
                    continue  # skip offline
            to_check.append(did)
        except Exception:
            continue
    _HEALTH_ROUND_META["running"] = True
    _HEALTH_ROUND_META["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _HEALTH_ROUND_META["finished_at"] = None
    _HEALTH_ROUND_META["total"] = len(to_check)
    _HEALTH_ROUND_META["checked"] = 0
    conc = _deep_check_concurrency()
    sem = asyncio.Semaphore(conc)
    interval = 1.0

    async def worker(did: int):
        async with sem:
            await asyncio.sleep(interval)  # stagger start
            lock = await _health_check_lock(did)
            try:
                await asyncio.wait_for(lock.acquire(), timeout=5.0)
            except Exception:
                return  # skip if busy
            try:
                await check_device(registry, did, _status_cache_getter)
                _HEALTH_ROUND_META["checked"] += 1
            finally:
                try:
                    lock.release()
                except Exception:
                    pass

    tasks = []
    for did in to_check:
        if did is None:
            continue
        tasks.append(asyncio.create_task(worker(int(did))))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _HEALTH_ROUND_META["running"] = False
    _HEALTH_ROUND_META["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def _health_scheduler() -> None:
    global _HEALTH_ROUND_TASK
    try:
        while True:
            await asyncio.sleep(_deep_check_interval_seconds())
            try:
                cur = _HEALTH_ROUND_TASK
                if cur is not None and not cur.done():
                    continue
            except Exception:
                pass
            async with _HEALTH_ROUND_LOCK:
                try:
                    cur = _HEALTH_ROUND_TASK
                    if cur is not None and not cur.done():
                        continue
                    _HEALTH_ROUND_TASK = asyncio.create_task(_run_health_round())
                except Exception:
                    pass
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


@app.get("/status")
async def status_page() -> FileResponse:
    path = STATIC_DIR / "status.html"
    if not path.exists():
        return FileResponse(STATIC_DIR / "index.html")
    return FileResponse(path)


@app.get("/api/health")
async def api_health() -> JSONResponse:
    registry = getattr(app.state, "registry", None)
    if registry is None:
        return JSONResponse({"devices": [], "summary": {"ok": 0, "stale": 0, "problems": 0, "offline": 0, "unknown": 0}, "round": dict(_HEALTH_ROUND_META)})
    try:
        health_all = registry.get_all_health()
        devices = registry.get_all_devices()
    except Exception:
        health_all = {}
        devices = []
    out_devices = []
    summary = {"ok": 0, "stale": 0, "problems": 0, "offline": 0, "unknown": 0}
    for d in devices:
        if not isinstance(d, dict):
            continue
        did = d.get("id")
        h = health_all.get(did) if did is not None else None
        if not h or not isinstance(h, dict):
            h = {"state": "unknown", "checked_at": None, "channels": []}
        item = {
            "device": d,
            "health": h,
        }
        out_devices.append(item)
        if isinstance(h, dict):
            st = h.get("state") or "unknown"
            if st == "ok":
                summary["ok"] += 1
            elif st == "stale":
                summary["stale"] += 1
            elif st in ("offline", "auth_failed", "unreachable", "storage_problem", "unavailable"):
                if st == "offline":
                    summary["offline"] += 1
                else:
                    summary["problems"] += 1
            elif st == "unknown":
                summary["unknown"] += 1
            else:
                summary["unknown"] += 1
        else:
            summary["unknown"] += 1
    return JSONResponse({"devices": out_devices, "summary": summary, "round": dict(_HEALTH_ROUND_META)})


@app.post("/api/devices/{device_id}/health/check")
async def api_device_health_check(device_id: int) -> JSONResponse:
    registry = getattr(app.state, "registry", None)
    if registry is None or check_device is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    now = time.time()
    last = _HEALTH_LAST_CHECK.get(device_id)
    if last is not None and now - last < 30.0:
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    lock = await _health_check_lock(device_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=5.0)
    except Exception:
        return JSONResponse({"error": "busy"}, status_code=409)
    try:
        res = await check_device(registry, device_id, _status_cache_getter)
        _HEALTH_LAST_CHECK[device_id] = time.time()
        return JSONResponse(res)
    finally:
        try:
            lock.release()
        except Exception:
            pass


@app.post("/api/health/check-all")
async def api_health_check_all() -> JSONResponse:
    global _HEALTH_ROUND_TASK
    # ensure tcp status fresh if empty
    registry = getattr(app.state, "registry", None)
    checker = getattr(app.state, "status_checker", None)
    try:
        if checker is not None and hasattr(checker, "snapshot"):
            snap = checker.snapshot()
            devs_snap = snap.get("devices") if isinstance(snap, dict) else None
            if not devs_snap:
                try:
                    if hasattr(checker, "run_once"):
                        await checker.run_once()
                except Exception:
                    pass
    except Exception:
        pass
    async with _HEALTH_ROUND_LOCK:
        cur = _HEALTH_ROUND_TASK
        if cur is not None and not cur.done():
            return JSONResponse({"running": True}, status_code=409)
        # count target devices
        targets = 0
        try:
            if registry is not None:
                devs = registry.get_all_devices()
                for d in devs:
                    did = d.get("id") if isinstance(d, dict) else None
                    if did is None:
                        continue
                    st = _status_cache_getter(did)
                    if st and isinstance(st, dict) and st.get("state") == "offline":
                        continue
                    targets += 1
        except Exception:
            targets = 0
        _HEALTH_ROUND_TASK = asyncio.create_task(_run_health_round())
    return JSONResponse({"started": True, "devices": targets}, status_code=202)


# start scheduler is handled in lifespan
