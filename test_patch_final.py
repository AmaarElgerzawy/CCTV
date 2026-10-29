import asyncio
import tempfile
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet
import httpx

import app as app_module
from registry import DeviceRegistry, RegistrySettings

SECRET_KEY = Fernet.generate_key().decode("ascii")
PASSWORD = "secret"

async def run():
    tmp = tempfile.TemporaryDirectory()
    d = Path(tmp.name)
    reg = DeviceRegistry(RegistrySettings(db_path=d/"dvr.db", secret_key=SECRET_KEY, status_interval=3600.0, status_concurrency=4, status_timeout=2.0, allow_public_hosts=True))
    app_module.app.state.registry = reg
    app_module.app.state.status_checker = None
    app_module.app.state.stream_manager = None
    app_module._DEVICE_INFO_CACHE.clear()
    app_module._RPC_LOCKS.clear()
    app_module._HEALTH_ROUND_TASK = None
    reg.create_device({"name":"D1","host":"192.168.1.10","http_port":80,"rtsp_port":554,"username":"admin","password":PASSWORD})
    reg.create_device({"name":"D2","host":"192.168.1.11","http_port":80,"rtsp_port":554,"username":"admin","password":PASSWORD})
    reg.create_device({"name":"D3","host":"192.168.1.12","http_port":80,"rtsp_port":554,"username":"admin","password":PASSWORD})

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def login(self): pass
        def logout(self): pass
        def close(self): pass
        def get_device_info(self): return {"SystemInfo": {"SerialNo": "X"}}
        def get_channels(self): return []
        def get_config(self, name): return {}
    with mock.patch("dvr_client.DvrClient", FakeClient):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver") as client:
            resp = await client.post("/api/health/check-all")
            print(resp.status_code)
            print(resp.text)
            await asyncio.sleep(6)
            resp2 = await client.get("/api/health")
            print(resp2.status_code)
            data = resp2.json()
            print(data.get('summary'))
    tmp.cleanup()

asyncio.run(run())
