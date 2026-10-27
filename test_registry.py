"""Offline tests for the Phase 5a device registry.

Run with:

    .\\.venv\\Scripts\\python.exe -m unittest test_registry -v

No DVR is contacted: the connection test uses an httpx mock transport and the
status checker uses fake sockets. State lives in a temporary directory, and the
tests never print a password or an encryption key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import httpx
from cryptography.fernet import Fernet

import app as app_module
import registry as registry_module
from dvr_client import DvrClient
from registry import (
    MAX_CSV_ROWS,
    REGISTRY_DISABLED_MESSAGE,
    DeviceRegistry,
    RegistryConfigError,
    RegistryError,
    RegistrySettings,
    StatusChecker,
    StatusRoundBusy,
)

SECRET_KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
PASSWORD = "Zx9-plain-secret-123"
CSV_HEADER = "name,group,host,http_port,rtsp_port,username,password"
FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
FIXED_ISO = "2026-01-02T03:04:05+00:00"


class FakeWriter:
    """Minimal stand-in for asyncio.StreamWriter."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeNet:
    """Fake TCP connector that records concurrency and can time out."""

    def __init__(
        self,
        open_ports: set[tuple[str, int]] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.open_ports = set(open_ports or ())
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.calls: list[tuple[str, int]] = []
        self.writers: list[FakeWriter] = []

    def is_open(self, host: str, port: int) -> bool:
        return (host, port) in self.open_ports

    async def __call__(self, host: str, port: int):
        self.calls.append((host, port))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if not self.is_open(host, port):
                raise OSError("connection refused")
            writer = FakeWriter()
            self.writers.append(writer)
            return object(), writer
        finally:
            self.active -= 1


async def _refused(host: str, port: int):
    raise OSError("connection refused")


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _settings(
    db_path: Path,
    *,
    key: str = SECRET_KEY,
    allow_public: bool = False,
) -> RegistrySettings:
    return RegistrySettings(
        db_path=db_path,
        secret_key=key,
        status_interval=3600.0,
        status_concurrency=4,
        status_timeout=0.5,
        allow_public_hosts=allow_public,
    )


def _device_body(**overrides) -> dict:
    body = {
        "name": "Cam A",
        "host": "192.168.1.10",
        "http_port": 80,
        "rtsp_port": 554,
        "notes": "roof",
        "username": "admin",
        "password": PASSWORD,
    }
    body.update(overrides)
    return body


class RegistryCase(unittest.IsolatedAsyncioTestCase):
    """Temp SQLite database and a patched, empty DVR_* environment."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        env = mock.patch.dict(
            os.environ,
            {"DVR_HOST": "", "DVR_USER": "", "DVR_PASS": ""},
        )
        env.start()
        self.addCleanup(env.stop)
        self.registry = self.make_registry()

    def make_registry(
        self, *, key: str = SECRET_KEY, allow_public: bool = False
    ) -> DeviceRegistry:
        return DeviceRegistry(
            _settings(
                self.dir / "dvr.db",
                key=key,
                allow_public=allow_public,
            )
        )

    def db_bytes(self) -> bytes:
        return (self.dir / "dvr.db").read_bytes()

    def add_device(self, **overrides) -> dict:
        return self.registry.create_device(_device_body(**overrides))


class SettingsTests(unittest.TestCase):
    def from_env(self, **extra) -> RegistrySettings:
        env = {"APP_SECRET_KEY": SECRET_KEY}
        env.update(extra)
        return RegistrySettings.from_env(Path("base"), env=env)

    def test_missing_key_is_a_config_error(self) -> None:
        with self.assertRaises(RegistryConfigError):
            RegistrySettings.from_env(Path("base"), env={})
        with self.assertRaises(RegistryConfigError):
            RegistrySettings.from_env(
                Path("base"), env={"APP_SECRET_KEY": "   "}
            )

    def test_invalid_key_is_a_config_error(self) -> None:
        with self.assertRaises(RegistryConfigError):
            RegistrySettings.from_env(
                Path("base"), env={"APP_SECRET_KEY": "not-a-fernet-key"}
            )

    def test_defaults(self) -> None:
        settings = self.from_env()
        self.assertEqual(settings.db_path, Path("base") / "data" / "dvr.db")
        self.assertEqual(settings.status_interval, 60.0)
        self.assertEqual(settings.status_concurrency, 10)
        self.assertEqual(settings.status_timeout, 2.0)
        self.assertFalse(settings.allow_public_hosts)

    def test_invalid_numbers_fall_back(self) -> None:
        settings = self.from_env(
            STATUS_INTERVAL_SECONDS="abc",
            STATUS_CONCURRENCY="0",
            STATUS_TIMEOUT_SECONDS="-1",
        )
        self.assertEqual(settings.status_interval, 60.0)
        self.assertEqual(settings.status_concurrency, 10)
        self.assertEqual(settings.status_timeout, 2.0)

    def test_overrides_and_absolute_dir(self) -> None:
        absolute = Path(tempfile.gettempdir()) / "registry-abs"
        settings = self.from_env(
            DATA_DIR=str(absolute),
            STATUS_INTERVAL_SECONDS="15",
            STATUS_CONCURRENCY="3",
            STATUS_TIMEOUT_SECONDS="0.5",
            ALLOW_PUBLIC_HOSTS="true",
        )
        self.assertEqual(settings.db_path, absolute / "dvr.db")
        self.assertEqual(settings.status_interval, 15.0)
        self.assertEqual(settings.status_concurrency, 3)
        self.assertEqual(settings.status_timeout, 0.5)
        self.assertTrue(settings.allow_public_hosts)


class StorageTests(RegistryCase):
    def test_password_is_encrypted_and_never_in_the_database(self) -> None:
        device = self.add_device()
        self.assertTrue(device["has_own_credentials"])
        username, password = self.registry.effective_credentials(
            self.registry._device_row(device["id"])
        )
        self.assertEqual((username, password), ("admin", PASSWORD))
        self.assertNotIn(PASSWORD.encode("utf-8"), self.db_bytes())
        serialized = json.dumps(device)
        self.assertNotIn(PASSWORD, serialized)
        self.assertNotIn("password", serialized)

    def test_default_credentials_are_used_without_an_override(self) -> None:
        self.registry.set_default_credentials("root", PASSWORD)
        device = self.registry.create_device(
            {"name": "Cam B", "host": "cam-b.local"}
        )
        self.assertFalse(device["has_own_credentials"])
        row = self.registry._device_row(device["id"])
        self.assertEqual(
            self.registry.effective_credentials(row), ("root", PASSWORD)
        )
        override = self.add_device()
        row = self.registry._device_row(override["id"])
        self.assertEqual(
            self.registry.effective_credentials(row), ("admin", PASSWORD)
        )

    def test_a_corrupt_override_falls_back_to_the_default(self) -> None:
        self.registry.set_default_credentials("root", PASSWORD)
        device = self.add_device()
        with self.registry._db() as conn:
            conn.execute(
                "UPDATE devices SET password_enc = ? WHERE id = ?",
                ("not-a-token", device["id"]),
            )
        row = self.registry._device_row(device["id"])
        with self.assertLogs("registry", level="WARNING"):
            self.assertEqual(
                self.registry.effective_credentials(row),
                ("root", PASSWORD),
            )

    def test_default_credentials_response_has_no_password(self) -> None:
        result = self.registry.set_default_credentials("root", PASSWORD)
        self.assertEqual(result, {"username": "root", "has_password": True})
        self.assertEqual(
            self.registry.get_default_credentials(),
            {"username": "root", "has_password": True},
        )
        self.assertNotIn(PASSWORD, json.dumps(result))

    def test_update_semantics(self) -> None:
        device = self.add_device()
        device_id = device["id"]

        kept = self.registry.update_device(device_id, {"name": "Cam A2"})
        self.assertTrue(kept["has_own_credentials"])
        row = self.registry._device_row(device_id)
        self.assertEqual(
            self.registry.effective_credentials(row), ("admin", PASSWORD)
        )

        renamed = self.registry.update_device(
            device_id, {"username": "operator"}
        )
        row = self.registry._device_row(device_id)
        self.assertEqual(
            self.registry.effective_credentials(row),
            ("operator", PASSWORD),
        )
        self.assertTrue(renamed["has_own_credentials"])

        cleared = self.registry.update_device(
            device_id, {"username": "", "password": ""}
        )
        self.assertFalse(cleared["has_own_credentials"])
        row = self.registry._device_row(device_id)
        self.assertEqual(self.registry.effective_credentials(row), (None, None))

        no_creds = self.registry.update_device(
            device_id, {"password": ""}
        )
        self.assertFalse(no_creds["has_own_credentials"])

    def test_username_only_on_a_device_without_credentials_is_rejected(
        self,
    ) -> None:
        device = self.registry.create_device(
            {"name": "Cam C", "host": "192.168.1.12"}
        )
        with self.assertRaises(RegistryError):
            self.registry.update_device(
                device["id"], {"username": "admin"}
            )

    def test_groups_and_deletion(self) -> None:
        group = self.registry.create_group("Floor 1")
        device = self.add_device(group_id=group["id"])
        self.assertEqual(device["group"], "Floor 1")
        self.assertEqual(
            self.registry.list_groups()[0]["device_count"], 1
        )
        updated = self.registry.update_group(group["id"], "Floor 2")
        self.assertEqual(updated["name"], "Floor 2")
        self.registry.delete_group(group["id"])
        moved = self.registry.get_device(device["id"])
        self.assertIsNone(moved["group_id"])
        self.assertIsNone(moved["group"])

    def test_duplicate_names_and_hosts(self) -> None:
        self.add_device()
        with self.assertRaises(RegistryError) as caught:
            self.add_device(name="cam a")
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(RegistryError) as caught:
            self.add_device(name="Cam B", host="192.168.1.10")
        self.assertEqual(caught.exception.status_code, 409)
        other = self.add_device(name="Cam C", http_port=8080)
        self.assertEqual(other["http_port"], 8080)
        self.add_device(name="Cam D", host="cam-d.local", http_port=80)

    def test_group_name_duplicates(self) -> None:
        self.registry.create_group("Alpha")
        with self.assertRaises(RegistryError) as caught:
            self.registry.create_group("alpha")
        self.assertEqual(caught.exception.status_code, 409)

    def test_validation_rules(self) -> None:
        cases = [
            {"name": ""},
            {"name": "x" * 65},
            {"host": ""},
            {"host": "192.168.1.300"},
            {"host": "8.8.8.8"},
            {"host": "bad_host!"},
            {"http_port": 0},
            {"http_port": 65536},
            {"http_port": "80"},
            {"rtsp_port": 0},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(RegistryError):
                    self.add_device(**overrides)
        loopback = self.add_device(name="Local", host="127.0.0.1")
        self.assertEqual(loopback["host"], "127.0.0.1")
        link_local = self.add_device(
            name="Link", host="169.254.1.2", http_port=81
        )
        self.assertEqual(link_local["host"], "169.254.1.2")
        hostname = self.add_device(
            name="Host", host="DVR-Example.local", http_port=82
        )
        self.assertEqual(hostname["host"], "dvr-example.local")
        public = self.make_registry(allow_public=True)
        allowed = public.create_device(
            {"name": "Public", "host": "8.8.8.8"}
        )
        self.assertEqual(allowed["host"], "8.8.8.8")

    def test_public_hosts_stay_disabled_by_default(self) -> None:
        with self.assertRaises(RegistryError):
            self.add_device(host="1.1.1.1")

    def test_filtering_and_search(self) -> None:
        group = self.registry.create_group("Group A")
        self.add_device(name="Alpha", group_id=group["id"])
        self.add_device(name="Beta", host="192.168.1.20")
        self.assertEqual(
            [item["name"] for item in self.registry.list_devices()],
            ["Alpha", "Beta"],
        )
        grouped = self.registry.list_devices(group_id=group["id"])
        self.assertEqual([item["name"] for item in grouped], ["Alpha"])
        ungrouped = self.registry.list_devices(ungrouped=True)
        self.assertEqual([item["name"] for item in ungrouped], ["Beta"])
        found = self.registry.list_devices(query="bet")
        self.assertEqual([item["name"] for item in found], ["Beta"])

    def test_csv_dry_run_writes_nothing(self) -> None:
        self.add_device(name="Existing", host="192.168.1.30")
        text = "\n".join(
            [
                CSV_HEADER,
                "New,Floor 9,192.168.1.40,80,554,,",
                "Broken,,999.1.1.1,,,,",
                "Existing,,192.168.1.30,,,, ",
                "New,,192.168.1.41,,,,",
                "DupHost,,192.168.1.40,,,, ",
            ]
        ) + "\n"
        result = self.registry.import_csv(text, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["counts"]["rows"], 5)
        self.assertEqual(result["counts"]["ok"], 1)
        self.assertEqual(result["counts"]["error"], 1)
        self.assertEqual(result["counts"]["duplicate"], 3)
        self.assertEqual(result["counts"]["imported"], 0)
        self.assertEqual(
            [row["status"] for row in result["rows"]],
            ["ok", "error", "duplicate", "duplicate", "duplicate"],
        )
        self.assertEqual(
            [row["row"] for row in result["rows"]], [2, 3, 4, 5, 6]
        )
        self.assertEqual(len(self.registry.list_devices()), 1)
        self.assertEqual(self.registry.list_groups(), [])

    def test_csv_real_import_writes_only_valid_rows(self) -> None:
        text = "\n".join(
            [
                CSV_HEADER,
                "Cam 1,Floor 2,192.168.1.50,80,554,admin," + PASSWORD,
                "Cam 2,,192.168.1.51,81,555,,,",
                "Bad,,192.168.1.52,0,,,",
            ]
        )
        result = self.registry.import_csv(text, dry_run=False)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["counts"]["imported"], 2)
        self.assertEqual(result["counts"]["error"], 1)
        self.assertNotIn(PASSWORD, json.dumps(result))
        names = [item["name"] for item in self.registry.list_devices()]
        self.assertEqual(names, ["Cam 1", "Cam 2"])
        groups = self.registry.list_groups()
        self.assertEqual([group["name"] for group in groups], ["Floor 2"])
        self.assertNotIn(PASSWORD.encode("utf-8"), self.db_bytes())

        devices = self.registry.list_devices()
        first = devices[0]
        self.assertTrue(first["has_own_credentials"])
        self.assertEqual(first["http_port"], 80)
        second = devices[1]
        self.assertFalse(second["has_own_credentials"])
        self.assertEqual(second["http_port"], 81)
        self.assertEqual(second["rtsp_port"], 555)
        row = self.registry._device_row(first["id"])
        self.assertEqual(
            self.registry.effective_credentials(row), ("admin", PASSWORD)
        )

    def test_csv_limits(self) -> None:
        too_many = [CSV_HEADER] + [
            f"Cam {index},,192.168.1.{index % 200 + 1},80,554,,"
            for index in range(MAX_CSV_ROWS + 1)
        ]
        with self.assertRaises(RegistryError):
            self.registry.import_csv("\n".join(too_many), dry_run=True)

        oversized = CSV_HEADER + "\n" + "a" * (256 * 1024) + ",1.2.3.4\n"
        with self.assertRaises(RegistryError):
            self.registry.import_csv(oversized, dry_run=True)

        with self.assertRaises(RegistryError):
            self.registry.import_csv("name,host\n", dry_run=True)
        with self.assertRaises(RegistryError):
            self.registry.import_csv("name,host\n,,,,,\n", dry_run=True)
        with self.assertRaises(RegistryError):
            self.registry.import_csv(
                "name\nOnlyName\n", dry_run=True
            )
        with self.assertRaises(RegistryError):
            self.registry.import_csv(123, dry_run=True)

    def test_csv_bad_ports_are_row_errors(self) -> None:
        text = "\n".join(
            [
                CSV_HEADER,
                "Cam, ,192.168.1.60,http,554,,",
                "Cam2,,192.168.1.61,80,rtsp,,",
            ]
        )
        result = self.registry.import_csv(text, dry_run=True)
        self.assertEqual(result["counts"]["error"], 2)
        self.assertIn("whole number", result["rows"][0]["message"])

    def test_first_run_migration_once(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "DVR_HOST": "192.168.1.70:8080",
                "DVR_USER": "admin",
                "DVR_PASS": PASSWORD,
            },
        ):
            migrated = self.make_registry()
        devices = migrated.list_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["name"], "Default device")
        self.assertEqual(devices[0]["http_port"], 8080)
        self.assertNotIn(PASSWORD.encode("utf-8"), self.db_bytes())
        row = migrated._device_row(devices[0]["id"])
        self.assertEqual(
            migrated.effective_credentials(row), ("admin", PASSWORD)
        )

        with mock.patch.dict(
            os.environ,
            {
                "DVR_HOST": "192.168.1.71",
                "DVR_USER": "second",
                "DVR_PASS": PASSWORD,
            },
        ):
            again = self.make_registry()
        self.assertEqual(len(again.list_devices()), 1)

    def test_migration_skips_when_devices_exist(self) -> None:
        self.add_device()
        with mock.patch.dict(
            os.environ,
            {
                "DVR_HOST": "192.168.1.72",
                "DVR_USER": "admin",
                "DVR_PASS": PASSWORD,
            },
        ):
            again = self.make_registry()
        names = [item["name"] for item in again.list_devices()]
        self.assertEqual(names, ["Cam A"])

    def test_migration_without_credentials_writes_nothing(self) -> None:
        self.assertEqual(self.registry.list_devices(), [])

    def test_password_never_in_logs(self) -> None:
        handler = _ListHandler()
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(handler.close)

        device = self.add_device()
        self.registry.update_device(
            device["id"], {"password": PASSWORD + "-2"}
        )
        self.registry.import_csv(
            CSV_HEADER + "\nCam,,192.168.1.80,,,," + PASSWORD,
            dry_run=True,
        )
        self.registry.set_default_credentials("root", PASSWORD)
        with self.assertRaises(RegistryError):
            self.registry.create_device(
                {"name": "X", "host": "public.example", "password": "y"}
            )
        text = "\n".join(handler.messages)
        self.assertNotIn(PASSWORD, text)


class ApiTests(RegistryCase):
    def setUp(self) -> None:
        super().setUp()
        self.net = FakeNet()
        self.checker = StatusChecker(
            self.registry,
            interval=3600.0,
            concurrency=4,
            timeout=0.3,
            connector=self.net,
        )
        app_module.app.state.registry = self.registry
        app_module.app.state.status_checker = self.checker
        self.addCleanup(self._reset_state)

    def _reset_state(self) -> None:
        app_module.app.state.registry = None
        app_module.app.state.status_checker = None

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.app),
            base_url="http://testserver",
        )

    async def test_registry_disabled_returns_503_and_app_still_works(
        self,
    ) -> None:
        app_module.app.state.registry = None
        app_module.app.state.status_checker = None
        async with self.client() as client:
            for method, path, body in [
                ("GET", "/api/devices", None),
                ("POST", "/api/groups", {"name": "G"}),
                ("GET", "/api/status", None),
                ("POST", "/api/status/refresh", None),
                ("PUT", "/api/settings/default-credentials", {}),
                ("POST", "/api/devices/import", {"csv": CSV_HEADER}),
            ]:
                response = await client.request(method, path, json=body)
                self.assertEqual(response.status_code, 503, path)
                self.assertEqual(
                    response.json(),
                    {"error": REGISTRY_DISABLED_MESSAGE},
                )
            index = await client.get("/")
            self.assertEqual(index.status_code, 200)
            self.assertIn("DVR overview", index.text)
            page = await client.get("/devices")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Add device", page.text)

    async def test_content_type_is_enforced(self) -> None:
        async with self.client() as client:
            wrong = await client.post(
                "/api/groups",
                content=b"name=G",
                headers={"Content-Type": "text/plain"},
            )
            self.assertEqual(wrong.status_code, 415)
            broken = await client.post(
                "/api/groups",
                content=b"{",
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(broken.status_code, 400)
            not_object = await client.post("/api/groups", json=[1, 2])
            self.assertEqual(not_object.status_code, 400)

    async def test_device_and_group_lifecycle(self) -> None:
        async with self.client() as client:
            group = await client.post("/api/groups", json={"name": "Roof"})
            self.assertEqual(group.status_code, 201)
            group_id = group.json()["group"]["id"]

            created = await client.post(
                "/api/devices",
                json=_device_body(group_id=group_id),
            )
            self.assertEqual(created.status_code, 201)
            device = created.json()["device"]
            device_id = device["id"]
            self.assertEqual(device["group"], "Roof")
            self.assertTrue(device["has_own_credentials"])
            self.assertNotIn(PASSWORD, created.text)

            listed = await client.get("/api/devices", params={"q": "cam"})
            self.assertEqual(len(listed.json()["devices"]), 1)
            self.assertNotIn(PASSWORD, listed.text)
            self.assertNotIn("password", listed.text)

            kept = await client.put(
                f"/api/devices/{device_id}", json={"notes": "updated"}
            )
            self.assertEqual(kept.status_code, 200)
            self.assertTrue(kept.json()["device"]["has_own_credentials"])
            self.assertNotIn(PASSWORD, kept.text)

            cleared = await client.put(
                f"/api/devices/{device_id}",
                json={"username": "", "password": ""},
            )
            self.assertFalse(
                cleared.json()["device"]["has_own_credentials"]
            )

            bad = await client.post(
                "/api/devices", json={"name": "x", "host": "8.8.8.8"}
            )
            self.assertEqual(bad.status_code, 400)
            duplicate = await client.post(
                "/api/devices", json=_device_body(name="Cam A")
            )
            self.assertEqual(duplicate.status_code, 409)

            deleted_group = await client.delete(f"/api/groups/{group_id}")
            self.assertEqual(deleted_group.status_code, 200)
            moved = await client.get("/api/devices")
            self.assertIsNone(moved.json()["devices"][0]["group_id"])

            deleted = await client.delete(f"/api/devices/{device_id}")
            self.assertEqual(deleted.status_code, 200)
            missing = await client.get("/api/status")
            self.assertEqual(
                missing.json()["counts"]["total"], 0
            )

    async def test_default_credentials_endpoint(self) -> None:
        async with self.client() as client:
            initial = await client.get("/api/settings/default-credentials")
            self.assertEqual(
                initial.json(), {"username": "", "has_password": False}
            )
            saved = await client.put(
                "/api/settings/default-credentials",
                json={"username": "root", "password": PASSWORD},
            )
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(
                saved.json(), {"username": "root", "has_password": True}
            )
            self.assertNotIn(PASSWORD, saved.text)
            fetched = await client.get(
                "/api/settings/default-credentials"
            )
            self.assertNotIn(PASSWORD, fetched.text)
            missing_password = await client.put(
                "/api/settings/default-credentials",
                json={"username": "root"},
            )
            self.assertEqual(missing_password.status_code, 400)

    async def test_csv_import_endpoint_and_password_safety(self) -> None:
        text = "\n".join(
            [
                CSV_HEADER,
                "Cam 1,,192.168.1.90,,,admin," + PASSWORD,
                "Bad,,999.1.1.1,,,, ",
            ]
        )
        async with self.client() as client:
            preview = await client.post(
                "/api/devices/import",
                params={"dry_run": "true"},
                json={"csv": text},
            )
            self.assertEqual(preview.status_code, 200)
            body = preview.json()
            self.assertEqual(body["counts"]["ok"], 1)
            self.assertEqual(body["counts"]["error"], 1)
            self.assertNotIn(PASSWORD, preview.text)
            self.assertEqual(self.registry.list_devices(), [])

            imported = await client.post(
                "/api/devices/import",
                params={"dry_run": "false"},
                json={"csv": text},
            )
            self.assertEqual(imported.status_code, 200)
            self.assertEqual(imported.json()["counts"]["imported"], 1)
            self.assertNotIn(PASSWORD, imported.text)
            self.assertEqual(len(self.registry.list_devices()), 1)

            bad_mode = await client.post(
                "/api/devices/import",
                params={"dry_run": "maybe"},
                json={"csv": text},
            )
            self.assertEqual(bad_mode.status_code, 400)
            wrong_type = await client.post(
                "/api/devices/import",
                params={"dry_run": "true"},
                json={"csv": 5},
            )
            self.assertEqual(wrong_type.status_code, 400)

    async def test_import_rejects_wrong_content_type(self) -> None:
        async with self.client() as client:
            response = await client.post(
                "/api/devices/import?dry_run=true",
                content=b"csv",
                headers={"Content-Type": "text/csv"},
            )
            self.assertEqual(response.status_code, 415)

    async def test_connection_test_classes(self) -> None:
        device = self.add_device()
        url = f"/api/devices/{device['id']}/test"
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload["method"])
            if payload["method"] == "global.login":
                if payload["params"]["password"] == "":
                    return httpx.Response(
                        200,
                        json={
                            "error": {"code": 268632079},
                            "params": {"realm": "r", "random": "n"},
                            "session": "s1",
                            "id": payload["id"],
                        },
                    )
                return httpx.Response(
                    200,
                    json={"result": True, "session": "s2", "id": payload["id"]},
                )
            return httpx.Response(
                200, json={"result": True, "id": payload["id"]}
            )

        real_client = DvrClient

        def factory(host, username, password, **kwargs):
            kwargs.pop("timeout", None)
            return real_client(
                host,
                username,
                password,
                transport=httpx.MockTransport(handler),
            )

        async with self.client() as client:
            with mock.patch.object(
                registry_module, "DvrClient", factory
            ):
                ok = await client.post(url)
            self.assertEqual(ok.json(), {"ok": True})
            self.assertNotIn(PASSWORD, ok.text)
            self.assertEqual(requests.count("global.logout"), 1)

        def auth_fail(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            if payload["method"] == "global.login":
                if payload["params"]["password"] == "":
                    return httpx.Response(
                        200,
                        json={
                            "error": {"code": 268632079},
                            "params": {"realm": "r", "random": "n"},
                            "session": "s1",
                            "id": payload["id"],
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "error": {"code": 268632085},
                        "result": False,
                        "session": "s1",
                        "id": payload["id"],
                    },
                )
            return httpx.Response(200, json={"result": True})

        def refused(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        def not_json(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>no</html>")

        cases = [
            (refused, "unreachable"),
            (auth_fail, "authentication failed"),
            (not_json, "other"),
        ]
        async with self.client() as client:
            for handler_fn, reason in cases:
                def factory(host, username, password, **kwargs):
                    kwargs.pop("timeout", None)
                    return real_client(
                        host,
                        username,
                        password,
                        transport=httpx.MockTransport(handler_fn),
                    )

                with mock.patch.object(
                    registry_module, "DvrClient", factory
                ):
                    response = await client.post(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {"ok": False, "reason": reason},
                )
                self.assertNotIn(PASSWORD, response.text)

    async def test_connection_test_without_credentials(self) -> None:
        device = self.registry.create_device(
            {"name": "No creds", "host": "192.168.1.91"}
        )
        async with self.client() as client:
            response = await client.post(
                f"/api/devices/{device['id']}/test"
            )
        self.assertEqual(
            response.json(),
            {"ok": False, "reason": "authentication failed"},
        )

    async def test_status_endpoint_and_refresh(self) -> None:
        self.add_device(name="Online", host="192.168.1.100")
        self.add_device(name="Partial", host="192.168.1.101")
        self.add_device(name="Offline", host="192.168.1.102")
        self.net.open_ports = {
            ("192.168.1.100", 80),
            ("192.168.1.100", 554),
            ("192.168.1.101", 80),
        }
        async with self.client() as client:
            before = await client.get("/api/status")
            self.assertEqual(before.json()["counts"]["unknown"], 3)
            self.assertEqual(self.net.calls, [])

            refreshed = await client.post("/api/status/refresh")
            self.assertEqual(refreshed.status_code, 200)
            body = refreshed.json()
            self.assertEqual(body["counts"]["total"], 3)
            self.assertEqual(body["counts"]["online"], 1)
            self.assertEqual(body["counts"]["partial"], 1)
            self.assertEqual(body["counts"]["offline"], 1)

            cached = await client.get("/api/status")
            calls = len(self.net.calls)
            self.assertEqual(cached.json()["counts"]["online"], 1)
            self.assertEqual(len(self.net.calls), calls)

    async def test_refresh_while_running_returns_429(self) -> None:
        self.add_device()
        self.net.delay = 0.2
        task = asyncio.create_task(self.checker.refresh())
        await asyncio.sleep(0.02)
        async with self.client() as client:
            response = await client.post("/api/status/refresh")
        self.assertEqual(response.status_code, 429)
        await task


class LifespanTests(unittest.TestCase):
    def test_lifespan_starts_and_stops_the_checker(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "lifespan"
            env = {
                "APP_SECRET_KEY": SECRET_KEY,
                "DATA_DIR": str(data_dir),
                "STATUS_INTERVAL_SECONDS": "3600",
                "STATUS_CONCURRENCY": "2",
                "STATUS_TIMEOUT_SECONDS": "0.05",
                "ALLOW_PUBLIC_HOSTS": "false",
                "DVR_HOST": "",
                "DVR_USER": "",
                "DVR_PASS": "",
                "GO2RTC_URL": "",
            }
            with mock.patch.dict(os.environ, env):
                with TestClient(app_module.app) as client:
                    checker = app_module.app.state.status_checker
                    self.assertIsNotNone(checker)
                    self.assertTrue(checker.is_running)
                    self.assertTrue((data_dir / "dvr.db").exists())
                    response = client.get("/api/status")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        response.json()["counts"]["total"], 0
                    )
                self.assertFalse(checker.is_running)
                self.assertIsNone(checker._task)


class StatusCheckerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self._env = mock.patch.dict(
            os.environ,
            {"DVR_HOST": "", "DVR_USER": "", "DVR_PASS": ""},
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.registry = DeviceRegistry(_settings(self.dir / "dvr.db"))

    def add(self, name: str, host: str) -> dict:
        return self.registry.create_device({"name": name, "host": host})

    def make_checker(self, net: FakeNet, *, concurrency=4, timeout=0.3):
        return StatusChecker(
            self.registry,
            interval=3600.0,
            concurrency=concurrency,
            timeout=timeout,
            clock=lambda: FIXED_NOW,
            connector=net,
        )

    async def test_states_and_concurrency_limit(self) -> None:
        for index in range(6):
            self.add(f"Cam {index}", f"192.168.1.{10 + index}")
        net = FakeNet(
            open_ports={
                ("192.168.1.10", 80),
                ("192.168.1.10", 554),
                ("192.168.1.11", 554),
            },
            delay=0.01,
        )
        checker = self.make_checker(net, concurrency=2)
        snapshot = await checker.refresh()
        states = {
            item["host"]: item["state"]
            for item in snapshot["devices"]
        }
        self.assertEqual(states["192.168.1.10"], "online")
        self.assertEqual(states["192.168.1.11"], "partial")
        self.assertEqual(states["192.168.1.12"], "offline")
        self.assertLessEqual(net.max_active, 2)
        self.assertEqual(len(net.calls), 12)
        writers = net.writers
        self.assertTrue(all(writer.closed for writer in writers))

    async def test_timeout_marks_offline(self) -> None:
        self.add("Slow", "192.168.1.20")
        net = FakeNet(delay=1.0)
        checker = self.make_checker(net, timeout=0.05)
        started = time.monotonic()
        snapshot = await checker.refresh()
        elapsed = time.monotonic() - started
        self.assertEqual(snapshot["devices"][0]["state"], "offline")
        self.assertIsNone(snapshot["devices"][0]["last_seen"])
        self.assertLess(elapsed, 0.8)

    async def test_last_seen_is_persisted(self) -> None:
        self.add("Cam", "192.168.1.30")
        net = FakeNet(open_ports={("192.168.1.30", 80)})
        checker = self.make_checker(net)
        first = await checker.refresh()
        self.assertEqual(first["devices"][0]["state"], "partial")
        self.assertEqual(first["devices"][0]["last_seen"], FIXED_ISO)

        with self.registry._db() as conn:
            stored = conn.execute(
                "SELECT last_seen FROM devices"
            ).fetchone()[0]
        self.assertEqual(stored, FIXED_ISO)

        restarted = self.make_checker(FakeNet())
        snapshot = await restarted.snapshot()
        self.assertEqual(snapshot["devices"][0]["state"], "unknown")
        self.assertEqual(snapshot["devices"][0]["last_seen"], FIXED_ISO)

        net.open_ports = set()
        offline = await checker.refresh()
        self.assertEqual(offline["devices"][0]["state"], "offline")
        self.assertEqual(offline["devices"][0]["last_seen"], FIXED_ISO)
        with self.registry._db() as conn:
            kept = conn.execute(
                "SELECT last_seen FROM devices"
            ).fetchone()[0]
        self.assertEqual(kept, FIXED_ISO)

    async def test_snapshot_never_touches_the_network(self) -> None:
        self.add("Cam", "192.168.1.40")
        net = FakeNet(open_ports={("192.168.1.40", 80)})
        checker = self.make_checker(net)
        await checker.refresh()
        calls = len(net.calls)

        async def boom(host: str, port: int):
            raise AssertionError("snapshot must not connect")

        checker._connector = boom
        snapshot = await checker.snapshot()
        self.assertEqual(snapshot["devices"][0]["state"], "partial")
        self.assertEqual(len(net.calls), calls)

    async def test_new_device_appears_as_unknown(self) -> None:
        await self.make_checker(FakeNet()).refresh()
        self.add("Late", "192.168.1.50")
        snapshot = await self.make_checker(FakeNet()).snapshot()
        self.assertEqual(snapshot["counts"]["unknown"], 1)

    async def test_only_one_round_at_a_time(self) -> None:
        self.add("Cam", "192.168.1.60")
        net = FakeNet(delay=0.1)
        checker = self.make_checker(net)
        task = asyncio.create_task(checker.refresh())
        await asyncio.sleep(0.02)
        with self.assertRaises(StatusRoundBusy):
            await checker.refresh()
        await task

    async def test_stop_cancels_the_task(self) -> None:
        self.add("Cam", "192.168.1.70")
        net = FakeNet(delay=0.05)
        checker = StatusChecker(
            self.registry,
            interval=0.01,
            concurrency=2,
            timeout=0.3,
            connector=net,
        )
        checker.start()
        self.assertTrue(checker.is_running)
        await asyncio.sleep(0.06)
        await checker.stop()
        self.assertFalse(checker.is_running)
        calls = len(net.calls)
        await asyncio.sleep(0.1)
        self.assertEqual(len(net.calls), calls)
        self.assertIsNone(checker._task)

    async def test_round_failure_keeps_the_checker_alive(self) -> None:
        self.add("Cam", "192.168.1.80")
        net = FakeNet(delay=0.01)
        checker = StatusChecker(
            self.registry,
            interval=0.02,
            concurrency=2,
            timeout=0.3,
            connector=net,
        )
        original = self.registry.status_targets
        calls = {"count": 0}

        def flaky():
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("database is busy")
            return original()

        self.registry.status_targets = flaky
        checker.start()
        with self.assertLogs("registry", level="WARNING"):
            await asyncio.sleep(0.15)
        await checker.stop()
        self.assertGreaterEqual(calls["count"], 2)

    async def test_no_network_errors_escape(self) -> None:
        self.add("Cam", "192.168.1.90")
        checker = self.make_checker(FakeNet())
        checker._connector = _refused
        snapshot = await checker.refresh()
        self.assertEqual(snapshot["counts"]["offline"], 1)


if __name__ == "__main__":
    unittest.main()
