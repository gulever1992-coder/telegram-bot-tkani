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
from utils import pricelist, regions
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


def _new_item(category: str, name: str = "", price_asm: int = 0, from_price: bool = False) -> dict:
    return {"category": category, "name": name, "qty": 1, "long_case": False, "assembly_price": 0, "parts": 1,
            "price_asm": price_asm, "from_price": from_price}


def _new_quote() -> dict:
    return {"items": [], "distance_km": 0, "lift_mode": "none", "floors": 0, "lift_difficulty": "normal",
            "time_slot": False, "carry_extra_m": 0}


# лист прайса -> тип изделия для тарифа доставки
SOURCE_CATEGORY = {"Кресла": "armchair", "Диваны": "big", "Кровати": "big", "Стулья": "small", "Пуфы": "small",
                   "Корпус": "case"}


def _item_queue(item: dict) -> list[str]:
    cat = item["category"]
    q = ["case_length"] if cat == "case" else []
    if not item.get("from_price"):
        q.append("name")
    q.append("qty")
    if cat == "big":
        q.append("parts")
    if item.get("from_price"):
        if item.get("price_asm"):
            q.append("assembly")  # стоимость из прайса — спрашиваем только «нужна / не нужна»
    elif cat in ("armchair", "big"):
        q.append("assembly")  # позиции нет в прайсе — выбор вручную
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
    if step == "parts":
        return (f"🧩 Из скольких частей «{item['name'] or 'изделие'}»? (для ручного подъёма и проноса)",
                [("1 — цельный", "parts:1"), ("2 части", "parts:2"), ("3 части", "parts:3"), ("4 части", "parts:4")],
                True)
    if item.get("from_price"):
        return (f"🔧 Сборка по прайсу: <b>{rub(item['price_asm'])}</b> за шт. Нужна?",
                [("✅ Со сборкой", "asm:yes"), ("❌ Без сборки", "asm:no")], False)
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
        buttons = [("❌ Без подъёма (отказ / 1 этаж / грузовой лифт)", "lift:none")]
        if cats - {"case"}:
            buttons.append(("Занос и подъём на лифте", "lift:elevator"))
            buttons.append(("Вручную, без лифта", "lift:manual"))
        else:
            buttons.append(("Подъём нужен (рассчитаем индивидуально)", "lift:elevator"))
        return "🛗 Нужен подъём в квартиру? (один способ на весь заказ)", buttons, False
    if step == "floors":
        return "На какой этаж поднимать (число этажей)?", [], True
    if step == "lift_difficulty":
        return (
            "🧗 Сложность подъёма дивана/кровати (ставка за 1 часть за этаж):",
            [(f"{label[:1].upper() + label[1:]} — {dl.MANUAL_BIG_RATES[key]} ₽", f"diff:{key}")
             for key, label in dl.MANUAL_BIG_LABELS.items()],
            False,
        )
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


def _category_markup() -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, label in dl.CATEGORIES:
        b.row(InlineKeyboardButton(text=label, callback_data=f"deliv:cat:{key}"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return b.as_markup()


async def _start_item(target: types.Message, state: FSMContext) -> None:
    await state.update_data(phase="pick_category")
    n = len((await state.get_data())["q"]["items"]) + 1
    await target.answer(
        f"Что везём? (позиция {n})\n✏ Напишите название из прайса, например <i>диван Джун</i> или "
        "<i>кровать Либерти</i> — стоимость сборки подтяну из прайса.\nИли выберите тип вручную:",
        reply_markup=_category_markup(),
    )


def _quote_queue_after_lift(q: dict, queue: list[str]) -> list[str]:
    if q["lift_mode"] != "manual":
        return queue
    extra = ["floors"]
    if any(it["category"] == "big" for it in q["items"]):
        extra.append("lift_difficulty")
    return extra + queue


def _item_fields(it: dict) -> dict:
    keys = ("category", "name", "qty", "long_case", "assembly_price", "parts")
    return {k: it[k] for k in keys if k in it}


async def _finish_quote(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    quote = dl.Quote(items=[dl.Item(**_item_fields(it)) for it in data["q"]["items"]],
                     distance_km=data["q"]["distance_km"], lift_mode=data["q"]["lift_mode"],
                     floors=data["q"]["floors"], lift_difficulty=data["q"].get("lift_difficulty", "normal"),
                     time_slot=data["q"]["time_slot"], carry_extra_m=data["q"]["carry_extra_m"])
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


# --- доставка в регион: город из таблицы + позиции текстом -------------------------------


def _region_block(entry: dict) -> str:
    lines = [f"🌍 <b>{entry['city']}</b>", f"Откуда выгоднее отгружать: {entry['from'] or '—'}"]
    if entry["tk"]:
        lines.append(f"\n<b>Транспортная компания:</b>\n{entry['tk']}")
    if entry["ati"]:
        lines.append(f"\n<b>АТИ (частный перевозчик):</b>\n{entry['ati']}")
    return "\n".join(lines)


def _region_result(entry: dict, items: list[tuple[str, int]]) -> str:
    lines = [_region_block(entry), "", "📦 <b>Позиции:</b>"]
    for name, qty in items:
        key = regions.item_key(name)
        rng = regions.price_range(entry, key) if key else None
        if rng:
            lo, hi = rng
            note = "" if qty == 1 else f" — цена за {qty} шт уточняется у ТК"
            lines.append(f"— {name} × {qty}: от {rub(lo)} до {rub(hi)} за 1 шт{note}")
        else:
            lines.append(f"— {name} × {qty}: тарифа на эту позицию в таблице нет, уточните у ТК/логистики")
    lines.append(
        "\n<i>Диапазон — по прайсу ТК для стандартных габаритов (диван 2300×1060×850, кресло 870×930×1060). "
        "Если позиция крупнее или позиций несколько — точную стоимость подтверждает транспортная компания.</i>"
    )
    return "\n".join(lines)


def _region_result_markup() -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📄 Открыть таблицу тарифов", url=REGIONS_URL))
    b.row(InlineKeyboardButton(text="🔁 Новый расчёт", callback_data="menu:delivery"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    return b.as_markup()


async def _region_ask_items(target: types.Message, state: FSMContext, entry: dict) -> None:
    await state.update_data(phase="region_items", region_entry=entry)
    await target.answer(
        _region_block(entry) + "\n\n📦 Какие позиции и в каком количестве? Например:\nДиван 1 шт\nКресло 2 шт"
    )


@router.callback_query(F.data.startswith("deliv:rcity:"), DeliveryForm.run)
async def cb_region_city_pick(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    matches = data.get("region_matches") or []
    idx = int(call.data.split(":")[2])
    if data.get("phase") != "region_pick" or idx >= len(matches):
        await call.answer("Список устарел, напишите город ещё раз", show_alert=True)
        return
    await call.answer()
    await _region_ask_items(call.message, state, matches[idx])


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
        await state.set_state(DeliveryForm.run)
        await state.update_data(phase="region_city")
        await call.message.answer(
            "🌍 <b>Доставка в регион</b>\nСвоей доставкой не возим — везёт транспортная компания или частный "
            "перевозчик, по городу доставки смотрим готовые тарифы.\n\nВ какой город доставка? Напишите название:"
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
    item = _new_item(category)
    await state.update_data(item=item, queue=_item_queue(item), phase="item")
    await call.answer()
    await _ask(call.message, state)


@router.callback_query(F.data.startswith("deliv:pick:"), DeliveryForm.run)
async def cb_pick(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    found = data.get("found") or []
    i = int(call.data.split(":")[2])
    if data.get("phase") != "pick_category" or i >= len(found):
        await call.answer("Этот шаг уже пройден")
        return
    await call.answer()
    f = found[i]
    item = _new_item(f["category"], f["title"], f["asm"], from_price=True)
    await state.update_data(item=item, queue=_item_queue(item), phase="item", found=None)
    asm = f"сборка по прайсу {rub(f['asm'])}" if f["asm"] else "сборки в прайсе нет"
    await call.message.answer(f"✔ <b>{f['title']}</b> — {asm}.")
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
        elif step == "parts":
            item["parts"] = int(rest)
        elif step == "assembly":
            if rest == "yes":
                item["assembly_price"] = int(item.get("price_asm") or 0)
            elif rest == "no":
                item["assembly_price"] = 0
            else:  # ручной выбор, позиции нет в прайсе
                item["assembly_price"] = dl.ASSEMBLY_PRICE.get(rest, 0)
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
        queue = _quote_queue_after_lift(q, queue[1:])
    elif step == "floors":
        return  # этажи вводятся текстом
    elif step == "lift_difficulty":
        q["lift_difficulty"] = rest
        queue = queue[1:]
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


async def _search_item(message: types.Message, state: FSMContext, query: str) -> None:
    status = await message.answer("🔎 Ищу в прайсе...")
    try:
        items = await pricelist.search_catalog(query)
    except pricelist.PriceError as exc:
        await status.edit_text(f"⚠ {exc}\nВыберите тип вручную:", reply_markup=_category_markup())
        return
    found = [
        {"title": it.title, "category": SOURCE_CATEGORY.get(it.source, "big"), "asm": int(it.assembly or 0),
         "size": "x".join(str(v) for v in it.dims) if it.dims else it.size_raw}
        for it in items
    ]
    if not found:
        await status.edit_text(f"«{query}» в прайсе не нашёл. Напишите иначе или выберите тип вручную:",
                               reply_markup=_category_markup())
        return
    await state.update_data(found=found)
    b = InlineKeyboardBuilder()
    for i, f in enumerate(found):
        asm = f" · сборка {rub(f['asm'])}" if f["asm"] else ""
        size = f" {f['size']}" if f.get("size") else ""
        b.row(InlineKeyboardButton(text=f"{f['title']}{size}{asm}"[:64], callback_data=f"deliv:pick:{i}"))
    b.row(InlineKeyboardButton(text="📦 Нет в списке — выбрать тип вручную", callback_data="deliv:manual"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    await status.edit_text("Выберите позицию:", reply_markup=b.as_markup())


@router.callback_query(F.data == "deliv:manual", DeliveryForm.run)
async def cb_manual(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await call.message.answer("Выберите тип изделия:", reply_markup=_category_markup())


@router.message(DeliveryForm.run, F.text)
async def msg_value(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    phase = data.get("phase")

    if phase == "region_city":
        status = await message.answer("🔎 Ищу город в таблице...")
        try:
            matches = await regions.find(message.text)
        except regions.RegionError as exc:
            b = InlineKeyboardBuilder()
            b.row(InlineKeyboardButton(text="📄 Открыть таблицу тарифов", url=REGIONS_URL))
            b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
            await status.edit_text(f"⚠ {exc}", reply_markup=b.as_markup())
            return
        if not matches:
            b = InlineKeyboardBuilder()
            b.row(InlineKeyboardButton(text="📄 Открыть таблицу тарифов", url=REGIONS_URL))
            b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
            await status.edit_text(
                f"«{message.text.strip()}» не нашёл в таблице. Попробуйте другое написание или откройте таблицу сами.",
                reply_markup=b.as_markup(),
            )
            return
        if len(matches) == 1:
            await status.delete()
            await _region_ask_items(message, state, matches[0])
            return
        await state.update_data(phase="region_pick", region_matches=matches)
        b = InlineKeyboardBuilder()
        for i, m in enumerate(matches[:15]):
            b.row(InlineKeyboardButton(text=m["city"], callback_data=f"deliv:rcity:{i}"))
        b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
        await status.edit_text("Нашёл несколько городов, уточните:", reply_markup=b.as_markup())
        return

    if phase == "region_items":
        entry = data["region_entry"]
        items = regions.parse_items(message.text)
        if not items:
            await message.answer("Не разобрал позиции. Например:\nДиван 1 шт\nКресло 2 шт")
            return
        text = _region_result(entry, items)
        analytics.track(message.chat.id, "delivery", entry["city"], data={"region": entry["city"], "items": len(items)})
        await state.clear()
        await message.answer(text, reply_markup=_region_result_markup())
        return

    if phase == "pick_category":
        await _search_item(message, state, message.text.strip())
        return

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
    if data["phase"] == "item" and step == "parts":
        item = data["item"]
        item["parts"] = max(n, 1)
        await state.update_data(item=item, queue=queue[1:])
        await _ask(message, state)
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
