import asyncio
import logging
from aiogram import F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from .backend import BackendError
from .renderers import chat_url, split_text

router = Router()
log = logging.getLogger(__name__)
# Only transport backpressure; durable active conversation lives in Django.
active_users = set()


def menu(url, session_id=None):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Chat here", callback_data="chat_here")],
            [
                InlineKeyboardButton(
                    text="🌼 Open chat app",
                    web_app=WebAppInfo(url=chat_url(url, session_id)),
                )
            ],
        ]
    )


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
    await message.answer(
        "Welcome to Abeba 🌼\nA space to ask, learn and grow.\n\n"
        "Choose Chat here to talk in Telegram, or Open chat app for the Abeba web interface. Both share your conversation and allowance.\n\n"
        "Chat here means you agree to send your messages and recent conversation context to Abeba’s AI provider. Telegram also handles messages you send here. "
        "Avoid identifying details. Abeba provides educational information, not a diagnosis.\n\n"
        "/new — new conversation\n/history — recent conversations\n/language — English or Amharic\n/stop — pause native chat\n/privacy — data and consent controls",
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
            "Messages are stored by Abeba to maintain your history and processed by its configured AI provider when you ask a question. Telegram also handles native bot messages. This account uses your Telegram identity.\n\nUse /withdraw to revoke AI consent across native and Mini App chat. Account linking/deletion and reminders are later milestones."
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
    text = "Conversation selected. Send a follow-up here or open it in the chat app."
    if chat["messages"]:
        text += "\n\nLast answer:\n" + chat["messages"][-1]["text"][:1800]
    await callback.message.answer(text, reply_markup=menu(config.mini_app_url, chat_id))


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


@router.message(Command("ask"))
@router.message(F.text & ~F.text.startswith("/"))
async def chat(message: Message, backend, config, command: CommandObject | None = None):
    user_id = message.from_user.id
    if user_id in active_users:
        await message.answer("An answer is already being prepared. Please wait.")
        return
    if message.chat.type != "private":
        await message.answer("Please open Abeba in a private chat to continue.")
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
        for chunk in split_text(result["reply"]):
            await message.answer(chunk, parse_mode=None)
        await message.answer(
            "Continue here, or open this conversation in the chat app. AI can make mistakes; consult a professional for personal health decisions.",
            reply_markup=menu(config.mini_app_url, result["session_id"]),
        )
    finally:
        active_users.discard(user_id)


@router.message()
async def unsupported(message: Message):
    await message.answer(
        "This initial version supports text chat. Use /start for options; voice and other features are coming next."
    )
