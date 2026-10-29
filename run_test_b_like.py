import asyncio
from unittest import mock
import httpx
import app as app_module
from test_phase6a_health_fix5 import HealthAllDevicesTests

t = HealthAllDevicesTests()
t.setUp()
try:
    t.add_device(name="D1", host="192.168.1.10")
    t.add_device(name="D2", host="192.168.1.11")
    t.add_device(name="D3", host="192.168.1.12")

    async def handler(request: httpx.Request):
        return httpx.Response(200, json={"ret": 100, "data": {"Status": {"SerialInfo": {"SerialNo": "X"}}}})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    async def run():
        with mock.patch("httpx.AsyncClient", side_effect=make_client):
            async with t.client() as client:
                resp = await client.post("/api/health/check-all")
                print(resp.status_code)
                print(resp.text)
                await asyncio.sleep(6)
                resp2 = await client.get("/api/health")
                print(resp2.status_code)
                data = resp2.json()
                print(len(data.get('devices',[])))
                print(data.get('summary'))
    asyncio.run(run())
finally:
    t.tearDown()
