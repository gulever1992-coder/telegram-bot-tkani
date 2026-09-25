"""Запуск бота (polling): python bot.py

Основной бот + (если задан токен) админ-бот владельца в одном процессе.
"""

import asyncio
import logging

import analytics
import config
from core import dp, make_bot

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")


async def main() -> None:
    bot = make_bot()
    await bot.delete_webhook(drop_pending_updates=True)
    tasks = [asyncio.create_task(dp.start_polling(bot), name="main-bot")]

    admin_bot = None
    if config.ADMIN_BOT_TOKEN:
        from admin_bot import admin_dp, make_admin_bot

        admin_bot = make_admin_bot()
        analytics.configure(admin_bot)
        try:
            await admin_bot.delete_webhook(drop_pending_updates=True)
            loaded = await analytics.load()
            log.info("Админ-бот включён, событий в журнале: %s", loaded)
            tasks.append(asyncio.create_task(admin_dp.start_polling(admin_bot, handle_signals=False), name="admin-bot"))
        except Exception:  # noqa: BLE001 — сбой админ-бота не должен ронять основной
            log.exception("Админ-бот не запустился, продолжаю без него")
            analytics.configure(None)

    try:
        # основной бот сам обрабатывает SIGINT/SIGTERM и завершается — тогда гасим и остальное
        await asyncio.wait({tasks[0]}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if analytics.enabled():
            await analytics.persist()  # успеть сохранить журнал перед остановкой
        for task in tasks[1:]:
            task.cancel()
        if admin_bot is not None:
            await admin_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
