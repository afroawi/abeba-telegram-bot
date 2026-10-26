from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto

from abeba_bot.backend import Backend, BackendError
from abeba_bot.handlers import chat, deliver_audio, listen_answer, start, retired_illustration, active_users
from abeba_bot.media import send_character
from abeba_bot.renderers import markdown_html, reply_html, split_html
from test_bot import message, backend

CONFIG = SimpleNamespace(mini_app_url='https://web.abebachat.com/telegram/chat', images_enabled=True, audio_enabled=True)


class CheckedHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.text = [], ''

    def handle_starttag(self, tag, attrs):
        assert tag in {'b', 'i', 'code', 'pre', 'a'}
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, text):
        self.text += text


def test_formatted_answer_escapes_untrusted_html_and_links():
    rendered = reply_html({'reply': '# Hello\n**Clear** advice & care\n- A point\n<script>unsafe</script>',
                           'structured': {'examples': ['One example'], 'suggested_followups': ['What next?'],
                                          'links': [{'title': '<WHO>', 'url': 'https://who.int/path?a=1&b=2'},
                                                    {'title': 'unsafe', 'url': 'javascript:alert(1)'}]}})
    assert '<b>Hello</b>' in rendered and '<b>Clear</b>' in rendered
    assert '• A point' in rendered and '&lt;script&gt;' in rendered
    assert '<script>' not in rendered and 'javascript:' not in rendered
    assert 'https://who.int/path?a=1&amp;b=2' in rendered
    assert 'One example' in rendered and 'What next?' in rendered


def test_html_chunks_keep_text_entities_and_nested_tags_intact():
    text = '🌼 & < > አበባ ' * 900
    rendered = '<b>Heading</b>\n<a href="https://who.int/"><i>' + markdown_html(text) + '</i></a>'
    chunks = split_html(rendered)
    reconstructed = ''
    for chunk in chunks:
        parser = CheckedHTML()
        parser.feed(chunk)
        assert not parser.stack
        assert len(parser.text.encode('utf-16-le')) // 2 <= 3800
        reconstructed += parser.text
    assert reconstructed == 'Heading\n' + text
    assert len(chunks) > 1


def test_code_and_invalid_link_are_readable():
    rendered = markdown_html('`<tag>`\n```\na & b\n```\n[x](javascript:bad)')
    assert '<code>&lt;tag&gt;</code>' in rendered
    assert '<pre>a &amp; b</pre>' in rendered
    assert 'javascript:' not in rendered


async def test_answer_has_structured_formatting_and_only_listen():
    msg, api = message(), backend()
    api.request.return_value = {'reply': '**HIV facts**', 'session_id': 7, 'message_id': 81,
                                'structured': {'examples': ['Testing helps.']}}
    await chat(msg, api, CONFIG)
    call = msg.answer.call_args
    assert call.kwargs['parse_mode'] == 'HTML'
    assert call.kwargs['link_preview_options'].is_disabled
    assert 'Testing helps.' in call.args[0]
    callbacks = [button.callback_data for row in call.kwargs['reply_markup'].inline_keyboard for button in row]
    assert callbacks == ['listen:81']
    assert api.request.await_count == 1  # No audio/provider request during text chat.


async def test_welcome_includes_character_and_formatted_menu():
    msg = message()
    msg.answer_photo = AsyncMock()
    await start(msg, backend(), CONFIG)
    msg.answer_photo.assert_awaited_once()
    assert msg.answer.call_args.kwargs['parse_mode'] == 'HTML'
    assert '/listen' in msg.answer.call_args.args[0]


async def test_missing_photo_keeps_welcome_text_working():
    msg = message()
    msg.answer_photo = AsyncMock(side_effect=TelegramBadRequest(method=SendPhoto(chat_id=42, photo='missing'), message='Cannot send'))
    await start(msg, backend(), CONFIG)
    msg.answer.assert_awaited_once()
    assert 'Welcome' in msg.answer.call_args.args[0]
    assert not await send_character(msg, '../../secret')


def callback(msg=None, data='listen:81'):
    return SimpleNamespace(answer=AsyncMock(), message=msg or message(),
                           from_user=SimpleNamespace(id=42, is_bot=False), data=data)


async def test_listen_is_on_demand_and_sends_each_mp3_part():
    msg, api = message(), backend()
    msg.answer_audio = AsyncMock()
    api.audio = AsyncMock(side_effect=[(b'ID3-part-1', 2), (b'ID3-part-2', 2)])
    await listen_answer(callback(msg), api, CONFIG)
    assert api.audio.await_count == 2
    assert msg.answer_audio.await_count == 2
    assert 'AI-generated' in msg.answer_audio.call_args_list[0].kwargs['caption']
    assert '2/2' in msg.answer_audio.call_args_list[1].kwargs['caption']
    assert msg.answer_audio.call_args_list[0].args[0].data == b'ID3-part-1'
    assert 42 not in active_users


async def test_audio_consent_pause_and_groups_do_not_generate():
    msg, api = message(), backend(consent=False)
    api.audio = AsyncMock()
    await listen_answer(callback(msg), api, CONFIG)
    api.audio.assert_not_called()
    api.session.reset_mock()
    await listen_answer(callback(message(private=False)), api, CONFIG)
    api.session.assert_not_called()


async def test_audio_failure_is_clear_and_does_not_leave_user_busy():
    msg, api = message(), backend()
    api.audio = AsyncMock(side_effect=BackendError(503, 'Spoken replies are not configured yet.'))
    await deliver_audio(msg, await api.session(42), api, 81, 42)
    assert 'text answer is still available' in msg.answer.call_args.args[0]
    assert 42 not in active_users


async def test_disabled_media_flags_hide_buttons_and_skip_photo():
    msg, api = message(), backend()
    config = SimpleNamespace(mini_app_url=CONFIG.mini_app_url, images_enabled=False, audio_enabled=False)
    await start(msg, api, config)
    msg.answer.reset_mock()
    api.request.return_value = {'reply': 'Answer', 'session_id': 7, 'message_id': 81}
    await chat(msg, api, config)
    assert msg.answer.call_args.kwargs['reply_markup'] is None


async def test_retired_illustration_does_not_send_a_photo():
    msg = message()
    msg.answer_photo = AsyncMock()
    cb = callback(msg, 'illustration:hiv')
    await retired_illustration(cb)
    msg.answer_photo.assert_not_called()
    assert '/learn' in cb.answer.call_args.args[0]


async def test_audio_backend_requires_binary_and_part_headers():
    def handler(request):
        assert request.headers['Authorization'] == 'Bearer private-test'
        assert request.url.path == '/api/v1/channels/chat/audio/'
        return httpx.Response(200, content=b'ID3-audio', headers={
            'Content-Type': 'audio/mpeg', 'X-Abeba-Audio-Part': '1', 'X-Abeba-Audio-Parts': '1'})
    api = Backend('https://backend.example/api/v1/', 'test')
    await api.client.aclose()
    api.client = httpx.AsyncClient(base_url='https://backend.example/api/v1/', transport=httpx.MockTransport(handler))
    assert await api.audio('private-test', 81) == (b'ID3-audio', 1)
    await api.close()


async def test_audio_backend_rejects_html_response():
    api = Backend('https://backend.example/api/v1/', 'test')
    await api.client.aclose()
    api.client = httpx.AsyncClient(base_url='https://backend.example/api/v1/', transport=httpx.MockTransport(
        lambda _: httpx.Response(200, text='<html>not audio</html>')))
    with pytest.raises(BackendError, match='invalid audio'):
        await api.audio('test', 81)
    await api.close()


@pytest.mark.parametrize('text,expected', [(' read it aloud ', 'listen_latest'), ('How can I learn more?', 'chat'), ('/learn', 'learn_start'), ('/chat', 'chat_entry'), ('/app', 'open_app')])
async def test_audio_phrases_route_before_general_text(text, expected):
    from datetime import datetime, timezone
    from aiogram.types import Message, Chat, User
    from abeba_bot.handlers import router
    msg = Message(message_id=10, date=datetime.now(timezone.utc), chat=Chat(id=42, type='private'),
                  from_user=User(id=42, is_bot=False, first_name='Test'), text=text)
    for handler in router.message.handlers:
        matched, _ = await handler.check(msg, bot=SimpleNamespace(me=AsyncMock()))
        if matched:
            assert handler.callback.__name__ == expected
            break
    else:
        pytest.fail('No message handler matched')
