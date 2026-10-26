import argparse
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, ErrorEvent, MenuButtonCommands
from .backend import Backend, BackendError
from .config import Config
from .handlers import router
from .health import health_server


async def run(config):
    backend = Backend(config.api_base_url, config.service_token)
    bot = Bot(config.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    @dispatcher.errors()
    async def error(event: ErrorEvent):
        exc = event.exception
        # Do not log URLs, update payloads, health questions or auth credentials.
        logging.getLogger(__name__).warning(
            "Bot operation failed: %s", type(exc).__name__
        )
        message = event.update.message or (
            event.update.callback_query.message if event.update.callback_query else None
        )
        if message:
            detail = (
                str(exc)
                if isinstance(exc, BackendError)
                else "Abeba could not complete that request. Please refresh history before asking again."
            )
            try:
                await message.answer(detail, parse_mode=None)
            except Exception:
                pass
        return True

    try:
        info = await bot.get_webhook_info()
        if info.url:
            raise RuntimeError(
                "This bot has a webhook. Use a separate development bot or deliberately remove the webhook before polling."
            )
        await bot.set_my_commands(
            [
                BotCommand(command=cmd, description=desc)
                for cmd, desc in [
                    ("start", "Welcome and commands"),
                    ("learn", "Browse topics in your language"),
                    ("chat", "Start or resume AI chat"),
                    ("app", "Open the Abeba web chat"),
                    ("ask", "Ask Abeba"),
                    ("new", "Start a new conversation"),
                    ("history", "Recent conversations"),
                    ("language", "Choose chat language"),
                    ("listen", "Hear the latest answer (AI voice)"),
                    ("stop", "Pause native chat"),
                    ("privacy", "Privacy and consent"),
                    ("withdraw", "Withdraw AI consent"),
                    ("help", "How to use Abeba"),
                ]
            ]
        )
        await bot.set_chat_menu_button(
            menu_button=MenuButtonCommands()
        )
        async with health_server(config.health_host, config.health_port):
            await dispatcher.start_polling(
                bot,
                backend=backend,
                config=config,
                allowed_updates=["message", "callback_query"],
                tasks_concurrency_limit=20,
            )
    finally:
        await backend.close()
        await bot.session.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration without connecting"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    for name in ("httpx", "httpcore", "aiogram"):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        config = Config.load()
        if args.check:
            print("Configuration valid. No Telegram or backend request was made.")
            return
        asyncio.run(run(config))
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
