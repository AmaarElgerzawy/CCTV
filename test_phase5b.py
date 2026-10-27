"""Offline tests for Phase 5b device resolution and limits.

Run with:
    .\\.venv\\Scripts\\python.exe -m unittest test_phase5b -v
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from cryptography.fernet import Fernet

import app as app_module
import cgi_client
import registry as registry_module
import stream_manager
from registry import DeviceRegistry, RegistrySettings, ResolvedDevice


SECRET_KEY = Fernet.generate_key().decode()
PASSWORD = "Zx9-plain-secret-123"


class FakeWriter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeNet:
    def __init__(self, open_ports: set[tuple[str, int]] | None = None) -> None:
        self.open_ports = set(open_ports or ())

    async def __call__(self, host: str, port: int):
        if (host, port) not in self.open_ports:
            raise OSError("connection refused")
        return object(), FakeWriter()


def _settings(db_path: Path, key: str = SECRET_KEY) -> RegistrySettings:
    return RegistrySettings(
        db_path=db_path,
        secret_key=key,
        status_interval=3600.0,
        status_concurrency=4,
        status_timeout=2.0,
        allow_public_hosts=True,
    )


def _device_body(**overrides):
    body = {
        "name": "DVR1",
        "host": "192.168.1.10",
        "http_port": 80,
        "rtsp_port": 554,
        "username": "admin",
        "password": PASSWORD,
    }
    body.update(overrides)
    return body


class Phase5bCase(unittest.IsolatedAsyncioTestCase):
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
        self.registry = DeviceRegistry(_settings(self.dir / "dvr.db"))
        self._clear_caches()

    def _clear_caches(self) -> None:
        app_module._DEVICE_INFO_CACHE.clear()
        app_module._RPC_LOCKS.clear()
        cgi_client.clear_cache()

    def add_device(self, **overrides):
        return self.registry.create_device(_device_body(**overrides))


class ResolutionTests(Phase5bCase):
    def test_one_device_no_id(self) -> None:
        self.add_device(name="Only")
        resolved = self.registry.resolve_device(None)
        self.assertIsInstance(resolved, ResolvedDevice)
        self.assertEqual(resolved.name, "Only")
        self.assertNotIn("password", repr(resolved))

    def test_several_devices_need_id(self) -> None:
        self.add_device(name="A")
        self.add_device(name="B", host="192.168.1.11")
        with self.assertRaises(Exception) as ctx:
            self.registry.resolve_device(None)
        self.assertTrue("device_id is required" in str(ctx.exception).lower())


    def test_unknown_id_404(self) -> None:
        with self.assertRaises(Exception) as ctx:
            self.registry.resolve_device(999)
        self.assertTrue("404" in str(ctx.exception) or "not found" in str(ctx.exception).lower())


    def test_no_credentials_409(self) -> None:
        self.registry.set_default_credentials("admin", "pw")
        dev = self.add_device(username=None, password=None)
        # Clear defaults via direct DB manipulation
        with self.registry._db() as conn:
            conn.execute("DELETE FROM settings WHERE key = 'default_username'")
            conn.execute("DELETE FROM settings WHERE key = 'default_password'")
        with self.assertRaises(Exception) as ctx:
            self.registry.resolve_device(dev["id"])
        self.assertTrue("409" in str(ctx.exception) or "credentials" in str(ctx.exception).lower())


    def test_own_vs_default(self) -> None:
        self.registry.set_default_credentials("def", "pw")
        dev = self.add_device(username=None, password=None)
        resolved = self.registry.resolve_device(dev["id"])
        self.assertEqual(resolved.username, "def")
        self.assertEqual(resolved.password, "pw")

    def test_base_url_and_rtsp_port(self) -> None:
        dev = self.add_device(host="example.com", http_port=8080, rtsp_port=8554)
        resolved = self.registry.resolve_device(dev["id"])
        self.assertEqual(resolved.base_url, "http://example.com:8080")
        self.assertEqual(resolved.rtsp_port, 8554)


class ApiDeviceResolutionTests(Phase5bCase):
    def setUp(self) -> None:
        super().setUp()
        app_module.app.state.registry = self.registry
        app_module.app.state.stream_manager = None
        self.addCleanup(self._reset)

    def _reset(self) -> None:
        app_module.app.state.registry = None
        app_module.app.state.stream_manager = None

    def client(self):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.app),
            base_url="http://testserver",
        )


class StreamLimitTests(unittest.TestCase):
    def test_per_device_limits(self) -> None:
        from stream_manager import StreamDevice, StreamManager

        calls = []

        def fake_put(self, path, params=None, **kwargs):
            calls.append(("put", params))
            class R:
                status_code = 200
            return R()

        def fake_delete(self, path, params=None, **kwargs):
            class R:
                status_code = 200
            return R()

        with mock.patch("httpx.Client.put", fake_put), mock.patch("httpx.Client.delete", fake_delete):
            m = StreamManager(
                go2rtc_url="http://127.0.0.1:1984",
                max_channel=8,
                max_live_streams=100,
                max_main_streams=100,
                max_playback_streams=100,
                max_streams_per_device=2,
                max_main_per_device=1,
            )
            d1 = StreamDevice(device_id=1, host="1.1.1.1", rtsp_port=554, username="u", password="p")
            m.open(d1, 1, "sub")
            m.open(d1, 2, "sub")
            with self.assertRaises(stream_manager.StreamLimitError) as ctx:
                m.open(d1, 3, "sub")
            self.assertIn("limit", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
