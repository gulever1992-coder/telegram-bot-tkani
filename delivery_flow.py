"""Стоимость доставки: калькулятор для Москвы и Санкт-Петербурга (несколько позиций в одном расчёте),
ссылка на таблицу для регионов."""

from __future__ import annotations

import asyncio
import re

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import logging

import analytics
import delivery as dl
from utils import pricelist, regions
from utils.kp import rub

router = Router()
log = logging.getLogger("delivery")
REGIONS_URL = (
    "https://docs.google.com/spreadsheets/d/1d3UN8U5H-SEFK_xApNV34b5PWk4G8ah3-NUuYz_3cxQ/"
    "edit?gid=2086428513#gid=2086428513"
)

async def _warm_price() -> None:
    try:
        await pricelist.search_catalog("прогрев")
    except Exception:
        pass


class DeliveryForm(StatesGroup):
    run = State()


def _num(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None




_PARTS_RE = [(re.compile(r"из\s+(двух|2)\b|2\s*-?х?\s*част", re.I), 2),
             (re.compile(r"из\s+(тр[её]х|3)\b|3\s*-?х?\s*част", re.I), 3),
             (re.compile(r"из\s+(четыр\w*|4)\b|4\s*-?х?\s*част", re.I), 4)]


def _parts_from_title(title: str) -> int | None:
    for rx, n in _PARTS_RE:
        if rx.search(title or ""):
            return n
    return None


def _new_item(category: str, name: str = "", price_asm: int = 0, from_price: bool = False,
              dims: list | None = None) -> dict:
    parts = _parts_from_title(name)
    long_case = bool(dims) and category == "case" and max(dims[:2] or [0]) > 1000
    return {"category": category, "name": name, "qty": 1, "long_case": long_case, "assembly_price": 0,
            "parts": parts or 1, "parts_known": parts is not None or category != "big",
            "price_asm": price_asm, "from_price": from_price, "dims_known": bool(dims)}


def _new_quote() -> dict:
    return {"items": [], "distance_km": 0, "lift_mode": "none", "floors": 0, "lift_difficulty": "normal",
            "time_slot": False, "carry_extra_m": 0}


# лист прайса -> тип изделия для тарифа доставки
SOURCE_CATEGORY = {"Кресла": "armchair", "Диваны": "big", "Кровати": "big", "Стулья": "small", "Пуфы": "small",
                   "Корпус": "case"}


def _item_queue(item: dict) -> list[str]:
    """По позиции спрашиваем только количество (длину тумбы — если её нет в прайсе)."""
    q = ["case_length"] if item["category"] == "case" and not item.get("dims_known") else []
    return q + ["qty"]


QUOTE_QUEUE = ["distance_km", "lift_mode", "time_slot", "carry_extra", "assembly"]


def _parts_steps(q: dict) -> list[str]:
    """Число частей не спрашиваем: берём из названия («из двух частей»), иначе изделие цельное."""
    return []


def _assembly_total(q: dict) -> int:
    return sum(int(it.get("price_asm") or 0) * it["qty"] for it in q["items"])


# --- вопросы по позиции --------------------------------------------------------------------


def _item_prompt(step: str, item: dict) -> tuple[str, list[tuple[str, str]], bool]:
    """(текст, кнопки, можно_текстом)"""
    if step == "case_length":
        return "📏 Длина изделия:", [("До 100 см", "len:short"), ("Более 100 см", "len:long")], False
    return f"🔢 «{item['name'] or 'Позиция'}» — сколько штук?", [(str(i), f"qty:{i}") for i in (1, 2, 3, 4)], True


def _quote_prompt(step: str, q: dict) -> tuple[str, list[tuple[str, str]], bool]:
    if step == "distance_km":
        return (
            "📍 Расстояние от МКАД, км? (первые {0} км бесплатно, дальше +{1} ₽/км — можно написать число)".format(
                dl.MKAD_FREE_KM, dl.EXTRA_KM_PRICE),
            [("В пределах МКАД", "km:0"), ("20 км", "km:20"), ("30 км", "km:30"), ("50 км", "km:50")],
            True,
        )
    if step == "lift_mode":
        return "🛗 Подъём в квартиру:", [
            ("❌ Не нужен", "lift:none"), ("🛗 На лифте", "lift:elevator"), ("🚶 Вручную, без лифта", "lift:manual"),
        ], False
    if step == "floors":
        return "Какой этаж? (напишите число)", [], True
    if step == "lift_difficulty":
        return (
            "🧗 Подъём дивана/кровати по лестнице:",
            [(f"{label[:1].upper() + label[1:]} — {dl.MANUAL_BIG_RATES[key]} ₽", f"diff:{key}")
             for key, label in dl.MANUAL_BIG_LABELS.items()],
            False,
        )
    if step.startswith("parts:"):
        it = q["items"][int(step.split(":")[1])]
        return (f"🧩 «{it['name'] or 'Изделие'}» — из скольких частей?",
                [("Цельный", "parts:1"), ("2 части", "parts:2"), ("3 части", "parts:3")], True)
    if step == "time_slot":
        return f"⏰ Доставка ко времени (с 12:00, +{rub(dl.TIME_SLOT_FEE)})?", [("Нет", "time:0"), ("Да", "time:1")], False
    if step == "assembly":
        lines = [f"• {it['name']}" + (f" × {it['qty']}" if it["qty"] > 1 else "") + f" — {rub(it['price_asm'] * it['qty'])}"
                 for it in q["items"] if it.get("price_asm")]
        return ("🔧 Сборка по прайсу:\n" + "\n".join(lines) + f"\nИтого сборка: <b>{rub(_assembly_total(q))}</b>",
                [("✅ Со сборкой", "asm:yes"), ("❌ Без сборки", "asm:no")], False)
    return (  # carry_extra
        f"🚶 Ручной пронос дальше {dl.CARRY_FREE_M} м от машины? (можно написать метры)",
        [("Нет", "carry:0"), ("30 м", "carry:30"), ("50 м", "carry:50"), ("100 м", "carry:100")],
        True,
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
    if data["phase"] == "quote" and step == "assembly" and not _assembly_total(data["q"]):
        await state.update_data(queue=queue[1:])  # сборки по прайсу нет — вопрос не задаём
        await _ask(target, state)
        return
    if data["phase"] == "item":
        text, buttons, allow_text = _item_prompt(step, data["item"])
    else:
        text, buttons, allow_text = _quote_prompt(step, data["q"])
    await target.answer(text, reply_markup=_markup(buttons))


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
        f"✏ Что везём? (позиция {n}) Напишите модель, например <i>диван Джун</i> или <i>кровать Либерти</i> — "
        "размеры и сборку найду в прайсе.",
        reply_markup=kb_cancel(),
    )


def kb_cancel() -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return b.as_markup()


def _quote_queue_after_lift(q: dict, queue: list[str]) -> list[str]:
    if q["lift_mode"] != "manual":
        return queue
    extra = ["floors"]
    if any(it["category"] == "big" for it in q["items"]):
        extra.append("lift_difficulty")
    return extra + _parts_steps(q) + queue


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
    asyncio.create_task(_warm_price())  # прайс грузится заранее, пока менеджер выбирает город
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
    item = _new_item(category, data.get("last_query") or dl.CATEGORY_LABEL[category])
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
    await _select_found(call.message, state, found[i], edit=True)


async def _select_found(target: types.Message, state: FSMContext, f: dict, edit: bool = False) -> None:
    item = _new_item(f["category"], f["title"], f["asm"], from_price=True, dims=f.get("dims"))
    await state.update_data(item=item, queue=_item_queue(item), phase="item", found=None)
    size = f" {f['size']}" if f.get("size") else ""
    asm = f"сборка {rub(f['asm'])}" if f["asm"] else "без сборки"
    text = f"✔ <b>{f['title']}</b>{size} — {asm}."
    if edit:
        try:
            await target.edit_text(text)
        except Exception:
            await target.answer(text)
    else:
        await target.answer(text)
    await _ask(target, state)


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
        await state.update_data(item=item, queue=queue[1:])
        await _ask(call.message, state)
        return
    # phase == quote
    q = data["q"]
    if step == "distance_km":
        q["distance_km"] = int(rest)
        queue = queue[1:]
    elif step.startswith("parts:"):
        _set_parts(q, step, int(rest))
        queue = queue[1:]
    elif step == "assembly":
        for it in q["items"]:
            it["assembly_price"] = int(it.get("price_asm") or 0) if rest == "yes" else 0
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
        queue = _after_carry(q, int(rest), queue[1:])
    await state.update_data(q=q, queue=queue)
    await _ask(call.message, state)


def _set_parts(q: dict, step: str, n: int) -> None:
    it = q["items"][int(step.split(":")[1])]
    it["parts"], it["parts_known"] = max(n, 1), True


def _after_carry(q: dict, meters: int, queue: list[str]) -> list[str]:
    """Пронос считается за место — если он нужен, уточняем части у диванов/кроватей (если ещё не знаем)."""
    q["carry_extra_m"] = meters
    if meters > dl.CARRY_FREE_M:
        pending = [s for s in _parts_steps(q) if s not in queue]
        return pending + queue
    return queue


async def _search_item(message: types.Message, state: FSMContext, query: str) -> None:
    status = await message.answer("🔎 Ищу в прайсе...")
    try:
        items = await asyncio.wait_for(pricelist.search_catalog(query), timeout=20)
    except (pricelist.PriceError, asyncio.TimeoutError) as exc:
        log.warning("доставка: поиск «%s» не удался: %r", query, exc)
        await _replace(status, "⚠ Прайс сейчас не отвечает. Выберите тип изделия — посчитаю без сборки:",
                       _category_markup())
        return
    log.info("доставка: поиск «%s» — найдено %d", query, len(items))
    found = [
        {"title": it.title, "category": SOURCE_CATEGORY.get(it.source, "big"), "asm": int(it.assembly or 0),
         "size": "x".join(str(v) for v in it.dims) if it.dims else it.size_raw,
         "dims": list(it.dims) if it.dims else None}
        for it in items
    ]
    await state.update_data(last_query=query)
    if not found:
        await _replace(status, f"«{query}» в прайсе не нашёл. Напишите иначе или выберите тип:", _category_markup())
        return
    if len(found) == 1:  # единственный вариант — выбираем сами, без лишней кнопки
        await _select_found(status, state, found[0], edit=True)
        return
    await state.update_data(found=found)
    same_title = len({f["title"] for f in found}) == 1
    b = InlineKeyboardBuilder()
    for i, f in enumerate(found):
        asm = f" · сборка {rub(f['asm'])}" if f["asm"] else ""
        label = (f["size"] or f["title"]) if same_title else f"{f['title']} {f['size'] or ''}".strip()
        b.row(InlineKeyboardButton(text=f"{label}{asm}"[:64], callback_data=f"deliv:pick:{i}"))
    b.row(InlineKeyboardButton(text="📦 Нет в списке", callback_data="deliv:manual"),
          InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    head = f"<b>{found[0]['title']}</b> — выберите размер:" if same_title else "Выберите модель и размер:"
    await _replace(status, head, b.as_markup())


async def _replace(status: types.Message, text: str, markup) -> None:
    """Заменить «Ищу…» результатом; если правка не прошла — прислать новым сообщением."""
    try:
        await status.edit_text(text, reply_markup=markup)
        log.info("доставка: поиск — ответ показан (msg %s)", status.message_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("доставка: не удалось изменить сообщение %s: %r", status.message_id, exc)
        await status.answer(text, reply_markup=markup)


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
    if data["phase"] != "quote":
        return
    q = data["q"]
    if step == "distance_km":
        q["distance_km"] = n
        queue = queue[1:]
    elif step == "floors":
        q["floors"] = n
        queue = queue[1:]
    elif step.startswith("parts:"):
        _set_parts(q, step, n)
        queue = queue[1:]
    elif step == "carry_extra":
        queue = _after_carry(q, n, queue[1:])
    else:
        await message.answer("Выберите вариант кнопкой выше 👆")
        return
    await state.update_data(q=q, queue=queue)
    await _ask(message, state)
