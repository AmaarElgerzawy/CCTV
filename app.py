import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import FastAPI
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

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="DVR Web", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

logger = logging.getLogger(__name__)


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
