import asyncio
import os
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from cryptography.fernet import Fernet
import httpx

import app as app_module
from registry import DeviceRegistry, RegistrySettings


SECRET_KEY = Fernet.generate_key().decode("ascii")
PASSWORD = "secret"


class Phase6aHealthRequiredCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        env = mock.patch.dict(os.environ, {"DVR_HOST": "", "DVR_USER": "", "DVR_PASS": ""}, clear=False)
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
        app_module.app.state.status_checker = None
        app_module.app.state.stream_manager = None
        app_module._DEVICE_INFO_CACHE.clear()
        app_module._RPC_LOCKS.clear()
        app_module._HEALTH_ROUND_TASK = None
        app_module._HEALTH_ROUND_META = {"running": False, "started_at": None, "finished_at": None, "checked": 0, "total": 0}

    def tearDown(self):
        app_module.app.state.registry = None
        app_module.app.state.status_checker = None
        app_module.app.state.stream_manager = None

    def add_device(self, **overrides):
        body = {
            "name": overrides.get("name", f"DVR{len(self.registry.list_devices())+1}"),
            "host": overrides.get("host", "192.168.1.10"),
            "http_port": overrides.get("http_port", 80),
            "rtsp_port": overrides.get("rtsp_port", 554),
            "username": "admin",
            "password": PASSWORD,
        }
        body.update({k: v for k, v in overrides.items() if k in ("name", "host", "http_port", "rtsp_port", "username", "password", "group_id", "notes")})
        return self.registry.create_device(body)

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver")


class Phase6aHealthRequiredTests(Phase6aHealthRequiredCase):
    async def test_a_three_devices_all_unknown(self):
        self.add_device(name="D1", host="192.168.1.10")
        self.add_device(name="D2", host="192.168.1.11")
        self.add_device(name="D3", host="192.168.1.12")
        async with self.client() as client:
            resp = await client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data.get("devices", [])), 3)
            for it in data["devices"]:
                h = it.get("health") or {}
                self.assertEqual(h.get("state"), "unknown")
                self.assertIsNone(h.get("checked_at"))
                self.assertEqual(h.get("channels"), [])
            self.assertEqual(data["summary"].get("unknown"), 3)

    async def test_b_check_all_with_mocks(self):
        self.add_device(name="D1", host="192.168.1.10")
        self.add_device(name="D2", host="192.168.1.11")
        self.add_device(name="D3", host="192.168.1.12")

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def login(self):
                return None

            def logout(self):
                return None

            def close(self):
                return None

            def get_device_info(self):
                return {"SystemInfo": {"SerialNo": "X"}}

            def get_channels(self):
                return []

            def get_config(self, name):
                return {}

        with mock.patch("dvr_client.DvrClient", FakeClient):
            async with self.client() as client:
                resp = await client.post("/api/health/check-all")
                self.assertEqual(resp.status_code, 202)
                await asyncio.sleep(8)
                resp2 = await client.get("/api/health")
                self.assertEqual(resp2.status_code, 200)
                data = resp2.json()
                self.assertEqual(len(data.get("devices", [])), 3)

    async def test_c_second_check_all_conflict(self):
        self.add_device(name="D1", host="192.168.1.10")

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def login(self):
                return None

            def logout(self):
                return None

            def close(self):
                return None

            def get_device_info(self):
                import time
                time.sleep(5)
                return {"SystemInfo": {"SerialNo": "X"}}

            def get_channels(self):
                return []

            def get_config(self, name):
                return {}

        with mock.patch("dvr_client.DvrClient", FakeClient):
            async with self.client() as client:
                r1 = await client.post("/api/health/check-all")
                self.assertEqual(r1.status_code, 202)
                r2 = await client.post("/api/health/check-all")
                self.assertIn(r2.status_code, (409,))
                self.assertTrue(r2.json().get("running") is True)
                await asyncio.sleep(15)
