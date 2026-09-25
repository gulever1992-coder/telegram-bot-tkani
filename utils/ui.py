"""Мелкие помощники для сообщений бота."""

from __future__ import annotations

from aiogram import types
from aiogram.exceptions import TelegramBadRequest


async def show(message: types.Message, text: str, **kwargs) -> None:
    """Заменяет текст сообщения; если это файл/фото (текста нет) — присылает новое сообщение."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "not modified" in str(exc):
            return
        await message.answer(text, **kwargs)
