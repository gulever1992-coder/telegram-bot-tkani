"""Планируемые продажи (кнопка меню; раньше «Мои сделки»): добавление и редактирование, полный список — владельцу автоматически."""

from __future__ import annotations

import datetime as dt
import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import analytics
import pipeline as pl
import profiles
from utils.kp import rub
from utils.ui import show

router = Router()
MSK = dt.timezone(dt.timedelta(hours=3))

FIELDS = ["client", "amount", "arrived", "stage", "status", "planned_date", "blocker"]
FIELD_TITLES = {
    "client": "Клиент / сделка", "amount": "Потенциальная сумма", "arrived": "Когда пришёл",
    "stage": "Текущая стадия", "status": "Статус", "planned_date": "Дата продажи", "blocker": "Что мешает",
}
REQUIRED = {"client", "status", "arrived", "planned_date"}


class PipeForm(StatesGroup):
    run = State()


def _today() -> dt.date:
    return dt.datetime.now(MSK).date()


def _fmt(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


_DATE_RE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?$")


def _parse_date(text: str) -> str | None:
    """Гибкий разбор даты: 05.10.2026, 05.10.26, 05.10 (год — текущий), 5/10, 5-10-26 и т.п."""
    m = _DATE_RE.match(text.strip())
    if not m:
        return None
    day, month, year = m.groups()
    day, month = int(day), int(month)
    if year is None:
        year = _today().year
    else:
        year = int(year)
        if year < 100:
            year += 2000
    try:
        return _fmt(dt.date(year, month, day))
    except ValueError:
        return None


def _num(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


# --- вопросы -----------------------------------------------------------------------


def _prompt(field: str) -> tuple[str, list[tuple[str, str]]]:
    if field == "client":
        return "1️⃣ <b>Клиент / сделка</b> — имя или компания:", []
    if field == "amount":
        return "2️⃣ <b>Потенциальная сумма</b>, руб.:", []
    if field == "arrived":
        return (
            "3️⃣ <b>Когда пришёл клиент?</b> Выберите кнопкой или напишите дату (например: 5.10 или 05.10.2026):",
            [("Сегодня", "ago:0"), ("Вчера", "ago:1"), ("3 дня назад", "ago:3"), ("Неделю назад", "ago:7")],
        )
    if field == "stage":
        return "4️⃣ <b>Текущая стадия</b>:", [(s, f"stage:{i}") for i, s in enumerate(pl.STAGES)]
    if field == "status":
        return "5️⃣ <b>Статус</b>:", [(label, f"status:{key}") for key, label in pl.STATUSES]
    if field == "planned_date":
        return (
            "6️⃣ <b>Планируемая дата продажи</b>. Выберите кнопкой или напишите дату (например: 20.10 или 20.10.2026):",
            [("Сегодня", "in:0"), ("Через 3 дня", "in:3"), ("Через неделю", "in:7"), ("Через 2 недели", "in:14")],
        )
    return "7️⃣ <b>Что мешает / следующий шаг</b>:", []


def _ask_markup(field: str, buttons: list[tuple[str, str]]) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, value in buttons:
        b.row(InlineKeyboardButton(text=label, callback_data=f"pipe:v:{value}"))
    if field not in REQUIRED:
        b.row(InlineKeyboardButton(text="⏭ Пропустить", callback_data="pipe:skip"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="pipe:cancel"))
    return b.as_markup()


def _parse(field: str, value: str) -> tuple[str | int | None, str | None]:
    """(значение, None) или (None, сообщение об ошибке). value уже без кнопочных префиксов."""
    if field == "client":
        return (value.strip(), None) if value and value.strip() else (None, "Введите название сделки или имя клиента.")
    if field == "amount":
        n = _num(value)
        return (n, None) if n else (None, "Введите сумму числом, например 250000.")
    if field in ("arrived", "planned_date"):
        d = _parse_date(value)
        return (d, None) if d else (None, "Не разобрал дату. Например: 5.10, 05.10.2026 или 05/10/26.")
    if field == "status":
        return (value, None) if value in pl.STATUS_LABEL else (None, "Выберите статус кнопкой.")
    if field == "stage":
        return (value, None) if value in pl.STAGES else (None, "Выберите стадию кнопкой.")
    return value.strip(), None


def _quick_value(field: str, token: str) -> str | None:
    """Разбирает значение кнопки (ago:N / in:N / stage:i / status:key) в готовое значение поля."""
    kind, _, rest = token.partition(":")
    if kind == "ago":
        return _fmt(_today() - dt.timedelta(days=int(rest)))
    if kind == "in":
        return _fmt(_today() + dt.timedelta(days=int(rest)))
    if kind == "stage":
        i = int(rest)
        return pl.STAGES[i] if i < len(pl.STAGES) else None
    if kind == "status":
        return rest if rest in pl.STATUS_LABEL else None
    return None


# --- отображение ---------------------------------------------------------------------


def _deal_line(i: int, d: dict) -> str:
    amount = rub(d["amount"]) if d.get("amount") else "—"
    return f"{i}. {pl.STATUS_LABEL.get(d['status'], '')} <b>{d['client']}</b> — {amount}"


def _list_text(uid: int) -> str:
    items = pl.deals(uid)
    if not items:
        return "📈 <b>Планируемые продажи</b>\n\nПока нет ни одной сделки. Добавьте первую кнопкой ниже."
    lines = ["📈 <b>Планируемые продажи</b>", ""] + [_deal_line(i, d) for i, d in enumerate(items, 1)]
    lines.append(f"\nИтого потенциально: <b>{rub(pl.total(items))}</b> по {len(items)} сделкам")
    return "\n".join(lines)


def _list_markup(uid: int) -> types.InlineKeyboardMarkup:
    items = pl.deals(uid)
    b = InlineKeyboardBuilder()
    for i, d in enumerate(items, 1):
        b.row(InlineKeyboardButton(text=f"✏ {i}. {d['client'][:30]}", callback_data=f"pipe:open:{d['id']}"))
    b.row(InlineKeyboardButton(text="➕ Добавить сделку", callback_data="pipe:add"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    return b.as_markup()


def _deal_text(d: dict) -> str:
    amount = rub(d["amount"]) if d.get("amount") else "не указана"
    return (
        f"<b>{d['client']}</b>\n"
        f"Потенциальная сумма: {amount}\n"
        f"Когда пришёл: {d.get('arrived') or '—'}\n"
        f"Текущая стадия: {d.get('stage') or '—'}\n"
        f"Статус: {pl.STATUS_LABEL.get(d['status'], '—')}\n"
        f"Планируемая дата продажи: {d.get('planned_date') or '—'}\n"
        f"Что мешает / следующий шаг: {d.get('blocker') or '—'}"
    )


def _deal_markup(deal_id: int) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i in range(0, len(FIELDS), 2):
        pair = FIELDS[i:i + 2]
        b.row(*[InlineKeyboardButton(text=f"✏ {FIELD_TITLES[f]}", callback_data=f"pipe:edit:{deal_id}:{f}") for f in pair])
    b.row(InlineKeyboardButton(text="🗑 Удалить сделку", callback_data=f"pipe:del:{deal_id}"))
    b.row(InlineKeyboardButton(text="⬅ К списку", callback_data="pipe:back"))
    return b.as_markup()


def _report_text(uid: int) -> str:
    prof = profiles.get(uid) or {}
    items = pl.deals(uid)
    lines = [
        f"📈 <b>План продаж — {prof.get('name') or 'менеджер'}</b>",
        f"Шоу-рум: {profiles.norm_showroom(prof.get('showroom', '')) or '—'}",
        f"Обновлено: {dt.datetime.now(MSK):%d.%m.%Y %H:%M}", "",
    ]
    if not items:
        lines.append("Сделок пока нет.")
    for i, d in enumerate(items, 1):
        amount = rub(d["amount"]) if d.get("amount") else "не указана"
        lines.append(
            f"{i}. <b>{d['client']}</b> {pl.STATUS_LABEL.get(d['status'], '')}\n"
            f"   Потенциально: {amount}\n"
            f"   Пришёл: {d.get('arrived') or '—'}\n"
            f"   Стадия: {d.get('stage') or '—'}\n"
            f"   Планируемая дата: {d.get('planned_date') or '—'}\n"
            f"   Мешает / шаг: {d.get('blocker') or '—'}"
        )
    if items:
        lines.append(f"\nИтого потенциально: <b>{rub(pl.total(items))}</b> по {len(items)} сделкам")
    return "\n".join(lines)


def _notify_owner(uid: int) -> None:
    analytics.notify(_report_text(uid))


# --- вход и список ---------------------------------------------------------------------


@router.callback_query(F.data == "menu:pipeline")
async def cb_pipeline(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call.message, _list_text(call.from_user.id), reply_markup=_list_markup(call.from_user.id))
    await call.answer()


@router.callback_query(F.data == "pipe:back")
async def cb_back(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await call.message.answer(_list_text(call.from_user.id), reply_markup=_list_markup(call.from_user.id))


@router.callback_query(F.data == "pipe:cancel")
async def cb_cancel(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Отменено")
    await call.message.answer(_list_text(call.from_user.id), reply_markup=_list_markup(call.from_user.id))


# --- добавление новой сделки --------------------------------------------------------------


@router.callback_query(F.data == "pipe:add")
async def cb_add(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PipeForm.run)
    await state.update_data(mode="add", deal=pl.new_deal(), queue=list(FIELDS))
    await call.answer()
    await _ask_add(call.message, state)


async def _ask_add(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data["queue"]
    if not queue:
        deal = pl.add(target.chat.id, data["deal"])
        _notify_owner(target.chat.id)
        await state.clear()
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="➕ Добавить ещё", callback_data="pipe:add"))
        b.row(InlineKeyboardButton(text="⬅ К списку", callback_data="pipe:back"))
        await target.answer(f"✔ Добавлено: <b>{deal['client']}</b>.", reply_markup=b.as_markup())
        return
    field = queue[0]
    text, buttons = _prompt(field)
    await target.answer(text, reply_markup=_ask_markup(field, buttons))


@router.callback_query(F.data.startswith("pipe:v:"), PipeForm.run)
async def cb_value(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data.get("queue") or []
    if not queue:
        await call.answer("Этот вопрос уже пройден")
        return
    field = queue[0]
    value = _quick_value(field, call.data[len("pipe:v:"):])
    if value is None:
        await call.answer("Список устарел", show_alert=True)
        return
    await call.answer()
    await _commit(call.message, state, field, value)


@router.callback_query(F.data == "pipe:skip", PipeForm.run)
async def cb_skip(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data.get("queue") or []
    if not queue or queue[0] in REQUIRED:
        await call.answer()
        return
    await call.answer("Пропущено")
    await _commit(call.message, state, queue[0], None if queue[0] == "amount" else "")


@router.message(PipeForm.run, F.text)
async def msg_value(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data.get("queue") or []
    if not queue:
        return
    field = queue[0]
    value, error = _parse(field, message.text)
    if error:
        text, buttons = _prompt(field)
        await message.answer(error, reply_markup=_ask_markup(field, buttons))
        return
    await _commit(message, state, field, value)


async def _commit(target: types.Message, state: FSMContext, field: str, value) -> None:
    data = await state.get_data()
    if data["mode"] == "add":
        deal = data["deal"]
        deal[field] = value
        queue = data["queue"][1:]
        await state.update_data(deal=deal, queue=queue)
        await _ask_add(target, state)
    else:  # editfield
        pl.update(target.chat.id, data["deal_id"], **{field: value})
        _notify_owner(target.chat.id)
        await state.clear()
        d = pl.get(target.chat.id, data["deal_id"])
        if d:
            await target.answer(_deal_text(d), reply_markup=_deal_markup(d["id"]))
        else:
            await target.answer(_list_text(target.chat.id), reply_markup=_list_markup(target.chat.id))


# --- просмотр и правка одной сделки -----------------------------------------------------


@router.callback_query(F.data.startswith("pipe:open:"))
async def cb_open(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    deal_id = int(call.data.split(":")[2])
    d = pl.get(call.from_user.id, deal_id)
    if not d:
        await call.answer("Сделка не найдена (возможно, уже удалена)", show_alert=True)
        return
    await call.answer()
    await show(call.message, _deal_text(d), reply_markup=_deal_markup(deal_id))


@router.callback_query(F.data.startswith("pipe:edit:"))
async def cb_edit_field(call: types.CallbackQuery, state: FSMContext) -> None:
    _, _, deal_id, field = call.data.split(":")
    deal_id = int(deal_id)
    if not pl.get(call.from_user.id, deal_id):
        await call.answer("Сделка не найдена", show_alert=True)
        return
    await state.set_state(PipeForm.run)
    await state.update_data(mode="editfield", deal_id=deal_id, queue=[field])
    text, buttons = _prompt(field)
    await call.answer()
    await call.message.answer(text, reply_markup=_ask_markup(field, buttons))


@router.callback_query(F.data.startswith("pipe:del:"))
async def cb_delete(call: types.CallbackQuery, state: FSMContext) -> None:
    deal_id = int(call.data.split(":")[2])
    d = pl.get(call.from_user.id, deal_id)
    if not d:
        await call.answer("Уже удалена")
        return
    pl.remove(call.from_user.id, deal_id)
    _notify_owner(call.from_user.id)
    await call.answer("Сделка удалена")
    await call.message.answer(_list_text(call.from_user.id), reply_markup=_list_markup(call.from_user.id))
