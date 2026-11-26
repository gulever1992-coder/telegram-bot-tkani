"""Рекламация и запрос логисту: менеджер передаёт заявку рекламационисту / логисту (и владельцу) по шагам.

Заявка уходит через админ-бота рекламационистам, которых назначил владелец (⚙ Настройки в админ-боте), и самому
владельцу. Менеджер получает отбивки: «взята в работу», «решена» и комментарии рекламациониста.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import analytics
import claims
import config
import profiles
from onboarding import norm_phone
from utils.ui import show

router = Router()
log = logging.getLogger("claim")
MAX_PHOTOS = 8


class ClaimForm(StatesGroup):
    ask = State()


@dataclass
class Step:
    key: str
    prompt: str
    kind: str = "text"  # text | phone | photos


STEPS = [
    Step("order", "Договор / заказ клиента (номер или название):"),
    Step("client", "ФИО клиента:"),
    Step("client_phone", "Телефон клиента:", "phone"),
    Step("item", "Какое изделие: модель, ткань, цвет:"),
    Step("problem", "Опишите проблему: что именно не так, когда обнаружили:"),
    Step("photos", f"Пришлите фото проблемы (до {MAX_PHOTOS} штук). Когда закончите — нажмите «Готово»:", "photos"),
]


LOG_STEPS = [
    Step("order", "Договор / заказ клиента (номер или название):"),
    Step("client", "ФИО клиента:"),
    Step("client_phone", "Телефон клиента:", "phone"),
    Step("item", "Адрес доставки: город, улица, дом, подъезд, этаж, есть ли лифт:"),
    Step("problem", "Что нужно от логиста: доставка, сроки, забор, перенос даты, вопрос — опишите подробно:"),
    Step("photos", f"Пришлите фото или скан (накладная, место установки — до {MAX_PHOTOS} штук). "
                   "Когда закончите — нажмите «Готово»:", "photos"),
]
FLOW_STEPS = {"claim": STEPS, "log": LOG_STEPS}


def _next(answers: dict, kind: str = "claim") -> Step | None:
    return next((s for s in FLOW_STEPS.get(kind, STEPS) if s.key not in answers), None)


def _kb(step: Step | None = None) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if step and step.kind == "photos":
        b.row(InlineKeyboardButton(text="✅ Готово", callback_data="clm:photos"))
        b.row(InlineKeyboardButton(text="⏭ Без фото", callback_data="clm:nophoto"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return b.as_markup()


def _my(uid: int, kind: str = "claim") -> list[dict]:
    return sorted((c for c in analytics.CLAIMS.values()
                   if (c.get("by") or {}).get("uid") == uid and claims.kind_of(c) == kind), key=lambda c: -c["ts"])


def _kind_arg(data: str) -> str:
    """clm:new:log -> log; старые кнопки без вида — рекламация."""
    parts = data.split(":")
    return parts[2] if len(parts) > 2 and parts[2] in claims.KINDS else "claim"


def _recipients_text(kind: str) -> str:
    k = claims.KINDS[kind]
    users = analytics.SETTINGS.get(k["users"]) or []
    return ", ".join(u["name"] for u in users) if users else f"руководителю ({k['who']} пока не назначен)"


# --- меню ------------------------------------------------------------------------------------------


async def _menu(call: types.CallbackQuery, state: FSMContext, kind: str) -> None:
    await state.clear()
    k = claims.KINDS[kind]
    mine = _my(call.from_user.id, kind)
    open_n = sum(1 for c in mine if c["status"] != "done")
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Передать новую заявку", callback_data=f"clm:new:{kind}"))
    if mine:
        b.row(InlineKeyboardButton(text=f"📋 Мои заявки ({len(mine)}, в работе: {open_n})",
                                   callback_data=f"clm:list:{kind}"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await show(call.message, f"{k['icon']} <b>{k['title']}</b>\nПередайте заявку {k['to']} — он отметит, когда возьмёт "
                             "её в работу и когда решит, и я сразу сообщу вам.", reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data == "menu:claim")
async def cb_claim(call: types.CallbackQuery, state: FSMContext) -> None:
    await _menu(call, state, "claim")


@router.callback_query(F.data == "menu:logist")
async def cb_logist(call: types.CallbackQuery, state: FSMContext) -> None:
    await _menu(call, state, "log")


@router.callback_query(F.data.startswith("clm:list"))
async def cb_list(call: types.CallbackQuery) -> None:
    kind = _kind_arg(call.data)
    mine = _my(call.from_user.id, kind)
    lines = [f"📋 <b>Мои заявки: {claims.KINDS[kind]['plural'].lower()}</b>"] + ["\n" + claims.short_line(c) for c in mine[:12]]
    for c in mine[:12]:
        for cm in (c.get("comments") or [])[-1:]:
            lines.append(f"   💬 {claims.esc(cm['by'])}: {claims.esc(cm['text'])[:120]}")
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Новая заявка", callback_data=f"clm:new:{kind}"),
          InlineKeyboardButton(text="🏠 Меню", callback_data="menu:home"))
    await call.message.answer("\n".join(lines)[:4000], reply_markup=b.as_markup())
    await call.answer()


# --- опрос -------------------------------------------------------------------------------------------


async def _ask(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    answers = data["answers"]
    step = _next(answers, data.get("kind", "claim"))
    if step is None:
        await _confirm(target, state)
        return
    n = len(answers) + 1
    await target.answer(f"{n}. {step.prompt}", reply_markup=_kb(step))


@router.callback_query(F.data.startswith("clm:new"))
async def cb_new(call: types.CallbackQuery, state: FSMContext) -> None:
    kind = _kind_arg(call.data)
    await state.clear()
    await state.set_state(ClaimForm.ask)
    await state.update_data(answers={}, photos=[], kind=kind)
    await call.answer()
    await call.message.answer(f"{claims.KINDS[kind]['icon']} Отвечайте на вопросы по очереди — в конце проверите заявку и отправите.")
    await _ask(call.message, state)


@router.message(ClaimForm.ask, F.text)
async def msg_text(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    answers = dict(data["answers"])
    step = _next(answers, data.get("kind", "claim"))
    if step is None:
        return
    if step.kind == "photos":
        await message.answer("Пришлите фото картинкой или нажмите «Готово» / «Без фото».", reply_markup=_kb(step))
        return
    text = message.text.strip()
    if step.kind == "phone":
        text = norm_phone(text) or ""
        if not text:
            await message.answer("Не похоже на номер телефона. Формат: +7 999 123-45-67. Ещё раз:", reply_markup=_kb())
            return
    if len(text) < 2:
        await message.answer("Слишком коротко — напишите подробнее.", reply_markup=_kb(step))
        return
    answers[step.key] = text[:1500]
    await state.update_data(answers=answers)
    await _ask(message, state)


@router.message(ClaimForm.ask, F.photo | F.document)
async def msg_photo(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    step = _next(data["answers"], data.get("kind", "claim"))
    if step is None or step.kind != "photos":
        await message.answer("Сейчас фото не нужно — ответьте на вопрос выше.")
        return
    file = message.photo[-1] if message.photo else (
        message.document if message.document and (message.document.mime_type or "").startswith("image/") else None)
    if file is None:
        await message.answer("Нужна картинка (фото). Другие файлы приложить нельзя.", reply_markup=_kb(step))
        return
    photos = list(data.get("photos") or [])
    if len(photos) >= MAX_PHOTOS:
        await message.answer(f"Максимум {MAX_PHOTOS} фото — нажмите «Готово».", reply_markup=_kb(step))
        return
    buf = io.BytesIO()
    await message.bot.download(file, destination=buf)
    photos.append(buf.getvalue())
    await state.update_data(photos=photos)
    await message.answer(f"📷 Принято: {len(photos)}. Ещё фото — или «Готово».", reply_markup=_kb(step))


@router.callback_query(F.data.in_({"clm:photos", "clm:nophoto"}), ClaimForm.ask)
async def cb_photos_done(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    step = _next(data["answers"], data.get("kind", "claim"))
    if step is None or step.kind != "photos":
        await call.answer("Уже пройдено")
        return
    answers = dict(data["answers"])
    answers["photos"] = True
    if call.data == "clm:nophoto":
        await state.update_data(photos=[])
    await state.update_data(answers=answers)
    await call.answer()
    await _ask(call.message, state)


def _summary(answers: dict, n_photos: int, prof: dict, kind: str = "claim") -> str:
    a = answers
    k = claims.KINDS[kind]
    return (
        f"{k['icon']} <b>Проверьте заявку: {k['title'].lower()}</b>\n\n"
        f"Менеджер: {claims.esc(prof.get('name'))}" + (f" · {claims.esc(prof.get('showroom'))}" if prof.get("showroom") else "") + "\n"
        f"Договор / заказ: <b>{claims.esc(a['order'])}</b>\n"
        f"Клиент: {claims.esc(a['client'])}, {claims.esc(a['client_phone'])}\n"
        f"{k['item']}: {claims.esc(a['item'])}\n\n"
        f"<b>{k['main']}:</b>\n{claims.esc(a['problem'])}\n\n"
        f"📷 Фото: {n_photos}"
    )


async def _confirm(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    prof = profiles.get(target.chat.id) or {}
    kind = data.get("kind", "claim")
    who = _recipients_text(kind)
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=f"✅ Передать {claims.KINDS[kind]['to']}", callback_data="clm:send"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    await target.answer(_summary(data["answers"], len(data.get("photos") or []), prof, kind) + f"\n\nКому: {claims.esc(who)}",
                        reply_markup=b.as_markup())


@router.callback_query(F.data == "clm:send", ClaimForm.ask)
async def cb_send(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    a = data["answers"]
    if not all(k in a for k in ("order", "client", "client_phone", "item", "problem")):
        await call.answer("Заявка не заполнена", show_alert=True)
        return
    if not analytics.enabled():
        await call.answer("Админ-бот не настроен — заявку передать некуда.", show_alert=True)
        return
    await call.answer("Отправляю…")
    await state.clear()
    uid = call.from_user.id
    prof = profiles.get(uid) or {}
    kind = data.get("kind", "claim")
    claim = {
        "id": analytics.next_claim_id(), "kind": kind, "ts": int(time.time()), "status": "new", "log": [], "comments": [],
        "by": {"uid": uid, "name": prof.get("name", ""), "showroom": prof.get("showroom", ""),
               "phone": prof.get("phone", ""), "telegram": prof.get("telegram", "")},
        "order": a["order"], "client": a["client"], "client_phone": a["client_phone"], "item": a["item"],
        "problem": a["problem"],
    }
    from admin_bot import claim_chats  # внутри функции: admin_bot импортирует модули бота

    chats = claim_chats(kind)
    await claims.deliver(analytics._admin_bot, claim, chats, list(data.get("photos") or []))
    analytics.CLAIMS[claim["id"]] = claim
    analytics.track(uid, "claim" if kind == "claim" else "logist", f"№{claims.number(claim)} {a['order']}"[:60], data={"id": claim["id"]})
    analytics.save_state()
    to = _recipients_text(kind)
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📋 Мои заявки", callback_data=f"clm:list:{kind}"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await call.message.answer(
        f"✅ <b>Заявка №{claims.number(claim)} передана</b> — {claims.esc(to)}.\n"
        "Как только её возьмут в работу и решат, я сообщу вам сюда.", reply_markup=b.as_markup())
