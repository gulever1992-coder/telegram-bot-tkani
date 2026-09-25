"""Профиль менеджера. Хранится закреплённым сообщением в личном чате пользователя с ботом.

Так профиль переживает перезапуск бота (а бот на GitHub Actions перезапускается каждые ~6 часов),
не требует базы данных и не попадает в публичный репозиторий.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

HEADER = "👤 Профиль менеджера"
FOOTER = "Эти данные подставляются в КП. Изменить — в меню «👤 Мой профиль»."
FIELDS = [("name", "ФИО"), ("phone", "Телефон"), ("telegram", "Telegram"), ("showroom", "Шоу-рум")]
DEFAULT_SHOWROOM = "Флагманский шоу-рум Creatica · г. Москва, Новодевичий проезд, д. 2"

_cache: dict[int, dict | None] = {}


def render(profile: dict) -> str:
    lines = [HEADER] + [f"{label}: {profile.get(key) or '—'}" for key, label in FIELDS]
    return "\n".join(lines) + "\n\n" + FOOTER


def parse(text: str) -> dict | None:
    if not text or not text.startswith(HEADER):
        return None
    labels = {label: key for key, label in FIELDS}
    profile: dict = {}
    for line in text.splitlines()[1:]:
        label, sep, value = line.partition(": ")
        if sep and label in labels:
            value = value.strip()
            profile[labels[label]] = "" if value == "—" else value
    return profile if profile.get("name") else None


def get(uid: int) -> dict | None:
    """Профиль из памяти (после load)."""
    return _cache.get(uid)


# --- ввод-вывод (отдельно, чтобы подменять в тестах) --------------------------------


async def _read(bot: Bot, uid: int) -> tuple[str, int] | None:
    chat = await bot.get_chat(uid)
    pinned = getattr(chat, "pinned_message", None)
    if pinned is not None and getattr(pinned, "text", None):
        return pinned.text, pinned.message_id
    return None


async def _write(bot: Bot, uid: int, text: str, message_id: int | None) -> int:
    if message_id:
        try:
            await bot.edit_message_text(text, chat_id=uid, message_id=message_id, parse_mode=None)
            return message_id
        except TelegramAPIError:
            pass  # сообщение удалено или не изменилось — создадим заново
    sent = await bot.send_message(uid, text, parse_mode=None)
    try:
        await bot.pin_chat_message(uid, sent.message_id, disable_notification=True)
    except TelegramAPIError:
        pass
    return sent.message_id


# --- API -----------------------------------------------------------------------------


async def load(bot: Bot, uid: int) -> dict | None:
    if uid in _cache:
        return _cache[uid]
    try:
        found = await _read(bot, uid)
    except TelegramAPIError:
        return None  # временная ошибка — не запоминаем, попробуем в следующий раз
    profile = None
    if found:
        profile = parse(found[0])
        if profile:
            profile["msg_id"] = found[1]
    _cache[uid] = profile
    return profile


async def save(bot: Bot, uid: int, profile: dict) -> dict:
    old = _cache.get(uid) or {}
    profile = {k: (profile.get(k) or "") for k, _ in FIELDS}
    profile["msg_id"] = await _write(bot, uid, render(profile), old.get("msg_id"))
    _cache[uid] = profile
    return profile
