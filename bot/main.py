from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher

from bot.handlers import router
from lecturelog.config import get_settings


async def _run():
    settings = get_settings()
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN")

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
