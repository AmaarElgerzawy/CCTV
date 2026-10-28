import os, tempfile
from pathlib import Path
from unittest import mock
from cryptography.fernet import Fernet
SECRET_KEY = Fernet.generate_key().decode()
os.environ["APP_SECRET_KEY"] = SECRET_KEY

import httpx
import app as app_module
import cgi_client
from registry import DeviceRegistry, RegistrySettings

tmp = tempfile.TemporaryDirectory()
reg = DeviceRegistry(RegistrySettings(db_path=Path(tmp.name)/"dvr.db", secret_key=SECRET_KEY, status_interval=3600, status_concurrency=4, status_timeout=2, allow_public_hosts=True))
app_module.app.state.registry = reg
app_module.app.state.stream_manager = None
app_module._DEVICE_INFO_CACHE.clear()
app_module._RPC_LOCKS.clear()
cgi_client.clear_cache()

dev = reg.create_device({"name":"DVR1","host":"192.168.1.10","http_port":80,"rtsp_port":554,"username":"admin","password":"pw"})

call_count = 0
def handler(request: httpx.Request):
    global call_count
    call_count += 1
    print('DRV:', request.url)
    path = request.url.path
    if path.endswith("/cgi-bin/mediaFileFind.cgi"):
        action = request.url.params.get("action")
        if action == "factory.create":
            return httpx.Response(200, text="result=12345\r\n")
        if action == "findFile":
            return httpx.Response(200, text="OK\r\n")
        if action == "findNextFile":
            return httpx.Response(200, text="found=1\r\nitems[0].StartTime=2026-10-28 01:00:00\r\nitems[0].EndTime=2026-10-28 02:00:00\r\nitems[0].Type=General\r\nitems[0].Length=1024\r\nitems[0].Channel=1\r\nitems[0].VideoStream=Main\r\n")
        if action == "close": return httpx.Response(200,text="OK\r\n")
        if action == "destroy": return httpx.Response(200,text="OK\r\n")
    if path.endswith("/cgi-bin/global.cgi"):
        if request.url.params.get("action")=="getCurrentTime":
            return httpx.Response(200,text="result=2026-10-28 12:00:00\r\n")
    if path.endswith("/cgi-bin/storageDevice.cgi"):
        if request.url.params.get("action")=="getDeviceAllInfo":
            return httpx.Response(200,text="list.info[0].Name=HD0\r\nlist.info[0].State=Success\r\nlist.info[0].Detail[0].IsError=false\r\n")
    return httpx.Response(404,text="not found")

transport = httpx.MockTransport(handler)
# Patch cgi_client's AsyncClient to use our transport
orig = cgi_client.httpx.AsyncClient
class Patched(orig):
    def __init__(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("auth", None)
        super().__init__(*args, **kwargs)

with mock.patch.object(cgi_client.httpx, "AsyncClient", Patched):
    import asyncio
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url='http://testserver') as c:
            r = await c.get('/api/recordings?channel=1&date=2026-10-28&stream=main&device_id=%s' % dev['id'])
            print('R:', r.status_code)
            print(r.text)
    asyncio.run(run())
