from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import httpx
import pytest
from abeba_bot.health import health_server
from abeba_bot.config import Config
import abeba_bot.__main__ as main


async def test_liveness_and_socket_cleanup():
    async with health_server("127.0.0.1", 0) as port:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
            response = await client.get("/healthz")
            assert response.status_code == 200
            assert (await client.get("/")).json() == response.json()
            assert response.json() == {"status": "ok", "mode": "polling"}
            assert response.headers["cache-control"] == "no-store"
            assert (await client.get("/webhook")).status_code == 404
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(f"http://127.0.0.1:{port}/healthz")


async def test_polling_owns_health_server_and_releases_clients(monkeypatch):
    bot = SimpleNamespace(
        get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="")),
        set_my_commands=AsyncMock(),
        set_chat_menu_button=AsyncMock(),
        session=SimpleNamespace(close=AsyncMock()),
    )
    api = SimpleNamespace(close=AsyncMock())
    config = Config("test", "http://backend:8000/api/v1/", "test", "https://abeba.invalid/telegram/chat", "127.0.0.1", 0)
    bound_ports = []
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def tracked_server(host, port):
        async with health_server(host, port) as actual_port:
            bound_ports.append(actual_port)
            yield

    async def poll(*args, **kwargs):
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f"http://127.0.0.1:{bound_ports[0]}/healthz")
            assert response.status_code == 200
        raise RuntimeError("simulated polling failure")

    dispatcher = SimpleNamespace(
        include_router=Mock(), errors=lambda: lambda fn: fn,
        start_polling=AsyncMock(side_effect=poll),
    )
    monkeypatch.setattr(main, "Bot", lambda token: bot)
    monkeypatch.setattr(main, "Backend", lambda *args: api)
    monkeypatch.setattr(main, "Dispatcher", lambda: dispatcher)
    monkeypatch.setattr(main, "health_server", tracked_server)
    with pytest.raises(RuntimeError, match="simulated polling failure"):
        await main.run(config)
    commands = {item.command for item in bot.set_my_commands.call_args.args[0]}
    assert {'learn', 'chat', 'app', 'listen', 'withdraw'} <= commands
    assert bot.set_chat_menu_button.call_args.kwargs['menu_button'].type == 'commands'
    bot.session.close.assert_awaited_once()
    api.close.assert_awaited_once()
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(f"http://127.0.0.1:{bound_ports[0]}/healthz")


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_invalid_health_port_rejected(monkeypatch, port):
    monkeypatch.setenv("ABEBA_BOT_HEALTH_PORT", port)
    with pytest.raises(ValueError, match="ABEBA_BOT_HEALTH_PORT"):
        Config.load()
