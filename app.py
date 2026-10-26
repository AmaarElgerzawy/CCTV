import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dvr_client import (
    DvrAuthError,
    DvrClient,
    DvrConfigError,
    DvrError,
    DvrLogoutError,
    DvrRpcError,
    DvrSessionExpired,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="DVR Web", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

logger = logging.getLogger(__name__)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/device/info")
def device_info() -> JSONResponse:
    client: DvrClient | None = None
    try:
        # One client per request: no global session state is kept.
        client = DvrClient.from_env()
        client.login()
        info = client.get_device_info()
    except DvrConfigError as exc:
        logger.error("DVR is not configured: %s", exc)
        return _error(
            503,
            "DVR is not configured. Set DVR_HOST, DVR_USER and DVR_PASS in .env.",
        )
    except DvrAuthError:
        logger.warning("DVR login was rejected")
        return _error(502, "The DVR rejected the login.")
    except DvrSessionExpired as exc:
        logger.warning("DVR session expired: %s", exc)
        return _error(502, "The DVR session is no longer valid. Try again.")
    except DvrRpcError as exc:
        logger.warning("DVR RPC failed: %s", exc)
        return _error(502, "The DVR did not return the requested information.")
    except DvrError as exc:
        logger.warning("DVR request failed: %s", exc)
        return _error(502, "Could not communicate with the DVR.")
    except Exception as exc:  # pragma: no cover - defensive
        # Log the type only; exception strings can contain upstream data.
        logger.error("Unexpected error while reading DVR info: %s", type(exc).__name__)
        return _error(500, "Unexpected server error.")
    finally:
        if client is not None:
            try:
                client.logout()
            except DvrLogoutError as exc:
                logger.warning("DVR logout failed: %s", exc)
            finally:
                client.close()

    return JSONResponse({"info": info})


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})
