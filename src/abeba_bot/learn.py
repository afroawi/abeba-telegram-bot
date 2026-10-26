"""Browse published Learn content without AI calls or channel credentials."""
from html import escape
import re
from urllib.parse import parse_qs, quote, urlsplit

from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from .backend import BackendError
from .renderers import markdown_html, safe_url, split_html

LANGUAGES = {
    'en': 'English', 'am': 'አማርኛ · Amharic', 'ti': 'ትግርኛ · Tigrinya',
    'om': 'Oromo', 'aa': 'Afar', 'sid': 'Sidama', 'so': 'Somali',
}
COPY = {
    'en': {'topics': 'Learn topics', 'page': 'Page', 'choose': 'Choose a topic to read.', 'back': '← Topics',
           'languages': '🌐 Reading language', 'next': 'Next →', 'previous': '← Previous',
           'actions': 'Key points', 'support': 'Support services', 'source': 'Source', 'audio': 'Audio resources'},
    'am': {'topics': 'የመማሪያ ርዕሶች', 'page': 'ገጽ', 'choose': 'ለማንበብ ርዕስ ይምረጡ።', 'back': '← ርዕሶች',
           'languages': '🌐 የንባብ ቋንቋ', 'next': 'ቀጣይ →', 'previous': '← ቀዳሚ',
           'actions': 'ዋና ዋና ነጥቦች', 'support': 'የድጋፍ አገልግሎቶች', 'source': 'ምንጭ', 'audio': 'የድምፅ ምንጮች'},
}


def private(message):
    return bool(message and message.chat.type == 'private' and message.from_user and not message.from_user.is_bot)


def language_picker(context=None):
    buttons = []
    for language, label in LANGUAGES.items():
        data = f'learn:switch:{language}:{context}' if context else f'learn:list:{language}:1'
        buttons.append([InlineKeyboardButton(text=label, callback_data=data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def show_languages(message, context=None):
    await message.answer(
        '<b>🌼 Learn with Abeba</b>\n\nChoose a reading language. / የንባብ ቋንቋ ይምረጡ።',
        parse_mode='HTML', reply_markup=language_picker(context),
    )


async def learn_start(message):
    if private(message):
        await show_languages(message)


async def catalog(backend, language, page):
    data = await backend.request('GET', 'learn/topics/', public=True, params={'language': language, 'page': page})
    if isinstance(data, list):
        return {'results': data, 'next': None, 'previous': None}
    return data


def linked_page(url):
    if not url:
        return None
    try:
        page = int(parse_qs(urlsplit(url).query).get('page', ['1'])[0])
        return page if 1 <= page <= 10000 else None
    except (ValueError, TypeError):
        return None


async def show_topics(message, backend, language, page):
    labels = COPY.get(language, COPY['en'])
    data = await catalog(backend, language, page)
    topics = data.get('results') or []
    rows = [[InlineKeyboardButton(text=str(topic['title'])[:64], callback_data=f'learn:read:{language}:{page}:{int(topic["id"])}')]
            for topic in topics]
    pagination = []
    for key, label in [('previous', 'previous'), ('next', 'next')]:
        target = linked_page(data.get(key))
        if target:
            pagination.append(InlineKeyboardButton(text=labels[label], callback_data=f'learn:list:{language}:{target}'))
    if pagination:
        rows.append(pagination)
    rows.append([InlineKeyboardButton(text=labels['languages'], callback_data='learn:languages')])
    heading = f'<b>🌼 {labels["topics"]}</b>\n{escape(LANGUAGES[language])} · {labels["page"]} {page}\n\n'
    if topics:
        heading += labels['choose']
        actual = {topic.get('language', language) for topic in topics}
        if actual != {language}:
            heading += '\n\n' + fallback_note(language, next(iter(actual)) if len(actual) == 1 else 'en')
    else:
        heading += 'No published topics are available in this language yet. Please choose another language.'
    await message.answer(heading, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def fallback_note(requested, actual):
    if requested == 'am':
        return f'ይህ ይዘት በአማርኛ ገና አልተዘጋጀም። የሚታየው ቋንቋ፦ {escape(LANGUAGES.get(actual, actual))}።'
    return f'This content is not published in {escape(LANGUAGES[requested])} yet. Showing {escape(LANGUAGES.get(actual, actual))}.'


def article_html(topic, requested):
    actual = topic.get('language', requested)
    labels = COPY.get(requested, COPY['en'])
    output = ['<b>' + escape(str(topic['title'])) + '</b>', escape(LANGUAGES.get(actual, actual))]
    if actual != requested:
        output.append(fallback_note(requested, actual))
    if topic.get('summary'):
        output.append(markdown_html(topic['summary']))
    if topic.get('content_markdown'):
        output.append(markdown_html(topic['content_markdown']))
    else:
        for section in sorted(topic.get('sections') or [], key=lambda row: row.get('sort_order', 0)):
            if section.get('title'):
                output.append('<b>' + escape(str(section['title'])) + '</b>')
            if section.get('body'):
                output.append(markdown_html(section['body']))
            bullets = [item for item in section.get('bullets') or [] if isinstance(item, str) and item.strip()]
            if bullets:
                output.append('\n'.join('• ' + markdown_html(item) for item in bullets))
    actions = [item for item in topic.get('key_actions') or [] if isinstance(item, str) and item.strip()]
    if actions:
        output.append('<b>' + labels['actions'] + '</b>\n' + '\n'.join('• ' + markdown_html(item) for item in actions))
    services = topic.get('support_services') or []
    if services:
        output.append('<b>' + labels['support'] + '</b>')
        for service in services:
            output.append('\n'.join(escape(str(service[key])) for key in ('name', 'description', 'contact') if service.get(key)))
    audios = [audio for audio in topic.get('audios') or [] if safe_url(audio.get('audio_url', ''))]
    if audios:
        output.append('<b>' + labels['audio'] + '</b>\n' + '\n'.join(
            f'<a href="{escape(audio["audio_url"], quote=True)}">{escape(str(audio.get("title") or labels["audio"]))}</a>' for audio in audios))
    if safe_url(topic.get('source_url', '')):
        output.append(f'<a href="{escape(topic["source_url"], quote=True)}">{labels["source"]}</a>')
    return '\n\n'.join(output)


async def show_article(message, backend, requested, source_language, page, topic_id):
    # Resolve a stable catalog ID, not a long slug in Telegram's 64-byte callback.
    # Re-check this page so an edited/deleted topic cannot open the wrong article.
    data = await catalog(backend, source_language, page)
    row = next((row for row in data.get('results') or [] if row['id'] == topic_id), None)
    if not row:
        await message.answer('The topic list has changed. Please choose your topic again.')
        await show_topics(message, backend, requested, 1)
        return
    slug = row['slug']
    if not isinstance(slug, str) or not re.fullmatch(r'[-a-zA-Z0-9_]{1,160}', slug):
        raise BackendError(502, 'This topic is unavailable right now.')
    topic = await backend.request('GET', f'learn/topics/{quote(slug, safe="")}/', public=True, params={'language': requested})
    labels = COPY.get(requested, COPY['en'])
    context = f'{source_language}:{page}:{topic_id}'
    back_page = page if requested == source_language else 1
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels['back'], callback_data=f'learn:list:{requested}:{back_page}')],
        [InlineKeyboardButton(text=labels['languages'], callback_data=f'learn:langs:{context}')],
    ])
    chunks = split_html(article_html(topic, requested))
    for index, chunk in enumerate(chunks):
        await message.answer(chunk, parse_mode='HTML', link_preview_options=LinkPreviewOptions(is_disabled=True),
                             reply_markup=keyboard if index == len(chunks) - 1 else None)


def reading_context(fields):
    language, page, topic_id = fields
    if language not in LANGUAGES:
        raise ValueError
    page, topic_id = int(page), int(topic_id)
    if not 1 <= page <= 10000 or not 1 <= topic_id < 2**63:
        raise ValueError
    return language, page, topic_id


async def learn_callback(callback, backend):
    await callback.answer()
    if not callback.message or callback.message.chat.type != 'private' or callback.from_user.is_bot:
        return
    fields = callback.data.split(':')
    try:
        if fields == ['learn', 'languages']:
            await show_languages(callback.message)
        elif len(fields) == 4 and fields[1] == 'list':
            language, page = fields[2], int(fields[3])
            if language not in LANGUAGES or not 1 <= page <= 10000:
                raise ValueError
            await show_topics(callback.message, backend, language, page)
        elif len(fields) == 5 and fields[1] in {'read', 'langs'}:
            language, page, topic_id = reading_context(fields[2:])
            if fields[1] == 'langs':
                await show_languages(callback.message, f'{language}:{page}:{topic_id}')
            else:
                await show_article(callback.message, backend, language, language, page, topic_id)
        elif len(fields) == 6 and fields[1] == 'switch':
            requested = fields[2]
            if requested not in LANGUAGES:
                raise ValueError
            source_language, page, topic_id = reading_context(fields[3:])
            await show_article(callback.message, backend, requested, source_language, page, topic_id)
        else:
            raise ValueError
    except (ValueError, TypeError):
        await callback.message.answer('This reading option has expired. Use /learn to browse again.')
    except BackendError as exc:
        if exc.status == 404:
            await callback.message.answer('This topic or page is no longer available. Use /learn to browse the latest topics.')
        else:
            raise


def register_learn_handlers(router):
    router.message.register(learn_start, Command('learn'))
    router.callback_query.register(learn_callback, F.data.startswith('learn:'))
