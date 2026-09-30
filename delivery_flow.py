"""Стоимость доставки: калькулятор для Москвы и Санкт-Петербурга, ссылка на таблицу для регионов."""

from __future__ import annotations

import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import analytics
import delivery as dl
from utils.kp import rub

router = Router()
REGIONS_URL = (
    "https://docs.google.com/spreadsheets/d/1d3UN8U5H-SEFK_xApNV34b5PWk4G8ah3-NUuYz_3cxQ/"
    "edit?gid=2086428513#gid=2086428513"
)


class DeliveryForm(StatesGroup):
    run = State()


def _num(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _new_q() -> dict:
    return {"category": "", "qty": 1, "long_case": False, "distance_km": 0, "lift_mode": "none",
            "floors": 0, "assembly": "none", "time_slot": False, "carry_extra_m": 0}


def _queue_for(category: str) -> list[str]:
    q = ["case_length"] if category == "case" else []
    q += ["qty", "distance_km", "lift_mode"]
    if category in ("armchair", "big"):
        q.append("assembly")
    q += ["time_slot", "carry_extra"]
    return q


# --- вопросы -----------------------------------------------------------------------


def _prompt(step: str, q: dict) -> tuple[str, list[tuple[str, str]], bool]:
    """(текст, кнопки, можно_текстом)"""
    if step == "case_length":
        return "📏 Длина изделия:", [("До 100 см", "len:short"), ("Более 100 см", "len:long")], False
    if step == "qty":
        return "🔢 Количество, шт:", [(str(i), f"qty:{i}") for i in (1, 2, 3, 4, 5, 6)], True
    if step == "distance_km":
        return (
            "📍 На каком расстоянии от МКАД адрес доставки, км?\n"
            f"Первые {dl.MKAD_FREE_KM} км за МКАД входят в стоимость, дальше +{dl.EXTRA_KM_PRICE} ₽/км.",
            [("В пределах МКАД", "km:0"), ("20 км", "km:20"), ("30 км", "km:30"), ("50 км", "km:50")],
            True,
        )
    if step == "lift_mode":
        cat = q["category"]
        buttons = [("Без подъёма (1 этаж / есть грузовой лифт)", "lift:none")]
        if cat == "case":
            buttons.append(("Подъём нужен (рассчитаем индивидуально)", "lift:individual"))
        else:
            t = dl.LIFT[cat]
            buttons.append((f"Занос и подъём на лифте — {rub(t['lift'])}", "lift:elevator"))
            buttons.append((f"Вручную, без лифта — {rub(t['manual_per_floor'])}/этаж", "lift:manual"))
        return "🛗 Нужен подъём в квартиру?", buttons, False
    if step == "floors":
        return "На какой этаж поднимать (число этажей)?", [], True
    if step == "assembly":
        if q["category"] == "armchair":
            return "🔧 Нужна сборка кресла?", [
                (f"Со сборкой — {rub(dl.ASSEMBLY_ARMCHAIR)}", "asm:armchair"), ("Без сборки", "asm:none"),
            ], False
        return "🔧 Нужна сборка? Выберите позицию:", [
            (f"Диван: только ножки — {rub(dl.ASSEMBLY_SOFA['legs'])}", "asm:sofa_legs"),
            (f"Диван: полная сборка — {rub(dl.ASSEMBLY_SOFA['full'])}", "asm:sofa_full"),
            (f"Кровать: обычная — {rub(dl.ASSEMBLY_BED['regular'])}", "asm:bed_regular"),
            (f"Кровать Некст / Либерти / Бостон — {rub(dl.ASSEMBLY_BED['special'])}", "asm:bed_special"),
            ("Матрас / без сборки", "asm:none"),
        ], False
    if step == "time_slot":
        return f"⏰ Доставка ко времени (с 12:00)? +{rub(dl.TIME_SLOT_FEE)}", [
            ("Да", "time:1"), ("Нет", "time:0"),
        ], False
    return (  # carry_extra
        f"🚶 Нужен ручной пронос дальше {dl.CARRY_FREE_M} м (по территории дома/паркингу)?",
        [("Нет", "carry:0"), ("Да — укажу метры", "carry:ask")],
        False,
    )


def _markup(buttons: list[tuple[str, str]]) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, value in buttons:
        b.row(InlineKeyboardButton(text=label, callback_data=f"deliv:v:{value}"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return b.as_markup()


async def _ask(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data["queue"]
    if not queue:
        await _finish(target, state)
        return
    text, buttons, _ = _prompt(queue[0], data["q"])
    await target.answer(text, reply_markup=_markup(buttons))


def _apply_value(q: dict, step: str, token: str) -> str | None:
    """Возвращает текст ошибки или None. token — часть после 'deliv:v:'."""
    kind, _, rest = token.partition(":")
    if step == "case_length":
        q["long_case"] = rest == "long"
    elif step == "qty":
        q["qty"] = int(rest)
    elif step == "distance_km":
        q["distance_km"] = int(rest)
    elif step == "lift_mode":
        q["lift_mode"] = {"none": "none", "elevator": "elevator", "manual": "manual", "individual": "elevator"}[rest]
    elif step == "assembly":
        q["assembly"] = rest
    elif step == "time_slot":
        q["time_slot"] = rest == "1"
    elif step == "carry_extra":
        if rest == "ask":
            return "ask"
        q["carry_extra_m"] = 0
    return None


async def _advance(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data["queue"][1:]
    if data["queue"][0] == "lift_mode" and data["q"]["lift_mode"] == "manual":
        queue = ["floors"] + queue
    await state.update_data(queue=queue)
    await _ask(target, state)


# --- вход ----------------------------------------------------------------------------


@router.callback_query(F.data == "menu:delivery")
async def cb_delivery(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🏙 Москва", callback_data="deliv:city:msk"),
          InlineKeyboardButton(text="🏙 Санкт-Петербург", callback_data="deliv:city:spb"))
    b.row(InlineKeyboardButton(text="🌍 Другой регион", callback_data="deliv:city:region"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await call.message.answer(
        "🚚 <b>Стоимость доставки</b>\nПо Москве и Санкт-Петербургу считаем по нашим тарифам (Феникс). "
        "В другие регионы — везёт транспортная компания, смотрим по таблице.\n\nКуда доставка?",
        reply_markup=b.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("deliv:city:"))
async def cb_city(call: types.CallbackQuery, state: FSMContext) -> None:
    city = call.data.split(":")[2]
    await call.answer()
    if city == "region":
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="📄 Открыть таблицу тарифов по городам", url=REGIONS_URL))
        b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
        await call.message.answer(
            "🌍 <b>Доставка в регион</b>\nСвоей доставкой не возим — тариф и лучшая транспортная компания "
            "смотрятся по городу доставки в таблице.\nОбратите внимание: расчёт в таблице — для стандартных "
            "габаритов (диван 2300×1060×850, кресло 870×930×1060); если позиция крупнее — цена в ТК будет выше.",
            reply_markup=b.as_markup(),
        )
        return
    city_label = "Москве" if city == "msk" else "Санкт-Петербургу"
    await state.set_state(DeliveryForm.run)
    await state.update_data(city=city_label, q=_new_q(), queue=[])
    b = InlineKeyboardBuilder()
    for key, label in dl.CATEGORIES:
        b.row(InlineKeyboardButton(text=label, callback_data=f"deliv:cat:{key}"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    await call.message.answer(f"Доставка по {city_label}. Что везём?", reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("deliv:cat:"), DeliveryForm.run)
async def cb_category(call: types.CallbackQuery, state: FSMContext) -> None:
    category = call.data.split(":")[2]
    data = await state.get_data()
    q = data["q"]
    q["category"] = category
    await state.update_data(q=q, queue=_queue_for(category))
    await call.answer()
    await _ask(call.message, state)


@router.callback_query(F.data.startswith("deliv:v:"), DeliveryForm.run)
async def cb_value(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data.get("queue") or []
    if not queue:
        await call.answer("Этот вопрос уже пройден")
        return
    step = queue[0]
    token = call.data[len("deliv:v:"):]
    q = data["q"]
    result = _apply_value(q, step, token)
    await state.update_data(q=q)
    await call.answer()
    if step == "carry_extra" and result == "ask":
        await call.message.answer("Сколько метров нужно пронести (всего, включая первые 15 м бесплатно)?")
        await state.update_data(queue=["carry_extra_m"] + queue[1:])
        return
    await _advance(call.message, state)


@router.message(DeliveryForm.run, F.text)
async def msg_value(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data.get("queue") or []
    if not queue:
        return
    step = queue[0]
    n = _num(message.text)
    if n is None or n < 0:
        await message.answer("Введите число, например 25.")
        return
    q = data["q"]
    if step == "qty":
        if n < 1:
            await message.answer("Количество должно быть не меньше 1.")
            return
        q["qty"] = n
    elif step == "distance_km":
        q["distance_km"] = n
    elif step == "floors":
        q["floors"] = n
    elif step == "carry_extra_m":
        q["carry_extra_m"] = n
        await state.update_data(q=q, queue=queue[1:])
        await _ask(message, state)
        return
    else:
        return
    await state.update_data(q=q)
    await _advance(message, state)


async def _finish(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    quote = dl.Quote(**data["q"])
    total = quote.compute()
    lines = [f"🚚 <b>Расчёт доставки — {data['city']}</b>", ""]
    for label, price in quote.lines:
        lines.append(f"{label} — <b>{rub(price)}</b>" if price else f"{label}")
    lines.append("")
    lines.append(f"<b>Итого: {rub(total)}</b>")
    text = "\n".join(lines)
    analytics.track(target.chat.id, "delivery", data["city"], data={"sum": total, "category": data["q"]["category"]})
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🔁 Новый расчёт", callback_data="menu:delivery"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await state.clear()
    await target.answer(text, reply_markup=b.as_markup())
