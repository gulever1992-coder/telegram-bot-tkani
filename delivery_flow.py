"""Стоимость доставки: калькулятор для Москвы и Санкт-Петербурга (несколько позиций в одном расчёте),
ссылка на таблицу для регионов."""

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


def _new_item(category: str) -> dict:
    return {"category": category, "name": "", "qty": 1, "long_case": False, "assembly": "none"}


def _new_quote() -> dict:
    return {"items": [], "distance_km": 0, "lift_mode": "none", "floors": 0, "time_slot": False, "carry_extra_m": 0}


def _item_queue(category: str) -> list[str]:
    q = ["case_length"] if category == "case" else []
    q.append("name")
    q.append("qty")
    if category in ("armchair", "big"):
        q.append("assembly")
    return q


QUOTE_QUEUE = ["distance_km", "lift_mode", "time_slot", "carry_extra"]


# --- вопросы по позиции --------------------------------------------------------------------


def _item_prompt(step: str, item: dict) -> tuple[str, list[tuple[str, str]], bool]:
    """(текст, кнопки, можно_текстом)"""
    if step == "case_length":
        return "📏 Длина изделия:", [("До 100 см", "len:short"), ("Более 100 см", "len:long")], False
    if step == "name":
        return (
            f"✏ Название позиции (например «Диван Джун»). Можно пропустить — тогда будет "
            f"«{dl.CATEGORY_LABEL[item['category']]}».",
            [], True,
        )
    if step == "qty":
        return "🔢 Количество, шт:", [(str(i), f"qty:{i}") for i in (1, 2, 3, 4, 5, 6)], True
    if item["category"] == "armchair":
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


def _quote_prompt(step: str, q: dict) -> tuple[str, list[tuple[str, str]], bool]:
    if step == "distance_km":
        return (
            "📍 На каком расстоянии от МКАД адрес доставки, км?\n"
            f"Первые {dl.MKAD_FREE_KM} км за МКАД входят в стоимость, дальше +{dl.EXTRA_KM_PRICE} ₽/км.",
            [("В пределах МКАД", "km:0"), ("20 км", "km:20"), ("30 км", "km:30"), ("50 км", "km:50"),
             ("✏ Указать км", "km:ask")],
            True,
        )
    if step == "lift_mode":
        cats = {it["category"] for it in q["items"]}
        buttons = [("Без подъёма (1 этаж / есть грузовой лифт)", "lift:none")]
        if cats - {"case"}:
            buttons.append(("Занос и подъём на лифте", "lift:elevator"))
            buttons.append(("Вручную, без лифта", "lift:manual"))
        else:
            buttons.append(("Подъём нужен (рассчитаем индивидуально)", "lift:elevator"))
        return "🛗 Нужен подъём в квартиру? (один способ на весь заказ)", buttons, False
    if step == "floors":
        return "На какой этаж поднимать (число этажей)?", [], True
    if step == "time_slot":
        return f"⏰ Доставка ко времени (с 12:00)? +{rub(dl.TIME_SLOT_FEE)}", [
            ("Да", "time:1"), ("Нет", "time:0"),
        ], False
    return (  # carry_extra
        f"🚶 Нужен ручной пронос дальше {dl.CARRY_FREE_M} м (по территории дома/паркингу)?",
        [("Нет", "carry:0"), ("Да — укажу метры", "carry:ask")],
        False,
    )


def _markup(buttons: list[tuple[str, str]], skip: bool = False) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, value in buttons:
        b.row(InlineKeyboardButton(text=label, callback_data=f"deliv:v:{value}"))
    if skip:
        b.row(InlineKeyboardButton(text="⏭ Пропустить", callback_data="deliv:skip"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return b.as_markup()


async def _ask(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data["queue"]
    if not queue:
        if data["phase"] == "item":
            await _finish_item(target, state)
        else:
            await _finish_quote(target, state)
        return
    step = queue[0]
    if data["phase"] == "item":
        text, buttons, allow_text = _item_prompt(step, data["item"])
    else:
        text, buttons, allow_text = _quote_prompt(step, data["q"])
    await target.answer(text, reply_markup=_markup(buttons, skip=(step == "name")))


async def _finish_item(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    q = data["q"]
    q["items"].append(data["item"])
    await state.update_data(q=q, item=None)
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Добавить ещё позицию", callback_data="deliv:more:add"))
    b.row(InlineKeyboardButton(text="✅ Позиций достаточно", callback_data="deliv:more:done"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    last = q["items"][-1]
    await state.update_data(phase="more")
    await target.answer(
        f"✔ Добавлено: <b>{last['name'] or dl.CATEGORY_LABEL[last['category']]}</b> × {last['qty']}. "
        f"Позиций в расчёте: {len(q['items'])}.",
        reply_markup=b.as_markup(),
    )


async def _start_item(target: types.Message, state: FSMContext) -> None:
    b = InlineKeyboardBuilder()
    for key, label in dl.CATEGORIES:
        b.row(InlineKeyboardButton(text=label, callback_data=f"deliv:cat:{key}"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    await state.update_data(phase="pick_category")
    await target.answer("Что везём? (позиция " + str(len((await state.get_data())["q"]["items"]) + 1) + ")",
                        reply_markup=b.as_markup())


async def _finish_quote(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    quote = dl.Quote(items=[dl.Item(**it) for it in data["q"]["items"]], distance_km=data["q"]["distance_km"],
                     lift_mode=data["q"]["lift_mode"], floors=data["q"]["floors"], time_slot=data["q"]["time_slot"],
                     carry_extra_m=data["q"]["carry_extra_m"])
    total = quote.compute()
    lines = [f"🚚 <b>Расчёт доставки — {data['city']}</b>", ""]
    for label, price in quote.lines:
        lines.append(f"{label} — <b>{rub(price)}</b>" if price else f"{label}")
    lines.append("")
    lines.append(f"<b>Итого: {rub(total)}</b>")
    text = "\n".join(lines)
    analytics.track(target.chat.id, "delivery", data["city"],
                    data={"sum": total, "items": len(quote.items)})
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🔁 Новый расчёт", callback_data="menu:delivery"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await state.clear()
    await target.answer(text, reply_markup=b.as_markup())


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
        "🚚 <b>Стоимость доставки</b>\nПо Москве и Санкт-Петербургу считаем по нашим тарифам (Феникс) — можно "
        "добавить сразу несколько позиций заказа. В другие регионы — везёт транспортная компания, смотрим по "
        "таблице.\n\nКуда доставка?",
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
    await state.update_data(city=city_label, q=_new_quote(), item=None, queue=[], phase="pick_category")
    await call.message.answer(f"Доставка по {city_label}. Добавим позиции заказа одну за другой.")
    await _start_item(call.message, state)


@router.callback_query(F.data.startswith("deliv:cat:"), DeliveryForm.run)
async def cb_category(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data["phase"] != "pick_category":
        await call.answer("Этот шаг уже пройден")
        return
    category = call.data.split(":")[2]
    await state.update_data(item=_new_item(category), queue=_item_queue(category), phase="item")
    await call.answer()
    await _ask(call.message, state)


@router.callback_query(F.data.startswith("deliv:more:"))
async def cb_more(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data["phase"] != "more":
        await call.answer("Уже выбрано")
        return
    await call.answer()
    if call.data.endswith("add"):
        await state.update_data(phase="pick_category")
        await _start_item(call.message, state)
        return
    await state.update_data(phase="quote", queue=list(QUOTE_QUEUE))
    await _ask(call.message, state)


@router.callback_query(F.data.startswith("deliv:v:"), DeliveryForm.run)
async def cb_value(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data.get("queue") or []
    if not queue or data["phase"] not in ("item", "quote"):
        await call.answer("Этот вопрос уже пройден")
        return
    step = queue[0]
    kind, _, rest = call.data[len("deliv:v:"):].partition(":")
    await call.answer()
    if data["phase"] == "item":
        item = data["item"]
        if step == "case_length":
            item["long_case"] = rest == "long"
        elif step == "qty":
            item["qty"] = int(rest)
        elif step == "assembly":
            item["assembly"] = rest
        await state.update_data(item=item, queue=queue[1:])
        await _ask(call.message, state)
        return
    # phase == quote
    q = data["q"]
    if step == "distance_km":
        if rest == "ask":
            await call.message.answer("Напишите расстояние от МКАД, км:")
            return
        q["distance_km"] = int(rest)
        queue = queue[1:]
    elif step == "lift_mode":
        q["lift_mode"] = rest  # none | elevator | manual
        queue = queue[1:]
        if q["lift_mode"] == "manual":
            queue = ["floors"] + queue
    elif step == "floors":
        return  # этажи вводятся текстом
    elif step == "time_slot":
        q["time_slot"] = rest == "1"
        queue = queue[1:]
    elif step == "carry_extra":
        if rest == "ask":
            await call.message.answer("Сколько метров нужно пронести (всего, включая первые 15 м бесплатно)?")
            await state.update_data(queue=["carry_extra_m"] + queue[1:])
            return
        q["carry_extra_m"] = 0
        queue = queue[1:]
    await state.update_data(q=q, queue=queue)
    await _ask(call.message, state)


@router.callback_query(F.data == "deliv:skip", DeliveryForm.run)
async def cb_skip(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data.get("queue") or []
    if not queue or queue[0] != "name" or data["phase"] != "item":
        await call.answer()
        return
    await call.answer("Пропущено")
    await state.update_data(queue=queue[1:])
    await _ask(call.message, state)


@router.message(DeliveryForm.run, F.text)
async def msg_value(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data.get("queue") or []
    if not queue:
        return
    step = queue[0]
    if data["phase"] == "item" and step == "name":
        item = data["item"]
        item["name"] = message.text.strip()
        await state.update_data(item=item, queue=queue[1:])
        await _ask(message, state)
        return
    n = _num(message.text)
    if n is None or n < 0:
        await message.answer("Введите число, например 25.")
        return
    if data["phase"] == "item" and step == "qty":
        if n < 1:
            await message.answer("Количество должно быть не меньше 1.")
            return
        item = data["item"]
        item["qty"] = n
        await state.update_data(item=item, queue=queue[1:])
        await _ask(message, state)
        return
    if data["phase"] == "quote" and step == "distance_km":
        q = data["q"]
        q["distance_km"] = n
        await state.update_data(q=q, queue=queue[1:])
        await _ask(message, state)
        return
    if data["phase"] == "quote" and step == "floors":
        q = data["q"]
        q["floors"] = n
        await state.update_data(q=q, queue=queue[1:])
        await _ask(message, state)
        return
    if data["phase"] == "quote" and step == "carry_extra_m":
        q = data["q"]
        q["carry_extra_m"] = n
        await state.update_data(q=q, queue=queue[1:])
        await _ask(message, state)
        return
