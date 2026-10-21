"""Хранилище сайта: один JSON-документ (пользователи, черновики, история КП, сделки) + фото.

Бесплатный хостинг стирает диск при каждом перезапуске, поэтому данные живут в Telegram — так же,
как журнал бота: документ закреплён в закрытом канале, куда добавлен админ-бот (WEB_STORE_CHAT).
Без канала (локальный запуск) — обычные файлы в web_data/.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import time
from collections import OrderedDict

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InputMediaDocument

log = logging.getLogger("web.store")
STORE_NAME = "creatica_web_store.json"
STORE_CAPTION = "🗄 Данные сайта Creatica — не удаляйте и не открепляйте это сообщение"
LOCAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_data")


def _empty() -> dict:
    return {"users": {}, "drafts": {}, "kp": {}, "deals": {}, "seq": 0}


class Store:
    def __init__(self, bot: Bot | None, chat_id: int | None):
        self.bot, self.chat = (bot, chat_id) if bot and chat_id else (None, None)
        self.data = _empty()
        self._msg_id: int | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._photos: OrderedDict[str, bytes] = OrderedDict()  # кэш фото в памяти

    @property
    def remote(self) -> bool:
        return self.bot is not None

    # --- загрузка / сохранение ---------------------------------------------------------------

    async def load(self) -> None:
        if not self.remote:
            path = os.path.join(LOCAL_DIR, STORE_NAME)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    self.data = {**_empty(), **json.load(f)}
            return
        info = await self.bot.get_chat(self.chat)
        pinned = getattr(info, "pinned_message", None)
        doc = getattr(pinned, "document", None)
        if doc is None or doc.file_name != STORE_NAME:
            log.info("данных сайта ещё нет — начинаем с пустого хранилища")
            return
        buf = io.BytesIO()
        await self.bot.download(doc, destination=buf)
        self.data = {**_empty(), **json.loads(buf.getvalue().decode("utf-8"))}
        self._msg_id = pinned.message_id
        log.info("данные сайта загружены: пользователей %s", len(self.data["users"]))

    def changed(self, delay: float = 2.0) -> None:
        """Сохранить чуть позже (несколько изменений подряд — одна запись)."""
        if self._task and not self._task.done():
            return

        async def later():
            await asyncio.sleep(delay)
            await self.flush()

        self._task = asyncio.get_running_loop().create_task(later())

    async def flush(self) -> None:
        raw = json.dumps(self.data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        async with self._lock:
            if not self.remote:
                os.makedirs(LOCAL_DIR, exist_ok=True)
                tmp = os.path.join(LOCAL_DIR, STORE_NAME + ".tmp")
                with open(tmp, "wb") as f:
                    f.write(raw)
                os.replace(tmp, os.path.join(LOCAL_DIR, STORE_NAME))
                return
            file = BufferedInputFile(raw, filename=STORE_NAME)
            try:
                if self._msg_id:
                    try:
                        await self.bot.edit_message_media(InputMediaDocument(media=file, caption=STORE_CAPTION),
                                                          chat_id=self.chat, message_id=self._msg_id)
                        return
                    except TelegramAPIError as exc:
                        if "not modified" in str(exc):
                            return
                        self._msg_id = None  # сообщение удалили — создадим заново
                sent = await self.bot.send_document(self.chat, BufferedInputFile(raw, filename=STORE_NAME),
                                                    caption=STORE_CAPTION, disable_notification=True)
                self._msg_id = sent.message_id
                await self.bot.pin_chat_message(self.chat, sent.message_id, disable_notification=True)
            except TelegramAPIError as exc:
                log.error("не удалось сохранить данные сайта: %s", exc)

    def next_id(self) -> int:
        self.data["seq"] += 1
        return self.data["seq"]

    # --- фото позиций КП ----------------------------------------------------------------------

    async def put_photo(self, jpeg: bytes) -> str:
        if not self.remote:
            key = "local:" + hashlib.sha1(jpeg).hexdigest()
            os.makedirs(os.path.join(LOCAL_DIR, "photos"), exist_ok=True)
            with open(os.path.join(LOCAL_DIR, "photos", key[6:] + ".jpg"), "wb") as f:
                f.write(jpeg)
        else:
            sent = await self.bot.send_document(self.chat, BufferedInputFile(jpeg, filename="photo.jpg"),
                                                disable_notification=True)
            key = sent.document.file_id
        self._remember(key, jpeg)
        return key

    async def get_photo(self, key: str | None) -> bytes | None:
        if not key:
            return None
        if key in self._photos:
            self._photos.move_to_end(key)
            return self._photos[key]
        try:
            if key.startswith("local:"):
                with open(os.path.join(LOCAL_DIR, "photos", key[6:] + ".jpg"), "rb") as f:
                    data = f.read()
            elif self.remote:
                buf = io.BytesIO()
                await self.bot.download(key, destination=buf)
                data = buf.getvalue()
            else:
                return None
        except (OSError, TelegramAPIError) as exc:
            log.warning("фото %s не загрузилось: %s", key[:20], exc)
            return None
        self._remember(key, data)
        return data

    def _remember(self, key: str, data: bytes) -> None:
        self._photos[key] = data
        while len(self._photos) > 60:
            self._photos.popitem(last=False)


def now() -> int:
    return int(time.time())
