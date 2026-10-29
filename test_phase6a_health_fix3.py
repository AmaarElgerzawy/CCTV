import asyncio
import os
import tempfile
import unittest
from unittest import mock

from cryptography.fernet import Fernet
import httpx


def _make_key() -> str:
    return Fernet.generate_key().decode("ascii")


class Phase6aHealthFixCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmpdir.name, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        os.environ["DATA_DIR"] = self.data_dir
        os.environ["APP_SECRET_KEY"] = _make_key()
        import app as app_module
        self.app_module = app_module

    def tearDown(self):
        try:
            self.tmpdir.cleanup()
        except Exception:
            pass
        for k in ["DATA_DIR", "APP_SECRET_KEY"]:
            os.environ.pop(k, None)


class HealthAllDevicesTests(Phase6aHealthFixCase):
    def test_a_three_devices_all_unknown(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app_module.app), base_url="http://testserver") as client:
                # get registry from app state
                registry = self.app_module.app.state.registry
                self.assertIsNotNone(registry)
                registry.create_device({"name": "D1", "host": "192.168.1.10", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
                registry.create_device({"name": "D2", "host": "192.168.1.11", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
                registry.create_device({"name": "D3", "host": "192.168.1.12", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
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

        asyncio.get_event_loop().run_until_complete(run())

    def test_b_check_all_with_mocks(self):
        async def handler(request: httpx.Request):
            return httpx.Response(200, json={"ret": 100, "data": {"Status": {"SerialInfo": {"SerialNo": "X"}}}})

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_async_client(*args, **kwargs)

        async def run():
            with mock.patch("httpx.AsyncClient", side_effect=make_client):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app_module.app), base_url="http://testserver") as client:
                    registry = self.app_module.app.state.registry
                    self.assertIsNotNone(registry)
                    registry.create_device({"name": "D1", "host": "192.168.1.10", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
                    registry.create_device({"name": "D2", "host": "192.168.1.11", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
                    registry.create_device({"name": "D3", "host": "192.168.1.12", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
                    resp = await client.post("/api/health/check-all")
                    self.assertEqual(resp.status_code, 202)
                    await asyncio.sleep(5)
                    resp2 = await client.get("/api/health")
                    self.assertEqual(resp2.status_code, 200)
                    data = resp2.json()
                    self.assertEqual(len(data.get("devices", [])), 3)

        asyncio.get_event_loop().run_until_complete(run())

    def test_c_second_check_all_conflict(self):
        async def handler(request: httpx.Request):
            await asyncio.sleep(4)
            return httpx.Response(200, json={"ret": 100, "data": {}})

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_async_client(*args, **kwargs)

        async def run():
            with mock.patch("httpx.AsyncClient", side_effect=make_client):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app_module.app), base_url="http://testserver") as client:
                    registry = self.app_module.app.state.registry
                    self.assertIsNotNone(registry)
                    registry.create_device({"name": "D1", "host": "192.168.1.10", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
                    r1 = await client.post("/api/health/check-all")
                    self.assertEqual(r1.status_code, 202)
                    r2 = await client.post("/api/health/check-all")
                    self.assertIn(r2.status_code, (409,))
                    self.assertTrue(r2.json().get("running") is True)
                    await asyncio.sleep(6)

        asyncio.get_event_loop().run_until_complete(run())
