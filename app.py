import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
from stream_manager import (
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
                "Live stream lease expired: %s", ", ".join(expired)
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the live manager, clean leftovers, and clean up on shutdown."""
    manager: StreamManager | None = None
    reaper: asyncio.Task[None] | None = None
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

    try:
        yield
    finally:
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


app = FastAPI(title="DVR Web", version="0.4.0", lifespan=lifespan)
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


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
