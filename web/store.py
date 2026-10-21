"""Хранилище сайта: один JSON-документ (пользователи, черновики, история КП, сделки) + фото.

Бесплатный хостинг стирает диск при каждом перезапуске, поэтому данные живут в Telegram:
документ — одно служебное сообщение в чате владельца с админ-ботом (без закрепления — закреп занят
журналом бота). Где оно лежит, админ-бот помнит в своём «описании» на отдельном языке (его никто не видит),
поэтому отдельный канал не нужен. Фото позиций КП — файлы Telegram: отправили, запомнили file_id, сообщение удалили.
Без админ-бота (локальный запуск) — обычные файлы в web_data/.
"""

from __future__ import annotations

import asyncio
import datetime as dt
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
PTR_LANG = "eo"  # язык «описания» бота, где лежит адрес документа с данными
PTR_PREFIX = "creatica-web-store:"
LOCAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_data")
MSK = dt.timezone(dt.timedelta(hours=3))


def _empty() -> dict:
    return {"users": {}, "drafts": {}, "kp": {}, "deals": {}, "seq": 0}


def _caption() -> str:
    stamp = dt.datetime.now(MSK).strftime("%d.%m %H:%M:%S")
    return f"🗄 Данные сайта Creatica (обновлено {stamp}) — не удаляйте это сообщение"


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
        ptr = (await self.bot.get_my_description(language_code=PTR_LANG)).description or ""
        if not ptr.startswith(PTR_PREFIX):
            log.info("данных сайта ещё нет — начинаем с пустого хранилища")
            return
        try:
            chat, msg_id = ptr[len(PTR_PREFIX):].split(":")
            if int(chat) != self.chat:
                log.warning("данные сайта лежали в другом чате (%s) — начинаем заново", chat)
                return
            # правка подписи возвращает само сообщение — так достаём документ по номеру сообщения
            try:
                msg = await self.bot.edit_message_caption(chat_id=self.chat, message_id=int(msg_id), caption=_caption())
            except TelegramAPIError as exc:
                if "not modified" not in str(exc):
                    raise
                msg = await self.bot.edit_message_caption(chat_id=self.chat, message_id=int(msg_id),
                                                          caption=_caption() + " ·")
            buf = io.BytesIO()
            await self.bot.download(msg.document, destination=buf)
        except (TelegramAPIError, ValueError, AttributeError) as exc:
            log.error("документ с данными сайта не найден (%s) — начинаем заново", exc)
            return
        self.data = {**_empty(), **json.loads(buf.getvalue().decode("utf-8"))}
        self._msg_id = int(msg_id)
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
            try:
                if self._msg_id:
                    try:
                        await self.bot.edit_message_media(
                            InputMediaDocument(media=BufferedInputFile(raw, filename=STORE_NAME), caption=_caption()),
                            chat_id=self.chat, message_id=self._msg_id)
                        return
                    except TelegramAPIError as exc:
                        if "not modified" in str(exc):
                            return
                        log.warning("документ с данными пропал (%s) — создаю новый", exc)
                sent = await self.bot.send_document(self.chat, BufferedInputFile(raw, filename=STORE_NAME),
                                                    caption=_caption(), disable_notification=True)
                self._msg_id = sent.message_id
                await self.bot.set_my_description(f"{PTR_PREFIX}{self.chat}:{sent.message_id}",
                                                  language_code=PTR_LANG)
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
            key = sent.document.file_id  # файл остаётся у Telegram и после удаления сообщения
            try:
                await self.bot.delete_message(self.chat, sent.message_id)
            except TelegramAPIError:
                pass
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
