"""Коммерческое предложение (КП): вопрос-ответ с менеджером -> премиальный PDF Creatica."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import io
import logging
import re
from dataclasses import asdict

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import analytics
import config
import keyboards as kb
import onboarding
import profiles
from utils import fabrics, pricelist
from utils.kp import KP, KPItem, PAYMENTS, build_kp_pdf, build_kp_preview, date_ru, prep_photo, rub
from utils.ui import show

router = Router()
log = logging.getLogger("kp")
MSK = dt.timezone(dt.timedelta(hours=3))
LAST_MANAGER: dict[int, tuple[str, str]] = {}  # пока бот работает, помним менеджера


class MyKP(StatesGroup):
    lost_reason = State()


class KPForm(StatesGroup):
    run = State()


HEAD = ["kind", "customer", "customer_phone"]  # менеджер, его телефон, Telegram, шоу-рум — из профиля
ITEM_STD = ["query", "qty", "size", "material", "link", "price", "discount", "photo"]
ITEM_VIS = ["title", "qty", "size", "material", "price", "discount", "photo",
            "alt_query", "alt_size", "alt_material", "alt_link", "alt_price", "alt_photo"]
TERMS = ["production", "services", "valid", "payments"]


# --- данные -----------------------------------------------------------------------


def _kp_new(profile: dict | None = None) -> dict:
    p = profile or {}
    return {
        "kind": "", "customer": "", "customer_phone": "", "manager": p.get("name", ""), "phone": p.get("phone", ""),
        "telegram": p.get("telegram", ""), "showroom": p.get("showroom", ""), "items": [],
        "production": "55 рабочих дней", "services": "По согласованию",
        "valid_days": 7, "payments": [True] * len(PAYMENTS),
    }


def _item_new() -> dict:
    return {"title": "", "qty": 1, "size": "", "material": "", "link": "", "unit_price": 0,
            "discount": 0.0, "photo": None, "alt": None, "default": {}}


def _build(k: dict) -> KP:
    now = dt.datetime.now(MSK)
    items = []
    for it in k["items"]:
        alt = it.get("alt")
        items.append(KPItem(
            title=it["title"], qty=it["qty"], size=it["size"], material=it["material"], link=it["link"],
            unit_price=it["unit_price"], discount=it["discount"], photo=it["photo"],
            alt=KPItem(title=alt["title"], qty=it["qty"], size=alt["size"], material=alt["material"],
                       link=alt["link"], unit_price=alt["unit_price"], discount=0, photo=alt["photo"])
            if alt and alt.get("title") else None,
        ))
    return KP(
        kind={"vis": "visual"}.get(k["kind"], "standard"), customer=k["customer"], client_phone=k.get("customer_phone", ""),
        manager=k["manager"], phone=k["phone"],
        telegram=k.get("telegram", ""), showroom=profiles.footer_text(k.get("showroom", "")),
        date=now.date(), valid_days=k["valid_days"], number=f"{now:%d%m}-{now:%H%M}", items=items,
        production=k["production"], services=k["services"],
        payments=[p for p, on in zip(PAYMENTS, k["payments"]) if on],
    )


def _json_safe(k: dict) -> dict:
    """k без фото (bytes) — компактный слепок для истории КП (пишется в общий журнал)."""

    def item_safe(it: dict) -> dict:
        base = {f: it[f] for f in ("title", "qty", "size", "material", "link", "unit_price", "discount")}
        base["alt"] = (
            {f: it["alt"][f] for f in ("title", "size", "material", "link", "unit_price")}
            if it.get("alt") else None
        )
        return base

    return {
        "kind": k["kind"], "customer": k["customer"], "customer_phone": k.get("customer_phone", ""),
        "production": k["production"], "services": k["services"],
        "valid_days": k["valid_days"], "payments": k["payments"], "items": [item_safe(it) for it in k["items"]],
    }


def _from_history(uid: int, st: dict) -> dict:
    """Восстанавливает редактируемое состояние КП из слепка истории (без фото — их нужно прикрепить заново)."""
    k = _kp_new(profiles.get(uid))
    for f in ("kind", "customer", "customer_phone", "production", "services", "valid_days", "payments"):
        if f in st:
            k[f] = st[f]
    items = []
    for it in st.get("items", []):
        item = {**_item_new(), **{f: it[f] for f in ("title", "qty", "size", "material", "link", "unit_price", "discount")}}
        if it.get("alt"):
            a = it["alt"]
            item["alt"] = {"title": a["title"], "size": a["size"], "material": a["material"], "link": a["link"],
                           "unit_price": a["unit_price"], "photo": None, "default": {}}
        items.append(item)
    k["items"] = items
    return k


def _dims(item: pricelist.Item) -> str:
    nums = list(item.dims) if item.dims else re.findall(r"\d+", item.size_raw or "")[:3]
    return "×".join(str(n) for n in nums)


def _num(text: str) -> float | None:
    cleaned = text.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


# --- вопросы -----------------------------------------------------------------------


def _split(step: str) -> tuple[bool, str]:
    return (True, step[4:]) if step.startswith("alt_") else (False, step)


def _target(c: dict, alt: bool) -> dict:
    if not alt:
        return c
    if c["alt"] is None:
        c["alt"] = {"title": "", "size": "", "material": "", "link": "", "unit_price": 0, "photo": None, "default": {}}
    return c["alt"]


def _prompt(step: str, k: dict, c: dict) -> tuple[str, list[tuple[str, str]], str | None]:
    """(текст, кнопки [(подпись, значение)], подпись кнопки «пропустить»)"""
    alt, base = _split(step)
    pre = "🅱 <b>Альтернатива из коллекции.</b> " if alt else ""
    t = _target(c, alt) if step not in HEAD + TERMS else c
    default = (t.get("default") or {}) if isinstance(t, dict) else {}
    skip = "⏭ Пропустить"
    if step == "kind":
        return ("📋 <b>Коммерческое предложение Creatica</b>\nКакой вариант КП?",
                [("📋 Стандарт — позиции с ценой и скидкой", "std"),
                 ("🎨 С визуализацией — индивидуальная позиция + альтернатива из коллекции", "vis")], None)
    if step == "customer":
        return "👤 <b>Заказчик</b> — ФИО или название организации:", [], None
    if step == "customer_phone":
        return "📞 <b>Телефон заказчика</b> (клиента):", [], None
    if step == "manager":
        return "🧑‍💼 Ответственный менеджер (ФИО):", [], skip
    if step == "phone":
        return "📞 Телефон менеджера:", [], skip
    if step == "query":
        return ("🔎 Название позиции — найду её в прайсе (например: <i>кресло Джун</i>):",
                [("✍ Заполнить вручную", "manual")], None)
    if step == "alt_query":
        return ("🅱 <b>Наша альтернатива из коллекции.</b> Название стандартной позиции для сравнения:",
                [("✍ Заполнить вручную", "manual")], "⏭ Без альтернативы")
    if step == "pick":
        return "Выберите позицию из списка:", [], None
    if base == "title":
        return pre + "Название позиции:", [], None
    if base == "qty":
        return "🔢 Количество, шт:", [(str(i), str(i)) for i in (1, 2, 3, 4, 6)], None
    if base == "size":
        btn = [(f"✅ {default['size']}", "keep")] if default.get("size") else []
        return pre + "📐 Размеры, мм (например 960×950×900):", btn, skip
    if base == "material":
        return pre + "🧵 Материал: обивка, цвет дерева, дополнительно:", [("4 кат. ткани", "4 кат. ткани")], skip
    if base == "link":
        return pre + "🔗 Ссылка на изделие на сайте (URL):", [], skip
    if base == "price":
        btn = [(f"✅ По прайсу «от»: {rub(default['price'])}", "keep")] if default.get("price") else []
        return pre + "💰 Цена за 1 шт, руб.:", btn, None
    if base == "discount":
        return "🏷 Скидка, % (число):", [(f"{v}%", str(v)) for v in (0, 3, 5, 10)], None
    if base == "photo":
        what = "визуализацию" if (k["kind"] == "vis" and not alt) else "фото изделия"
        return pre + f"📷 Пришлите {what} (картинкой) — или пропустите:", [], skip
    if step == "production":
        return "⏱ Сроки изготовления:", [("55 рабочих дней", "55 рабочих дней"), ("30 рабочих дней", "30 рабочих дней")], skip
    if step == "services":
        return "🧰 Дополнительные услуги:", [("По согласованию", "По согласованию"), ("Не требуются", "Не требуются")], skip
    if step == "valid":
        return "📅 Срок действия предложения, дней:", [("3", "3"), ("7", "7"), ("14", "14")], skip
    return "Введите значение:", [], skip


def _markup(step: str, buttons, skip_label) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, value in buttons:
        b.row(InlineKeyboardButton(text=label[:60], callback_data=f"kp:v:{value}"[:64]))
    if skip_label:
        b.row(InlineKeyboardButton(text=skip_label, callback_data="kp:skip"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return b.as_markup()


async def _next(target: types.Message, state: FSMContext, uid: int) -> None:
    data = await state.get_data()
    queue: list[str] = data["queue"]
    mode = data["mode"]
    k, c = data["kp"], data.get("cur")
    if queue:
        step = queue[0]
        if step == "payments":
            await _pay_screen(target, k)
            return
        if step == "manager" and uid in LAST_MANAGER:
            name, phone = LAST_MANAGER[uid]
            text, buttons, skip = _prompt(step, k, c or {})
            buttons = [(f"Как в прошлый раз: {name}", "last")] + buttons
            await target.answer(text, reply_markup=_markup(step, buttons, skip))
            return
        if step == "phone" and uid in LAST_MANAGER and LAST_MANAGER[uid][1]:
            text, buttons, skip = _prompt(step, k, c or {})
            buttons = [(f"{LAST_MANAGER[uid][1]}", "last")] + buttons
            await target.answer(text, reply_markup=_markup(step, buttons, skip))
            return
        text, buttons, skip = _prompt(step, k, c or {})
        await target.answer(text, reply_markup=_markup(step, buttons, skip))
        return
    if mode == "head":
        await _start_item(target, state)
    elif mode == "item":
        k["items"].append(c)
        await state.update_data(kp=k, cur=None, mode="more")
        await _more_menu(target, k)
    elif mode == "itemedit":  # правка одной позиции — кладём её на прежнее место
        i = data.get("edit_index", 0)
        if 0 <= i < len(k["items"]):
            k["items"][i] = c
        await state.update_data(kp=k, cur=None)
        await target.answer(f"✔ Позиция {i + 1} обновлена.")
        await _preview(target, state)
    else:  # terms | edit
        await _preview(target, state)


async def _start_item(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    k = data["kp"]
    queue = list(ITEM_VIS if k["kind"] == "vis" else ITEM_STD)
    await state.update_data(cur=_item_new(), queue=queue, mode="item")
    await _next(target, state, target.chat.id)


async def _more_menu(target: types.Message, k: dict) -> None:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Добавить ещё позицию", callback_data="kp:more:add"))
    b.row(InlineKeyboardButton(text="✅ Позиций достаточно", callback_data="kp:more:done"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    last = k["items"][-1]
    await target.answer(
        f"✔ Добавлено: <b>{last['title']}</b> — {last['qty']} шт. Позиций в КП: {len(k['items'])}.",
        reply_markup=b.as_markup(),
    )


async def _pay_screen(target: types.Message, k: dict, edit: bool = False) -> None:
    b = InlineKeyboardBuilder()
    for i, p in enumerate(PAYMENTS):
        b.row(InlineKeyboardButton(text=("✅ " if k["payments"][i] else "⬜ ") + p, callback_data=f"kp:pay:{i}"))
    b.row(InlineKeyboardButton(text="✅ Готово", callback_data="kp:paydone"))
    text = "💳 Способы оплаты — отметьте, какие указать в КП:"
    if edit:
        await show(target, text, reply_markup=b.as_markup())
    else:
        await target.answer(text, reply_markup=b.as_markup())


# --- обработка ответа --------------------------------------------------------------


async def _apply(target: types.Message, state: FSMContext, uid: int, value: str | None) -> str | None:
    """Записывает ответ на текущий шаг. Возвращает текст ошибки или None (тогда шаг пройден)."""
    data = await state.get_data()
    k, c, queue = data["kp"], data.get("cur"), data["queue"]
    step = queue[0]
    alt, base = _split(step)
    t = _target(c, alt) if c is not None and step not in HEAD + TERMS else None
    default = (t or {}).get("default") or {}
    empty = value is None
    popped = False

    if step == "kind":
        if value not in ("std", "vis"):
            return "Выберите вариант кнопкой."
        k["kind"] = value
    elif step in ("customer", "manager", "phone"):
        if value == "last":
            name, phone = LAST_MANAGER.get(uid, ("", ""))
            value = name if step == "manager" else phone
        if empty:
            if data["mode"] == "edit":
                pass  # пропустили при правке уже заполненного поля — оставляем как было
            elif step == "customer":
                return "Укажите ФИО или название заказчика — это обязательное поле."
            else:
                k[step] = ""
        else:
            k[step] = value or ""
    elif step == "customer_phone":
        if empty:
            if data["mode"] == "edit":
                pass
            else:
                return "Укажите телефон заказчика — это обязательное поле."
        else:
            phone_val = onboarding.norm_phone(value)
            if not phone_val:
                return "Похоже, это не номер телефона. Формат: +7 999 123-45-67 (10 или 11 цифр)."
            k["customer_phone"] = phone_val
    elif step == "alt_query" and empty:
        c["alt"] = None
        queue[:] = [q for q in queue if not q.startswith("alt_")]
        popped = True  # шаг alt_query уже убран вместе со всеми alt_*
    elif base == "title":
        if not value:
            return "Введите название."
        t["title"] = value
    elif base == "qty":
        n = _num(value or "1")
        if empty:
            n = 1
        if not n or n < 1 or n != int(n):
            return "Введите количество целым числом, например 2."
        c["qty"] = int(n)
    elif base in ("size", "material", "link") and empty and data["mode"] == "itemedit":
        pass  # «Пропустить» при правке позиции — оставляем прежнее значение
    elif base in ("size", "material", "link"):
        if value == "keep":
            value = default.get(base, "")
        if base == "link" and value and not re.match(r"^https?://", value):
            value = "https://" + value
        t[base] = value or ""
    elif base == "price":
        if value == "keep":
            n = default.get("price")
        else:
            n = _num(value or "")
        if n is None or n < 0:
            return "Введите цену числом, например 48000."
        t["unit_price"] = int(round(n))
    elif base == "discount":
        n = _num(value or "0")
        if n is None or not 0 <= n < 100:
            return "Введите скидку в процентах, например 5."
        c["discount"] = float(n)
    elif step in ("production", "services"):
        if value:
            k[step] = value
    elif step == "valid":
        if value:
            n = _num(value)
            if not n or not 1 <= n <= 90:
                return "Введите число дней, например 7."
            k["valid_days"] = int(n)
    if not popped:
        queue.pop(0)
    await state.update_data(kp=k, cur=c, queue=queue)
    return None


async def _answer(target: types.Message, state: FSMContext, uid: int, value: str | None) -> None:
    error = await _apply(target, state, uid, value)
    if error:
        data = await state.get_data()
        text, buttons, skip = _prompt(data["queue"][0], data["kp"], data.get("cur") or {})
        await target.answer(error, reply_markup=_markup(data["queue"][0], buttons, skip))
        return
    await _next(target, state, uid)


# --- вход ----------------------------------------------------------------------------


@router.callback_query(F.data == "menu:kp")
async def cb_kp(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(KPForm.run)
    await state.update_data(kp=_kp_new(profiles.get(call.from_user.id)), queue=list(HEAD), mode="head", cur=None, terms_done=False)
    await show(
        call.message,
        "📋 <b>Коммерческое предложение</b>\n"
        "Отвечайте на вопросы по очереди — в конце пришлю готовый PDF в фирменном стиле Creatica.",
    )
    await call.answer()
    await _next(call.message, state, call.from_user.id)


@router.callback_query(F.data.startswith("kp:v:") & (F.data != "kp:v:manual"), KPForm.run)
async def cb_value(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data["queue"]:
        await call.answer("Этот вопрос уже пройден")
        return
    await call.answer()
    await _answer(call.message, state, call.from_user.id, call.data[len("kp:v:"):])


@router.callback_query(F.data == "kp:skip", KPForm.run)
async def cb_skip(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data["queue"]:
        await call.answer()
        return
    step = data["queue"][0]
    if step in ("kind", "customer", "customer_phone", "query", "pick", "qty", "price", "discount"):
        await call.answer("Этот вопрос нужно заполнить", show_alert=True)
        return
    await call.answer("Пропущено")
    await _answer(call.message, state, call.from_user.id, None)


@router.message(KPForm.run, F.text)
async def msg_text(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data["queue"]
    if not queue:
        await message.answer("Выберите действие кнопкой ниже.")
        return
    step = queue[0]
    text = message.text.strip()
    if step in ("query", "alt_query"):
        await _search(message, state, text, step)
        return
    if step in ("pick", "alt_pick", "kind", "payments"):
        await message.answer("Выберите вариант кнопкой.")
        return
    if step in ("photo", "alt_photo"):
        await message.answer("Пришлите картинку или нажмите «Пропустить».")
        return
    await _answer(message, state, message.from_user.id, text)


# --- поиск в прайсе ------------------------------------------------------------------


async def _search(message: types.Message, state: FSMContext, query: str, step: str) -> None:
    if len(query) < 2:
        await message.answer("Напишите название хотя бы из двух букв.")
        return
    status = await message.answer("🔎 Ищу в прайсе...")
    try:
        items = await pricelist.search(query)
    except pricelist.PriceError as exc:
        await status.edit_text(f"⚠ Не удалось прочитать прайс: {exc}")
        return
    b = InlineKeyboardBuilder()
    if not items:
        b.row(InlineKeyboardButton(text="✍ Заполнить вручную", callback_data="kp:v:manual"))
        await status.edit_text(f"«{query}» в прайсе не найдено. Попробуйте другое название или заполните вручную.",
                               reply_markup=b.as_markup())
        return
    data = await state.get_data()
    queue = data["queue"]
    queue[0] = "alt_pick" if step == "alt_query" else "pick"
    await state.update_data(queue=queue, results=[asdict(i) for i in items])
    for i, item in enumerate(items):
        b.row(InlineKeyboardButton(text=item.label[:60], callback_data=f"kp:pick:{i}"))
    b.row(InlineKeyboardButton(text="✍ Заполнить вручную", callback_data="kp:v:manual"))
    await status.edit_text("Нашёл. Выберите позицию:", reply_markup=b.as_markup())


@router.callback_query(F.data == "kp:v:manual", KPForm.run)
async def cb_manual(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data["queue"]
    if not queue or queue[0] not in ("query", "alt_query", "pick", "alt_pick"):
        await call.answer("Этот вопрос уже пройден")
        return
    queue[0] = "alt_title" if queue[0].startswith("alt") else "title"
    await state.update_data(queue=queue)
    await call.answer()
    await _next(call.message, state, call.from_user.id)


@router.callback_query(F.data.startswith("kp:pick:"), KPForm.run)
async def cb_pick(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data["queue"]
    if not queue or queue[0] not in ("pick", "alt_pick"):
        await call.answer("Этот вопрос уже пройден")
        return
    idx = int(call.data.split(":")[2])
    results = data.get("results", [])
    if idx >= len(results):
        await call.answer("Список устарел, повторите поиск", show_alert=True)
        return
    raw = dict(results[idx])
    if raw.get("dims"):
        raw["dims"] = tuple(raw["dims"])
    item = pricelist.Item(**raw)
    try:
        item = await pricelist.enrich(item)
    except pricelist.PriceError:
        pass
    alt = queue[0] == "alt_pick"
    c = data["cur"]
    t = _target(c, alt)
    t["title"] = item.title
    t["default"] = {
        "size": _dims(item),
        "price": int(round(item.price_from)) if item.price_from else 0,
    }
    queue.pop(0)
    await state.update_data(cur=c, queue=queue)
    await show(call.message, f"Выбрано: <b>{item.title}</b>")
    await call.answer()
    await _next(call.message, state, call.from_user.id)


# --- фото --------------------------------------------------------------------------


async def _fetch(message: types.Message) -> bytes | None:
    """Скачивает присланное фото/картинку. None — если это не картинка."""
    file = None
    if message.photo:
        file = message.photo[-1]
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file = message.document
    if file is None:
        return None
    buf = io.BytesIO()
    await message.bot.download(file, destination=buf)
    return buf.getvalue()


@router.message(KPForm.run, F.photo | F.document)
async def msg_photo(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data["queue"]
    if not queue or queue[0] not in ("photo", "alt_photo"):
        await message.answer("Сейчас фото не требуется — ответьте на вопрос выше.")
        return
    raw = await _fetch(message)
    jpeg = prep_photo(raw)
    if not jpeg:
        await message.answer("Не получилось прочитать картинку. Пришлите фото ещё раз или нажмите «Пропустить».")
        return
    c = data["cur"]
    _target(c, queue[0].startswith("alt_"))["photo"] = jpeg
    queue.pop(0)
    await state.update_data(cur=c, queue=queue)
    await _next(message, state, message.from_user.id)


# --- позиции, условия ----------------------------------------------------------------


@router.callback_query(F.data.startswith("kp:more:"), KPForm.run)
async def cb_more(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data["mode"] != "more":
        await call.answer("Уже выбрано")
        return
    await call.answer()
    if call.data.endswith("add"):
        await _start_item(call.message, state)
        return
    if data.get("terms_done"):
        await _preview(call.message, state)
        return
    k = data["kp"]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✅ Оставить как есть", callback_data="kp:terms:keep"))
    b.row(InlineKeyboardButton(text="✏ Изменить условия", callback_data="kp:terms:edit"))
    await state.update_data(mode="terms_choice")
    await call.message.answer(
        "📑 <b>Условия по умолчанию:</b>\n"
        f"Сроки изготовления: {k['production']}\n"
        f"Дополнительные услуги: {k['services']}\n"
        f"Действует: {k['valid_days']} дн.\n"
        f"Оплата: {len([1 for p in k['payments'] if p])} способов (наличные, карта, перевод, QR, счёт)",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data.startswith("kp:terms:"), KPForm.run)
async def cb_terms(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data["mode"] != "terms_choice":
        await call.answer("Уже выбрано")
        return
    await call.answer()
    await state.update_data(terms_done=True)
    if call.data.endswith("keep"):
        await _preview(call.message, state)
        return
    await state.update_data(mode="terms", queue=list(TERMS))
    await _next(call.message, state, call.from_user.id)


@router.callback_query(F.data.startswith("kp:pay:"), KPForm.run)
async def cb_pay(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    k = data["kp"]
    i = int(call.data.split(":")[2])
    k["payments"][i] = not k["payments"][i]
    await state.update_data(kp=k)
    await call.answer()
    await _pay_screen(call.message, k, edit=True)


@router.callback_query(F.data == "kp:paydone", KPForm.run)
async def cb_paydone(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data["queue"]
    if not queue or queue[0] != "payments":
        await call.answer("Уже выбрано")
        return
    if not any(data["kp"]["payments"]):
        await call.answer("Отметьте хотя бы один способ", show_alert=True)
        return
    queue.pop(0)
    await state.update_data(queue=queue)
    await call.answer()
    await _next(call.message, state, call.from_user.id)


# --- предпросмотр и файл -----------------------------------------------------------


def _summary(k: dict) -> str:
    kp = _build(k)
    lines = [
        "📋 <b>КП готово к выпуску</b>",
        f"Заказчик: {kp.customer or '—'}{', ' + kp.client_phone if kp.client_phone else ''}",
        f"Менеджер: {kp.manager or '—'}{', ' + kp.phone if kp.phone else ''}",
        f"Действует до: {date_ru(kp.valid_until)}",
        "",
    ]
    for n, it in enumerate(kp.items, 1):
        disc = f", −{it.discount:g}%" if it.discount else ""
        lines.append(
            f"{n}. {it.title} — {it.qty} × <s>{rub(it.list_unit_price)}</s> {rub(it.unit_price)}{disc}"
            f" = <b>{rub(it.total())}</b>"
        )
        if it.alt:
            lines.append(f"    альтернатива: {it.alt.title} — {rub(it.alt.total(it.qty))}")
    lines.append("")
    lines.append(f"<b>Итого: {rub(kp.total())}</b>")
    return "\n".join(lines)


def _preview_markup() -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📄 Получить PDF", callback_data="kp:make"))
    b.row(
        InlineKeyboardButton(text="✏ Изменить позицию", callback_data="kp:items"),
        InlineKeyboardButton(text="➕ Позиция", callback_data="kp:addmore"),
    )
    b.row(
        InlineKeyboardButton(text="✏ Заказчик / менеджер", callback_data="kp:editcust"),
        InlineKeyboardButton(text="✏ Условия", callback_data="kp:editterms"),
    )
    b.row(InlineKeyboardButton(text="🆕 Новое КП", callback_data="menu:kp"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    return b.as_markup()


async def _preview(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    k = data["kp"]
    await state.update_data(mode="preview", queue=[])
    text = _summary(k)
    if not k["items"]:
        text += "\n\nПозиций пока нет — добавьте хотя бы одну."
    png = None
    if k["items"]:
        try:
            png = build_kp_preview(_build(k))
        except Exception:
            png = None
    if png:
        await target.answer_photo(BufferedInputFile(png, filename="kp_preview.png"), caption=text[:1020],
                                  reply_markup=_preview_markup())
    else:
        await target.answer(text, reply_markup=_preview_markup())


@router.callback_query(F.data == "kp:addmore", KPForm.run)
async def cb_add2(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _start_item(call.message, state)


@router.callback_query(F.data == "kp:dellast", KPForm.run)
async def cb_dellast(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    k = data["kp"]
    if k["items"]:
        k["items"].pop()
    await state.update_data(kp=k)
    await call.answer("Удалено")
    await _preview(call.message, state)


# --- правка отдельной позиции ---------------------------------------------------------

# (подпись кнопки, шаги опроса) — что можно поменять в позиции, не пересоздавая КП
ITEM_FIELDS = [
    ("Количество", ["qty"]), ("Цена", ["price"]), ("Скидка", ["discount"]),
    ("Размеры", ["size"]), ("Материал / ткань", ["material"]), ("Ссылка", ["link"]),
    ("Фото", ["photo"]), ("Название", ["title"]),
]
ALT_FIELDS = [
    ("Альтернатива: цена", ["alt_price"]), ("Альтернатива: материал", ["alt_material"]),
    ("Альтернатива: размеры", ["alt_size"]), ("Альтернатива: фото", ["alt_photo"]),
]


def _item_line(n: int, it: dict) -> str:
    return f"{n}. {it['title'] or 'Без названия'} — {it['qty']} шт × {rub(it.get('unit_price') or 0)}"


@router.callback_query(F.data == "kp:items", KPForm.run)
async def cb_items(call: types.CallbackQuery, state: FSMContext) -> None:
    k = (await state.get_data())["kp"]
    await call.answer()
    if not k["items"]:
        await call.message.answer("Позиций пока нет.")
        return
    b = InlineKeyboardBuilder()
    for i, it in enumerate(k["items"]):
        b.row(InlineKeyboardButton(text=f"✏ {i + 1}. {(it['title'] or 'Без названия')[:40]}", callback_data=f"kp:it:{i}"),
              InlineKeyboardButton(text="🗑", callback_data=f"kp:itdel:{i}"))
    b.row(InlineKeyboardButton(text="⬅ Назад к КП", callback_data="kp:back"))
    await call.message.answer("Какую позицию изменить?\n\n" + "\n".join(
        _item_line(i + 1, it) for i, it in enumerate(k["items"])), reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("kp:it:"), KPForm.run)
async def cb_item(call: types.CallbackQuery, state: FSMContext) -> None:
    k = (await state.get_data())["kp"]
    i = int(call.data.split(":")[2])
    if i >= len(k["items"]):
        await call.answer("Список устарел", show_alert=True)
        return
    await call.answer()
    it = k["items"][i]
    fields = ITEM_FIELDS + (ALT_FIELDS if it.get("alt") else [])
    b = InlineKeyboardBuilder()
    for j, (label, _) in enumerate(fields):
        b.button(text=label, callback_data=f"kp:itf:{i}:{j}")
    b.adjust(2)
    b.row(InlineKeyboardButton(text="🔄 Заполнить позицию заново", callback_data=f"kp:itnew:{i}"))
    b.row(InlineKeyboardButton(text="🗑 Удалить позицию", callback_data=f"kp:itdel:{i}"))
    b.row(InlineKeyboardButton(text="⬅ Назад к КП", callback_data="kp:back"))
    await call.message.answer(
        f"✏ <b>Позиция {i + 1}</b>\n{_item_line(i + 1, it)}"
        + (f"\nРазмеры: {it['size']}" if it.get("size") else "")
        + (f"\nМатериал: {it['material']}" if it.get("material") else "")
        + (f"\nСкидка: {it['discount']:g}%" if it.get("discount") else "")
        + "\n\nЧто изменить?",
        reply_markup=b.as_markup(),
    )


async def _edit_item(call: types.CallbackQuery, state: FSMContext, i: int, queue: list[str], fresh: bool) -> None:
    k = (await state.get_data())["kp"]
    if i >= len(k["items"]):
        await call.answer("Список устарел", show_alert=True)
        return
    await call.answer()
    cur = _item_new() if fresh else copy.deepcopy(k["items"][i])
    await state.update_data(mode="itemedit", edit_index=i, cur=cur, queue=queue)
    await _next(call.message, state, call.from_user.id)


@router.callback_query(F.data.startswith("kp:itf:"), KPForm.run)
async def cb_item_field(call: types.CallbackQuery, state: FSMContext) -> None:
    _, _, i, j = call.data.split(":")
    i, j = int(i), int(j)
    k = (await state.get_data())["kp"]
    fields = ITEM_FIELDS + (ALT_FIELDS if i < len(k["items"]) and k["items"][i].get("alt") else [])
    if j >= len(fields):
        await call.answer("Список устарел", show_alert=True)
        return
    await _edit_item(call, state, i, list(fields[j][1]), fresh=False)


@router.callback_query(F.data.startswith("kp:itnew:"), KPForm.run)
async def cb_item_new(call: types.CallbackQuery, state: FSMContext) -> None:
    k = (await state.get_data())["kp"]
    queue = list(ITEM_VIS if k["kind"] == "vis" else ITEM_STD)
    await _edit_item(call, state, int(call.data.split(":")[2]), queue, fresh=True)


@router.callback_query(F.data.startswith("kp:itdel:"), KPForm.run)
async def cb_item_del(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    k = data["kp"]
    i = int(call.data.split(":")[2])
    if i >= len(k["items"]):
        await call.answer("Список устарел", show_alert=True)
        return
    k["items"].pop(i)
    await state.update_data(kp=k)
    await call.answer(f"Позиция {i + 1} удалена")
    await _preview(call.message, state)


@router.callback_query(F.data == "kp:editcust", KPForm.run)
async def cb_editcust(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(mode="edit", queue=["customer", "customer_phone", "manager", "phone"])
    await call.answer()
    await _next(call.message, state, call.from_user.id)


@router.callback_query(F.data == "kp:editterms", KPForm.run)
async def cb_editterms(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(mode="edit", queue=list(TERMS))
    await call.answer()
    await _next(call.message, state, call.from_user.id)


@router.callback_query(F.data == "kp:make", KPForm.run)
async def cb_make(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    k = data["kp"]
    if not k["items"]:
        await call.answer("Добавьте хотя бы одну позицию", show_alert=True)
        return
    await call.answer("Готовлю PDF…")
    kp = _build(k)
    pdf = build_kp_pdf(kp)
    filename = f"KP_Creatica_{kp.number}.pdf"
    contacts = " · ".join(x for x in (kp.phone, kp.telegram) if x)
    analytics.track(
        call.message.chat.id, "kp", kp.customer,
        data={"sum": kp.total(), "items": len(kp.items), "number": kp.number, "customer": kp.customer,
              "kind": kp.kind, "state": _json_safe(k)},
        document=(pdf, filename),
        caption=(
            f"📋 КП №{kp.number}\n"
            f"👤 Менеджер: {kp.manager or '—'}" + (f" · {contacts}" if contacts else "") + "\n"
            f"🏬 Шоу-рум: {profiles.norm_showroom(k.get('showroom', '')) or '—'}\n"
            f"Заказчик: {kp.customer or '—'}" + (f" · {kp.client_phone}" if kp.client_phone else "") + "\n"
            f"Позиций: {len(kp.items)} · Итого: {rub(kp.total())}"
        ),
    )
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✏ Изменить", callback_data="kp:back"))
    b.row(InlineKeyboardButton(text="🆕 Новое КП", callback_data="menu:kp"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await call.message.answer_document(
        BufferedInputFile(pdf, filename=filename),
        caption=f"📋 КП для: {kp.customer or 'заказчика'} — итого {rub(kp.total())}",
        reply_markup=b.as_markup(),
    )
    asyncio.create_task(_send_fabric_info(call.message, k))  # справка по тканям — следом, не задерживая PDF


async def _send_fabric_info(target: types.Message, k: dict) -> None:
    """Ткани из поля «Материал» позиций КП: цена, категория, наличие (и выбранного цвета)."""
    texts = []
    for it in k["items"]:
        texts.append(it.get("material") or "")
        if it.get("alt"):
            texts.append(it["alt"].get("material") or "")
    text = "\n".join(t for t in texts if t)
    if not text:
        return
    try:
        price, stock = await asyncio.wait_for(fabrics.load(config.PRICE_CSV_URL, config.STOCK_CSV_URL), timeout=25)
    except Exception:  # noqa: BLE001 — справка необязательна, КП уже отправлено
        log.warning("КП: не удалось загрузить ткани для справки", exc_info=True)
        return
    blocks, seen = [], set()
    for line in texts:
        for fabric, rest in fabrics.find_in_text(price, line):
            if (fabric.name, fabric.supplier, tuple(rest)) in seen:
                continue
            seen.add((fabric.name, fabric.supplier, tuple(rest)))
            rows = fabrics.stock_for(fabric, stock, price)
            has_stock = any(s.supplier == fabric.supplier for s in stock)
            block = fabrics.format_fabric(fabric, rows, has_stock, limit=1500)
            color = fabrics.color_line(rows, rest) if rows else ""
            if color:
                head, _, tail = block.partition("\n")
                block = f"{head}\n{color}\n{tail}"
            blocks.append(block)
    if not blocks:
        return
    text = "🧵 <b>Ткани в этом КП</b>\n\n" + "\n\n".join(blocks[:4])
    if len(text) <= 4000:
        await target.answer(text)
        return
    await target.answer("🧵 <b>Ткани в этом КП</b>")  # длинно — каждая ткань отдельным сообщением
    for block in blocks[:4]:
        await target.answer(block)


@router.callback_query(F.data == "kp:back", KPForm.run)
async def cb_back(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _preview(call.message, state)


# --- ранее созданные КП: список и повторное редактирование -------------------------------


@router.callback_query(F.data == "menu:mykp")
async def cb_my_kp(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    uid = call.from_user.id
    mine = sorted(
        (e for e in analytics.EVENTS if e.get("kind") == "kp" and e["uid"] == uid and e["data"].get("state")
         and not e["data"].get("closed")),
        key=lambda e: -e["ts"],
    )[:15]
    b = InlineKeyboardBuilder()
    if not mine:
        b.row(InlineKeyboardButton(text="📋 Создать КП", callback_data="menu:kp"))
        b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
        await show(call.message, "📄 У вас пока нет оформленных КП.", reply_markup=b.as_markup())
        await call.answer()
        return
    await state.update_data(history=mine)
    for i, e in enumerate(mine):
        d = e["data"]
        label = f"№{d.get('number', '')} · {d.get('customer') or 'без заказчика'} · {rub(d.get('sum', 0))}"
        b.row(
            InlineKeyboardButton(text=label[:34], callback_data=f"kp:hist:{i}"),
            InlineKeyboardButton(text="✅", callback_data=f"kp:won:{i}"),
            InlineKeyboardButton(text="❌", callback_data=f"kp:lost:{i}"),
        )
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await show(
        call.message,
        "📄 <b>Ваши КП</b> — выберите, чтобы поправить и выпустить заново." "\n\n"
        "✅ — клиент купил (КП уйдёт из списка), ❌ — клиент отказался (нужно написать причину):",
        reply_markup=b.as_markup(),
    )
    await call.answer()


def _find_event(uid: int, ts: int, number) -> dict | None:
    return next(
        (e for e in analytics.EVENTS
         if e.get("kind") == "kp" and e["uid"] == uid and e["ts"] == ts and e["data"].get("number") == number),
        None,
    )


def _close_kp(ev: dict, outcome: str, reason: str = "") -> None:
    ev["data"]["closed"] = outcome
    if reason:
        ev["data"]["lost_reason"] = reason
    analytics._persist_soon()
    d = ev["data"]
    head = "✅ КП превращено в продажу" if outcome == "won" else "❌ КП: клиент отказался"
    text = (
        f"{head} — {ev.get('name') or 'менеджер'}" + "\n"
        + f"№{d.get('number', '')} · <b>{d.get('customer') or 'без заказчика'}</b> · {rub(d.get('sum', 0))}"
    )
    if reason:
        text += "\n" + f"Причина: {reason}"
    analytics.notify(text)


@router.callback_query(F.data.startswith("kp:won:"))
async def cb_hist_won(call: types.CallbackQuery, state: FSMContext) -> None:
    mine = (await state.get_data()).get("history") or []
    idx = int(call.data.split(":")[2])
    ev = _find_event(call.from_user.id, mine[idx]["ts"], mine[idx]["data"].get("number")) if idx < len(mine) else None
    if not ev:
        await call.answer("Список устарел, откройте «Мои КП» заново", show_alert=True)
        return
    _close_kp(ev, "won")
    await cb_my_kp(call, state)


@router.callback_query(F.data.startswith("kp:lost:"))
async def cb_hist_lost(call: types.CallbackQuery, state: FSMContext) -> None:
    mine = (await state.get_data()).get("history") or []
    idx = int(call.data.split(":")[2])
    ev = _find_event(call.from_user.id, mine[idx]["ts"], mine[idx]["data"].get("number")) if idx < len(mine) else None
    if not ev:
        await call.answer("Список устарел, откройте «Мои КП» заново", show_alert=True)
        return
    await state.set_state(MyKP.lost_reason)
    await state.update_data(ts=ev["ts"], number=ev["data"].get("number"))
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:mykp"))
    await call.answer()
    await call.message.answer(
        f"❌ КП №{ev['data'].get('number', '')} · {ev['data'].get('customer') or 'без заказчика'}" + "\n"
        + "Напишите причину отказа:",
        reply_markup=b.as_markup(),
    )


@router.message(MyKP.lost_reason, F.text)
async def msg_kp_lost_reason(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    reason = message.text.strip()
    if not reason:
        await message.answer("Напишите причину текстом.")
        return
    ev = _find_event(message.chat.id, data["ts"], data["number"])
    await state.clear()
    if ev:
        _close_kp(ev, "lost", reason)
        await message.answer("❌ Отказ записан, КП убрано из списка.")
    await message.answer("Откройте «📄 Мои КП», чтобы увидеть обновлённый список.")


@router.callback_query(F.data.startswith("kp:hist:"))
async def cb_hist_open(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    mine = data.get("history") or []
    idx = int(call.data.split(":")[2])
    if idx >= len(mine):
        await call.answer("Список устарел, откройте «Мои КП» заново", show_alert=True)
        return
    k = _from_history(call.from_user.id, mine[idx]["data"]["state"])
    await state.set_state(KPForm.run)
    await state.update_data(kp=k, cur=None, mode="preview", queue=[], terms_done=True)
    await call.answer()
    await call.message.answer(
        "✏ Открыто для правки. Фото позиций пришлось сбросить — при необходимости пришлите заново "
        "(удалите позицию и добавьте её снова с фото)."
    )
    await _preview(call.message, state)
