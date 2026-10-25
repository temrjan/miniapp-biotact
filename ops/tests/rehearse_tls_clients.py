"""Fresh HTTPX and actual aiogram/aiohttp session trust; no bot/business calls."""

import asyncio
import json

import httpx
from aiogram.client.session.aiohttp import AiohttpSession

INCIDENT = [
    "dv-g3b-emudhra.we-have-a-problem.com",
    "dv-g3b-noct-emudhra.we-have-a-problem.com",
    "dv-g3b-aaa-emudhra.we-have-a-problem.com",
    "dv-g3b-aaa-noct-emudhra.we-have-a-problem.com",
]
CONTROL = [
    "pypi.org",
    "files.pythonhosted.org",
    "api.telegram.org",
    "app.biotact.uz",
    "mesh.multicard.uz",
    "team.biotact.uz",
]
results = {"httpx": {}, "aiogram_aiohttp": {}}
with httpx.Client(trust_env=False, timeout=5) as client:
    for host in INCIDENT + CONTROL:
        try:
            response = client.get("https://" + host + "/")
            results["httpx"][host] = "accepted"
        except httpx.ConnectError as error:
            if "CERTIFICATE_VERIFY_FAILED" not in str(error):
                raise
            results["httpx"][host] = "certificate_rejected"


async def aiohttp_probe():
    owner = AiohttpSession(timeout=5)
    try:
        session = await owner.create_session()
        for host in INCIDENT + CONTROL:
            try:
                async with session.get("https://" + host + "/", allow_redirects=False) as _response:
                    results["aiogram_aiohttp"][host] = "accepted"
            except Exception as error:
                if "certificate verify failed" not in str(error).lower():
                    raise
                results["aiogram_aiohttp"][host] = "certificate_rejected"
    finally:
        await owner.close()


asyncio.run(aiohttp_probe())
print(json.dumps(results, indent=2))
for library in results.values():
    assert all(library[host] == "accepted" for host in CONTROL)
    assert all(library[host] == "certificate_rejected" for host in INCIDENT)
print("FRESH_HTTPX_AND_ACTUAL_AIOGRAM_SESSION_TRUST_PASSED")
