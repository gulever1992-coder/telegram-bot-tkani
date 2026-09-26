"""Ценник на мебель: поиск позиции в прайсе -> вопросы -> предпросмотр -> PDF / Word (А4)."""

from __future__ import annotations

import re
from dataclasses import asdict

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import analytics
import keyboards as kb
from utils.ui import show
from utils import pricelist
from utils.tag import Tag, build_tag_docx, build_tag_pdf, build_tag_preview, money

router = Router()

FRAME_OPTIONS = ["березовая фанера", "массив дерева", "металл"]
FILLER_OPTIONS = ["ппу, лебяжий пух", "ппу", "холлофайбер"]

FIELD_TITLES = {
    "title": "Название",
    "dims": "Габариты",
    "article": "Артикул",
    "frame": "Каркас",
    "filler": "Наполнитель",
    "price": "Цена образца",
    "price_from": "Цена «от»",
}


class TagForm(StatesGroup):
    query = State()
    pick = State()
    ask = State()
    edit = State()


# --- служебное ----------------------------------------------------------------


def _to_int(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text.split(",")[0].split(".")[0])
    return int(digits) if digits else None


def _numbers(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text or "")]


def _new_tag(item: pricelist.Item) -> dict:
    nums = _numbers(item.size_raw)
    if item.dims:
        nums = list(item.dims)
    length, depth, height = (nums + ["", "", ""])[:3] if len(nums) >= 2 else ("", "", "")
    price_from = int(round(item.price_from)) if item.price_from else None
    return {
        "title": item.title,
        "length": str(length) if length != "" else "",
        "depth": str(depth) if depth != "" else "",
        "height": str(height) if height != "" else "",
        "frame": "",
        "filler": "",
        "article": item.article or "",
        "price": None,
        "price_from": price_from,
        "list_price": price_from,
        "asked": [],
    }


def _tag(d: dict) -> Tag:
    return Tag(
        title=d["title"], length=d["length"], depth=d["depth"], height=d["height"],
        frame=d["frame"], filler=d["filler"], article=d["article"],
        price=d["price_from"], sample_price=d["price"],
    )


def _dims_missing(d: dict) -> bool:
    return not (d["length"] and d["depth"] and d["height"])


def _next_field(d: dict) -> str | None:
    asked = d["asked"]
    if "price" not in asked:
        return "price"
    if not d["price_from"] and "price_from" not in asked:
        return "price_from"
    if _dims_missing(d) and "dims" not in asked:
        return "dims"
    if not d["article"] and "article" not in asked:
        return "article"
    if "frame" not in asked:
        return "frame"
    if "filler" not in asked:
        return "filler"
    return None


def _prompt(d: dict, field: str) -> tuple[str, list[tuple[str, str]]]:
    if field == "price":
        hint = f"\nПо прайсу цена «от»: <b>{money(d['list_price'])} ₽</b> — она печатается крупно." if d["list_price"] else ""
        return (
            "💰 <b>Цена образца</b> — по какой цене продаётся выставленный образец, руб. (например 190000)."
            "\nОна печатается мелко над крупной ценой с подписью «Цена образца»." + hint,
            [],
        )
    if field == "price_from":
        return (
            "🏷 <b>Цена «от»</b> — минимальная цена позиции по прайсу, руб. Она печатается крупно внизу. "
            "В прайсе её не нашёл — введите числом:",
            [],
        )
    if field == "dims":
        have = [d["length"], d["depth"], d["height"]]
        known = " / ".join(x or "—" for x in have)
        return (
            f"📐 В прайсе нет полных размеров (сейчас: {known}).\n"
            "Введите длину, глубину и высоту в мм через пробел, например: 1190 1000 780",
            [],
        )
    if field == "article":
        return "🔢 Артикул не найден в прайсе. Введите артикул:", []
    if field == "frame":
        return "🪵 Каркас (материал):", [(o, f"tag:opt:frame:{i}") for i, o in enumerate(FRAME_OPTIONS)]
    if field == "filler":
        return "🛋 Наполнитель:", [(o, f"tag:opt:filler:{i}") for i, o in enumerate(FILLER_OPTIONS)]
    return "Введите значение:", []


def _ask_markup(buttons: list[tuple[str, str]]) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, cb in buttons:
        builder.row(InlineKeyboardButton(text=label, callback_data=cb))
    builder.row(InlineKeyboardButton(text="⏭ Пропустить", callback_data="tag:skip"))
    builder.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return builder.as_markup()


async def _ask(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    d = data["tag"]
    field = _next_field(d)
    if field is None:
        await _preview(target, state)
        return
    text, buttons = _prompt(d, field)
    await state.set_state(TagForm.ask)
    await state.update_data(field=field)
    await target.answer(text, reply_markup=_ask_markup(buttons))


def _summary(d: dict) -> str:
    def v(x):
        return x if x else "—"

    sample = f"{money(d['price'])} ₽" if d["price"] else "—"
    big = f"от {money(d['price_from'])} ₽" if d["price_from"] else "—"
    inst = f"\nВ рассрочку: от {_tag(d).installment} руб./месяц" if d["price_from"] else ""
    lines = [
        "🏷 <b>Ценник — проверьте данные</b>",
        f"<b>{d['title']}</b>",
        f"Габариты, мм: {v(d['length'])} × {v(d['depth'])} × {v(d['height'])}  (Д × Г × В)",
        f"Каркас: {v(d['frame'])}",
        f"Наполнитель: {v(d['filler'])}",
        f"Артикул: {v(d['article'])}",
        f"Цена образца (мелко сверху): <b>{sample}</b>",
        f"Цена «от» (крупно): <b>{big}</b>{inst}",
    ]
    return "\n".join(lines)


def _preview_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📄 PDF", callback_data="tag:make:pdf"),
        InlineKeyboardButton(text="📝 Word", callback_data="tag:make:docx"),
        InlineKeyboardButton(text="📦 Оба", callback_data="tag:make:both"),
    )
    edits = ["title", "dims", "article", "frame", "filler", "price", "price_from"]
    for i in range(0, len(edits), 2):
        builder.row(
            *[
                InlineKeyboardButton(text=f"✏ {FIELD_TITLES[f]}", callback_data=f"tag:edit:{f}")
                for f in edits[i : i + 2]
            ]
        )
    builder.row(InlineKeyboardButton(text="🏷 Новый ценник", callback_data="menu:tag"))
    builder.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    return builder.as_markup()


async def _preview(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(TagForm.edit)
    await state.update_data(field=None)
    d = data["tag"]
    png = None
    try:
        png = build_tag_preview(_tag(d))
    except Exception:  # превью необязательно — без него бот работает
        png = None
    if png:
        await target.answer_photo(
            BufferedInputFile(png, filename="preview.png"), caption=_summary(d), reply_markup=_preview_markup()
        )
    else:
        await target.answer(_summary(d), reply_markup=_preview_markup())


def _apply(d: dict, field: str, value) -> None:
    """Записывает ответ в ценник. value=None — «пропущено»."""
    if field == "dims":
        if value:
            d["length"], d["depth"], d["height"] = (str(n) for n in value)
    elif field in ("title", "article", "frame", "filler"):
        if value is not None:
            d[field] = value
    elif field in ("price", "price_from"):
        d[field] = value or None
    if field not in d["asked"]:
        d["asked"].append(field)


def _parse(field: str, text: str):
    """(значение, None) или (None, сообщение об ошибке)."""
    text = text.strip()
    if field == "dims":
        nums = _numbers(text)
        if len(nums) == 3:
            return nums, None
        return None, "Нужны три числа через пробел: длина глубина высота, например 1190 1000 780."
    if field in ("price", "price_from"):
        value = _to_int(text)
        if value and value > 0:
            return value, None
        return None, "Введите цену числом, например 39800."
    if not text:
        return None, "Введите текст."
    return text, None


# --- вход и поиск ---------------------------------------------------------------


@router.callback_query(F.data == "menu:tag")
async def cb_tag(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(TagForm.query)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✍ Заполнить вручную", callback_data="tag:manual"))
    builder.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await show(call.message, 
        "🏷 <b>Ценник на мебель (А4)</b>\n"
        "Напишите название позиции из прайса, например: <i>кресло Джун</i>, <i>диван Александр</i>, <i>стул Аура</i>.",
        reply_markup=builder.as_markup(),
    )
    await call.answer()


@router.callback_query(F.data == "tag:manual", StateFilter(TagForm.query, TagForm.pick))
async def cb_manual(call: types.CallbackQuery, state: FSMContext) -> None:
    item = pricelist.Item(title="", source="ручной ввод")
    await state.update_data(tag=_new_tag(item), results=[])
    await state.set_state(TagForm.edit)
    await state.update_data(field="title")
    await show(call.message, "✍ Введите название для ценника (например: Кресло DJUN):", reply_markup=kb.cancel_keyboard())
    await call.answer()


@router.message(TagForm.query, F.text)
async def msg_query(message: types.Message, state: FSMContext) -> None:
    query = message.text.strip()
    if len(query) < 2:
        await message.answer("Напишите название хотя бы из двух букв.")
        return
    status = await message.answer("🔎 Ищу в прайсе...")
    try:
        items = await pricelist.search(query)
    except pricelist.PriceError as exc:
        await status.edit_text(f"⚠ Не удалось прочитать прайс: {exc}", reply_markup=kb.cancel_keyboard())
        return
    builder = InlineKeyboardBuilder()
    if not items:
        builder.row(InlineKeyboardButton(text="✍ Заполнить вручную", callback_data="tag:manual"))
        builder.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
        await status.edit_text(
            f"Позиция «{query}» в прайсе не найдена. Попробуйте другое название или заполните вручную.",
            reply_markup=builder.as_markup(),
        )
        return
    await state.update_data(results=[asdict(i) for i in items])
    await state.set_state(TagForm.pick)
    for i, item in enumerate(items):
        builder.row(InlineKeyboardButton(text=item.label[:60], callback_data=f"tag:pick:{i}"))
    builder.row(InlineKeyboardButton(text="🔎 Искать другое", callback_data="menu:tag"))
    builder.row(InlineKeyboardButton(text="✍ Заполнить вручную", callback_data="tag:manual"))
    await status.edit_text("Нашёл. Выберите нужную позицию:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("tag:pick:"), TagForm.pick)
async def cb_pick(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
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
    await state.update_data(tag=_new_tag(item))
    await show(call.message, f"Выбрано: <b>{item.title}</b>")
    await call.answer()
    await _ask(call.message, state)


# --- ответы на вопросы -----------------------------------------------------------


@router.callback_query(F.data.startswith("tag:opt:"), TagForm.ask)
async def cb_option(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    _, _, field, idx = call.data.split(":")
    options = FRAME_OPTIONS if field == "frame" else FILLER_OPTIONS
    if data.get("field") != field:
        await call.answer("Этот вопрос уже пройден")
        return
    d = data["tag"]
    _apply(d, field, options[int(idx)])
    await state.update_data(tag=d)
    await call.answer()
    await _ask(call.message, state)


@router.callback_query(F.data == "tag:skip", TagForm.ask)
async def cb_skip(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    d, field = data["tag"], data.get("field")
    if not field:
        await call.answer()
        return
    _apply(d, field, None)
    await state.update_data(tag=d)
    await call.answer("Пропущено")
    await _ask(call.message, state)


@router.message(TagForm.ask, F.text)
async def msg_answer(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    d, field = data["tag"], data.get("field")
    value, error = _parse(field, message.text)
    if error:
        text, buttons = _prompt(d, field)
        await message.answer(error, reply_markup=_ask_markup(buttons))
        return
    _apply(d, field, value)
    await state.update_data(tag=d)
    await _ask(message, state)


# --- редактирование после предпросмотра -------------------------------------------


@router.callback_query(F.data.startswith("tag:edit:"), TagForm.edit)
async def cb_edit(call: types.CallbackQuery, state: FSMContext) -> None:
    field = call.data.split(":")[2]
    data = await state.get_data()
    d = data["tag"]
    await state.update_data(field=field)
    await call.answer()
    if field == "price_from":
        builder = InlineKeyboardBuilder()
        if d["list_price"]:
            builder.row(InlineKeyboardButton(text=f"Вернуть по прайсу: {money(d['list_price'])}", callback_data="tag:setlist"))
        builder.row(InlineKeyboardButton(text="⬅ К ценнику", callback_data="tag:back"))
        await call.message.answer("Введите цену «от» (крупная цена) числом:", reply_markup=builder.as_markup())
        return
    prompts = {
        "title": "Введите новое название:",
        "dims": "Введите длину, глубину, высоту в мм через пробел (например: 1190 1000 780):",
        "article": "Введите артикул:",
        "frame": "Введите материал каркаса:",
        "filler": "Введите наполнитель:",
        "price": "Введите цену образца, руб. (печатается мелко сверху):",
    }
    builder = InlineKeyboardBuilder()
    if field in ("frame", "filler"):
        options = FRAME_OPTIONS if field == "frame" else FILLER_OPTIONS
        for i, o in enumerate(options):
            builder.row(InlineKeyboardButton(text=o, callback_data=f"tag:setopt:{field}:{i}"))
    builder.row(InlineKeyboardButton(text="⬅ К ценнику", callback_data="tag:back"))
    await call.message.answer(prompts[field], reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("tag:setopt:"), TagForm.edit)
async def cb_setopt(call: types.CallbackQuery, state: FSMContext) -> None:
    _, _, field, idx = call.data.split(":")
    options = FRAME_OPTIONS if field == "frame" else FILLER_OPTIONS
    d = (await state.get_data())["tag"]
    _apply(d, field, options[int(idx)])
    await state.update_data(tag=d)
    await call.answer()
    await _preview(call.message, state)


@router.callback_query(F.data == "tag:setlist", TagForm.edit)
async def cb_setlist(call: types.CallbackQuery, state: FSMContext) -> None:
    d = (await state.get_data())["tag"]
    _apply(d, "price_from", d["list_price"])
    await state.update_data(tag=d)
    await call.answer()
    await _preview(call.message, state)


@router.callback_query(F.data == "tag:back", TagForm.edit)
async def cb_back(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _preview(call.message, state)


@router.message(TagForm.edit, F.text)
async def msg_edit(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    d, field = data["tag"], data.get("field")
    if not field:
        await message.answer("Выберите действие кнопкой под ценником.")
        return
    value, error = _parse(field, message.text)
    if error:
        await message.answer(error)
        return
    _apply(d, field, value)
    await state.update_data(tag=d)
    # название, введённое вручную, продолжает вопросы; остальное — назад к ценнику
    if field == "title" and len(d["asked"]) == 1 and _next_field(d):
        await _ask(message, state)
        return
    await _preview(message, state)


# --- файлы ----------------------------------------------------------------------


def _file_stem(d: dict) -> str:
    article = re.sub(r"[^\w-]", "", d["article"], flags=re.ASCII)
    return f"Tag_{article}" if article else "Tag"


@router.callback_query(F.data.startswith("tag:make:"), TagForm.edit)
async def cb_make(call: types.CallbackQuery, state: FSMContext) -> None:
    d = (await state.get_data())["tag"]
    if not d["title"]:
        await call.answer("Сначала введите название", show_alert=True)
        return
    fmt = call.data.split(":")[2]
    await call.answer("Готовлю файл…")
    tag = _tag(d)
    stem = _file_stem(d)
    analytics.track(call.message.chat.id, "tag", d["title"], data={"format": fmt})
    after = InlineKeyboardBuilder()
    after.row(InlineKeyboardButton(text="✏ Изменить данные", callback_data="tag:back"))
    after.row(InlineKeyboardButton(text="🏷 Новый ценник", callback_data="menu:tag"))
    after.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    caption = f"🏷 Ценник: {d['title']}"
    if fmt in ("pdf", "both"):
        await call.message.answer_document(
            BufferedInputFile(build_tag_pdf(tag), filename=f"{stem}.pdf"),
            caption=caption + " (PDF)",
            reply_markup=after.as_markup() if fmt == "pdf" else None,
        )
    if fmt in ("docx", "both"):
        await call.message.answer_document(
            BufferedInputFile(build_tag_docx(tag), filename=f"{stem}.docx"),
            caption=caption + " (Word)",
            reply_markup=after.as_markup(),
        )
