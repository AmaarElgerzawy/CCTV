import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from cryptography.fernet import Fernet


def _make_key() -> str:
    return Fernet.generate_key().decode("ascii")


class HealthFixTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmpdir.name, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        os.environ["DATA_DIR"] = self.data_dir
        os.environ["APP_SECRET_KEY"] = _make_key()
        # ensure clean modules
        for k in list(__import__("sys").modules.keys()):
            if k.startswith("app") or k.startswith("registry") or k.startswith("health_checker"):
                pass  # don't force remove to be safe
        # import fresh
        import importlib
        import app as app_module
        importlib.reload(app_module)
        self.app_module = app_module

    def tearDown(self):
        try:
            self.tmpdir.cleanup()
        except Exception:
            pass
        for k in ["DATA_DIR", "APP_SECRET_KEY"]:
            os.environ.pop(k, None)

    def test_a_three_devices_all_unknown(self):
        # create 3 devices via registry
        registry = self.app_module.app.state.registry
        self.assertIsNotNone(registry)
        dev1 = registry.create_device({"name": "D1", "host": "192.168.1.10", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
        dev2 = registry.create_device({"name": "D2", "host": "192.168.1.11", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
        dev3 = registry.create_device({"name": "D3", "host": "192.168.1.12", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
        from fastapi.testclient import TestClient
        with TestClient(self.app_module.app) as client:
            resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data.get("devices", [])), 3)
            for it in data["devices"]:
                h = it.get("health") or {}
                self.assertEqual(h.get("state"), "unknown")
                self.assertIsNone(h.get("checked_at"))
                self.assertEqual(h.get("channels"), [])
            self.assertEqual(data["summary"].get("unknown"), 3)

    def test_b_check_all_with_mocks(self):
        registry = self.app_module.app.state.registry
        self.assertIsNotNone(registry)
        dev1 = registry.create_device({"name": "D1", "host": "192.168.1.10", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
        dev2 = registry.create_device({"name": "D2", "host": "192.168.1.11", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
        dev3 = registry.create_device({"name": "D3", "host": "192.168.1.12", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
        from fastapi.testclient import TestClient
        import httpx

        async def handler(request: httpx.Request):
            # return ok responses for common endpoints
            return httpx.Response(200, json={"ret": 100, "msg": "ok", "data": {"Status": {"SerialInfo": {"SerialNo": "X"}}}})

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_async_client(*args, **kwargs)

        with mock.patch("httpx.AsyncClient", side_effect=make_client):
            with TestClient(self.app_module.app) as client:
                resp = client.post("/api/health/check-all")
                self.assertEqual(resp.status_code, 202)
                # wait a bit for round
                time.sleep(2)
                resp2 = client.get("/api/health")
                self.assertEqual(resp2.status_code, 200)
                data = resp2.json()
                self.assertEqual(len(data.get("devices", [])), 3)

    def test_c_check_all_conflict(self):
        registry = self.app_module.app.state.registry
        self.assertIsNotNone(registry)
        dev1 = registry.create_device({"name": "D1", "host": "192.168.1.10", "http_port": 80, "rtsp_port": 554, "username": "u", "password": "p"})
        from fastapi.testclient import TestClient
        import httpx

        async def handler(request: httpx.Request):
            await asyncio.sleep(1)
            return httpx.Response(200, json={"ret": 100, "data": {}})

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient

        def make_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_async_client(*args, **kwargs)

        with mock.patch("httpx.AsyncClient", side_effect=make_client):
            with TestClient(self.app_module.app) as client:
                r1 = client.post("/api/health/check-all")
                self.assertEqual(r1.status_code, 202)
                r2 = client.post("/api/health/check-all")
                self.assertIn(r2.status_code, (409,))
                self.assertTrue(r2.json().get("running") or r2.json().get("error"))
                time.sleep(3)


if __name__ == "__main__":
    unittest.main()
