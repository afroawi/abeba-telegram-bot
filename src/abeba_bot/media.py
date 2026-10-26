"""Curated local character art. Model-generated media URLs are never fetched."""
import logging
from importlib.resources import files
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile

log = logging.getLogger(__name__)
ASSETS = {
    'welcome': ('welcome.png', 'A little knowledge. A little kindness. 🌼'),
    'contraception': ('contraception.png', 'Let’s learn about family planning. 🌼'),
    'hiv': ('hiv-awareness.png', 'Clear information, without judgment. 🌼'),
}


async def send_character(message, kind='welcome'):
    if kind not in ASSETS:
        return False
    filename, caption = ASSETS[kind]
    try:
        await message.answer_photo(FSInputFile(files('abeba_bot').joinpath('assets', filename)), caption=caption, parse_mode=None)
        return True
    except (TelegramAPIError, OSError) as exc:
        # Character delivery is optional; never lose a text answer because of it.
        log.warning('Character delivery failed: %s', type(exc).__name__)
        return False
