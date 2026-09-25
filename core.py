"""Логика бота: меню, картинки (Nano Banana), поиск ткани, выплата дизайнеру.

Диалоги (FSM) хранятся в памяти работающей программы.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import re

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile

import analytics
import config
from utils.ui import show
import keyboards as kb
from refund_flow import router as refund_router
from tag_flow import router as tag_router
from kp_flow import router as kp_router
from onboarding import ProfileGate, router as onb_router
from utils import images as legacy_images
from utils import nano, sheets
from utils.agency import AgencyData, build_agency_package

WELCOME_TEXT = "👋 Привет! Я рабочий бот-помощник.\n\nВыберите действие в меню ниже:"
router = Router()
dp = Dispatcher()
dp.message.outer_middleware(ProfileGate())
dp.callback_query.outer_middleware(ProfileGate())
dp.include_router(onb_router)
dp.include_router(router)
dp.include_router(refund_router)
dp.include_router(tag_router)
dp.include_router(kp_router)

MSK = dt.timezone(dt.timedelta(hours=3))


def make_bot() -> Bot:
    return Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def secret_token() -> str:
    return hashlib.sha256(config.TELEGRAM_BOT_TOKEN.encode()).hexdigest()[:32]


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=kb.main_menu())


@router.callback_query(F.data == "menu:home")
async def cb_home(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call.message, WELCOME_TEXT, reply_markup=kb.main_menu())
    await call.answer()


# --- Картинки (Nano Banana) -------------------------------------------------


class ImageFlow(StatesGroup):
    up_furniture = State()
    up_fabric = State()
    up_note = State()
    in_furniture = State()
    in_room = State()
    in_note = State()
    text_prompt = State()


UPHOLSTERY_PROMPT = (
    "Edit the FIRST image: change the upholstery of the furniture to the fabric {fabric}. "
    "Reproduce the fabric's color, pattern, weave and texture accurately, at a realistic scale. "
    "Keep the furniture's shape, frame, legs, stitching, proportions, camera angle, background, "
    "lighting and shadows exactly as in the first image. Photorealistic result. {note}"
)
INTERIOR_PROMPT = (
    "Place the furniture from the FIRST image into the interior shown in the SECOND image. "
    "Match realistic scale, perspective, lighting, reflections and shadows to the room, and place it "
    "naturally on the floor. Do not change the furniture's design, color or fabric, and keep the room "
    "unchanged apart from the added furniture. Photorealistic result. {note}"
)


def _photo_ref(message: types.Message) -> tuple[str, str] | None:
    if message.photo:
        return message.photo[-1].file_id, "image/jpeg"
    doc = message.document
    if doc and (doc.mime_type or "").startswith("image/"):
        return doc.file_id, doc.mime_type
    return None


async def _download(bot: Bot, ref: tuple[str, str]) -> tuple[bytes, str]:
    buffer = await bot.download(ref[0])
    return buffer.read(), ref[1]


@router.callback_query(F.data == "menu:image")
async def cb_image(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call.message, "🎨 <b>Создать картинку</b>\nВыберите, что сделать:", reply_markup=kb.image_menu())
    await call.answer()


@router.callback_query(F.data == "img:upholstery")
async def cb_upholstery(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ImageFlow.up_furniture)
    await show(call.message, 
        "🛋 <b>Поменять обивку</b>\n\nШаг 1 из 3. Пришлите <b>фото мебели</b> (диван, кресло, стул...).",
        reply_markup=kb.cancel_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "img:interior")
async def cb_interior(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ImageFlow.in_furniture)
    await show(call.message, 
        "🏠 <b>Поставить мебель в интерьер</b>\n\nШаг 1 из 3. Пришлите <b>фото мебели</b>.",
        reply_markup=kb.cancel_keyboard(),
    )
    await call.answer()


@router.callback_query(F.data == "img:text")
async def cb_text_image(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ImageFlow.text_prompt)
    await show(call.message, 
        "✍ <b>Картинка по описанию</b>\n\nОпишите, что нарисовать (можно по-русски):",
        reply_markup=kb.cancel_keyboard(),
    )
    await call.answer()


# --- обивка ---


@router.message(ImageFlow.up_furniture, F.photo | F.document)
async def up_furniture(message: types.Message, state: FSMContext) -> None:
    ref = _photo_ref(message)
    if not ref:
        await message.answer("Пришлите именно фото (картинку).", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(furniture=ref)
    await state.set_state(ImageFlow.up_fabric)
    await message.answer(
        "Шаг 2 из 3. Пришлите <b>фото ткани</b> (образец, текстура) — или опишите ткань словами, "
        "например: «бежевый велюр» или «серая рогожка».",
        reply_markup=kb.cancel_keyboard(),
    )


@router.message(ImageFlow.up_fabric, F.photo | F.document)
async def up_fabric_photo(message: types.Message, state: FSMContext) -> None:
    ref = _photo_ref(message)
    if not ref:
        await message.answer("Пришлите фото ткани или опишите её словами.", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(fabric_ref=ref, fabric_text=None)
    await _ask_note(message, state, ImageFlow.up_note)


@router.message(ImageFlow.up_fabric, F.text)
async def up_fabric_text(message: types.Message, state: FSMContext) -> None:
    await state.update_data(fabric_ref=None, fabric_text=message.text.strip())
    await _ask_note(message, state, ImageFlow.up_note)


async def _ask_note(message: types.Message, state: FSMContext, new_state) -> None:
    await state.set_state(new_state)
    await message.answer(
        "Шаг 3 из 3. Есть пожелания? Напишите (например: «только сиденье, подушки оставить») "
        "или нажмите «Пропустить».",
        reply_markup=kb.choice_keyboard("⏭ Пропустить", "img:skip"),
    )


@router.callback_query(F.data == "img:skip", ImageFlow.up_note)
async def up_skip(call: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await _run_upholstery(call.message, state, bot, "")


@router.message(ImageFlow.up_note, F.text)
async def up_note(message: types.Message, state: FSMContext, bot: Bot) -> None:
    await _run_upholstery(message, state, bot, message.text.strip())


async def _run_upholstery(target: types.Message, state: FSMContext, bot: Bot, note: str) -> None:
    d = await state.get_data()
    images = [await _download(bot, d["furniture"])]
    if d.get("fabric_ref"):
        images.append(await _download(bot, d["fabric_ref"]))
        fabric = "shown in the SECOND image"
    else:
        fabric = f"described as: {d['fabric_text']}"
    prompt = UPHOLSTERY_PROMPT.format(fabric=fabric, note=f"Additional wishes: {note}" if note else "")
    await _generate_and_send(target, state, prompt, images)


# --- мебель в интерьер ---


@router.message(ImageFlow.in_furniture, F.photo | F.document)
async def in_furniture(message: types.Message, state: FSMContext) -> None:
    ref = _photo_ref(message)
    if not ref:
        await message.answer("Пришлите именно фото (картинку).", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(furniture=ref)
    await state.set_state(ImageFlow.in_room)
    await message.answer(
        "Шаг 2 из 3. Пришлите <b>фото интерьера</b> (комнаты), куда нужно поставить мебель.",
        reply_markup=kb.cancel_keyboard(),
    )


@router.message(ImageFlow.in_room, F.photo | F.document)
async def in_room(message: types.Message, state: FSMContext) -> None:
    ref = _photo_ref(message)
    if not ref:
        await message.answer("Пришлите именно фото (картинку).", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(room=ref)
    await _ask_note(message, state, ImageFlow.in_note)


@router.callback_query(F.data == "img:skip", ImageFlow.in_note)
async def in_skip(call: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await _run_interior(call.message, state, bot, "")


@router.message(ImageFlow.in_note, F.text)
async def in_note(message: types.Message, state: FSMContext, bot: Bot) -> None:
    await _run_interior(message, state, bot, message.text.strip())


async def _run_interior(target: types.Message, state: FSMContext, bot: Bot, note: str) -> None:
    d = await state.get_data()
    images = [await _download(bot, d["furniture"]), await _download(bot, d["room"])]
    prompt = INTERIOR_PROMPT.format(note=f"Additional wishes: {note}" if note else "")
    await _generate_and_send(target, state, prompt, images)


# --- по описанию ---


@router.message(ImageFlow.text_prompt, F.text)
async def text_image(message: types.Message, state: FSMContext) -> None:
    await _generate_and_send(message, state, message.text.strip(), [])


# --- общее ---


@router.message(
    ImageFlow.up_furniture, ~F.photo & ~F.document
)
@router.message(ImageFlow.in_furniture, ~F.photo & ~F.document)
@router.message(ImageFlow.in_room, ~F.photo & ~F.document)
async def need_photo(message: types.Message) -> None:
    await message.answer("Здесь нужно прислать фото (картинку).", reply_markup=kb.cancel_keyboard())


async def _generate_and_send(
    target: types.Message, state: FSMContext, prompt: str, images: list[tuple[bytes, str]]
) -> None:
    status = await target.answer("🎨 Создаю картинку, это может занять до минуты...")
    try:
        try:
            result = await nano.generate(prompt, images)
        except nano.NanoError:
            if images or config.GEMINI_API_KEY:
                raise
            result = await legacy_images.generate_image(prompt)  # без ключа: простая генерация
        await target.answer_photo(
            photo=BufferedInputFile(result, filename="result.png"),
            caption="Готово!",
            reply_markup=kb.result_keyboard("menu:image"),
        )
        analytics.track(target.chat.id, "image", prompt[:40])
    except nano.NanoError as exc:
        await target.answer(f"❌ {exc}", reply_markup=kb.image_menu())
    except Exception as exc:  # noqa: BLE001
        await target.answer(f"❌ Не получилось создать картинку: {exc}", reply_markup=kb.image_menu())
    finally:
        await status.delete()
        await state.clear()


# --- Выплата дизайнеру: вопрос -> ответ, в конце пакет документов -----------

DIGITS = re.compile(r"\D")


class PaymentForm(StatesGroup):
    agent_name = State()
    inn = State()
    ogrnip = State()
    address = State()
    rs = State()
    bank = State()
    ks = State()
    bik = State()
    phone = State()
    email = State()
    order_name = State()
    sale_amount = State()
    reward = State()
    tax = State()
    date = State()


def _digits(text: str) -> str:
    return DIGITS.sub("", text)


def _amount(text: str) -> float | None:
    try:
        value = float(text.replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


async def _ask(target: types.Message, state: FSMContext, new_state, question: str, markup=None) -> None:
    await state.set_state(new_state)
    await target.answer(question, reply_markup=markup or kb.cancel_keyboard())


@router.callback_query(F.data == "menu:payment")
async def cb_payment(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PaymentForm.agent_name)
    await show(call.message, 
        "💵 <b>Выплата дизайнеру</b>\n"
        "Отвечайте на вопросы по очереди, в конце пришлю готовый пакет документов "
        "(договор, отчёт, счёт, акт).\n\n"
        "1. ФИО дизайнера (полностью):",
        reply_markup=kb.cancel_keyboard(),
    )
    await call.answer()


@router.message(PaymentForm.agent_name, F.text)
async def pay_name(message: types.Message, state: FSMContext) -> None:
    await state.update_data(agent_name=message.text.strip())
    await _ask(message, state, PaymentForm.inn, "2. ИНН дизайнера (12 цифр):")


@router.message(PaymentForm.inn, F.text)
async def pay_inn(message: types.Message, state: FSMContext) -> None:
    inn = _digits(message.text)
    if len(inn) not in (10, 12):
        await message.answer("ИНН — только цифры, 10 или 12 штук (например 500100732259). Введите ещё раз или нажмите «Отмена»:", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(inn=inn)
    await _ask(
        message, state, PaymentForm.ogrnip, "3. ОГРНИП / ОГРН (если нет — нажмите «Нет»):",
        kb.choice_keyboard("Нет", "pay:none"),
    )


@router.callback_query(F.data == "pay:none", PaymentForm.ogrnip)
async def pay_ogrnip_none(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(ogrnip="-")
    await call.answer()
    await _ask(call.message, state, PaymentForm.address, "4. Юридический адрес дизайнера (например: г. Москва):")


@router.message(PaymentForm.ogrnip, F.text)
async def pay_ogrnip(message: types.Message, state: FSMContext) -> None:
    await state.update_data(ogrnip=message.text.strip())
    await _ask(message, state, PaymentForm.address, "4. Юридический адрес дизайнера (например: г. Москва):")


@router.message(PaymentForm.address, F.text)
async def pay_address(message: types.Message, state: FSMContext) -> None:
    await state.update_data(address=message.text.strip())
    await _ask(message, state, PaymentForm.rs, "5. Расчётный счёт (Р/с, 20 цифр):")


@router.message(PaymentForm.rs, F.text)
async def pay_rs(message: types.Message, state: FSMContext) -> None:
    rs = _digits(message.text)
    if len(rs) != 20:
        await message.answer("Р/с — только цифры, ровно 20 штук. Введите ещё раз или нажмите «Отмена»:", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(rs=rs)
    await _ask(message, state, PaymentForm.bank, "6. Название банка (например: Филиал № 7701 Банка ВТБ (ПАО) в г. Москве):")


@router.message(PaymentForm.bank, F.text)
async def pay_bank(message: types.Message, state: FSMContext) -> None:
    await state.update_data(bank=message.text.strip())
    await _ask(message, state, PaymentForm.ks, "7. Корреспондентский счёт (К/с, 20 цифр):")


@router.message(PaymentForm.ks, F.text)
async def pay_ks(message: types.Message, state: FSMContext) -> None:
    ks = _digits(message.text)
    if len(ks) != 20:
        await message.answer("К/с — только цифры, ровно 20 штук. Введите ещё раз или нажмите «Отмена»:", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(ks=ks)
    await _ask(message, state, PaymentForm.bik, "8. БИК банка (9 цифр):")


@router.message(PaymentForm.bik, F.text)
async def pay_bik(message: types.Message, state: FSMContext) -> None:
    bik = _digits(message.text)
    if len(bik) != 9:
        await message.answer("БИК — только цифры, ровно 9 штук (например 044525000). Введите ещё раз или нажмите «Отмена»:", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(bik=bik)
    await _ask(message, state, PaymentForm.phone, "9. Телефон дизайнера:")


@router.message(PaymentForm.phone, F.text)
async def pay_phone(message: types.Message, state: FSMContext) -> None:
    await state.update_data(phone=message.text.strip())
    await _ask(message, state, PaymentForm.email, "10. Email дизайнера:")


@router.message(PaymentForm.email, F.text)
async def pay_email(message: types.Message, state: FSMContext) -> None:
    if "@" not in message.text:
        await message.answer("Похоже, это не email. Введите ещё раз:", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(email=message.text.strip())
    await _ask(
        message, state, PaymentForm.order_name,
        "11. Название заказа / клиента (например: ИП Иванов Иван Иванович ИИИ-01):",
    )


@router.message(PaymentForm.order_name, F.text)
async def pay_order(message: types.Message, state: FSMContext) -> None:
    await state.update_data(order_name=message.text.strip())
    await _ask(message, state, PaymentForm.sale_amount, "12. Сумма заказа (стоимость товара), руб.:")


@router.message(PaymentForm.sale_amount, F.text)
async def pay_sale(message: types.Message, state: FSMContext) -> None:
    value = _amount(message.text)
    if value is None:
        await message.answer("Введите сумму числом, например 111868. Ещё раз:", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(sale_amount=value)
    await _ask(message, state, PaymentForm.reward, "13. Вознаграждение дизайнера, руб.:")


@router.message(PaymentForm.reward, F.text)
async def pay_reward(message: types.Message, state: FSMContext) -> None:
    value = _amount(message.text)
    if value is None:
        await message.answer("Введите сумму числом, например 4500. Ещё раз:", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(reward=value)
    await _ask(
        message, state, PaymentForm.tax, "14. Налогообложение, % (нажмите «20» или введите своё):",
        kb.choice_keyboard("20", "pay:tax20"),
    )


async def _ask_date(target: types.Message, state: FSMContext) -> None:
    await _ask(
        target, state, PaymentForm.date,
        "15. Дата документов (нажмите «Сегодня» или введите ДД.ММ.ГГГГ):",
        kb.choice_keyboard("📅 Сегодня", "pay:today"),
    )


@router.callback_query(F.data == "pay:tax20", PaymentForm.tax)
async def pay_tax_default(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(tax="20")
    await call.answer()
    await _ask_date(call.message, state)


@router.message(PaymentForm.tax, F.text)
async def pay_tax(message: types.Message, state: FSMContext) -> None:
    tax = message.text.strip().replace("%", "").replace(",", ".")
    if _amount(tax) is None:
        await message.answer("Введите процент числом, например 20. Ещё раз:", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(tax=tax)
    await _ask_date(message, state)


async def _finish(target: types.Message, state: FSMContext, date: dt.date) -> None:
    d = await state.get_data()
    data = AgencyData(
        agent_name=d["agent_name"], inn=d["inn"], ogrnip=d["ogrnip"], address=d["address"],
        rs=d["rs"], bank=d["bank"], ks=d["ks"], bik=d["bik"], phone=d["phone"], email=d["email"],
        order_name=d["order_name"], sale_amount=d["sale_amount"], reward=d["reward"],
        tax=d["tax"], date=date,
    )
    status = await target.answer("⏳ Готовлю документы...")
    analytics.track(target.chat.id, "payout", data.agent_name)
    pdf = build_agency_package(data)
    document = BufferedInputFile(pdf.read(), filename=f"Agency_Package_{data.inn}.pdf")
    await target.answer_document(
        document=document,
        caption=f"Готово! Договор № {data.number}: договор, реквизиты, отчёт, счёт и акт.",
        reply_markup=kb.main_menu(),
    )
    await status.delete()
    await state.clear()


@router.callback_query(F.data == "pay:today", PaymentForm.date)
async def pay_date_today(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _finish(call.message, state, dt.datetime.now(MSK).date())


@router.message(PaymentForm.date, F.text)
async def pay_date(message: types.Message, state: FSMContext) -> None:
    try:
        date = dt.datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Формат даты — ДД.ММ.ГГГГ, например 20.09.2026. Ещё раз:", reply_markup=kb.cancel_keyboard())
        return
    await _finish(message, state, date)


# --- Поиск ткани: цена и остаток --------------------------------------------


class SearchForm(StatesGroup):
    query = State()


SEARCH_HINT = (
    "🔎 <b>Поиск ткани</b>\n"
    "Введите название ткани (можно часть названия), например: <i>кашемир</i>.\n"
    "Я скажу цену за погонный метр и остаток у производителя."
)


@router.callback_query(F.data == "menu:search")
async def cb_search(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SearchForm.query)
    await show(call.message, SEARCH_HINT, reply_markup=kb.search_keyboard())
    await call.answer()


@router.message(SearchForm.query, F.text)
async def search_fabric(message: types.Message, state: FSMContext) -> None:
    query = message.text.strip()
    analytics.track(message.chat.id, "search", query[:40])
    try:
        prices, stocks = await asyncio.gather(
            sheets.fetch_pairs(config.PRICE_CSV_URL), sheets.fetch_pairs(config.STOCK_CSV_URL)
        )
    except sheets.SheetError as exc:
        await message.answer(f"❌ {exc}", reply_markup=kb.search_keyboard())
        return

    names: list[str] = []
    for name, _ in prices + stocks:
        if sheets.name_matches(query, name) and name not in names:
            names.append(name)

    if not names:
        await message.answer(
            f"Ткань «{query}» не нашёл. Проверьте название или откройте прайс онлайн.\n"
            "Можно ввести другое название.",
            reply_markup=kb.search_keyboard(),
        )
        return

    price_by = {sheets.normalize(n): v for n, v in prices}
    stock_by = {sheets.normalize(n): v for n, v in stocks}
    blocks = []
    for name in names[:10]:
        key = sheets.normalize(name)
        price = price_by.get(key)
        stock = stock_by.get(key)
        blocks.append(
            f"🧵 <b>{name}</b>\n"
            f"💰 Цена: {price + ' ₽ за погонный метр' if price else 'нет в прайсе'}\n"
            f"📦 Остаток у производителя: {stock + ' м' if stock else 'нет данных'}"
        )
    text = "\n\n".join(blocks)
    if len(names) > 10:
        text += f"\n\nНайдено {len(names)}, показаны первые 10. Уточните название."
    await message.answer(text + "\n\nМожно ввести следующую ткань.", reply_markup=kb.search_keyboard())
