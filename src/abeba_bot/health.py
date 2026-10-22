"""Process liveness for the polling bot; contains no account or credential data."""

from contextlib import asynccontextmanager
from aiohttp import web


async def health(request):
    return web.json_response(
        {"status": "ok", "mode": "polling"}, headers={"Cache-Control": "no-store"}
    )


@asynccontextmanager
async def health_server(host, port):
    app = web.Application(client_max_size=1024)
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
        yield runner.addresses[0][1]
    finally:
        await runner.cleanup()
