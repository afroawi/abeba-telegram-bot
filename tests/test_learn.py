from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from abeba_bot.backend import Backend
from abeba_bot.learn import (
    LANGUAGES, article_html, learn_start, learn_callback, language_picker,
    show_topics, show_article, linked_page,
)
from abeba_bot.handlers import chat_entry, enable, open_app
from test_bot import message, backend
from test_presentation import callback, CheckedHTML, CONFIG


TOPIC = {'id': 17, 'slug': 'family-planning', 'title': 'Family planning', 'summary': 'Clear information.', 'language': 'en'}


def api_for_learn(detail=None):
    async def request(method, path, *args, **kwargs):
        assert method == 'GET'
        assert kwargs['public'] is True
        if path == 'learn/topics/':
            return {'results': [TOPIC], 'next': None, 'previous': None}
        if path == 'learn/topics/family-planning/':
            return detail or {**TOPIC, 'content_markdown': '## Learn more\n- One point\n- Another point'}
        pytest.fail(f'Unexpected path: {path}')
    return SimpleNamespace(request=AsyncMock(side_effect=request), session=AsyncMock())


async def test_learn_opens_seven_languages_without_account_or_ai():
    msg = message()
    await learn_start(msg)
    rows = msg.answer.call_args.kwargs['reply_markup'].inline_keyboard
    assert len(rows) == 7
    assert {row[0].callback_data.split(':')[2] for row in rows} == set(LANGUAGES)
    assert 'Chat here' not in msg.answer.call_args.args[0]


async def test_learn_never_reads_private_identity_in_groups():
    msg = message(private=False)
    await learn_start(msg)
    msg.answer.assert_not_called()
    api = api_for_learn()
    await learn_callback(callback(msg, 'learn:list:en:1'), api)
    api.request.assert_not_called()
    api.session.assert_not_called()


async def test_topic_list_and_pagination_only_use_fixed_backend_path():
    msg = message()
    api = SimpleNamespace(request=AsyncMock(return_value={
        'results': [TOPIC], 'next': 'https://untrusted.example/anything?page=2', 'previous': None}))
    await show_topics(msg, api, 'en', 1)
    api.request.assert_awaited_once_with('GET', 'learn/topics/', public=True, params={'language': 'en', 'page': 1})
    buttons = [b for row in msg.answer.call_args.kwargs['reply_markup'].inline_keyboard for b in row]
    assert any(button.callback_data == 'learn:read:en:1:17' for button in buttons)
    assert any(button.callback_data == 'learn:list:en:2' for button in buttons)
    assert all(not button.web_app for button in buttons)
    assert linked_page('https://example.test/?page=-1') is None


async def test_reading_a_topic_renders_published_text_without_ai_session():
    msg, api = message(), api_for_learn()
    await learn_callback(callback(msg, 'learn:read:en:1:17'), api)
    text = msg.answer.call_args.args[0]
    assert '<b>Family planning</b>' in text
    assert '<b>Learn more</b>' in text and '• One point' in text
    api.session.assert_not_called()
    assert api.request.await_count == 2
    controls = msg.answer.call_args.kwargs['reply_markup'].inline_keyboard
    assert controls[0][0].callback_data == 'learn:list:en:1'
    assert controls[1][0].callback_data == 'learn:langs:en:1:17'
    assert all('listen:' not in button.callback_data for row in controls for button in row)


async def test_switch_language_reads_same_slug_and_labels_english_fallback():
    msg, api = message(), api_for_learn()
    await learn_callback(callback(msg, 'learn:switch:am:en:1:17'), api)
    assert api.request.call_args.kwargs['params'] == {'language': 'am'}
    text = msg.answer.call_args.args[0]
    assert 'English' in text and 'ገና' in text
    assert msg.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'learn:list:am:1'


async def test_article_language_picker_has_short_callbacks_for_long_slug():
    picker = language_picker('sid:10000:9223372036854775807')
    assert all(len(row[0].callback_data.encode('utf-8')) <= 64 for row in picker.inline_keyboard)
    msg, api = message(), api_for_learn()
    await learn_callback(callback(msg, 'learn:langs:en:1:17'), api)
    api.request.assert_not_called()
    assert msg.answer.call_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data == 'learn:switch:am:en:1:17'


async def test_long_article_preserves_all_text_and_places_navigation_last():
    msg = message()
    text = 'አበባ መረጃ ትሰጣለች 🌼 ' * 1000
    api = api_for_learn({**TOPIC, 'content_markdown': text, 'language': 'am'})
    await show_article(msg, api, 'am', 'en', 1, 17)
    assert msg.answer.await_count > 1
    result = ''
    for index, call in enumerate(msg.answer.call_args_list):
        parser = CheckedHTML()
        parser.feed(call.args[0])
        assert not parser.stack
        assert len(parser.text.encode('utf-16-le')) // 2 <= 3800
        result += parser.text
        if index < msg.answer.await_count - 1:
            assert call.kwargs['reply_markup'] is None
    assert text in result
    assert msg.answer.call_args.kwargs['reply_markup'] is not None


async def test_stale_topic_does_not_open_a_different_article():
    msg, api = message(), api_for_learn()
    await show_article(msg, api, 'en', 'en', 1, 999)
    assert all(call.args[1] == 'learn/topics/' for call in api.request.call_args_list)
    assert 'topic list has changed' in msg.answer.call_args_list[0].args[0]


@pytest.mark.parametrize('data', ['learn:list:xx:1', 'learn:list:en:-1', 'learn:read:en:1:-1', 'learn:switch:../../:en:1:17', 'learn:read:en:1:1:extra'])
async def test_invalid_callbacks_never_reach_backend(data):
    msg, api = message(), api_for_learn()
    await learn_callback(callback(msg, data), api)
    api.request.assert_not_called()
    assert '/learn' in msg.answer.call_args.args[0]


def test_sections_support_links_and_untrusted_html_are_readable_and_safe():
    text = article_html({**TOPIC, 'sections': [{'title': '<Title>', 'body': '**Helpful** information', 'bullets': ['A point'], 'sort_order': 0}],
                         'key_actions': ['One action'], 'support_services': [{'name': 'Support', 'description': 'A description', 'contact': '1234'}],
                         'source_url': 'javascript:bad', 'audios': [{'title': 'Listen to the lesson', 'audio_url': 'https://example.org/lesson.mp3'}]}, 'en')
    assert '&lt;Title&gt;' in text and '<b>Helpful</b>' in text
    assert 'One action' in text and '1234' in text
    assert 'https://example.org/lesson.mp3' in text and 'javascript:' not in text


async def test_public_catalog_request_sends_no_channel_or_service_credential():
    def handler(request):
        assert request.headers.get('Authorization') is None
        assert request.headers.get('X-Abeba-Service-Token') is None
        assert dict(request.url.params) == {'language': 'am', 'page': '1'}
        return httpx.Response(200, json={'results': []})
    api = Backend('https://backend.example/api/v1/', 'private-service-test')
    await api.client.aclose()
    api.client = httpx.AsyncClient(base_url='https://backend.example/api/v1/', transport=httpx.MockTransport(handler))
    await api.request('GET', 'learn/topics/', public=True, params={'language': 'am', 'page': 1})
    await api.close()


async def test_chat_command_requires_explicit_consent(monkeypatch):
    msg, api = message(), backend(consent=False)
    import abeba_bot.handlers as handlers
    monkeypatch.setattr(handlers, 'identity', AsyncMock(return_value=await api.session(42)))
    await chat_entry(msg, api)
    api.request.assert_not_called()
    assert 'Before we chat' in msg.answer.call_args.args[0]
    assert msg.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'consent:accept'


async def test_explicit_consent_callback_enables_native_chat():
    msg, api = message(), backend(consent=False)
    api.session.return_value['consent_version'] = 'ai-v1'
    await enable(callback(msg, 'consent:accept'), api, CONFIG)
    assert api.request.call_args.args[3] == {'ai_consent': True, 'consent_version': 'ai-v1', 'bot_enabled': True}
    assert not msg.answer.call_args.kwargs.get('reply_markup')


async def test_app_handoff_exists_only_on_explicit_command(monkeypatch):
    msg, api = message(), backend()
    import abeba_bot.handlers as handlers
    monkeypatch.setattr(handlers, 'identity', AsyncMock(return_value=await api.session(42)))
    await open_app(msg, api, CONFIG)
    button = msg.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0]
    assert button.web_app.url == 'https://web.abebachat.com/telegram/chat?session=7'
    api.request.assert_not_called()
