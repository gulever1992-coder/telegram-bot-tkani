"""Заявление на возврат денежных средств: выбор кнопками, затем вопросы по очереди -> PDF."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Callable

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import keyboards as kb
from utils.refund import REFUND_COMPANIES, build_refund_pdf

router = Router()
MSK = dt.timezone(dt.timedelta(hours=3))


class RefundForm(StatesGroup):
    ask = State()


@dataclass
class Step:
    key: str
    prompt: str
    kind: str = "text"  # text | date | amount | digits | passport | card
    lengths: tuple = ()
    buttons: tuple = ()  # ((подпись, значение), ...)
    when: Callable[[dict], bool] | None = None


CARD = lambda a: a.get("method") == "card"  # noqa: E731

STEPS_FIZ = [
    Step("purchase_date", "Дата покупки (ДД.ММ.ГГГГ):", "date"),
    Step("order", "Договор / заказ клиента (номер или название):"),
    Step("name", "ФИО покупателя (полностью):"),
    Step("passport", "Паспорт: серия и номер (10 цифр, например 4510 123456):", "passport"),
    Step("passport_issuer", "Кем выдан паспорт:"),
    Step("passport_date", "Когда выдан паспорт (ДД.ММ.ГГГГ):", "date"),
    Step("amount", "Сумма возврата, руб. (например 45500):", "amount"),
    Step("reason", "Причина возврата:"),
    Step(
        "method", "Как вернуть деньги?", "choice",
        buttons=(("💵 Из кассы магазина", "cash"), ("💳 На карту / счёт", "card")),
    ),
    Step("recipient", "Получатель (ФИО полностью):", buttons=(("Тот же, что покупатель", "=name"),), when=CARD),
    Step("bank", "Банк получателя:", when=CARD),
    Step("bik", "БИК банка (9 цифр):", "digits", (9,), when=CARD),
    Step("bank_inn", "ИНН банка (10 цифр):", "digits", (10, 12), when=CARD),
    Step("ks", "Корреспондентский счёт банка (20 цифр):", "digits", (20,), when=CARD),
    Step("account", "№ лицевого или карточного счёта (20 цифр):", "digits", (20,), when=CARD),
    Step("card", "№ карты (16 цифр) — или нажмите «Нет»:", "card", buttons=(("Нет", "-"),), when=CARD),
    Step("doc_date", "Дата заявления (ДД.ММ.ГГГГ):", "date", buttons=(("📅 Сегодня", "=today"),)),
]

STEPS_YUR = [
    Step("purchase_date", "Дата покупки (ДД.ММ.ГГГГ):", "date"),
    Step("order", "Договор / заказ клиента (номер или название):"),
    Step("org", "Название организации-покупателя (например: ООО «Ромашка»):"),
    Step(
        "director",
        "Руководитель в родительном падеже — должность и ФИО (например: генерального директора Петрова Петра Петровича):",
    ),
    Step("director_name", "ФИО руководителя в именительном падеже (для подписи, например: Петров Пётр Петрович):"),
    Step("amount", "Сумма возврата, руб. (например 45500):", "amount"),
    Step("reason", "Причина возврата:"),
    Step("pay_org", "Реквизиты для возврата. Наименование организации:", buttons=(("Как выше", "=org"),)),
    Step("pay_inn", "ИНН / КПП организации (например: 7701234567/770101001):"),
    Step("pay_ogrn", "ОГРН (13 или 15 цифр):", "digits", (13, 15)),
    Step("pay_rs", "Расчётный счёт (20 цифр):", "digits", (20,)),
    Step("pay_ls", "Лицевой счёт (если нет — нажмите «Нет»):", buttons=(("Нет", "-"),)),
    Step("bank", "Банк получателя:"),
    Step("bik", "БИК банка (9 цифр):", "digits", (9,)),
    Step("bank_inn", "ИНН банка (10 цифр):", "digits", (10, 12)),
    Step("ks", "Корреспондентский счёт банка (20 цифр):", "digits", (20,)),
    Step("doc_date", "Дата заявления (ДД.ММ.ГГГГ):", "date", buttons=(("📅 Сегодня", "=today"),)),
]


def _steps(kind: str) -> list[Step]:
    return STEPS_FIZ if kind == "fiz" else STEPS_YUR


def _next_step(kind: str, answers: dict) -> Step | None:
    for step in _steps(kind):
        if step.key in answers:
            continue
        if step.when and not step.when(answers):
            continue
        return step
    return None


def _markup(step: Step) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, value in step.buttons:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"ref:v:{value}"))
    builder.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return builder.as_markup()


def _amount(text: str) -> float | None:
    try:
        value = float(text.replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


def _parse(step: Step, text: str):
    """Возвращает (значение, None) или (None, сообщение об ошибке)."""
    text = text.strip()
    if step.kind == "date":
        try:
            return dt.datetime.strptime(text, "%d.%m.%Y").date(), None
        except ValueError:
            return None, "Формат даты — ДД.ММ.ГГГГ, например 24.09.2026."
    if step.kind == "amount":
        value = _amount(text)
        return (value, None) if value else (None, "Введите сумму числом, например 45500.")
    if step.kind == "digits":
        digits = re.sub(r"\D", "", text)
        if len(digits) in step.lengths:
            return digits, None
        want = " или ".join(str(n) for n in step.lengths)
        return None, f"Нужны только цифры, ровно {want} шт. Введите ещё раз."
    if step.kind == "passport":
        digits = re.sub(r"\D", "", text)
        if len(digits) == 10:
            return digits, None
        return None, "В паспорте 10 цифр: серия (4) и номер (6), например 4510 123456."
    if step.kind == "card":
        if text.lower() in {"нет", "-"}:
            return "-", None
        digits = re.sub(r"\D", "", text)
        if 16 <= len(digits) <= 19:
            return digits, None
        return None, "Номер карты — 16 цифр, либо нажмите «Нет»."
    if not text:
        return None, "Введите текст."
    return text, None


async def _ask(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    step = _next_step(data["kind"], data["answers"])
    if step is None:
        await _finish(target, state)
        return
    n = len(data["answers"]) + 1
    await target.answer(f"{n}. {step.prompt}", reply_markup=_markup(step))


async def _store(target: types.Message, state: FSMContext, step: Step, value) -> None:
    data = await state.get_data()
    answers = dict(data["answers"])
    answers[step.key] = value
    await state.update_data(answers=answers)
    await _ask(target, state)


async def _finish(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    a = dict(data["answers"])
    if "passport" in a:
        a["passport_series"], a["passport_number"] = a["passport"][:4], a["passport"][4:]
    for key in ("card", "pay_ls"):
        if a.get(key) == "-":
            a[key] = ""
    status = await target.answer("⏳ Готовлю заявление...")
    pdf = build_refund_pdf(data["company"], data["kind"], a)
    who = "fizlico" if data["kind"] == "fiz" else "yurlico"
    name = f"Zayavlenie_vozvrat_{data['company']}_{who}_{a['doc_date']:%Y%m%d}.pdf"
    label = REFUND_COMPANIES[data["company"]]["label"]
    kind_ru = "физлицо" if data["kind"] == "fiz" else "юрлицо"
    await target.answer_document(
        BufferedInputFile(pdf.read(), filename=name),
        caption=f"Готово! Заявление на возврат: {label}, {kind_ru}.",
        reply_markup=kb.main_menu(),
    )
    await status.delete()
    await state.clear()


# --- выбор кнопками -----------------------------------------------------------


@router.callback_query(F.data == "menu:refund")
async def cb_refund(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    builder = InlineKeyboardBuilder()
    for key, co in REFUND_COMPANIES.items():
        builder.row(InlineKeyboardButton(text=co["label"], callback_data=f"ref:co:{key}"))
    builder.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await call.message.edit_text(
        "📝 <b>Заявление на возврат денежных средств</b>\nНа какую организацию оформляем?",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ref:co:"))
async def cb_company(call: types.CallbackQuery, state: FSMContext) -> None:
    company = call.data.split(":")[2]
    if company not in REFUND_COMPANIES:
        await call.answer("Неизвестная организация", show_alert=True)
        return
    await state.update_data(company=company)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Физическое лицо", callback_data="ref:kind:fiz"))
    builder.row(InlineKeyboardButton(text="🏢 Юридическое лицо", callback_data="ref:kind:yur"))
    builder.row(InlineKeyboardButton(text="⬅ Назад", callback_data="menu:refund"))
    await call.message.edit_text(
        f"📝 Заявление на возврат — <b>{REFUND_COMPANIES[company]['label']}</b>\nКто покупатель?",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("ref:kind:"))
async def cb_kind(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "company" not in data:
        await call.answer("Начните заново: 📝 Заявление на возврат", show_alert=True)
        return
    kind = call.data.split(":")[2]
    await state.update_data(kind=kind, answers={})
    await state.set_state(RefundForm.ask)
    who = "физического лица" if kind == "fiz" else "юридического лица"
    await call.message.edit_text(
        f"📝 Заявление на возврат — {REFUND_COMPANIES[data['company']]['label']}, покупатель — {who}.\n"
        "Отвечайте на вопросы по очереди, в конце пришлю готовый PDF."
    )
    await call.answer()
    await _ask(call.message, state)


# --- ответы -------------------------------------------------------------------


@router.callback_query(F.data.startswith("ref:v:"), RefundForm.ask)
async def cb_value(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    step = _next_step(data["kind"], data["answers"])
    value = call.data[len("ref:v:"):]
    if step is None or value not in {v for _, v in step.buttons}:
        await call.answer("Этот вопрос уже пройден")
        return
    answers = data["answers"]
    if value == "=today":
        value = dt.datetime.now(MSK).date()
    elif value == "=name":
        value = answers.get("name", "")
    elif value == "=org":
        value = answers.get("org", "")
    await call.answer()
    await _store(call.message, state, step, value)


@router.message(RefundForm.ask, F.text)
async def msg_answer(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    step = _next_step(data["kind"], data["answers"])
    if step is None:
        await _finish(message, state)
        return
    if step.kind == "choice":
        await message.answer("Выберите вариант кнопкой ниже.", reply_markup=_markup(step))
        return
    value, error = _parse(step, message.text)
    if error:
        await message.answer(error, reply_markup=_markup(step))
        return
    await _store(message, state, step, value)
