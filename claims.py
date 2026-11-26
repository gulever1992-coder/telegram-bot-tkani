"""Рекламации: общий код бота, админ-бота и сайта.

Менеджер передаёт заявку -> она приходит рекламационистам (кого назначил владелец) и самому владельцу в админ-бот.
Рекламационист кнопками отмечает «в работе» / «решена» -> владелец и менеджер получают отбивку.

Заявки из бота хранятся в журнале админ-бота (analytics.CLAIMS), заявки с сайта — в хранилище сайта; id начинается
с «b» (бот) или «w» (сайт). Кнопки у рекламациониста работают в админ-боте для обоих видов.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import html
import logging
import os
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument,
                           InputMediaPhoto)
from aiogram.utils.keyboard import InlineKeyboardBuilder

log = logging.getLogger("claims")
MSK = dt.timezone(dt.timedelta(hours=3))

STATUS = {"new": "🆕 Новая", "work": "🛠 В работе", "done": "✅ Решена"}
WEB_BASE = (os.getenv("WEB_BASE_URL") or "https://creatica-web.onrender.com").rstrip("/")

# основной бот (для отбивок менеджеру) — задаётся в bot.py
main_bot: Bot | None = None


# Виды заявок: рекламация и запрос логисту — устроены одинаково, различаются текстами и получателями.
KINDS = {
    "claim": {"icon": "📣", "title": "Рекламация", "plural": "Рекламации", "of": "по рекламации",
              "who": "рекламационист", "to": "рекламационисту", "users": "rec_users", "tokens": "rec_tokens",
              "item": "Изделие", "main": "Проблема"},
    "log": {"icon": "📦", "title": "Запрос логисту", "plural": "Запросы логисту", "of": "по запросу логисту",
            "who": "логист", "to": "логисту", "users": "log_users", "tokens": "log_tokens",
            "item": "Адрес доставки", "main": "Запрос"},
}


def kind_of(c: dict) -> str:
    return c.get("kind") if c.get("kind") in KINDS else "claim"


def kind(c: dict) -> dict:
    return KINDS[kind_of(c)]


def esc(x) -> str:
    return html.escape(str(x or ""))


def number(c: dict) -> str:
    """№12 для заявок бота, №С3 — для заявок с сайта."""
    return ("С" if c["id"].startswith("w") else "") + c["id"][1:]


def _when(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, MSK).strftime("%d.%m %H:%M")


def claim_text(c: dict) -> str:
    by = c.get("by") or {}
    k = kind(c)
    src = " · с сайта" if c["id"].startswith("w") else ""
    lines = [
        f"{k['icon']} <b>{k['title']} №{number(c)}</b> · {STATUS[c['status']]}",
        f"👤 Менеджер: {esc(by.get('name') or '—')}" + (f" · {esc(by['showroom'])}" if by.get("showroom") else "") + src,
        "",
        f"Договор / заказ: <b>{esc(c.get('order'))}</b>",
        f"Клиент: {esc(c.get('client'))}" + (f", {esc(c['client_phone'])}" if c.get("client_phone") else ""),
        f"{k['item']}: {esc(c.get('item'))}",
        "",
        f"<b>{k['main']}:</b>",
        esc(c.get("problem")),
    ]
    if c.get("photos_n"):
        lines.append(f"\n📷 Фото: {c['photos_n']}")
    hist = c.get("log") or []
    if hist:
        lines.append("")
        for h in hist:
            lines.append(f"{STATUS[h['status']]} — {esc(h.get('by'))}, {_when(h['ts'])}")
    for cm in c.get("comments") or []:
        lines.append(f"💬 {esc(cm.get('by'))}: {esc(cm.get('text'))}")
    return "\n".join(lines)[:3900]


def claim_markup(c: dict) -> InlineKeyboardMarkup | None:
    b = InlineKeyboardBuilder()
    if c["status"] == "new":
        b.row(InlineKeyboardButton(text="🛠 Взял в работу", callback_data=f"rc:{c['id']}:w"),
              InlineKeyboardButton(text="✅ Решена", callback_data=f"rc:{c['id']}:d"))
    elif c["status"] == "work":
        b.row(InlineKeyboardButton(text="✅ Решена", callback_data=f"rc:{c['id']}:d"))
    else:
        return None
    return b.as_markup()


def apply_status(c: dict, status: str, by: str) -> bool:
    """Меняет статус заявки. False — переход невозможен (уже решена / тот же статус)."""
    order = {"new": 0, "work": 1, "done": 2}
    if status not in order or order[status] <= order[c["status"]]:
        return False
    c["status"] = status
    c.setdefault("log", []).append({"ts": int(time.time()), "status": status, "by": by})
    return True


def add_comment(c: dict, by: str, text: str) -> None:
    c.setdefault("comments", []).append({"ts": int(time.time()), "by": by, "text": text[:800]})


def short_line(c: dict) -> str:
    by = c.get("by") or {}
    return (f"{STATUS[c['status']]} {kind(c)['icon']} №{number(c)} · {esc(c.get('order'))} · {esc(c.get('client'))} · "
            f"{esc(by.get('name'))}, {_when(c['ts'])}")


# --- отправка заявки получателям (админ-бот) ---------------------------------------------------------


async def deliver(admin_bot: Bot, c: dict, chats: list[int], photos: list) -> None:
    """Шлёт заявку каждому получателю (рекламационисты + владельцы) и запоминает сообщения, чтобы потом править.

    photos — список: bytes (загрузим как фото) или str (file_id админ-бота, пришлём документом)."""
    c["photos_n"] = len(photos)
    c["msgs"] = []
    seen: set[int] = set()
    for chat in chats:
        if chat in seen:
            continue
        seen.add(chat)
        try:
            sent = await admin_bot.send_message(chat, claim_text(c), reply_markup=claim_markup(c))
            c["msgs"].append({"chat": chat, "mid": sent.message_id})
        except TelegramAPIError as exc:
            log.warning("рекламация %s: не отправилась %s: %s", c["id"], chat, exc)
            continue
        await _send_photos(admin_bot, chat, photos)


async def _send_photos(bot: Bot, chat: int, photos: list) -> None:
    raw = [p for p in photos if isinstance(p, (bytes, bytearray))]
    ids = [p for p in photos if isinstance(p, str)]
    try:
        if len(raw) == 1:
            await bot.send_photo(chat, BufferedInputFile(bytes(raw[0]), filename="photo.jpg"))
        elif raw:
            for i in range(0, len(raw), 10):
                await bot.send_media_group(chat, [InputMediaPhoto(media=BufferedInputFile(bytes(b), filename=f"p{j}.jpg"))
                                                  for j, b in enumerate(raw[i:i + 10])])
        if len(ids) == 1:
            await bot.send_document(chat, ids[0])
        elif ids:
            for i in range(0, len(ids), 10):
                await bot.send_media_group(chat, [InputMediaDocument(media=x) for x in ids[i:i + 10]])
    except TelegramAPIError as exc:
        log.warning("рекламация: фото не отправились %s: %s", chat, exc)


async def refresh(admin_bot: Bot, c: dict) -> None:
    """Обновляет текст и кнопки во всех копиях заявки."""
    text, markup = claim_text(c), claim_markup(c)
    for m in c.get("msgs") or []:
        try:
            await admin_bot.edit_message_text(text, chat_id=m["chat"], message_id=m["mid"], reply_markup=markup)
        except TelegramAPIError as exc:
            if "not modified" not in str(exc):
                log.warning("рекламация %s: не обновилась копия %s: %s", c["id"], m, exc)


async def notify_requester(c: dict, text: str) -> None:
    """Отбивка менеджеру, который подал заявку (из бота — в Telegram; с сайта — он увидит статус на странице)."""
    uid = (c.get("by") or {}).get("uid")
    if not uid or main_bot is None:
        return
    try:
        await main_bot.send_message(uid, text)
    except TelegramAPIError as exc:
        log.warning("рекламация %s: менеджеру %s не доставлено: %s", c["id"], uid, exc)


# --- подпись запросов «бот -> сайт» (общий секрет выводится из токена админ-бота) ------------------------


def _key(admin_token: str) -> bytes:
    return hashlib.sha256(("creatica-bridge:" + admin_token).encode()).digest()


def sign(admin_token: str, ts: str, body: bytes) -> str:
    return hmac.new(_key(admin_token), ts.encode() + b"." + body, hashlib.sha256).hexdigest()


def check(admin_token: str, ts: str, body: bytes, sig: str) -> bool:
    try:
        if abs(time.time() - int(ts)) > 300:
            return False
    except ValueError:
        return False
    return hmac.compare_digest(sign(admin_token, ts, body), sig or "")


async def web_call(admin_token: str, path: str, payload: dict | None = None, method: str = "POST") -> dict | None:
    """Вызов сайта из бота. None — сайт не ответил (спит/недоступен)."""
    import json

    import aiohttp

    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    headers = {"X-Ts": ts, "X-Sig": sign(admin_token, ts, body), "Content-Type": "application/json"}
    for attempt in range(2):  # бесплатный Render просыпается до минуты
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, WEB_BASE + path, data=body, headers=headers,
                                           timeout=aiohttp.ClientTimeout(total=75)) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    log.warning("сайт ответил %s на %s", resp.status, path)
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("сайт не отвечает (%s), попытка %s: %r", path, attempt + 1, exc)
            ts = str(int(time.time()))
            headers["X-Ts"], headers["X-Sig"] = ts, sign(admin_token, ts, body)
    return None
