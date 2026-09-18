"""Локальный запуск бота (polling): python bot.py

В облаке (Vercel) бот запускается через api/webhook.py.
"""

import asyncio
import logging

from core import dp, make_bot

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    bot = make_bot()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
