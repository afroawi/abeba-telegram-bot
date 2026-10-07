from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from abeba_bot.backend import Backend, BackendError
from abeba_bot.handlers import chat, menu
from abeba_bot.renderers import chat_url, split_text


def message(text="Question", private=True):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=42, type="private" if private else "group"),
        from_user=SimpleNamespace(id=42, is_bot=False),
        message_id=10,
        answer=AsyncMock(),
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )


def backend(consent=True):
    return SimpleNamespace(
        session=AsyncMock(
            return_value={
                "token": "test",
                "ai_consent": consent,
                "bot_enabled": consent,
                "session_id": 7,
            }
        ),
        request=AsyncMock(return_value={"reply": "Answer", "session_id": 7}),
    )


def test_handoff_url_and_utf16_splitting():
    assert chat_url("https://abeba.example/telegram/chat", 7).endswith("?session=7")
    text = "🌼" * 3000
    chunks = list(split_text(text))
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 4000 for chunk in chunks)
    assert (
        menu("https://abeba.example/telegram/chat", 7)
        .inline_keyboard[1][0]
        .web_app.url.endswith("?session=7")
    )


async def test_group_chat_never_reaches_backend():
    msg, api = message(private=False), backend()
    await chat(
        msg, api, SimpleNamespace(mini_app_url="https://abeba.example/telegram/chat")
    )
    api.session.assert_not_called()
    api.request.assert_not_called()


async def test_consent_required_before_chat():
    msg, api = message(), backend(consent=False)
    await chat(
        msg, api, SimpleNamespace(mini_app_url="https://abeba.example/telegram/chat")
    )
    api.request.assert_not_called()
    assert "Choose Chat here" in msg.answer.call_args.args[0]


async def test_native_message_has_stable_idempotency_key_and_handoff():
    msg, api = message(), backend()
    await chat(
        msg, api, SimpleNamespace(mini_app_url="https://abeba.example/telegram/chat")
    )
    assert api.request.call_args.args[3]["request_key"] == "telegram:42:10"
    assert (
        msg.answer.call_args.kwargs["reply_markup"]
        .inline_keyboard[1][0]
        .web_app.url.endswith("?session=7")
    )


async def test_backend_error_message_and_service_header():
    api = Backend("https://backend.example/api/v1/", "private-test-secret")

    def handler(request):
        assert request.headers["X-Abeba-Service-Token"] == "private-test-secret"
        assert request.url.path == "/api/v1/channels/auth/telegram-service/"
        return httpx.Response(403, json={"error": {"message": "Denied"}})

    await api.client.aclose()
    api.client = httpx.AsyncClient(
        base_url="https://backend.example/api/v1/",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(BackendError, match="Denied"):
        await api.session(42)
    await api.close()
