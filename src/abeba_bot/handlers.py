import logging

import httpx
from aiogram.exceptions import TelegramAPIError
from aiogram import F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    LinkPreviewOptions,
    WebAppInfo,
)
from .backend import BackendError
from .renderers import chat_url, reply_html, split_html
from .media import illustration_for, send_character

router = Router()
log = logging.getLogger(__name__)
# Only transport backpressure; durable active conversation lives in Django.
active_users = set()


def menu(url, session_id=None, *, answer_id=None, illustration=None, audio_enabled=True):
    rows = [
        [InlineKeyboardButton(text="💬 Chat here", callback_data="chat_here")],
        [InlineKeyboardButton(text="🌼 Open chat app", web_app=WebAppInfo(url=chat_url(url, session_id)))],
    ]
    if answer_id and audio_enabled:
        rows.append([InlineKeyboardButton(text="🔊 Listen · በድምፅ", callback_data=f"listen:{answer_id}")])
    if illustration:
        rows.append([InlineKeyboardButton(text="🌼 Show illustration", callback_data=f"illustration:{illustration}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_reply(message, result, config, language="en"):
    chunks = split_html(reply_html(result, language))
    artwork = illustration_for(result.get("reply", "")) if getattr(config, "images_enabled", True) else None
    keyboard = menu(config.mini_app_url, result["session_id"], answer_id=result.get("message_id"),
                    illustration=artwork, audio_enabled=getattr(config, "audio_enabled", True))
    for index, chunk in enumerate(chunks):
        await message.answer(chunk, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True),
                             reply_markup=keyboard if index == len(chunks) - 1 else None)


async def identity(event, backend):
    private = (
        event.chat.type == "private"
        if isinstance(event, Message)
        else event.message and event.message.chat.type == "private"
    )
    if not private or not event.from_user or event.from_user.is_bot:
        if isinstance(event, Message):
            await event.answer("Please open Abeba in a private chat to continue.")
        else:
            await event.answer("Please open a private chat.", show_alert=True)
        return None
    return await backend.session(event.from_user.id)


@router.message(CommandStart())
@router.message(Command("menu", "help"))
async def start(message: Message, backend, config):
    if message.chat.type != "private":
        await message.answer("Please open Abeba in a private chat to continue.")
        return
    if getattr(config, "images_enabled", True):
        await send_character(message)
    await message.answer(
        "<b>Welcome to Abeba 🌼</b>\nA space to ask, learn and grow.\n\n"
        "<b>Choose your space</b>\n"
        "💬 <b>Chat here</b> — talk right here in Telegram.\n"
        "🌼 <b>Open chat app</b> — use the full web interface.\n"
        "Both share your conversation and allowance.\n\n"
        "<b>Before we chat</b>\n"
        "Choosing Chat here agrees to sending your messages and recent conversation context to Abeba’s AI provider. "
        "Telegram also handles messages here. Avoid identifying details. "
        "Abeba provides educational information, not a diagnosis.\n\n"
        "<b>Make yourself at home</b>\n"
        "/new — fresh conversation\n/history — recent conversations\n"
        "/listen — hear the latest answer in an AI-generated voice\n"
        "/language — English or አማርኛ\n/stop — pause native chat\n/privacy — data and consent",
        parse_mode="HTML",
        reply_markup=menu(config.mini_app_url),
    )


@router.callback_query(F.data == "chat_here")
async def enable(callback: CallbackQuery, backend, config):
    await callback.answer()
    session = await identity(callback, backend)
    if not session:
        return
    await backend.request(
        "PATCH",
        "channels/preferences/",
        session["token"],
        {
            "ai_consent": True,
            "consent_version": session["consent_version"],
            "bot_enabled": True,
        },
    )
    await callback.message.answer(
        "You can type your question here. Open the chat app any time to continue the same conversation.",
        reply_markup=menu(config.mini_app_url, session["session_id"]),
    )


@router.message(Command("stop"))
async def stop(message: Message, backend):
    session = await identity(message, backend)
    if session:
        await backend.request(
            "PATCH", "channels/preferences/", session["token"], {"bot_enabled": False}
        )
        await message.answer(
            "Native chat is paused. Use /start and choose Chat here to resume. You can still use the chat app. No reminder subscriptions exist in this initial version."
        )


@router.message(Command("privacy"))
async def privacy(message: Message, backend):
    session = await identity(message, backend)
    if session:
        await message.answer(
            "Messages are stored by Abeba to maintain your history and processed by its configured AI provider when you ask a question. Telegram also handles native bot messages. This account uses your Telegram identity.\n\nUse /withdraw to revoke AI consent across native and Mini App chat. Tap Listen or use /listen to send a saved answer to Abeba’s speech provider for an AI-generated spoken version. Audio is cached privately for up to 15 minutes and sent to Telegram only on request. Account linking/deletion and reminders are later milestones."
        )


@router.message(Command("withdraw"))
async def withdraw(message: Message, backend):
    session = await identity(message, backend)
    if session:
        await backend.request(
            "PATCH",
            "channels/preferences/",
            session["token"],
            {"ai_consent": False, "bot_enabled": False},
        )
        await message.answer(
            "AI consent withdrawn. New AI requests are disabled until you explicitly accept again."
        )


@router.message(Command("new"))
async def new(message: Message, backend, config):
    session = await identity(message, backend)
    if session:
        chat = await backend.request(
            "POST", "channels/chat/context/", session["token"], {}
        )
        await message.answer(
            "A fresh conversation is ready.",
            reply_markup=menu(config.mini_app_url, chat["session_id"]),
        )


@router.message(Command("history"))
async def history(message: Message, backend):
    session = await identity(message, backend)
    if not session:
        return
    data = await backend.request("GET", "channels/chat/history/", session["token"])
    buttons = [
        [
            InlineKeyboardButton(
                text=row["preview"][:60], callback_data=f"history:{row['id']}"
            )
        ]
        for row in data["results"]
    ]
    await message.answer(
        "Choose a recent conversation:" if buttons else "No conversations yet.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None,
    )


@router.callback_query(F.data.startswith("history:"))
async def select_history(callback: CallbackQuery, backend, config):
    await callback.answer()
    session = await identity(callback, backend)
    if not session:
        return
    try:
        chat_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        return
    chat = await backend.request(
        "PATCH", "channels/chat/context/", session["token"], {"session_id": chat_id}
    )
    answers = [row for row in chat["messages"] if row["role"] == "assistant"]
    if answers:
        answer = answers[-1]
        await callback.message.answer("<b>Conversation selected</b>\nHere’s the latest answer:", parse_mode="HTML")
        await send_reply(callback.message, {"reply": answer["text"], "structured": answer.get("structured", {}),
                                           "message_id": answer["id"], "session_id": chat_id}, config, session.get("language", "en"))
    else:
        await callback.message.answer("Conversation selected. Send your question here or open the chat app.",
                                      reply_markup=menu(config.mini_app_url, chat_id))


@router.message(Command("language"))
async def language(message: Message):
    if message.chat.type != "private":
        return
    await message.answer(
        "Choose your chat language:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="English", callback_data="language:en"),
                    InlineKeyboardButton(text="አማርኛ", callback_data="language:am"),
                ]
            ]
        ),
    )


@router.callback_query(F.data.in_({"language:en", "language:am"}))
async def set_language(callback: CallbackQuery, backend):
    await callback.answer()
    session = await identity(callback, backend)
    if session:
        await backend.request(
            "PATCH",
            "channels/preferences/",
            session["token"],
            {"language": callback.data.split(":")[1]},
        )
        await callback.message.answer("Chat language updated for the bot and chat app.")


@router.message(Command("listen"))
@router.message(F.text.lower().strip().in_({"read aloud", "read it aloud", "read that aloud", "send audio", "በድምፅ አንብቢልኝ"}))
async def listen_latest(message: Message, backend, config):
    session = await identity(message, backend)
    if not session:
        return
    if not getattr(config, "audio_enabled", True):
        await message.answer("Spoken replies are turned off. Your text answer is still available.")
        return
    if not session["ai_consent"] or not session["bot_enabled"]:
        await message.answer("Choose Chat here and accept AI processing first. Use /start to continue.")
        return
    context = await backend.request("GET", "channels/chat/context/", session["token"])
    answers = [row for row in context["messages"] if row["role"] == "assistant"]
    if not answers:
        await message.answer("Ask a question first, then tap Listen beneath the answer.")
        return
    await deliver_audio(message, session, backend, answers[-1]["id"], message.from_user.id)


@router.message(Command("ask"))
@router.message(F.text & ~F.text.startswith("/"))
async def chat(message: Message, backend, config, command: CommandObject | None = None):
    if message.chat.type != "private" or not message.from_user or message.from_user.is_bot:
        await message.answer("Please open Abeba in a private chat to continue.")
        return
    user_id = message.from_user.id
    if user_id in active_users:
        await message.answer("A request is already being prepared. Please wait.")
        return
    text = (command.args or "").strip() if command else message.text.strip()
    if not text:
        await message.answer("Type /ask followed by your question.")
        return
    active_users.add(user_id)
    try:
        session = await backend.session(user_id)
        if not session["ai_consent"] or not session["bot_enabled"]:
            await message.answer(
                "Choose Chat here to accept AI processing and enable native chat. Use /start to read the explanation.",
                reply_markup=menu(config.mini_app_url),
            )
            return
        if len(text) > 6000:
            await message.answer("Please keep your question under 6,000 characters.")
            return
        await message.bot.send_chat_action(message.chat.id, "typing")
        result = await backend.request(
            "POST",
            "channels/chat/messages/",
            session["token"],
            {
                "message": text,
                "request_key": f"telegram:{message.chat.id}:{message.message_id}",
            },
        )
        await send_reply(message, result, config, session.get("language", "en"))
    finally:
        active_users.discard(user_id)


async def deliver_audio(message, session, backend, answer_id, user_id):
    if not session["ai_consent"] or not session["bot_enabled"]:
        await message.answer("Choose Chat here and accept AI processing before requesting spoken replies. Use /start to continue.")
        return
    if user_id in active_users:
        await message.answer("A request is already being prepared. Please wait.")
        return
    active_users.add(user_id)
    try:
        await message.bot.send_chat_action(message.chat.id, "record_voice")
        audio, parts = await backend.audio(session["token"], answer_id)
        for part in range(1, parts + 1):
            if part > 1:
                audio, returned_parts = await backend.audio(session["token"], answer_id, part=part)
                if returned_parts != parts:
                    raise BackendError(409, "Audio settings changed. Tap Listen to start again.")
            caption = "በAI የተፈጠረ ድምፅ" if session.get("language") == "am" else "AI-generated Abeba voice"
            if parts > 1:
                caption += f" · {part}/{parts}"
            await message.answer_audio(BufferedInputFile(audio, filename=f"abeba-{answer_id}-{part}.mp3"),
                                       title=f"Abeba · {part}/{parts}" if parts > 1 else "Abeba’s answer",
                                       performer="Abeba (AI voice)", caption=caption, parse_mode=None)
    except (BackendError, httpx.HTTPError, TelegramAPIError) as exc:
        log.warning("Audio delivery failed: %s", type(exc).__name__)
        detail = str(exc) if isinstance(exc, BackendError) else "Could not send the audio right now. Please try again."
        await message.answer(detail + " Your text answer is still available.", parse_mode=None)
    finally:
        active_users.discard(user_id)


@router.callback_query(F.data.startswith("listen:"))
async def listen_answer(callback: CallbackQuery, backend, config):
    await callback.answer()
    session = await identity(callback, backend)
    if not session:
        return
    if not getattr(config, "audio_enabled", True):
        await callback.message.answer("Spoken replies are turned off. Your text answer is still available.")
        return
    try:
        answer_id = int(callback.data.split(":", 1)[1])
        if answer_id <= 0:
            raise ValueError
    except ValueError:
        return
    await deliver_audio(callback.message, session, backend, answer_id, callback.from_user.id)


@router.callback_query(F.data.startswith("illustration:"))
async def show_illustration(callback: CallbackQuery, config):
    await callback.answer()
    if not callback.message or callback.message.chat.type != "private" or callback.from_user.is_bot:
        return
    if not getattr(config, "images_enabled", True):
        await callback.message.answer("Character images are turned off.")
        return
    kind = callback.data.split(":", 1)[1]
    if not await send_character(callback.message, kind):
        await callback.message.answer("The illustration is unavailable right now. You can keep chatting here.")


@router.message()
async def unsupported(message: Message):
    if message.chat.type != "private":
        return
    await message.answer(
        "Send a text question to chat. Tap Listen under an answer, or use /listen, for a spoken reply. "
        "Incoming voice-note transcription is not enabled. Use /start for options."
    )
