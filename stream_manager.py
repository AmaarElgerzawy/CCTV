"""On-demand go2rtc stream registration for the DVR live view.

The backend owns the RTSP URL and never returns or logs it. The browser only
receives an opaque go2rtc stream name.

RTSP is separate from the DVR JSON-RPC session, so this module does not use
DvrClient and makes no DVR RPC calls.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

STREAM_PREFIX = "cctv-"
DEFAULT_GO2RTC_URL = "http://127.0.0.1:1984"
DEFAULT_PUBLIC_GO2RTC_URL = "http://127.0.0.1:1984"
DEFAULT_MAX_LIVE_STREAMS = 4
DEFAULT_MAX_MAIN_STREAMS = 1
_RTSP_PORT = 554
_STREAM_NAME = re.compile(r"^cctv-[A-Za-z0-9_-]{8,64}$")


class StreamError(Exception):
    """Safe live-view error: messages never contain a src or a response body."""


class StreamConfigError(StreamError):
    """Missing or invalid live-view configuration."""


class StreamLimitError(StreamError):
    """A configured stream limit would be exceeded."""


@dataclass(frozen=True)
class ActiveStream:
    channel: int
    quality: str


def is_stream_name(name: str) -> bool:
    return bool(_STREAM_NAME.match(name))


def _env_url(name: str, default: str) -> str:
    raw = os.environ.get(name, "").strip() or default
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StreamConfigError(f"{name} must be an http:// or https:// URL")
    return raw.rstrip("/")


def public_go2rtc_url() -> str:
    """Return the browser-facing go2rtc base URL only."""
    load_dotenv()
    return _env_url("PUBLIC_GO2RTC_URL", DEFAULT_PUBLIC_GO2RTC_URL)


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise StreamConfigError(f"{name} must be a positive integer") from None
    if value < 1:
        raise StreamConfigError(f"{name} must be a positive integer")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise StreamConfigError(f"{name} must be true or false")


def _dvr_hostname(dvr_host: str) -> str:
    raw = dvr_host.strip()
    if not raw:
        raise StreamConfigError("DVR_HOST is empty")
    if "://" not in raw:
        raw = "//" + raw
    host = urlsplit(raw).hostname
    if not host:
        raise StreamConfigError("DVR_HOST does not contain a host")
    if ":" in host:
        return f"[{host}]"
    return host


class StreamManager:
    """Small in-memory registry for go2rtc streams created by this process.

    Each entry stores only the channel and quality. The RTSP src is built for
    the registration request and is never stored, logged, or returned.
    """

    def __init__(
        self,
        *,
        go2rtc_url: str,
        dvr_host: str,
        dvr_user: str,
        dvr_pass: str,
        transcode_h264: bool = True,
        max_live_streams: int = DEFAULT_MAX_LIVE_STREAMS,
        max_main_streams: int = DEFAULT_MAX_MAIN_STREAMS,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if max_live_streams < 1 or max_main_streams < 1:
            raise StreamConfigError("Stream limits must be positive integers")
        self._dvr_host = _dvr_hostname(dvr_host)
        self._dvr_user = dvr_user
        self._dvr_pass = dvr_pass
        self._transcode_h264 = transcode_h264
        self._max_live_streams = max_live_streams
        self._max_main_streams = max_main_streams
        self._active: dict[str, ActiveStream] = {}
        self._created: set[str] = set()
        self._lock = threading.Lock()
        self._client = httpx.Client(
            base_url=go2rtc_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    @classmethod
    def from_env(cls, **kwargs: Any) -> "StreamManager":
        """Build a manager from .env / process environment.

        Errors name missing variables only; values are never included.
        """
        load_dotenv()
        missing = [
            name
            for name in ("DVR_HOST", "DVR_USER", "DVR_PASS")
            if not os.environ.get(name)
        ]
        if missing:
            raise StreamConfigError(
                "Missing environment variable(s): " + ", ".join(missing)
            )
        return cls(
            go2rtc_url=_env_url("GO2RTC_URL", DEFAULT_GO2RTC_URL),
            dvr_host=os.environ["DVR_HOST"],
            dvr_user=os.environ["DVR_USER"],
            dvr_pass=os.environ["DVR_PASS"],
            transcode_h264=_env_bool("STREAM_TRANSCODE_H264", True),
            max_live_streams=_env_positive_int(
                "MAX_LIVE_STREAMS", DEFAULT_MAX_LIVE_STREAMS
            ),
            max_main_streams=_env_positive_int(
                "MAX_MAIN_STREAMS", DEFAULT_MAX_MAIN_STREAMS
            ),
            **kwargs,
        )

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    @property
    def active_main_count(self) -> int:
        with self._lock:
            return sum(
                1 for stream in self._active.values() if stream.quality == "main"
            )

    def open(self, channel: int, quality: str = "sub") -> str:
        """Register one stream and return its opaque go2rtc name.

        The limit check and the registry reservation happen under one lock, so
        concurrent requests cannot exceed a configured limit.
        """
        if quality not in {"sub", "main"}:
            raise StreamConfigError("quality must be sub or main")
        if not 1 <= channel <= 4:
            raise StreamConfigError("channel must be between 1 and 4")

        name = f"{STREAM_PREFIX}{uuid.uuid4().hex}"
        with self._lock:
            if len(self._active) >= self._max_live_streams:
                raise StreamLimitError(
                    f"Live stream limit reached ({self._max_live_streams}). "
                    "Stop a stream and try again."
                )
            main_count = sum(
                1
                for stream in self._active.values()
                if stream.quality == "main"
            )
            if quality == "main" and main_count >= self._max_main_streams:
                raise StreamLimitError(
                    f"Main-stream limit reached ({self._max_main_streams}). "
                    "Close the larger view and try again."
                )
            self._active[name] = ActiveStream(channel=channel, quality=quality)
            self._created.add(name)

        try:
            self._register(name, self._build_source(channel, quality))
        except Exception:
            with self._lock:
                self._active.pop(name, None)
                self._created.discard(name)
            raise
        return name

    def close_stream(self, name: str) -> None:
        """Delete one stream as soon as it is closed.

        Idempotent: a name this process does not know is treated as closed and
        is not forwarded to go2rtc, so arbitrary names cannot be deleted.
        """
        if not is_stream_name(name):
            raise StreamConfigError("Invalid stream name")
        with self._lock:
            known = name in self._active or name in self._created
            self._active.pop(name, None)
        if not known:
            return
        self._delete(name)
        with self._lock:
            self._created.discard(name)

    def cleanup_leftovers(self) -> int:
        """Delete cctv-* streams left by an earlier process.

        Only response keys are inspected. Values are never logged or stored;
        go2rtc may include RTSP credentials in them.
        """
        try:
            response = self._client.get("/api/streams")
        except httpx.HTTPError as exc:
            logger.warning(
                "go2rtc leftover cleanup could not list streams (%s)",
                type(exc).__name__,
            )
            return 0
        if response.status_code != 200:
            logger.warning(
                "go2rtc leftover cleanup could not list streams (HTTP %s)",
                response.status_code,
            )
            return 0
        try:
            data = response.json()
        except ValueError:
            logger.warning("go2rtc leftover cleanup got an unreadable list")
            return 0
        if not isinstance(data, dict):
            logger.warning("go2rtc leftover cleanup got an unexpected list")
            return 0

        removed = 0
        for name in data:
            if not isinstance(name, str) or not name.startswith(STREAM_PREFIX):
                continue
            try:
                self._delete(name)
            except StreamError:
                logger.warning("go2rtc could not delete a leftover stream")
            else:
                removed += 1
        return removed

    def cleanup_created(self) -> int:
        """Delete every stream this process created. Best effort."""
        with self._lock:
            names = sorted(self._created)
            self._active.clear()
        removed = 0
        for name in names:
            try:
                self._delete(name)
            except StreamError:
                logger.warning("go2rtc could not delete a created stream")
            else:
                removed += 1
                with self._lock:
                    self._created.discard(name)
        return removed

    def close(self) -> None:
        with self._lock:
            self._active.clear()
            self._created.clear()
        self._client.close()

    def _build_source(self, channel: int, quality: str) -> str:
        subtype = 0 if quality == "main" else 1
        user = quote(self._dvr_user, safe="")
        password = quote(self._dvr_pass, safe="")
        rtsp_url = (
            f"rtsp://{user}:{password}@{self._dvr_host}:{_RTSP_PORT}"
            f"/cam/realmonitor?channel={channel}&subtype={subtype}"
        )
        if self._transcode_h264:
            return f"ffmpeg:{rtsp_url}#video=h264"
        return rtsp_url

    def _register(self, name: str, src: str) -> None:
        try:
            response = self._client.put(
                "/api/streams", params={"name": name, "src": src}
            )
        except httpx.HTTPError as exc:
            raise StreamError(
                f"go2rtc request failed ({type(exc).__name__})"
            ) from None
        if not 200 <= response.status_code < 300:
            raise StreamError(f"go2rtc returned HTTP {response.status_code}")

    def _delete(self, name: str) -> None:
        try:
            response = self._client.delete(
                "/api/streams", params={"src": name}
            )
        except httpx.HTTPError as exc:
            raise StreamError(
                f"go2rtc request failed ({type(exc).__name__})"
            ) from None
        if response.status_code == 404:
            return
        if not 200 <= response.status_code < 300:
            raise StreamError(f"go2rtc returned HTTP {response.status_code}")
