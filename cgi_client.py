"""Read-only Dahua DVR CGI client for recordings and storage health.

The DVR's HTTP CGI API uses Digest authentication and is separate from the
JSON-RPC session used elsewhere in this app. This module never calls the RPC
API, never downloads media, and never returns or logs the DVR's internal
``FilePath`` values or any request URL.

The finder flow follows the measured device behaviour:

1. ``action=factory.create`` returns ``result=<id>``.
2. ``action=findFile`` returns ``OK`` or ``Error / Bad Request!``.
3. ``action=findNextFile&count=100`` returns ``found=<k>`` plus item lines.
4. ``action=close`` and ``action=destroy`` always run for the same object.

TODO(spec): the channel offset between the requested channel and the response
items was measured on one device only. It is configurable with
``RESPONSE_CHANNEL_OFFSET`` (default ``-1``) until more devices are checked.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import threading
import time
from datetime import date as date_cls, datetime, time as time_cls
from typing import Any, Callable
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_RESPONSE_CHANNEL_OFFSET = -1
DEFAULT_CACHE_SECONDS = 60.0
_CGI_TIMEOUT = 10.0
_MAX_PAGES = 30
_PAGE_SIZE = 100
_MAX_REASON = 60
_MIN_GAP_SECONDS = 300
_DAY_SECONDS = 86400
_DAY_END = time_cls(23, 59, 59)
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

_STREAMS = {"main": "Main", "sub": "Extra1"}
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_OBJECT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RESULT_RE = re.compile(r"^result=(.+)$", re.MULTILINE)
_FOUND_RE = re.compile(r"^found=(\d+)[ \t\r]*$", re.MULTILINE)
_ITEM_RE = re.compile(
    r"^items\[(\d+)\]\.([A-Za-z][A-Za-z0-9_]*)"
    r"(?:\[(\d+)\])?=(.*)$"
)
_STATE_RE = re.compile(r"^list\.info\[(\d+)\]\.State=(.*)$", re.MULTILINE)
_PARTITION_RE = re.compile(r"^list\.info\[(\d+)\]\.", re.MULTILINE)
_DETAIL_ERROR_RE = re.compile(
    r"^list\.info\[(\d+)\]\.Detail\[(\d+)\]\.IsError=(.*)$", re.MULTILINE
)
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
_FILE_PATH_RE = re.compile(r"(?i)FilePath=.*")


class CgiError(Exception):
    """Safe CGI error: messages never contain a URL or a credential."""


class CgiConfigError(CgiError):
    """Missing or invalid local CGI configuration."""


class CgiAuthError(CgiError):
    """The DVR rejected the Digest authentication."""


class CgiNetworkError(CgiError):
    """The DVR could not be reached, timed out, or dropped the connection."""


def _normalize_base_url(host: str) -> str:
    value = host.strip()
    if not value:
        raise CgiConfigError("DVR_HOST is empty")
    if "://" not in value:
        value = f"http://{value}"
    url = httpx.URL(value)
    if url.scheme not in ("http", "https") or not url.host:
        raise CgiConfigError("DVR_HOST must be a hostname or an http(s) URL")
    if url.userinfo:
        raise CgiConfigError("DVR_HOST must not contain credentials")
    base = url.copy_with(path="/", query=None, fragment=None)
    return str(base).rstrip("/")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise CgiConfigError(f"{name} must be an integer") from None


def _env_nonnegative_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise CgiConfigError(f"{name} must be a positive number") from None
    if not math.isfinite(value) or value < 0:
        raise CgiConfigError(f"{name} must be a positive number")
    return value


def _safe_reason(text: str) -> str:
    """Return the first response line, with paths/URLs hidden, max 60 chars."""
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        cleaned = _FILE_PATH_RE.sub("(path hidden)", cleaned)
        cleaned = _URL_RE.sub("(url hidden)", cleaned)
        return cleaned[:_MAX_REASON]
    return "The DVR rejected the recordings query."


def _query(params: list[tuple[str, str]]) -> str:
    return "&".join(
        f"{quote(str(key), safe='.')}={quote(str(value), safe=':')}"
        for key, value in params
    )


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _parse_items(text: str) -> dict[int, dict[str, Any]]:
    """Parse ``items[N].Key`` and ``items[N].Flags[M]`` lines into dicts."""
    items: dict[int, dict[str, Any]] = {}
    for line in text.splitlines():
        match = _ITEM_RE.match(line.strip())
        if not match:
            continue
        index = int(match.group(1))
        key = match.group(2)
        sub_index = match.group(3)
        value = match.group(4).strip()
        item = items.setdefault(index, {})
        if sub_index is None:
            item[key] = value
        else:
            bucket = item.setdefault(key, {})
            if isinstance(bucket, dict):
                bucket[int(sub_index)] = value
    return items


_CACHE: dict[tuple[str, int, str, str], tuple[float, dict[str, Any]]] = {}
_CACHE_GUARD = threading.Lock()


def clear_cache() -> None:
    """Drop cached finder results (used by tests and manual diagnostics)."""
    with _CACHE_GUARD:
        _CACHE.clear()


def _cache_get(
    key: tuple[str, int, str, str], now: float
) -> dict[str, Any] | None:
    with _CACHE_GUARD:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        expires, result = entry
        if now >= expires:
            _CACHE.pop(key, None)
            return None
        return result


def _cache_put(
    key: tuple[str, int, str, str],
    result: dict[str, Any],
    expires: float,
) -> None:
    with _CACHE_GUARD:
        _CACHE[key] = (expires, result)


_LOCKS: dict[str, asyncio.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _device_lock(host: str) -> asyncio.Lock:
    """Return the per-device finder lock shared by every client instance."""
    with _LOCKS_GUARD:
        lock = _LOCKS.get(host)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[host] = lock
        return lock


class DvrCgi:
    """One read-only CGI client for a single DVR host."""

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        *,
        response_channel_offset: int = DEFAULT_RESPONSE_CHANNEL_OFFSET,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = _CGI_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not user:
            raise CgiConfigError("DVR_USER is empty")
        if not password:
            raise CgiConfigError("DVR_PASS is empty")
        if cache_seconds < 0 or not math.isfinite(cache_seconds):
            raise CgiConfigError("RECORDINGS_CACHE_SECONDS must be positive")

        self._host = _normalize_base_url(host)
        self._response_channel_offset = int(response_channel_offset)
        self._cache_seconds = float(cache_seconds)
        self._clock = clock
        self._http = httpx.AsyncClient(
            base_url=self._host,
            auth=httpx.DigestAuth(user, password),
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"Accept": "text/plain"},
        )

    @classmethod
    def from_env(cls, **kwargs: Any) -> "DvrCgi":
        """Build a client from DVR_HOST, DVR_USER, DVR_PASS and CGI settings."""
        load_dotenv()
        names = ("DVR_HOST", "DVR_USER", "DVR_PASS")
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise CgiConfigError(
                "Missing environment variable(s): " + ", ".join(missing)
            )
        return cls(
            os.environ["DVR_HOST"],
            os.environ["DVR_USER"],
            os.environ["DVR_PASS"],
            response_channel_offset=_env_int(
                "RESPONSE_CHANNEL_OFFSET",
                DEFAULT_RESPONSE_CHANNEL_OFFSET,
            ),
            cache_seconds=_env_nonnegative_float(
                "RECORDINGS_CACHE_SECONDS", DEFAULT_CACHE_SECONDS
            ),
            **kwargs,
        )

    def __repr__(self) -> str:
        return f"DvrCgi(base_url={self._host!r})"

    async def __aenter__(self) -> "DvrCgi":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_device_time(self) -> str:
        """Return the DVR clock as naive ``YYYY-MM-DD HH:MM:SS``."""
        text = await self._get(
            "/cgi-bin/global.cgi",
            [("action", "getCurrentTime")],
            "getCurrentTime",
        )
        match = _RESULT_RE.search(text)
        if not match:
            raise CgiError("The DVR returned an unexpected clock response.")
        value = match.group(1).strip()
        if not _TIME_RE.fullmatch(value):
            raise CgiError("The DVR returned an unexpected clock value.")
        return value

    async def find_files(
        self, channel: int, day: str, stream: str
    ) -> dict[str, Any]:
        """List one channel/stream/day, filtered and sorted, without FilePath.

        Unknown and invalid names raise; the caller validates user input. A DVR
        that answers findFile with ``Error / Bad Request!`` yields
        ``available: False`` rather than an exception.
        """
        if stream not in _STREAMS:
            raise ValueError("stream must be main or sub")
        requested_day = date_cls.fromisoformat(day)
        key = (self._host, channel, day, stream)

        cached = _cache_get(key, self._clock())
        if cached is not None:
            return cached

        async with _device_lock(self._host):
            cached = _cache_get(key, self._clock())
            if cached is not None:
                return cached
            result = await self._find_files(
                channel, requested_day, _STREAMS[stream]
            )
            _cache_put(
                key,
                result,
                self._clock() + self._cache_seconds,
            )
            return result

    async def _find_files(
        self, channel: int, day: date_cls, expected_stream: str
    ) -> dict[str, Any]:
        object_id = await self._create_finder()
        try:
            day_text = day.isoformat()
            status_text = await self._get(
                "/cgi-bin/mediaFileFind.cgi",
                [
                    ("action", "findFile"),
                    ("object", object_id),
                    ("condition.Channel", str(channel)),
                    ("condition.StartTime", f"{day_text} 00:00:00"),
                    ("condition.EndTime", f"{day_text} 23:59:59"),
                ],
                "findFile",
            )
            first_line = status_text.strip().splitlines()[0:1]
            first = first_line[0].strip() if first_line else ""
            if not first.upper().startswith("OK"):
                return {
                    "available": False,
                    "reason": _safe_reason(status_text),
                    "files": [],
                    "truncated": False,
                }

            files, truncated = await self._read_pages(
                object_id, channel, expected_stream, day
            )
            files.sort(key=lambda item: item["start"])
            return {
                "available": True,
                "files": files,
                "truncated": truncated,
            }
        finally:
            await self._finish_finder(object_id)

    async def _create_finder(self) -> str:
        text = await self._get(
            "/cgi-bin/mediaFileFind.cgi",
            [("action", "factory.create")],
            "factory.create",
        )
        match = _RESULT_RE.search(text)
        if not match:
            raise CgiError("The DVR did not create a recordings finder.")
        object_id = match.group(1).strip()
        if not _OBJECT_RE.fullmatch(object_id):
            raise CgiError("The DVR returned an invalid finder identifier.")
        return object_id

    async def _read_pages(
        self,
        object_id: str,
        channel: int,
        expected_stream: str,
        day: date_cls,
    ) -> tuple[list[dict[str, Any]], bool]:
        files: list[dict[str, Any]] = []
        expected_channel = channel + self._response_channel_offset
        day_start = datetime.combine(day, time_cls.min)
        day_end = datetime.combine(day, _DAY_END)

        for _ in range(_MAX_PAGES):
            text = await self._get(
                "/cgi-bin/mediaFileFind.cgi",
                [
                    ("action", "findNextFile"),
                    ("object", object_id),
                    ("count", str(_PAGE_SIZE)),
                ],
                "findNextFile",
            )
            found_match = _FOUND_RE.search(text)
            if not found_match:
                raise CgiError(
                    "The DVR returned an unexpected recordings page."
                )
            found = int(found_match.group(1))

            items = _parse_items(text)
            for index in sorted(items):
                item = items[index]
                if not _matches_item(
                    item, expected_channel, expected_stream
                ):
                    continue
                file = _file_from_item(item, day_start, day_end)
                if file is not None:
                    files.append(file)

            if found < _PAGE_SIZE:
                return files, False

        return files, True

    async def _finish_finder(self, object_id: str) -> None:
        """Close and destroy the finder; failures are logged, not raised."""
        for action in ("close", "destroy"):
            try:
                await self._get(
                    "/cgi-bin/mediaFileFind.cgi",
                    [("action", action), ("object", object_id)],
                    action,
                )
            except CgiError as exc:
                logger.warning("DVR CGI finder %s failed: %s", action, exc)

    async def _get(
        self,
        path: str,
        params: list[tuple[str, str]],
        label: str,
    ) -> str:
        """GET one CGI action. Logs the action name only, never the URL."""
        url = f"{path}?{_query(params)}"
        try:
            response = await self._http.get(url)
        except httpx.TimeoutException:
            logger.warning("DVR CGI %s timed out", label)
            raise CgiNetworkError(
                "The DVR did not answer the CGI request in time."
            ) from None
        except httpx.HTTPError:
            logger.warning("DVR CGI %s could not connect", label)
            raise CgiNetworkError(
                "Could not contact the DVR over CGI."
            ) from None

        if response.status_code in (401, 403):
            logger.warning("DVR CGI %s authentication was rejected", label)
            raise CgiAuthError("The DVR rejected the CGI authentication.")
        if not response.is_success:
            logger.warning(
                "DVR CGI %s returned HTTP %s", label, response.status_code
            )
            raise CgiError(
                f"The DVR CGI request failed (HTTP {response.status_code})."
            )
        return response.text

    async def get_storage_health(self) -> dict[str, Any]:
        """Return a non-alarming storage summary; failures become unknown."""
        try:
            text = await self._get(
                "/cgi-bin/storageDevice.cgi",
                [("action", "getDeviceAllInfo")],
                "getDeviceAllInfo",
            )
        except CgiError as exc:
            logger.warning("DVR storage health unavailable: %s", exc)
            return {"health": "unknown", "partitions": 0, "errors": 0}

        indices = {int(match.group(1)) for match in _PARTITION_RE.finditer(text)}
        states = {
            int(match.group(1)): match.group(2).strip()
            for match in _STATE_RE.finditer(text)
        }
        errors = 0
        for match in _DETAIL_ERROR_RE.finditer(text):
            if match.group(3).strip().lower() == "true":
                errors += 1

        health = "unknown"
        if indices:
            all_success = all(
                states.get(index, "").lower() == "success" for index in indices
            )
            health = "ok" if all_success and errors == 0 else "problem"
        return {
            "health": health,
            "partitions": len(indices),
            "errors": errors,
        }


def _matches_item(
    item: dict[str, Any], expected_channel: int, expected_stream: str
) -> bool:
    channel = _to_int(item.get("Channel"))
    if channel != expected_channel:
        return False
    video = str(item.get("VideoStream") or "").strip()
    return video.lower() == expected_stream.lower()


def _file_from_item(
    item: dict[str, Any],
    day_start: datetime,
    day_end: datetime,
) -> dict[str, Any] | None:
    """Convert one item, discarding FilePath and anything outside the day."""
    start_text = str(item.get("StartTime") or "").strip()
    end_text = str(item.get("EndTime") or "").strip()
    if not _TIME_RE.fullmatch(start_text):
        return None
    if not _TIME_RE.fullmatch(end_text):
        return None
    try:
        start = datetime.strptime(start_text, _TIME_FORMAT)
        end = datetime.strptime(end_text, _TIME_FORMAT)
    except ValueError:
        return None
    if end <= start:
        return None
    if end <= day_start or start > day_end:
        return None

    raw_flags = item.get("Flags")
    flags: list[str] = []
    if isinstance(raw_flags, dict):
        for _, value in sorted(raw_flags.items()):
            text = str(value).strip()
            if text:
                flags.append(text)
    elif isinstance(raw_flags, str) and raw_flags.strip():
        flags.append(raw_flags.strip())

    return {
        "start": start_text,
        "end": end_text,
        "type": str(item.get("Type") or "").strip(),
        "flags": flags,
        "length_bytes": _to_int(item.get("Length")) or 0,
    }


def day_summary(files: list[dict[str, Any]], day: date_cls) -> dict[str, Any]:
    """Compute clamped coverage, last end, and gaps of 5 minutes or more."""
    day_start = datetime.combine(day, time_cls.min)
    day_end = datetime.combine(day, _DAY_END)

    intervals: list[tuple[datetime, datetime]] = []
    for file in files:
        try:
            start = datetime.strptime(file["start"], _TIME_FORMAT)
            end = datetime.strptime(file["end"], _TIME_FORMAT)
        except (KeyError, TypeError, ValueError):
            continue
        start = max(start, day_start)
        end = min(end, day_end)
        if end > start:
            intervals.append((start, end))

    intervals.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))

    coverage = int(
        sum((end - start).total_seconds() for start, end in merged)
    )
    percent = round(coverage / _DAY_SECONDS * 100, 1)
    percent = min(percent, 100.0)

    gaps: list[dict[str, str]] = []
    cursor = day_start
    for start, end in merged:
        if (start - cursor).total_seconds() >= _MIN_GAP_SECONDS:
            gaps.append(
                {
                    "start": cursor.strftime(_TIME_FORMAT),
                    "end": start.strftime(_TIME_FORMAT),
                }
            )
        cursor = max(cursor, end)
    if merged and (day_end - cursor).total_seconds() >= _MIN_GAP_SECONDS:
        gaps.append(
            {
                "start": cursor.strftime(_TIME_FORMAT),
                "end": day_end.strftime(_TIME_FORMAT),
            }
        )

    return {
        "coverage_seconds": coverage,
        "coverage_percent": percent,
        "last_end": (
            merged[-1][1].strftime(_TIME_FORMAT) if merged else None
        ),
        "gaps": gaps,
    }
