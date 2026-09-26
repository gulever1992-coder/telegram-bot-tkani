"""Учёт действий менеджеров и пересылка КП владельцу через отдельного админ-бота.

Журнал событий хранится файлом, закреплённым в чате владельца с админ-ботом
(так он переживает перезапуски бота). Если админ-бот не настроен, всё это молча отключено.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InputMediaDocument

import config
import profiles

log = logging.getLogger("analytics")
MSK = dt.timezone(dt.timedelta(hours=3))
LOG_NAME = "creatica_bot_log.jsonl"
LOG_CAPTION = "🗄 Служебный журнал событий — не удаляйте это закреплённое сообщение"

KINDS = {
    "kp": "📋 КП",
    "tag": "🏷 Ценники",
    "refund": "📝 Возвраты",
    "payout": "💵 Выплаты дизайнерам",
    "search": "🔎 Поиск ткани",
    "image": "🎨 Картинки",
    "register": "🆕 Регистрации",
    "profile": "✏ Изменения профиля",
}

EVENTS: list[dict] = []
_admin_bot: Bot | None = None
_store_msg_id: int | None = None
_persist_task: asyncio.Task | None = None
_lock = asyncio.Lock()
_bg: set[asyncio.Task] = set()


def configure(admin_bot: Bot | None) -> None:
    global _admin_bot
    _admin_bot = admin_bot


def enabled() -> bool:
    return _admin_bot is not None and bool(config.ADMIN_CHAT_IDS)


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _bg.add(task)
    task.add_done_callback(_bg.discard)


# --- запись событий ------------------------------------------------------------------------


def track(
    uid: int,
    kind: str,
    detail: str = "",
    *,
    data: dict | None = None,
    document: tuple[bytes, str] | None = None,
    caption: str = "",
) -> None:
    """Записывает действие. document=(байты, имя файла) пересылается владельцу вместе с подписью."""
    if not enabled():
        return
    prof = profiles.get(uid) or {}
    event = {
        "ts": int(time.time()),
        "uid": uid,
        "name": prof.get("name", ""),
        "showroom": prof.get("showroom", ""),
        "kind": kind,
        "detail": detail,
        "data": data or {},
    }
    EVENTS.append(event)
    try:
        _spawn(_after(event, document, caption))
    except RuntimeError:  # нет запущенного цикла (тесты)
        pass


async def _after(event: dict, document: tuple[bytes, str] | None, caption: str) -> None:
    if document:
        for i, chat in enumerate(config.ADMIN_CHAT_IDS):
            try:
                sent = await _admin_bot.send_document(
                    chat, BufferedInputFile(document[0], filename=document[1]), caption=caption[:1020]
                )
                if i == 0:  # по этому id админ-бот потом перепришлёт файл КП по фильтру
                    event["data"]["msg"] = sent.message_id
            except TelegramAPIError as exc:
                log.warning("не удалось переслать КП владельцу %s: %s", chat, exc)
    _persist_soon()


def _persist_soon(delay: float = 8.0) -> None:
    global _persist_task
    if _persist_task and not _persist_task.done():
        return

    async def _later():
        await asyncio.sleep(delay)
        await persist()

    _persist_task = asyncio.get_running_loop().create_task(_later())


# --- хранение журнала ---------------------------------------------------------------------------


def _dump() -> bytes:
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in EVENTS).encode("utf-8")


async def persist() -> None:
    global _store_msg_id
    if not enabled():
        return
    chat = config.ADMIN_CHAT_IDS[0]
    async with _lock:
        data = _dump()
        try:
            if _store_msg_id:
                try:
                    await _admin_bot.edit_message_media(
                        InputMediaDocument(media=BufferedInputFile(data, filename=LOG_NAME), caption=LOG_CAPTION),
                        chat_id=chat, message_id=_store_msg_id,
                    )
                    return
                except TelegramAPIError as exc:
                    if "not modified" in str(exc):
                        return
                    _store_msg_id = None  # сообщение удалено — создадим заново
            sent = await _admin_bot.send_document(
                chat, BufferedInputFile(data, filename=LOG_NAME), caption=LOG_CAPTION, disable_notification=True
            )
            _store_msg_id = sent.message_id
            try:
                await _admin_bot.pin_chat_message(chat, sent.message_id, disable_notification=True)
            except TelegramAPIError:
                pass
        except TelegramAPIError as exc:
            log.warning("не удалось сохранить журнал: %s", exc)


async def load() -> int:
    """Читает журнал из закреплённого сообщения. Возвращает число событий."""
    global _store_msg_id
    if not enabled():
        return 0
    chat = config.ADMIN_CHAT_IDS[0]
    try:
        info = await _admin_bot.get_chat(chat)
        pinned = getattr(info, "pinned_message", None)
        doc = getattr(pinned, "document", None)
        if doc is None or doc.file_name != LOG_NAME:
            return 0
        import io

        buf = io.BytesIO()
        await _admin_bot.download(doc, destination=buf)
        loaded = []
        for line in buf.getvalue().decode("utf-8").splitlines():
            if line.strip():
                loaded.append(json.loads(line))
        EVENTS[:0] = loaded
        _store_msg_id = pinned.message_id
        return len(loaded)
    except (TelegramAPIError, ValueError, OSError) as exc:
        log.warning("не удалось прочитать журнал: %s", exc)
        return 0


# --- выборки для админ-бота ---------------------------------------------------------------------


def since(days: int | None) -> list[dict]:
    """days=1 — сегодня (по Москве), 7 — последние 7 дней, None — всё время."""
    if days is None:
        return list(EVENTS)
    now = dt.datetime.now(MSK)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=days - 1)
    cutoff = start.timestamp()
    return [e for e in EVENTS if e["ts"] >= cutoff]


def fmt_time(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, MSK).strftime("%d.%m %H:%M")
