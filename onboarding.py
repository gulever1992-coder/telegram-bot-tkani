"""Знакомство с новым менеджером (4 вопроса) и меню «Мой профиль».

Пока профиля нет, любое сообщение или кнопка ведут на знакомство (ProfileGate).
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import analytics
import keyboards as kb
import profiles
from utils.ui import show

router = Router()

WELCOME = "👋 Привет! Я рабочий бот-помощник.\n\nВыберите действие в меню ниже:"


class Onb(StatesGroup):
    name = State()
    phone = State()
    telegram = State()
    showroom = State()


# --- проверка ответов ---------------------------------------------------------------


def norm_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits[0] in "78":
        d = "7" + digits[1:]
        return f"+7 {d[1:4]} {d[4:7]}-{d[7:9]}-{d[9:11]}"
    return None


def norm_telegram(text: str) -> str | None:
    t = (text or "").strip()
    t = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/", "", t, flags=re.I).lstrip("@").strip("/")
    return "@" + t if re.fullmatch(r"[A-Za-z0-9_]{4,32}", t) else None


def norm_name(text: str) -> str | None:
    t = " ".join((text or "").split())
    return t if len(t) >= 3 and re.search(r"[A-Za-zА-Яа-яЁё]", t) else None


# --- вопросы ----------------------------------------------------------------------------


def _skip_markup(extra: list[tuple[str, str]] | None = None, cancel: bool = False) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, cb in extra or []:
        b.row(InlineKeyboardButton(text=label, callback_data=cb))
    if cancel:
        b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return b.as_markup()


async def ask(target: types.Message, state: FSMContext, step: str, user: types.User | None = None) -> None:
    data = await state.get_data()
    old = data.get("old") or {}
    editing = bool(old)
    cancel = editing
    if step == "name":
        await state.set_state(Onb.name)
        extra = [(f"Оставить: {old['name'][:40]}", "onb:keep")] if old.get("name") else []
        await target.answer("1️⃣ <b>Ваше ФИО</b> (как в документах, например: Иванов Пётр Сергеевич):",
                            reply_markup=_skip_markup(extra, cancel))
    elif step == "phone":
        await state.set_state(Onb.phone)
        contact = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True,
        )
        await target.answer("2️⃣ <b>Рабочий телефон</b> — нажмите кнопку ниже или напишите номер (+7 999 123-45-67):",
                            reply_markup=contact)
        if old.get("phone"):
            await target.answer("Или оставьте прежний:", reply_markup=_skip_markup([(f"Оставить: {old['phone']}", "onb:keep")]))
    elif step == "telegram":
        await state.set_state(Onb.telegram)
        extra = []
        if user is not None and user.username:
            extra.append((f"Мой аккаунт: @{user.username}", f"onb:tg:{user.username}"))
        if old.get("telegram"):
            extra.append((f"Оставить: {old['telegram']}", "onb:keep"))
        extra.append(("Нет / не указывать", "onb:none"))
        await target.answer("3️⃣ <b>Ваш Telegram</b> для связи с клиентом (например: @ivanov):",
                            reply_markup=_skip_markup(extra))
    elif step == "showroom":
        await state.set_state(Onb.showroom)
        extra = [("🏬 Флагманский, Новодевичий пр., 2", "onb:sr:default")]
        if old.get("showroom") and old["showroom"] != profiles.DEFAULT_SHOWROOM:
            extra.append((f"Оставить: {old['showroom'][:40]}", "onb:keep"))
        await target.answer("4️⃣ <b>Ваш шоу-рум</b> — выберите или напишите название и адрес:",
                            reply_markup=_skip_markup(extra))


ORDER = ["name", "phone", "telegram", "showroom"]


async def _advance(target: types.Message, state: FSMContext, user: types.User, field: str, value: str) -> None:
    data = await state.get_data()
    profile = dict(data.get("profile") or {})
    profile[field] = value
    await state.update_data(profile=profile)
    i = ORDER.index(field)
    if i + 1 < len(ORDER):
        await ask(target, state, ORDER[i + 1], user)
        return
    await _finish(target, state, user)


async def _finish(target: types.Message, state: FSMContext, user: types.User) -> None:
    data = await state.get_data()
    was_new = not data.get("old")
    saved = await profiles.save(target.bot, user.id, data["profile"])
    await state.clear()
    await target.answer("✅ Готово, данные сохранены.", reply_markup=ReplyKeyboardRemove())
    await target.answer(profiles.render(saved).split("\n\n")[0], reply_markup=kb.main_menu())
    analytics.track(user.id, "register" if was_new else "profile", saved.get("name", ""))


async def start(target: types.Message, state: FSMContext, user: types.User, editing: bool = False) -> None:
    await state.clear()
    old = dict(profiles.get(user.id) or {}) if editing else {}
    await state.update_data(profile={}, old={k: v for k, v in old.items() if k != "msg_id"})
    if not editing:
        await target.answer(
            "👋 <b>Добро пожаловать!</b>\nПрежде чем начать, давайте познакомимся — 4 коротких вопроса. "
            "Эти данные будут подставляться в коммерческие предложения."
        )
    await ask(target, state, "name", user)


# --- «шлагбаум»: пока нет профиля — только знакомство -------------------------------------


class ProfileGate(BaseMiddleware):
    async def __call__(self, handler: Callable[[Any, dict], Awaitable[Any]], event: Any, data: dict) -> Any:
        user = data.get("event_from_user")
        state: FSMContext | None = data.get("state")
        if user is None or state is None or user.is_bot:
            return await handler(event, data)
        message = event if isinstance(event, types.Message) else getattr(event, "message", None)
        if message is None or message.chat.type != "private":
            return await handler(event, data)
        current = await state.get_state()
        if current and current.startswith("Onb:"):
            if isinstance(event, types.Message) and (event.text or "").startswith("/start"):
                await state.clear()
            else:
                return await handler(event, data)
        profile = await profiles.load(data["bot"], user.id)
        if profile:
            return await handler(event, data)
        if isinstance(event, types.CallbackQuery):
            await event.answer()
        await start(message, state, user)
        return None


# --- обработчики ------------------------------------------------------------------------


@router.message(Onb.name, F.text)
async def msg_name(message: types.Message, state: FSMContext) -> None:
    name = norm_name(message.text)
    if not name:
        await message.answer("Напишите ФИО буквами, например: Иванов Пётр Сергеевич.")
        return
    await _advance(message, state, message.from_user, "name", name)


@router.message(Onb.phone, F.contact)
async def msg_contact(message: types.Message, state: FSMContext) -> None:
    phone = norm_phone(message.contact.phone_number) or message.contact.phone_number
    await message.answer("Номер получил.", reply_markup=ReplyKeyboardRemove())
    await _advance(message, state, message.from_user, "phone", phone)


@router.message(Onb.phone, F.text)
async def msg_phone(message: types.Message, state: FSMContext) -> None:
    phone = norm_phone(message.text)
    if not phone:
        await message.answer("Не похоже на номер. Формат: +7 999 123-45-67 (10 или 11 цифр).")
        return
    await message.answer("Записал.", reply_markup=ReplyKeyboardRemove())
    await _advance(message, state, message.from_user, "phone", phone)


@router.message(Onb.telegram, F.text)
async def msg_telegram(message: types.Message, state: FSMContext) -> None:
    tg = norm_telegram(message.text)
    if not tg:
        await message.answer("Напишите имя пользователя, например @ivanov (латиница, цифры, _), или нажмите «Нет».")
        return
    await _advance(message, state, message.from_user, "telegram", tg)


@router.message(Onb.showroom, F.text)
async def msg_showroom(message: types.Message, state: FSMContext) -> None:
    text = " ".join(message.text.split())
    if len(text) < 3:
        await message.answer("Напишите название и адрес шоу-рума.")
        return
    await _advance(message, state, message.from_user, "showroom", text)


@router.callback_query(F.data == "onb:keep")
async def cb_keep(call: types.CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    if not current or not current.startswith("Onb:"):
        await call.answer("Уже пройдено")
        return
    field = current.split(":")[1]
    old = (await state.get_data()).get("old") or {}
    await call.answer()
    if field == "phone":
        await call.message.answer("Оставил прежний.", reply_markup=ReplyKeyboardRemove())
    await _advance(call.message, state, call.from_user, field, old.get(field, ""))


@router.callback_query(F.data == "onb:none", Onb.telegram)
async def cb_tg_none(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _advance(call.message, state, call.from_user, "telegram", "")


@router.callback_query(F.data.startswith("onb:tg:"), Onb.telegram)
async def cb_tg_me(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _advance(call.message, state, call.from_user, "telegram", "@" + call.data[len("onb:tg:"):])


@router.callback_query(F.data == "onb:sr:default", Onb.showroom)
async def cb_showroom_default(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _advance(call.message, state, call.from_user, "showroom", profiles.DEFAULT_SHOWROOM)


# --- меню «Мой профиль» -------------------------------------------------------------------


@router.callback_query(F.data == "menu:profile")
async def cb_profile(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    profile = profiles.get(call.from_user.id) or {}
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✏ Изменить данные", callback_data="onb:edit"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    text = profiles.render(profile).split("\n\n")[0] if profile else "Профиль пока не заполнен."
    await show(call.message, text, reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data == "onb:edit")
async def cb_edit(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await start(call.message, state, call.from_user, editing=True)
