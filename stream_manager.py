"""On-demand go2rtc stream registration for the DVR live view and playback.

The backend owns the RTSP URL and never returns or logs it. The browser only
receives an opaque go2rtc stream name.

RTSP is separate from the DVR JSON-RPC session, so this module does not use
DvrClient and makes no DVR RPC calls.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

STREAM_PREFIX = "cctv-"
DEFAULT_GO2RTC_URL = "http://127.0.0.1:1984"
DEFAULT_PUBLIC_GO2RTC_URL = "http://127.0.0.1:1984"
DEFAULT_MAX_CHANNEL = 8
DEFAULT_MAX_LIVE_STREAMS = 6
DEFAULT_MAX_MAIN_STREAMS = 2
DEFAULT_MAX_PLAYBACK_STREAMS = 2
DEFAULT_PLAYBACK_MAX_SECONDS = 3600
DEFAULT_PLAYBACK_SECONDS = 600
PLAYBACK_MIN_SECONDS = 30
DEFAULT_LEASE_TIMEOUT_SECONDS = 30.0
DEFAULT_LEASE_CHECK_INTERVAL_SECONDS = 5.0
_RTSP_PORT = 554
_PLAYBACK_TIME_FORMAT = "%Y_%m_%d_%H_%M_%S"
_STREAM_NAME = re.compile(r"^cctv-[A-Za-z0-9_-]{8,64}$")


class StreamError(Exception):
    """Safe live-view error: messages never contain a src or a response body."""


class StreamConfigError(StreamError):
    """Missing or invalid live-view configuration."""


class StreamLimitError(StreamError):
    """A configured stream limit would be exceeded."""


@dataclass
class ActiveStream:
    channel: int
    quality: str
    last_renewed: float
    kind: str = "live"


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


def max_channel_from_env() -> int:
    """Return the highest accepted channel number, for the browser config."""
    load_dotenv()
    return _env_positive_int("MAX_CHANNEL", DEFAULT_MAX_CHANNEL)


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


def _env_positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise StreamConfigError(f"{name} must be a positive number") from None
    if value <= 0 or not math.isfinite(value):
        raise StreamConfigError(f"{name} must be a positive number")
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
        max_channel: int = DEFAULT_MAX_CHANNEL,
        max_live_streams: int = DEFAULT_MAX_LIVE_STREAMS,
        max_main_streams: int = DEFAULT_MAX_MAIN_STREAMS,
        max_playback_streams: int = DEFAULT_MAX_PLAYBACK_STREAMS,
        playback_max_seconds: int = DEFAULT_PLAYBACK_MAX_SECONDS,
        lease_timeout: float = DEFAULT_LEASE_TIMEOUT_SECONDS,
        lease_check_interval: float = DEFAULT_LEASE_CHECK_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if max_channel < 1:
            raise StreamConfigError("MAX_CHANNEL must be a positive integer")
        if max_live_streams < 1 or max_main_streams < 1:
            raise StreamConfigError("Stream limits must be positive integers")
        if max_playback_streams < 1:
            raise StreamConfigError(
                "MAX_PLAYBACK_STREAMS must be a positive integer"
            )
        if playback_max_seconds < PLAYBACK_MIN_SECONDS:
            raise StreamConfigError(
                f"PLAYBACK_MAX_SECONDS must be at least {PLAYBACK_MIN_SECONDS}"
            )
        if lease_timeout <= 0 or lease_check_interval <= 0:
            raise StreamConfigError("Lease settings must be positive numbers")
        self._dvr_host = _dvr_hostname(dvr_host)
        self._dvr_user = dvr_user
        self._dvr_pass = dvr_pass
        self._transcode_h264 = transcode_h264
        self._max_channel = max_channel
        self._max_live_streams = max_live_streams
        self._max_main_streams = max_main_streams
        self._max_playback_streams = max_playback_streams
        self._playback_max_seconds = playback_max_seconds
        self._lease_timeout = lease_timeout
        self._lease_check_interval = lease_check_interval
        self._clock = clock
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
            max_channel=_env_positive_int("MAX_CHANNEL", DEFAULT_MAX_CHANNEL),
            max_live_streams=_env_positive_int(
                "MAX_LIVE_STREAMS", DEFAULT_MAX_LIVE_STREAMS
            ),
            max_main_streams=_env_positive_int(
                "MAX_MAIN_STREAMS", DEFAULT_MAX_MAIN_STREAMS
            ),
            max_playback_streams=_env_positive_int(
                "MAX_PLAYBACK_STREAMS", DEFAULT_MAX_PLAYBACK_STREAMS
            ),
            playback_max_seconds=_env_positive_int(
                "PLAYBACK_MAX_SECONDS", DEFAULT_PLAYBACK_MAX_SECONDS
            ),
            lease_timeout=_env_positive_float(
                "LEASE_TIMEOUT_SECONDS", DEFAULT_LEASE_TIMEOUT_SECONDS
            ),
            lease_check_interval=_env_positive_float(
                "LEASE_CHECK_INTERVAL_SECONDS",
                DEFAULT_LEASE_CHECK_INTERVAL_SECONDS,
            ),
            **kwargs,
        )

    @property
    def max_channel(self) -> int:
        return self._max_channel

    @property
    def lease_check_interval(self) -> float:
        return self._lease_check_interval

    @property
    def max_playback_seconds(self) -> int:
        return self._playback_max_seconds

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for stream in self._active.values() if stream.kind == "live"
            )

    @property
    def active_main_count(self) -> int:
        with self._lock:
            return sum(
                1
                for stream in self._active.values()
                if stream.kind == "live" and stream.quality == "main"
            )

    @property
    def active_playback_count(self) -> int:
        with self._lock:
            return sum(
                1
                for stream in self._active.values()
                if stream.kind == "playback"
            )

    def open(self, channel: int, quality: str = "sub") -> str:
        """Register one stream and return its opaque go2rtc name.

        The limit check and the registry reservation happen under one lock, so
        concurrent requests cannot exceed a configured limit.
        """
        if quality not in {"sub", "main"}:
            raise StreamConfigError("quality must be sub or main")
        if not 1 <= channel <= self._max_channel:
            raise StreamConfigError(
                f"channel must be between 1 and {self._max_channel}"
            )

        name = f"{STREAM_PREFIX}{uuid.uuid4().hex}"
        with self._lock:
            live_count = sum(
                1
                for stream in self._active.values()
                if stream.kind == "live"
            )
            if live_count >= self._max_live_streams:
                raise StreamLimitError(
                    f"Live stream limit reached ({self._max_live_streams}). "
                    "Stop a stream and try again."
                )
            main_count = sum(
                1
                for stream in self._active.values()
                if stream.kind == "live" and stream.quality == "main"
            )
            if quality == "main" and main_count >= self._max_main_streams:
                raise StreamLimitError(
                    f"Main-stream limit reached ({self._max_main_streams}). "
                    "Stop another main stream or switch Grid quality to Sub."
                )
            self._active[name] = ActiveStream(
                channel=channel,
                quality=quality,
                last_renewed=self._clock(),
            )
            self._created.add(name)

        try:
            self._register(name, self._build_source(channel, quality))
        except Exception:
            with self._lock:
                self._active.pop(name, None)
                self._created.discard(name)
            raise
        return name

    def open_playback(
        self, channel: int, start: datetime, seconds: int
    ) -> str:
        """Register one recorded-playback stream and return its name.

        TODO(spec): the playback URL's channel parameter was confirmed on one
        device with channel 1 only. The UI keeps the 1-based channel number.
        """
        if not 1 <= channel <= self._max_channel:
            raise StreamConfigError(
                f"channel must be between 1 and {self._max_channel}"
            )
        if not PLAYBACK_MIN_SECONDS <= seconds <= self._playback_max_seconds:
            raise StreamConfigError(
                f"seconds must be between {PLAYBACK_MIN_SECONDS} and "
                f"{self._playback_max_seconds}"
            )

        name = f"{STREAM_PREFIX}{uuid.uuid4().hex}"
        with self._lock:
            playback_count = sum(
                1
                for stream in self._active.values()
                if stream.kind == "playback"
            )
            if playback_count >= self._max_playback_streams:
                raise StreamLimitError(
                    "Playback-stream limit reached "
                    f"({self._max_playback_streams}). "
                    "Stop a playback and try again."
                )
            self._active[name] = ActiveStream(
                channel=channel,
                quality="main",
                last_renewed=self._clock(),
                kind="playback",
            )
            self._created.add(name)

        try:
            self._register(
                name, self._build_playback_source(channel, start, seconds)
            )
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
        Closing also removes the lease, so it can never be expired later.
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

    def renew(self, names: list[str]) -> tuple[list[str], list[str]]:
        """Extend leases for known, unexpired streams.

        Unknown and already-expired names are returned separately; they are not
        an error. Duplicate input names are answered once.
        """
        now = self._clock()
        renewed: list[str] = []
        expired: list[str] = []
        seen: set[str] = set()
        with self._lock:
            for name in names:
                if name in seen:
                    continue
                seen.add(name)
                stream = self._active.get(name)
                if (
                    stream is None
                    or now - stream.last_renewed >= self._lease_timeout
                ):
                    expired.append(name)
                    continue
                stream.last_renewed = now
                renewed.append(name)
        return renewed, expired

    def expire_leases(self) -> list[str]:
        """Unregister every stream whose lease has expired.

        The slot is freed under the lock before the go2rtc delete. A failed
        delete keeps the name in the created set so shutdown can retry it.
        """
        now = self._clock()
        with self._lock:
            expired = [
                name
                for name, stream in self._active.items()
                if now - stream.last_renewed >= self._lease_timeout
            ]
            for name in expired:
                self._active.pop(name, None)
        for name in expired:
            try:
                self._delete(name)
            except StreamError:
                logger.warning("go2rtc could not delete an expired stream")
                continue
            with self._lock:
                self._created.discard(name)
        return expired

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

    def _build_playback_source(
        self, channel: int, start: datetime, seconds: int
    ) -> str:
        """Build the recorded-playback RTSP source, never logged or returned.

        Times use the DVR's own local clock, exactly like the recordings list,
        so no timezone conversion is applied.
        """
        user = quote(self._dvr_user, safe="")
        password = quote(self._dvr_pass, safe="")
        end = start + timedelta(seconds=seconds)
        start_text = start.strftime(_PLAYBACK_TIME_FORMAT)
        end_text = end.strftime(_PLAYBACK_TIME_FORMAT)
        rtsp_url = (
            f"rtsp://{user}:{password}@{self._dvr_host}:{_RTSP_PORT}"
            f"/cam/playback?channel={channel}"
            f"&starttime={start_text}&endtime={end_text}"
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
