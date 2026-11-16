"""Keep Telegram's native activity indicator alive during slow backend work."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from aiogram.exceptions import TelegramAPIError

log = logging.getLogger(__name__)


async def _pulse(bot, chat_id, action, interval):
    while True:
        await asyncio.sleep(interval)
        try:
            await bot.send_chat_action(chat_id, action)
        except TelegramAPIError:
            # A progress hint must never turn a successful request into an error.
            log.debug("Could not refresh Telegram chat action", exc_info=True)


@asynccontextmanager
async def chat_action(message, action="typing", interval=4.0):
    """Show an action immediately and refresh it until the operation completes."""
    try:
        await message.bot.send_chat_action(message.chat.id, action)
    except TelegramAPIError:
        log.debug("Could not start Telegram chat action", exc_info=True)
        yield
        return

    task = asyncio.create_task(_pulse(message.bot, message.chat.id, action, interval))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

