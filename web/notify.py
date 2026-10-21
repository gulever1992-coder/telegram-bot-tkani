"""Сообщения владельцу в админ-бот: всё, что менеджеры делают на сайте, приходит в Telegram.

Админ-бот здесь только отправляет сообщения (без приёма обновлений) — поэтому не мешает
тому же боту, который работает в основном процессе.
"""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger("web.notify")
_bot: Bot | None = None
_chats: list[int] = []
_bg: set[asyncio.Task] = set()
TAG = "🌐 <b>Сайт</b> · "


def configure(bot: Bot | None, chats: list[int]) -> None:
    global _bot, _chats
    _bot, _chats = bot, list(chats)


def who(user: dict) -> str:
    showroom = html.escape(user.get("showroom") or "")
    return f"👤 Менеджер: {html.escape(user.get('name') or '—')}" + (f" · {showroom}" if showroom else "")


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _bg.add(task)
    task.add_done_callback(_bg.discard)


def text(body: str, buttons: list[tuple[str, str]] | None = None) -> None:
    """buttons — [(подпись, url)], каждая кнопка отдельной строкой."""
    if _bot is None:
        log.info("[владельцу] %s %s", body, buttons or "")
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, url=u)] for t, u in buttons]) \
        if buttons else None

    async def send():
        for chat in _chats:
            for i in range(0, len(body), 3800):
                try:
                    await _bot.send_message(chat, TAG + body[i:i + 3800] if i == 0 else body[i:i + 3800],
                                            reply_markup=markup if i + 3800 >= len(body) else None)
                except TelegramAPIError as exc:
                    log.warning("не отправилось владельцу %s: %s", chat, exc)

    _spawn(send())


def document(data: bytes, filename: str, caption: str) -> None:
    if _bot is None:
        log.info("[владельцу] файл %s: %s", filename, caption)
        return

    async def send():
        for chat in _chats:
            try:
                await _bot.send_document(chat, BufferedInputFile(data, filename=filename),
                                         caption=(TAG + caption)[:1020])
            except TelegramAPIError as exc:
                log.warning("файл владельцу %s не отправился: %s", chat, exc)

    _spawn(send())
