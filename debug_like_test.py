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

import httpx  # noqa: E402
import app as app_module  # noqa: E402
import cgi_client  # noqa: E402
from registry import DeviceRegistry, RegistrySettings  # noqa: E402

PASSWORD = "Zx9-plain-secret-123"

class T(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        env = mock.patch.dict(os.environ, {"DVR_HOST": "", "DVR_USER": "", "DVR_PASS": "", "APP_SECRET_KEY": SECRET_KEY})
        env.start()
        self.addCleanup(env.stop)
        self.registry = DeviceRegistry(RegistrySettings(db_path=self.dir / "dvr.db", secret_key=SECRET_KEY, status_interval=3600.0, status_concurrency=4, status_timeout=2.0, allow_public_hosts=True))
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
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver")
    def add_device(self, **overrides):
        body = {"name": "DVR1", "host": "192.168.1.10", "http_port": 80, "rtsp_port": 554, "username": "admin", "password": PASSWORD}
        body.update(overrides)
        return self.registry.create_device(body)

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
        class Patched(cgi_client.httpx.AsyncClient):
            def __init__(self, *args, **kwargs) -> None:
                kwargs["transport"] = transport
                kwargs.pop("auth", None)
                super().__init__(*args, **kwargs)
        with mock.patch.object(cgi_client.httpx, "AsyncClient", Patched):
            async with self.client() as client:
                resp = await client.get(f"/api/recordings/time?device_id={device_id}")
                print(resp.status_code)
                print(resp.text)
                print(call_count)

if __name__ == "__main__":
    unittest.main()
