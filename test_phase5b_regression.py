"""Regression tests for device_id path through the API."""
from __future__ import annotations

import asyncio
import json
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
from registry import DeviceRegistry, RegistrySettings


SECRET_KEY = Fernet.generate_key().decode()
PASSWORD = "Zx9-plain-secret-123"


class FakeWriter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class Phase5bRegressionCase(unittest.IsolatedAsyncioTestCase):
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
        self.registry = DeviceRegistry(RegistrySettings(
            db_path=self.dir / "dvr.db",
            secret_key=SECRET_KEY,
            status_interval=3600.0,
            status_concurrency=4,
            status_timeout=2.0,
            allow_public_hosts=True,
        ))
        app_module.app.state.registry = self.registry
        app_module.app.state.stream_manager = None
        app_module._DEVICE_INFO_CACHE.clear()
        app_module._RPC_LOCKS.clear()
        cgi_client.clear_cache()
        self.addCleanup(self._reset)

    def _reset(self) -> None:
        app_module.app.state.registry = None
        app_module.app.state.stream_manager = None

    def client(self):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.app),
            base_url="http://testserver",
        )

    def add_device(self, **overrides):
        body = {
            "name": "DVR1",
            "host": "192.168.1.10",
            "http_port": 80,
            "rtsp_port": 554,
            "username": "admin",
            "password": PASSWORD,
        }
        body.update(overrides)
        return self.registry.create_device(body)


class CgiPathTests(Phase5bRegressionCase):
    async def test_recordings_through_device_id(self) -> None:
        dev = self.add_device()
        device_id = dev["id"]

        class FakeDvrCgi:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def aclose(self) -> None:
                return None

            async def get_device_time(self):
                return "2026-10-28 12:00:00"

            async def find_files(self, channel, date, stream):
                return {"available": True, "files": [], "truncated": False}

        with mock.patch("cgi_client.DvrCgi", FakeDvrCgi):
            async with self.client() as client:
                resp = await client.get(f"/api/recordings?channel=1&date=2026-10-28&stream=main&device_id={device_id}")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["available"], True)

    async def test_recordings_time_through_device_id(self) -> None:
        dev = self.add_device()
        device_id = dev["id"]

        class FakeDvrCgi:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def aclose(self) -> None:
                return None

            async def get_device_time(self):
                return "2026-10-28 12:00:00"

        with mock.patch("cgi_client.DvrCgi", FakeDvrCgi):
            async with self.client() as client:
                resp = await client.get(f"/api/recordings/time?device_id={device_id}")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertIn("device_time", data)

    async def test_storage_through_device_id(self) -> None:
        dev = self.add_device()
        device_id = dev["id"]

        class FakeDvrCgi:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def aclose(self) -> None:
                return None

            async def get_storage_health(self):
                return {"health": "ok", "disks": 1, "errors": 0}

        with mock.patch("cgi_client.DvrCgi", FakeDvrCgi):
            async with self.client() as client:
                resp = await client.get(f"/api/storage?device_id={device_id}")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["health"], "ok")


if __name__ == "__main__":
    unittest.main()
