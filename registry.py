"""Local device registry for the DVR web app.

The registry keeps groups, devices, and credentials in a small SQLite
database. Passwords are encrypted with a Fernet key from APP_SECRET_KEY and
are never returned, logged, or written to an export.

The online-status checker makes plain TCP connections only. It does not log in,
does not send an HTTP request, and keeps its last round in memory.
"""

from __future__ import annotations

import asyncio
import csv
import io
import ipaddress
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

from dvr_client import (
    DvrAuthError,
    DvrClient,
    DvrError,
    DvrLogoutError,
)

logger = logging.getLogger(__name__)

DEFAULT_HTTP_PORT = 80
DEFAULT_RTSP_PORT = 554
DEFAULT_STATUS_INTERVAL = 60.0
DEFAULT_STATUS_CONCURRENCY = 10
DEFAULT_STATUS_TIMEOUT = 2.0
DEFAULT_TEST_TIMEOUT = 5.0
MAX_CSV_BYTES = 256 * 1024
MAX_CSV_ROWS = 500
MAX_NAME_LENGTH = 64
MAX_NOTES_LENGTH = 1000
MAX_USERNAME_LENGTH = 64
MAX_PASSWORD_LENGTH = 512
CSV_COLUMNS = (
    "name",
    "group",
    "host",
    "http_port",
    "rtsp_port",
    "username",
    "password",
)

_DEFAULT_USERNAME_KEY = "default_username"
_DEFAULT_PASSWORD_KEY = "default_password_enc"
_MIGRATION_KEY = "first_run_migration"
_HEALTH_SCHEMA_MIGRATION_KEY = "health_schema_migrated"
_HOSTNAME_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
_DIGITS_AND_DOTS = re.compile(r"^[0-9.]+$")

REGISTRY_DISABLED_MESSAGE = (
    "Device registry is not configured. Set a valid APP_SECRET_KEY in .env."
)


class RegistryConfigError(Exception):
    """The registry cannot start with the current environment."""


class RegistryError(Exception):
    """A safe, user-facing registry validation or lookup failure."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class StatusRoundBusy(Exception):
    """A status round is already running."""


@dataclass(frozen=True)
class RegistrySettings:
    """Resolved registry settings. No secret is ever logged or returned."""

    db_path: Path
    secret_key: str
    status_interval: float = DEFAULT_STATUS_INTERVAL
    status_concurrency: int = DEFAULT_STATUS_CONCURRENCY
    status_timeout: float = DEFAULT_STATUS_TIMEOUT
    allow_public_hosts: bool = False

    @classmethod
    def from_env(
        cls,
        base_dir: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "RegistrySettings":
        if env is None:
            load_dotenv()
            env = os.environ

        key = str(env.get("APP_SECRET_KEY", "")).strip()
        if not key:
            raise RegistryConfigError("APP_SECRET_KEY is not set")
        try:
            Fernet(key.encode("ascii"))
        except (ValueError, TypeError):
            raise RegistryConfigError(
                "APP_SECRET_KEY is not a valid Fernet key"
            ) from None

        raw_dir = str(env.get("DATA_DIR", "./data")).strip() or "./data"
        data_dir = Path(raw_dir)
        if not data_dir.is_absolute():
            data_dir = (base_dir or Path.cwd()) / data_dir

        interval = _env_positive_float(
            env, "STATUS_INTERVAL_SECONDS", DEFAULT_STATUS_INTERVAL
        )
        timeout = _env_positive_float(
            env, "STATUS_TIMEOUT_SECONDS", DEFAULT_STATUS_TIMEOUT
        )
        concurrency = _env_positive_int(
            env, "STATUS_CONCURRENCY", DEFAULT_STATUS_CONCURRENCY
        )
        allow_public = str(
            env.get("ALLOW_PUBLIC_HOSTS", "false")
        ).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            db_path=data_dir / "dvr.db",
            secret_key=key,
            status_interval=interval,
            status_concurrency=concurrency,
            status_timeout=timeout,
            allow_public_hosts=allow_public,
        )


def _env_positive_float(
    env: Mapping[str, str], name: str, default: float
) -> float:
    try:
        value = float(str(env.get(name, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _env_positive_int(
    env: Mapping[str, str], name: str, default: int
) -> int:
    try:
        value = int(str(env.get(name, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def normalize_name(value: Any, field: str = "name") -> str:
    """Return a stripped 1..64 character name or raise RegistryError."""
    text = str(value).strip() if value is not None else ""
    if not 1 <= len(text) <= MAX_NAME_LENGTH:
        raise RegistryError(
            f"{field} must be between 1 and {MAX_NAME_LENGTH} characters."
        )
    if any(ord(char) < 32 for char in text):
        raise RegistryError(f"{field} must not contain control characters.")
    return text


def normalize_host(value: Any, *, allow_public: bool) -> str:
    """Validate a hostname or IPv4 literal and return it lowercased.

    IP literals must be private, loopback, or link-local unless the operator
    explicitly allows public addresses.
    """
    text = str(value).strip().lower() if value is not None else ""
    if text.endswith("."):
        text = text[:-1]
    if not text:
        raise RegistryError("host is required.")
    if len(text) > 253:
        raise RegistryError("host is too long.")

    if _DIGITS_AND_DOTS.fullmatch(text):
        try:
            address = ipaddress.IPv4Address(text)
        except ValueError:
            raise RegistryError("host is not a valid IPv4 address.") from None
        if not allow_public and not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
        ):
            raise RegistryError(
                "host must be a private, loopback, or link-local address."
            )
        return str(address)

    labels = text.split(".")
    if not labels or any(
        not _HOSTNAME_LABEL.fullmatch(label) for label in labels
    ):
        raise RegistryError("host is not a valid hostname.")
    return text


def normalize_port(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegistryError(f"{field} must be a whole number.")
    if not 1 <= value <= 65535:
        raise RegistryError(f"{field} must be between 1 and 65535.")
    return value


def normalize_text(
    value: Any,
    field: str,
    *,
    max_length: int,
    required: bool = False,
) -> str | None:
    if value is None:
        text = ""
    else:
        text = str(value).strip()
    if not text:
        if required:
            raise RegistryError(f"{field} is required.")
        return None
    if len(text) > max_length:
        raise RegistryError(
            f"{field} must be at most {max_length} characters."
        )
    return text


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_unused_channels(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        out = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                v = int(part)
            except ValueError:
                continue
            if v >= 1:
                out.append(v)
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for part in value:
            try:
                v = int(part)
            except (TypeError, ValueError):
                continue
            if v >= 1:
                out.append(v)
        return out
    return []


def _device_public(row: sqlite3.Row) -> dict[str, Any]:
    unused = _parse_unused_channels(row["unused_channels"] if "unused_channels" in row.keys() else "")
    stale_hours = row["stale_hours"] if "stale_hours" in row.keys() else None
    try:
        if stale_hours is not None and stale_hours != "":
            stale_hours = int(stale_hours)
        else:
            stale_hours = None
    except (TypeError, ValueError):
        stale_hours = None
    return {
        "id": row["id"],
        "name": row["name"],
        "group_id": row["group_id"],
        "group": row["group_name"],
        "host": row["host"],
        "http_port": row["http_port"],
        "rtsp_port": row["rtsp_port"],
        "has_own_credentials": bool(
            row["username"] and row["password_enc"]
        ),
        "notes": row["notes"] or "",
        "unused_channels": unused,
        "stale_hours": stale_hours,
    }


def _url_host(host: str) -> str:
    """Bracket an IPv6 literal for a URL. Registry hosts are IPv4 or names."""
    return f"[{host}]" if ":" in host else host


@dataclass(frozen=True)
class ResolvedDevice:
    """One registry device with request-scoped plaintext credentials.

    The password is excluded from ``repr`` so an accidental log call cannot
    print it. Callers must not store this object beyond the request that
    resolved it.
    """

    id: int
    name: str
    group: str | None
    host: str
    http_port: int
    rtsp_port: int
    username: str
    password: str = field(repr=False)

    @property
    def base_url(self) -> str:
        """Build the DVR HTTP base URL without a trailing slash."""
        if self.http_port == DEFAULT_HTTP_PORT:
            return f"http://{_url_host(self.host)}"
        return f"http://{_url_host(self.host)}:{self.http_port}"



def parse_csv_rows(text: str) -> list[tuple[int, dict[str, str]]]:
    """Parse and size-check a CSV, returning (line number, values) pairs.

    Line numbers count the header as line 1. Only non-empty data rows are
    returned, and the limits apply to those rows.
    """
    if not isinstance(text, str):
        raise RegistryError("csv must be a string.")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise RegistryError("CSV is larger than 256 KB.")
    if not text.strip():
        raise RegistryError("CSV is empty.")

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise RegistryError("CSV is empty.") from None

    headers = [cell.strip().lower() for cell in header]
    if not headers or any(not cell for cell in headers):
        raise RegistryError("CSV header has an empty column name.")
    if len(set(headers)) != len(headers):
        raise RegistryError("CSV header has duplicate column names.")
    missing = [name for name in ("name", "host") if name not in headers]
    if missing:
        raise RegistryError(
            "CSV header must include: name, host. "
            "Missing: " + ", ".join(missing) + "."
        )

    rows: list[tuple[int, dict[str, str]]] = []
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue
        if len(rows) >= MAX_CSV_ROWS:
            raise RegistryError("CSV has more than 500 data rows.")
        values = {
            headers[index]: cells[index].strip()
            for index in range(len(headers))
            if index < len(cells)
        }
        for name in headers:
            values.setdefault(name, "")
        rows.append((reader.line_num, values))

    if not rows:
        raise RegistryError("CSV has no data rows.")
    return rows


class DeviceRegistry:
    """SQLite-backed groups, devices, and encrypted credentials."""

    def __init__(
        self,
        settings: RegistrySettings,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._settings = settings
        self._db_path = settings.db_path
        self._fernet = Fernet(settings.secret_key.encode("ascii"))
        self._clock = clock
        self._init_db()
        self._migrate_first_run()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def allow_public_hosts(self) -> bool:
        return self._settings.allow_public_hosts

    # -- database helpers -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    group_id INTEGER REFERENCES groups(id)
                        ON DELETE SET NULL,
                    host TEXT NOT NULL,
                    http_port INTEGER NOT NULL DEFAULT 80,
                    rtsp_port INTEGER NOT NULL DEFAULT 554,
                    username TEXT,
                    password_enc TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    last_seen TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS devices_host_port
                    ON devices(host, http_port);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_health (
                    device_id INTEGER PRIMARY KEY REFERENCES devices(id)
                        ON DELETE CASCADE,
                    checked_at TEXT,
                    state TEXT,
                    storage_state TEXT,
                    storage_disks TEXT,
                    storage_errors TEXT,
                    device_time TEXT,
                    error_class TEXT,
                    channels_json TEXT
                );
                """
            )
        self._migrate_health_schema()
        if os.name != "nt":
            with suppress(OSError):
                os.chmod(self._db_path, 0o600)

    def _migrate_health_schema(self) -> None:
        with self._db() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
            if "unused_channels" not in cols:
                conn.execute("ALTER TABLE devices ADD COLUMN unused_channels TEXT DEFAULT ''")
            if "stale_hours" not in cols:
                conn.execute("ALTER TABLE devices ADD COLUMN stale_hours INTEGER")
            # ensure table exists
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS device_health (
                    device_id INTEGER PRIMARY KEY REFERENCES devices(id)
                        ON DELETE CASCADE,
                    checked_at TEXT,
                    state TEXT,
                    storage_state TEXT,
                    storage_disks TEXT,
                    storage_errors TEXT,
                    device_time TEXT,
                    error_class TEXT,
                    channels_json TEXT
                );
                """
            )

    def _migrate_first_run(self) -> None:
        """Create one device from DVR_* once, without logging the values."""
        with self._db() as conn:
            done = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (_MIGRATION_KEY,)
            ).fetchone()
            if done is not None:
                return
            count = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
            if count:
                self._set_setting(conn, _MIGRATION_KEY, "1")
                return

            host_value = str(os.environ.get("DVR_HOST", "")).strip()
            username = str(os.environ.get("DVR_USER", "")).strip()
            password = str(os.environ.get("DVR_PASS", ""))
            if not (host_value and username and password):
                return

            parsed = _split_device_host(host_value)
            if parsed is None:
                logger.warning(
                    "First-run migration skipped: DVR_HOST is not usable"
                )
                return
            host, http_port = parsed
            now = _iso(self._clock())
            conn.execute(
                """
                INSERT INTO devices (
                    name, group_id, host, http_port, rtsp_port,
                    username, password_enc, notes, created_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Default device",
                    host,
                    http_port,
                    DEFAULT_RTSP_PORT,
                    username,
                    self._encrypt(password),
                    "Created from DVR_HOST, DVR_USER and DVR_PASS.",
                    now,
                ),
            )
            self._set_setting(conn, _MIGRATION_KEY, "1")
        logger.info("First-run migration created the default device.")

    @staticmethod
    def _set_setting(
        conn: sqlite3.Connection, key: str, value: str
    ) -> None:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _get_setting(self, key: str) -> str | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else row["value"]

    def _encrypt(self, password: str) -> str:
        return self._fernet.encrypt(password.encode("utf-8")).decode("ascii")

    def _decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            logger.warning(
                "A stored credential could not be decrypted; "
                "the encryption key may have changed"
            )
            return None

    # -- groups -----------------------------------------------------------

    def list_groups(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT g.id, g.name, COUNT(d.id) AS device_count
                FROM groups g
                LEFT JOIN devices d ON d.group_id = g.id
                GROUP BY g.id, g.name
                ORDER BY g.name COLLATE NOCASE
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "device_count": row["device_count"],
            }
            for row in rows
        ]

    def create_group(self, name: Any) -> dict[str, Any]:
        clean = normalize_name(name, "group name")
        try:
            with self._db() as conn:
                cursor = conn.execute(
                    "INSERT INTO groups (name) VALUES (?)", (clean,)
                )
                group_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            raise RegistryError(
                "A group with this name already exists.", 409
            ) from None
        return {"id": group_id, "name": clean, "device_count": 0}

    def update_group(self, group_id: int, name: Any) -> dict[str, Any]:
        clean = normalize_name(name, "group name")
        try:
            with self._db() as conn:
                row = conn.execute(
                    "SELECT id FROM groups WHERE id = ?", (group_id,)
                ).fetchone()
                if row is None:
                    raise RegistryError("Group not found.", 404)
                conn.execute(
                    "UPDATE groups SET name = ? WHERE id = ?",
                    (clean, group_id),
                )
                count = conn.execute(
                    "SELECT COUNT(*) FROM devices WHERE group_id = ?",
                    (group_id,),
                ).fetchone()[0]
        except sqlite3.IntegrityError:
            raise RegistryError(
                "A group with this name already exists.", 409
            ) from None
        return {
            "id": group_id,
            "name": clean,
            "device_count": count,
        }

    def delete_group(self, group_id: int) -> None:
        with self._db() as conn:
            cursor = conn.execute(
                "DELETE FROM groups WHERE id = ?", (group_id,)
            )
            if cursor.rowcount == 0:
                raise RegistryError("Group not found.", 404)

    # -- devices ----------------------------------------------------------

    def list_devices(
        self,
        *,
        group_id: int | None = None,
        ungrouped: bool = False,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if ungrouped:
            clauses.append("d.group_id IS NULL")
        elif group_id is not None:
            clauses.append("d.group_id = ?")
            params.append(group_id)
        if query:
            clauses.append(
                "(d.name LIKE ? COLLATE NOCASE OR "
                "d.host LIKE ? COLLATE NOCASE)"
            )
            like = f"%{query.strip()}%"
            params.extend((like, like))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._db() as conn:
            rows = conn.execute(
                f"""
                SELECT d.*, g.name AS group_name
                FROM devices d
                LEFT JOIN groups g ON g.id = d.group_id
                {where}
                ORDER BY d.name COLLATE NOCASE
                """,
                params,
            ).fetchall()
        return [_device_public(row) for row in rows]

    def _fetch_device(
        self, conn: sqlite3.Connection, device_id: int
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT d.*, g.name AS group_name
            FROM devices d
            LEFT JOIN groups g ON g.id = d.group_id
            WHERE d.id = ?
            """,
            (device_id,),
        ).fetchone()
        if row is None:
            raise RegistryError("Device not found.", 404)
        return row

    def get_device(self, device_id: int) -> dict[str, Any]:
        with self._db() as conn:
            row = self._fetch_device(conn, device_id)
        return _device_public(row)

    def resolve_device(self, device_id: int | None = None) -> ResolvedDevice:
        """Resolve a registry device and its effective credentials.

        A missing id is only accepted when exactly one device exists. Unknown
        ids and empty registries are 404s; a device with no usable own or
        default credentials is a 409. The plaintext password is returned only
        to the caller and is never included in the public device dictionary.
        """
        with self._db() as conn:
            if device_id is None:
                rows = conn.execute(
                    """
                    SELECT d.*, g.name AS group_name
                    FROM devices d
                    LEFT JOIN groups g ON g.id = d.group_id
                    ORDER BY d.id
                    """
                ).fetchall()
                if not rows:
                    raise RegistryError("No devices are registered.", 404)
                if len(rows) > 1:
                    raise RegistryError("device_id is required", 400)
                row = rows[0]
            else:
                row = self._fetch_device(conn, device_id)

        username, password = self.effective_credentials(row)
        if not username or not password:
            raise RegistryError("No credentials configured.", 409)

        return ResolvedDevice(
            id=row["id"],
            name=row["name"],
            group=row["group_name"],
            host=row["host"],
            http_port=row["http_port"],
            rtsp_port=row["rtsp_port"],
            username=username,
            password=password,
        )

    def _device_row(self, device_id: int) -> sqlite3.Row:
        with self._db() as conn:
            return self._fetch_device(conn, device_id)

    def _check_duplicates(
        self,
        conn: sqlite3.Connection,
        *,
        name: str,
        host: str,
        http_port: int,
        device_id: int | None = None,
    ) -> None:
        exclude = -1 if device_id is None else device_id
        by_name = conn.execute(
            "SELECT id FROM devices WHERE name = ? AND id != ?",
            (name, exclude),
        ).fetchone()
        if by_name is not None:
            raise RegistryError(
                "A device with this name already exists.", 409
            )
        by_host = conn.execute(
            "SELECT id FROM devices WHERE host = ? AND http_port = ? "
            "AND id != ?",
            (host, http_port, exclude),
        ).fetchone()
        if by_host is not None:
            raise RegistryError(
                "A device with this host and HTTP port already exists.", 409
            )

    def _group_or_none(
        self, conn: sqlite3.Connection, value: Any
    ) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise RegistryError("group_id must be a group number.")
        row = conn.execute(
            "SELECT id FROM groups WHERE id = ?", (value,)
        ).fetchone()
        if row is None:
            raise RegistryError("Group not found.", 404)
        return value

    def create_device(self, body: Mapping[str, Any]) -> dict[str, Any]:
        name = normalize_name(body.get("name"))
        host = normalize_host(
            body.get("host"),
            allow_public=self._settings.allow_public_hosts,
        )
        http_port = normalize_port(
            body.get("http_port", DEFAULT_HTTP_PORT), "http_port"
        )
        rtsp_port = normalize_port(
            body.get("rtsp_port", DEFAULT_RTSP_PORT), "rtsp_port"
        )
        notes = normalize_text(
            body.get("notes"), "notes", max_length=MAX_NOTES_LENGTH
        ) or ""

        username = normalize_text(
            body.get("username"),
            "username",
            max_length=MAX_USERNAME_LENGTH,
        )
        password = body.get("password")
        password_text = "" if password is None else str(password)
        if username is None:
            if password_text:
                raise RegistryError(
                    "username is required when a password is set."
                )
            password_enc = None
        else:
            if not password_text:
                raise RegistryError(
                    "password is required when a username is set."
                )
            if len(password_text) > MAX_PASSWORD_LENGTH:
                raise RegistryError(
                    "password must be at most "
                    f"{MAX_PASSWORD_LENGTH} characters."
                )
            password_enc = self._encrypt(password_text)

        with self._db() as conn:
            group_id = self._group_or_none(conn, body.get("group_id"))
            self._check_duplicates(
                conn,
                name=name,
                host=host,
                http_port=http_port,
            )
            cursor = conn.execute(
                """
                INSERT INTO devices (
                    name, group_id, host, http_port, rtsp_port,
                    username, password_enc, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    group_id,
                    host,
                    http_port,
                    rtsp_port,
                    username,
                    password_enc,
                    notes,
                    _iso(self._clock()),
                ),
            )
            row = self._fetch_device(conn, cursor.lastrowid)
        return _device_public(row)

    def update_device(
        self, device_id: int, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        with self._db() as conn:
            existing = self._fetch_device(conn, device_id)
            name = (
                normalize_name(body["name"])
                if "name" in body
                else existing["name"]
            )
            host = (
                normalize_host(
                    body["host"],
                    allow_public=self._settings.allow_public_hosts,
                )
                if "host" in body
                else existing["host"]
            )
            http_port = (
                normalize_port(body["http_port"], "http_port")
                if "http_port" in body
                else existing["http_port"]
            )
            rtsp_port = (
                normalize_port(body["rtsp_port"], "rtsp_port")
                if "rtsp_port" in body
                else existing["rtsp_port"]
            )
            notes = (
                normalize_text(
                    body["notes"],
                    "notes",
                    max_length=MAX_NOTES_LENGTH,
                )
                or ""
                if "notes" in body
                else (existing["notes"] or "")
            )
            group_id = (
                self._group_or_none(conn, body["group_id"])
                if "group_id" in body
                else existing["group_id"]
            )

            username = existing["username"]
            password_enc = existing["password_enc"]
            username_given = "username" in body
            password_given = "password" in body

            if username_given:
                username = normalize_text(
                    body["username"],
                    "username",
                    max_length=MAX_USERNAME_LENGTH,
                )
                if username is None:
                    password_enc = None

            if password_given:
                password_text = (
                    "" if body["password"] is None else str(body["password"])
                )
                if not password_text:
                    username = None
                    password_enc = None
                else:
                    if len(password_text) > MAX_PASSWORD_LENGTH:
                        raise RegistryError(
                            "password must be at most "
                            f"{MAX_PASSWORD_LENGTH} characters."
                        )
                    if username is None:
                        raise RegistryError(
                            "username is required when a password is set."
                        )
                    password_enc = self._encrypt(password_text)
            elif username is not None and not password_enc:
                raise RegistryError(
                    "password is required when a username is set."
                )
            elif username is None:
                password_enc = None

            self._check_duplicates(
                conn,
                name=name,
                host=host,
                http_port=http_port,
                device_id=device_id,
            )
            unused_channels = existing["unused_channels"] if "unused_channels" in existing.keys() else ""
            stale_hours = existing["stale_hours"] if "stale_hours" in existing.keys() else None
            if "unused_channels" in body:
                parsed = _parse_unused_channels(body["unused_channels"])
                out = []
                seen = set()
                for v in parsed:
                    if v in seen:
                        continue
                    seen.add(v)
                    out.append(v)
                unused_channels = ",".join(str(x) for x in sorted(out))
            if "stale_hours" in body:
                shv = body["stale_hours"]
                if shv is None:
                    stale_hours = None
                else:
                    try:
                        sh = int(shv)
                    except (TypeError, ValueError):
                        raise RegistryError("stale_hours must be an integer between 1 and 720 or null.")
                    if sh < 1 or sh > 720:
                        raise RegistryError("stale_hours must be an integer between 1 and 720 or null.")
                    stale_hours = sh
            conn.execute(
                """
                UPDATE devices SET
                    name = ?, group_id = ?, host = ?, http_port = ?,
                    rtsp_port = ?, username = ?, password_enc = ?,
                    notes = ?,
                    unused_channels = ?, stale_hours = ?
                WHERE id = ?
                """,
                (
                    name,
                    group_id,
                    host,
                    http_port,
                    rtsp_port,
                    username,
                    password_enc,
                    notes,
                    unused_channels if unused_channels is not None else "",
                    stale_hours,
                    device_id,
                ),
            )
            row = self._fetch_device(conn, device_id)
        return _device_public(row)

    def delete_device(self, device_id: int) -> None:
        with self._db() as conn:
            cursor = conn.execute(
                "DELETE FROM devices WHERE id = ?", (device_id,)
            )
            if cursor.rowcount == 0:
                raise RegistryError("Device not found.", 404)

    # -- credentials ------------------------------------------------------

    def get_default_credentials(self) -> dict[str, Any]:
        username = self._get_setting(_DEFAULT_USERNAME_KEY) or ""
        encrypted = self._get_setting(_DEFAULT_PASSWORD_KEY)
        return {"username": username, "has_password": bool(encrypted)}

    def set_default_credentials(
        self, username: Any, password: Any
    ) -> dict[str, Any]:
        clean_user = normalize_text(
            username,
            "username",
            max_length=MAX_USERNAME_LENGTH,
            required=True,
        )
        password_text = "" if password is None else str(password)
        if not password_text:
            raise RegistryError("password is required.")
        if len(password_text) > MAX_PASSWORD_LENGTH:
            raise RegistryError(
                f"password must be at most {MAX_PASSWORD_LENGTH} characters."
            )
        with self._db() as conn:
            self._set_setting(conn, _DEFAULT_USERNAME_KEY, clean_user)
            self._set_setting(
                conn, _DEFAULT_PASSWORD_KEY, self._encrypt(password_text)
            )
        return {"username": clean_user, "has_password": True}

    def effective_credentials(
        self, row: sqlite3.Row
    ) -> tuple[str | None, str | None]:
        if row["username"] and row["password_enc"]:
            password = self._decrypt(row["password_enc"])
            if password is not None:
                return row["username"], password
        username = self._get_setting(_DEFAULT_USERNAME_KEY)
        encrypted = self._get_setting(_DEFAULT_PASSWORD_KEY)
        if username and encrypted:
            password = self._decrypt(encrypted)
            if password is not None:
                return username, password
        return None, None

    # -- CSV import -------------------------------------------------------

    def import_csv(self, text: Any, *, dry_run: bool) -> dict[str, Any]:
        """Validate a CSV (dry run) or write only its valid rows."""
        rows = parse_csv_rows(text)
        results: list[dict[str, Any]] = []
        ok = 0
        errors = 0
        duplicates = 0
        imported = 0

        with self._db() as conn:
            groups = {
                row["name"].lower(): row["id"]
                for row in conn.execute("SELECT id, name FROM groups")
            }
            existing_names = {
                row[0].lower()
                for row in conn.execute("SELECT name FROM devices")
            }
            existing_hosts = {
                (row[0], row[1])
                for row in conn.execute(
                    "SELECT host, http_port FROM devices"
                )
            }
            seen_names: set[str] = set()
            seen_hosts: set[tuple[str, int]] = set()

            for line, values in rows:
                try:
                    fields = self._import_fields(values)
                except RegistryError as exc:
                    results.append(
                        {
                            "row": line,
                            "status": "error",
                            "message": str(exc),
                        }
                    )
                    errors += 1
                    continue

                name_key = fields["name"].lower()
                host_key = (fields["host"], fields["http_port"])
                if name_key in existing_names or name_key in seen_names:
                    results.append(
                        {
                            "row": line,
                            "status": "duplicate",
                            "message": "A device with this name exists.",
                        }
                    )
                    duplicates += 1
                    continue
                if host_key in existing_hosts or host_key in seen_hosts:
                    results.append(
                        {
                            "row": line,
                            "status": "duplicate",
                            "message": (
                                "A device with this host and HTTP port "
                                "exists."
                            ),
                        }
                    )
                    duplicates += 1
                    continue

                group_id = None
                group_name = fields["group"]
                if group_name:
                    group_id = groups.get(group_name.lower())
                    if group_id is None:
                        if dry_run:
                            group_id = 0
                        else:
                            cursor = conn.execute(
                                "INSERT INTO groups (name) VALUES (?)",
                                (group_name,),
                            )
                            group_id = cursor.lastrowid
                        groups[group_name.lower()] = group_id

                if dry_run:
                    message = "Ready to add."
                else:
                    conn.execute(
                        """
                        INSERT INTO devices (
                            name, group_id, host, http_port, rtsp_port,
                            username, password_enc, notes, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)
                        """,
                        (
                            fields["name"],
                            group_id,
                            fields["host"],
                            fields["http_port"],
                            fields["rtsp_port"],
                            fields["username"],
                            fields["password_enc"],
                            _iso(self._clock()),
                        ),
                    )
                    imported += 1
                    message = "Added."
                seen_names.add(name_key)
                seen_hosts.add(host_key)
                ok += 1
                results.append(
                    {"row": line, "status": "ok", "message": message}
                )

        return {
            "dry_run": dry_run,
            "counts": {
                "rows": len(rows),
                "ok": ok,
                "error": errors,
                "duplicate": duplicates,
                "imported": imported,
            },
            "rows": results,
        }

    @staticmethod
    def _csv_port(value: str | None, field: str) -> int:
        text = str(value or "").strip()
        if not text:
            return (
                DEFAULT_HTTP_PORT
                if field == "http_port"
                else DEFAULT_RTSP_PORT
            )
        try:
            number = int(text, 10)
        except ValueError:
            raise RegistryError(f"{field} must be a whole number.") from None
        return normalize_port(number, field)

    def _import_fields(
        self, values: Mapping[str, str]
    ) -> dict[str, Any]:
        name = normalize_name(values.get("name"))
        host = normalize_host(
            values.get("host"),
            allow_public=self._settings.allow_public_hosts,
        )
        http_port = self._csv_port(values.get("http_port"), "http_port")
        rtsp_port = self._csv_port(values.get("rtsp_port"), "rtsp_port")
        group_raw = values.get("group", "").strip()
        group_name = normalize_name(group_raw, "group") if group_raw else None
        username = normalize_text(
            values.get("username"),
            "username",
            max_length=MAX_USERNAME_LENGTH,
        )
        password_text = values.get("password", "")
        if username is None and password_text:
            raise RegistryError(
                "username is required when a password is set."
            )
        if username is not None and not password_text:
            raise RegistryError(
                "password is required when a username is set."
            )
        if len(password_text) > MAX_PASSWORD_LENGTH:
            raise RegistryError(
                f"password must be at most {MAX_PASSWORD_LENGTH} characters."
            )
        password_enc = (
            self._encrypt(password_text) if password_text else None
        )
        return {
            "name": name,
            "group": group_name,
            "host": host,
            "http_port": http_port,
            "rtsp_port": rtsp_port,
            "username": username,
            "password_enc": password_enc,
        }

    # -- status support ---------------------------------------------------

    def status_targets(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT d.id, d.name, d.host, d.http_port, d.rtsp_port,
                       d.last_seen, g.name AS group_name
                FROM devices d
                LEFT JOIN groups g ON g.id = d.group_id
                ORDER BY d.name COLLATE NOCASE
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "group": row["group_name"],
                "host": row["host"],
                "http_port": row["http_port"],
                "rtsp_port": row["rtsp_port"],
                "last_seen": row["last_seen"],
            }
            for row in rows
        ]

    def set_last_seen(self, entries: Mapping[int, str]) -> None:
        if not entries:
            return
        with self._db() as conn:
            conn.executemany(
                "UPDATE devices SET last_seen = ? WHERE id = ?",
                [(value, key) for key, value in entries.items()],
            )

    # -- connection test --------------------------------------------------

    def test_device(
        self, device_id: int, *, timeout: float = DEFAULT_TEST_TIMEOUT
    ) -> dict[str, Any]:
        """Log in and log out once. The result never contains a secret."""
        row = self._device_row(device_id)
        username, password = self.effective_credentials(row)
        if not username or not password:
            return {"ok": False, "reason": "authentication failed"}

        client: DvrClient | None = None
        try:
            client = DvrClient(
                f"{row['host']}:{row['http_port']}",
                username,
                password,
                timeout=timeout,
            )
            client.login()
            return {"ok": True}
        except DvrAuthError as exc:
            # login() wraps transport failures in DvrAuthError, so the safe
            # message decides whether the recorder answered at all.
            if _is_unreachable(exc):
                return {"ok": False, "reason": "unreachable"}
            if _is_unexpected(exc):
                return {"ok": False, "reason": "other"}
            return {"ok": False, "reason": "authentication failed"}
        except DvrError as exc:
            if _is_unreachable(exc):
                return {"ok": False, "reason": "unreachable"}
            return {"ok": False, "reason": "other"}
        except Exception as exc:  # defensive: never expose the exception
            logger.warning(
                "Device test failed: %s", type(exc).__name__
            )
            return {"ok": False, "reason": "other"}
        finally:
            if client is not None:
                try:
                    client.logout()
                except DvrLogoutError as exc:
                    logger.warning(
                        "Device test logout failed: %s",
                        type(exc).__name__,
                    )
                except Exception as exc:  # defensive
                    logger.warning(
                        "Device test logout failed: %s",
                        type(exc).__name__,
                    )
                finally:
                    client.close()


def _is_unreachable(exc: BaseException) -> bool:
    """True when the DVR client reported a transport failure."""
    text = str(exc).lower()
    return "timed out" in text or "could not contact" in text


def _is_unexpected(exc: BaseException) -> bool:
    """True when the DVR answered with something that was not JSON."""
    text = str(exc).lower()
    return (
        "response that was not json" in text
        or "unexpected response shape" in text
    )


def _split_device_host(value: str) -> tuple[str, int] | None:
    """Parse DVR_HOST (host, host:port, or URL) for the first-run device."""
    raw = value.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "//" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port or DEFAULT_HTTP_PORT
    except ValueError:
        return None
    host = parts.hostname
    if not host:
        return None
    try:
        clean_host = normalize_host(host, allow_public=True)
        clean_port = normalize_port(port, "http_port")
    except RegistryError:
        return None
    return clean_host, clean_port


class StatusChecker:
    """Periodic TCP-only reachability check with an in-memory snapshot."""

    def __init__(
        self,
        registry: DeviceRegistry,
        *,
        interval: float = DEFAULT_STATUS_INTERVAL,
        concurrency: int = DEFAULT_STATUS_CONCURRENCY,
        timeout: float = DEFAULT_STATUS_TIMEOUT,
        clock: Callable[[], datetime] = _utc_now,
        connector: Callable[[str, int], Any] | None = None,
    ) -> None:
        self._registry = registry
        self._interval = interval
        self._concurrency = max(1, concurrency)
        self._timeout = timeout
        self._clock = clock
        self._connector = connector or asyncio.open_connection
        self._results: dict[int, dict[str, Any]] = {}
        self._round_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except StatusRoundBusy:
                pass
            except Exception as exc:  # keep the checker alive
                logger.warning(
                    "Status check failed: %s", type(exc).__name__
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval
                )
            except asyncio.TimeoutError:
                continue

    async def _port_open(self, host: str, port: int) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                self._connector(host, port), timeout=self._timeout
            )
        except (asyncio.TimeoutError, OSError, ValueError):
            return False
        with suppress(Exception):
            writer.close()
            await writer.wait_closed()
        return True

    async def refresh(self) -> dict[str, Any]:
        """Run one round. Raises StatusRoundBusy if one is already running."""
        if self._round_lock.locked():
            raise StatusRoundBusy()
        async with self._round_lock:
            targets = await asyncio.to_thread(
                self._registry.status_targets
            )
            semaphore = asyncio.Semaphore(self._concurrency)

            async def check(device: Mapping[str, Any]) -> dict[str, Any]:
                async with semaphore:
                    http_ok = await self._port_open(
                        device["host"], device["http_port"]
                    )
                    rtsp_ok = await self._port_open(
                        device["host"], device["rtsp_port"]
                    )
                if http_ok and rtsp_ok:
                    state = "online"
                elif http_ok or rtsp_ok:
                    state = "partial"
                else:
                    state = "offline"
                return {
                    "id": device["id"],
                    "http_ok": http_ok,
                    "rtsp_ok": rtsp_ok,
                    "state": state,
                }

            checked = await asyncio.gather(
                *(check(device) for device in targets)
            )
            now = _iso(self._clock())
            seen: dict[int, str] = {}
            results: dict[int, dict[str, Any]] = {}
            for item in checked:
                last_seen = now if (item["http_ok"] or item["rtsp_ok"]) else None
                if last_seen:
                    seen[item["id"]] = last_seen
                results[item["id"]] = {
                    **item,
                    "last_checked": now,
                    "last_seen": last_seen,
                }
            if seen:
                await asyncio.to_thread(self._registry.set_last_seen, seen)
            self._results = results
            return self._snapshot_from(targets)

    async def snapshot(self) -> dict[str, Any]:
        """Return the cached round plus stored last_seen. No network work."""
        targets = await asyncio.to_thread(self._registry.status_targets)
        return self._snapshot_from(targets)

    def _snapshot_from(
        self, targets: list[dict[str, Any]]
    ) -> dict[str, Any]:
        devices: list[dict[str, Any]] = []
        counts = {
            "total": len(targets),
            "online": 0,
            "partial": 0,
            "offline": 0,
            "unknown": 0,
        }
        for target in targets:
            cached = self._results.get(target["id"])
            if cached is None:
                state = "unknown"
                http_ok = None
                rtsp_ok = None
                last_checked = None
            else:
                state = cached["state"]
                http_ok = cached["http_ok"]
                rtsp_ok = cached["rtsp_ok"]
                last_checked = cached["last_checked"]
            last_seen = (
                cached["last_seen"]
                if cached is not None and cached["last_seen"]
                else target["last_seen"]
            )
            counts[state] += 1
            devices.append(
                {
                    "id": target["id"],
                    "name": target["name"],
                    "group": target["group"],
                    "host": target["host"],
                    "http_port": target["http_port"],
                    "rtsp_port": target["rtsp_port"],
                    "http_ok": http_ok,
                    "rtsp_ok": rtsp_ok,
                    "state": state,
                    "last_checked": last_checked,
                    "last_seen": last_seen,
                }
            )
        return {
            "counts": counts,
            "checked_at": _iso(self._clock()),
            "devices": devices,
        }


    def get_all_devices(self) -> list[dict[str, Any]]:
        return self.list_devices()

    def get_device_health(self, device_id: int) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM device_health WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        res = dict(row)
        try:
            if res.get("channels_json"):
                res["channels"] = json.loads(res["channels_json"])
            else:
                res["channels"] = []
        except Exception:
            res["channels"] = []
        res.pop("channels_json", None)
        if res.get("storage_disks"):
            try:
                res["storage_disks"] = json.loads(res["storage_disks"])
            except Exception:
                pass
        return res

    def get_all_health(self) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM device_health").fetchall()
        for row in rows:
            res = dict(row)
            try:
                if res.get("channels_json"):
                    res["channels"] = json.loads(res["channels_json"])
                else:
                    res["channels"] = []
            except Exception:
                res["channels"] = []
            res.pop("channels_json", None)
            if res.get("storage_disks"):
                try:
                    res["storage_disks"] = json.loads(res["storage_disks"])
                except Exception:
                    pass
            out[res["device_id"]] = res
        return out

    def save_health(self, device_id: int, data: dict[str, Any]) -> None:
        channels_json = json.dumps(data.get("channels") or [])
        storage_disks = json.dumps(data.get("storage_disks") or [])
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO device_health
                    (device_id, checked_at, state, storage_state, storage_disks,
                     storage_errors, device_time, error_class, channels_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    checked_at = excluded.checked_at,
                    state = excluded.state,
                    storage_state = excluded.storage_state,
                    storage_disks = excluded.storage_disks,
                    storage_errors = excluded.storage_errors,
                    device_time = excluded.device_time,
                    error_class = excluded.error_class,
                    channels_json = excluded.channels_json
                """,
                (
                    device_id,
                    data.get("checked_at"),
                    data.get("state"),
                    data.get("storage_state"),
                    storage_disks,
                    data.get("storage_errors"),
                    data.get("device_time"),
                    data.get("error_class"),
                    channels_json,
                ),
            )
