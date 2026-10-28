"""Regression tests for CGI recordings/time/storage through device_id with httpx transport."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

SECRET_KEY = Fernet.generate_key().decode()
os.environ["APP_SECRET_KEY"] = SECRET_KEY
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="dvr-web-test-")

# Import after env setup
import httpx  # noqa: E402
import app as app_module  # noqa: E402
import cgi_client  # noqa: E402
from registry import DeviceRegistry, RegistrySettings  # noqa: E402


PASSWORD = "Zx9-plain-secret-123"


class CgiCacheRegressionCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        env = mock.patch.dict(
            os.environ,
            {"DVR_HOST": "", "DVR_USER": "", "DVR_PASS": "", "APP_SECRET_KEY": SECRET_KEY},
        )
        env.start()
        self.addCleanup(env.stop)
        self.registry = DeviceRegistry(
            RegistrySettings(
                db_path=self.dir / "dvr.db",
                secret_key=SECRET_KEY,
                status_interval=3600.0,
                status_concurrency=4,
                status_timeout=2.0,
                allow_public_hosts=True,
            )
        )
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


class RealPathCgiTests(CgiCacheRegressionCase):
    async def test_recordings_real_path_cached(self) -> None:
        dev = self.add_device()
        device_id = dev["id"]
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            path = request.url.path
            if path.endswith("/cgi-bin/mediaFileFind.cgi"):
                action = request.url.params.get("action")
                if action == "factory.create":
                    return httpx.Response(200, text="result=12345\r\n")
                if action == "findFile":
                    return httpx.Response(
                        200, text="OK\r\n", headers={"Content-Type": "text/plain"}
                    )
                if action == "findNextFile":
                    return httpx.Response(
                        200,
                        text="found=1\r\nitems[0].StartTime=2026-10-28 01:00:00\r\nitems[0].EndTime=2026-10-28 02:00:00\r\nitems[0].Type=General\r\nitems[0].Length=1024\r\nitems[0].Channel=1\r\nitems[0].VideoStream=Main\r\n",
                        headers={"Content-Type": "text/plain"},
                    )
                if action == "close":
                    return httpx.Response(200, text="OK\r\n")
                if action == "destroy":
                    return httpx.Response(200, text="OK\r\n")
            if path.endswith("/cgi-bin/global.cgi"):
                action = request.url.params.get("action")
                if action == "getCurrentTime":
                    return httpx.Response(200, text="result=2026-10-28 12:00:00\r\n")
            return httpx.Response(404, text="not found")

        transport = httpx.MockTransport(handler)

        orig = cgi_client.httpx.AsyncClient

        class Patched(orig):
            def __init__(self, *args, **kwargs) -> None:
                base_url = kwargs.get("base_url") or ""
                if base_url.startswith("http://192.168.1.10"):
                    kwargs["transport"] = transport
                    kwargs.pop("auth", None)
                super().__init__(*args, **kwargs)

        with mock.patch.object(cgi_client.httpx, "AsyncClient", Patched):
            async with self.client() as client:
                resp1 = await client.get(
                    f"/api/recordings?channel=1&date=2026-10-28&stream=main&device_id={device_id}"
                )
                self.assertEqual(resp1.status_code, 200, resp1.text)
                data1 = resp1.json()
                self.assertIn("available", data1)
                self.assertIn("files", data1)
                self.assertGreater(call_count, 0)
                count1 = call_count
                resp2 = await client.get(
                    f"/api/recordings?channel=1&date=2026-10-28&stream=main&device_id={device_id}"
                )
                self.assertEqual(resp2.status_code, 200, resp2.text)
                count2 = call_count
                # find_files is cached per (host,device,channel,date,stream); clock may be re-fetched
                self.assertEqual(count2 - count1, 1)

    async def test_recordings_time_real_path(self) -> None:
        dev = self.add_device()
        device_id = dev["id"]
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            path = request.url.path
            if path.endswith("/cgi-bin/global.cgi"):
                action = request.url.params.get("action")
                if action == "getCurrentTime":
                    return httpx.Response(200, text="result=2026-10-28 12:00:00\r\n")
            return httpx.Response(404, text="not found")

        transport = httpx.MockTransport(handler)

        orig = cgi_client.httpx.AsyncClient

        class Patched(orig):
            def __init__(self, *args, **kwargs) -> None:
                base_url = kwargs.get("base_url") or ""
                if base_url.startswith("http://192.168.1.10"):
                    kwargs["transport"] = transport
                    kwargs.pop("auth", None)
                super().__init__(*args, **kwargs)

        with mock.patch.object(cgi_client.httpx, "AsyncClient", Patched):
            async with self.client() as client:
                resp = await client.get(f"/api/recordings/time?device_id={device_id}")
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertGreater(call_count, 0)
                data = resp.json()
                self.assertIn("device_time", data)
                self.assertIn("source", data)

    async def test_storage_real_path(self) -> None:
        dev = self.add_device()
        device_id = dev["id"]
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            path = request.url.path
            if path.endswith("/cgi-bin/storageDevice.cgi"):
                action = request.url.params.get("action")
                if action == "getDeviceAllInfo":
                    return httpx.Response(
                        200,
                        text="list.info[0].Name=HD0\r\nlist.info[0].State=Success\r\nlist.info[0].Detail[0].IsError=false\r\n",
                        headers={"Content-Type": "text/plain"},
                    )
            return httpx.Response(404, text="not found")

        transport = httpx.MockTransport(handler)

        orig = cgi_client.httpx.AsyncClient

        class Patched(orig):
            def __init__(self, *args, **kwargs) -> None:
                base_url = kwargs.get("base_url") or ""
                if base_url.startswith("http://192.168.1.10"):
                    kwargs["transport"] = transport
                    kwargs.pop("auth", None)
                super().__init__(*args, **kwargs)

        with mock.patch.object(cgi_client.httpx, "AsyncClient", Patched):
            async with self.client() as client:
                resp = await client.get(f"/api/storage?device_id={device_id}")
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertGreater(call_count, 0)
                data = resp.json()
                self.assertIn("health", data)
                self.assertIn("disks", data)
                self.assertIn("errors", data)

    async def test_findfile_bad_request_returns_available_false(self) -> None:
        dev = self.add_device()
        device_id = dev["id"]
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            path = request.url.path
            if path.endswith("/cgi-bin/mediaFileFind.cgi"):
                action = request.url.params.get("action")
                if action == "factory.create":
                    return httpx.Response(200, text="result=12345\r\n")
                if action == "findFile":
                    return httpx.Response(200, text="Error / Bad Request!\r\n")
                if action == "close":
                    return httpx.Response(200, text="OK\r\n")
                if action == "destroy":
                    return httpx.Response(200, text="OK\r\n")
            if path.endswith("/cgi-bin/global.cgi"):
                action = request.url.params.get("action")
                if action == "getCurrentTime":
                    return httpx.Response(200, text="result=2026-10-28 12:00:00\r\n")
            return httpx.Response(404, text="not found")

        transport = httpx.MockTransport(handler)

        orig = cgi_client.httpx.AsyncClient

        class Patched(orig):
            def __init__(self, *args, **kwargs) -> None:
                base_url = kwargs.get("base_url") or ""
                if base_url.startswith("http://192.168.1.10"):
                    kwargs["transport"] = transport
                    kwargs.pop("auth", None)
                super().__init__(*args, **kwargs)

        with mock.patch.object(cgi_client.httpx, "AsyncClient", Patched):
            async with self.client() as client:
                resp = await client.get(
                    f"/api/recordings?channel=1&date=2026-10-28&stream=main&device_id={device_id}"
                )
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertGreater(call_count, 0)
                data = resp.json()
                self.assertEqual(data["available"], False)


if __name__ == "__main__":
    unittest.main()
