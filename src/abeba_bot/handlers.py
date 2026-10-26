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
from .media import send_character
from .learn import register_learn_handlers

router = Router()
register_learn_handlers(router)
log = logging.getLogger(__name__)
# Only transport backpressure; durable active conversation lives in Django.
active_users = set()


def answer_controls(answer_id=None, audio_enabled=True):
    if not answer_id or not audio_enabled:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔊 Listen · በድምፅ", callback_data=f"listen:{answer_id}")
    ]])


def consent_controls():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="I agree — start chatting", callback_data="consent:accept")
    ]])


async def explain_consent(message):
    await message.answer(
        "<b>Before we chat</b>\n\n"
        "Your messages and recent conversation context are sent to Abeba’s AI provider to prepare replies. "
        "Telegram also handles messages you send here. Avoid identifying details. "
        "Abeba provides educational information, not a diagnosis.\n\n"
        "Tap I agree to enable AI chat. You can withdraw consent with /withdraw. "
        "You can read published topics with /learn at any time.",
        parse_mode="HTML", reply_markup=consent_controls(),
    )


async def send_reply(message, result, config, language="en"):
    chunks = split_html(reply_html(result, language))
    keyboard = answer_controls(result.get("message_id"), getattr(config, "audio_enabled", True))
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
        "Open the command menu or type / to find your options.\n\n"
        "/learn — browse and read topics in your language\n"
        "/chat — start or resume AI chat\n"
        "/app — open the web chat app\n"
        "/new — fresh conversation\n/history — recent conversations\n"
        "/listen — hear the latest answer in an AI-generated voice\n"
        "/language — AI chat language\n/stop — pause AI chat\n/privacy — data and consent",
        parse_mode="HTML",
    )


@router.message(Command("chat"))
async def chat_entry(message: Message, backend):
    session = await identity(message, backend)
    if not session:
        return
    if not session["ai_consent"]:
        await explain_consent(message)
        return
    if not session["bot_enabled"]:
        await backend.request("PATCH", "channels/preferences/", session["token"], {"bot_enabled": True})
    await message.answer("You’re ready to chat. Type your question whenever you’re ready.")


@router.message(Command("app"))
async def open_app(message: Message, backend, config):
    session = await identity(message, backend)
    if session:
        await message.answer(
            "Open your conversation in the Abeba chat app:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Open Abeba", web_app=WebAppInfo(url=chat_url(config.mini_app_url, session["session_id"])))
            ]]),
        )


@router.callback_query(F.data == "consent:accept")
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
    await callback.message.answer("You’re ready to chat. Type your question here. Use /learn for topics or /app for the web chat.")


@router.callback_query(F.data == "chat_here")
async def legacy_chat_entry(callback: CallbackQuery):
    await callback.answer()
    if callback.message and callback.message.chat.type == "private":
        await callback.message.answer("Use /chat to start or resume AI chat from the command menu.")



@router.message(Command("stop"))
async def stop(message: Message, backend):
    session = await identity(message, backend)
    if session:
        await backend.request(
            "PATCH", "channels/preferences/", session["token"], {"bot_enabled": False}
        )
        await message.answer(
            "AI chat is paused. Use /chat to resume, /learn to read topics, or /app to open the web chat."
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
        await callback.message.answer("Conversation selected. Send your question here or use /app for the web chat.")


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
        await message.answer("Use /chat and accept AI processing first.")
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
            if not session["ai_consent"]:
                await explain_consent(message)
            else:
                await message.answer("AI chat is paused. Use /chat to resume, or /learn to read topics.")
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
        await message.answer("Use /chat and accept AI processing before requesting spoken replies.")
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
async def retired_illustration(callback: CallbackQuery):
    # Old messages may still have the retired button; do not send another image.
    await callback.answer("Illustrations are no longer offered here. Use /learn to explore topics.")


@router.message()
async def unsupported(message: Message):
    if message.chat.type != "private":
        return
    await message.answer(
        "Send a text question to chat. Tap Listen under an answer, or use /listen, for a spoken reply. "
        "Incoming voice-note transcription is not enabled. Use /start for options."
    )
