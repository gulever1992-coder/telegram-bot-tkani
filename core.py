"""Логика бота.

Картинка работает через ForceReply (без памяти). Выплата дизайнеру — диалог
вопрос-ответ на FSM (память в процессе): для облака без постоянного процесса
(Vercel) нужно внешнее хранилище состояний.
"""

import datetime as dt
import hashlib

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, ForceReply

import config
import keyboards as kb
from utils import images, sheets
from utils.pdf import PaymentData, generate_payment_pdf

WELCOME_TEXT = "👋 Привет! Я рабочий бот-помощник.\n\nВыберите действие в меню ниже:"
IMAGE_PROMPT = "🎨 Опишите картинку, которую нужно нарисовать (можно по-русски):"
router = Router()
dp = Dispatcher()
dp.include_router(router)

MSK = dt.timezone(dt.timedelta(hours=3))


def make_bot() -> Bot:
    return Bot(
        token=config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def secret_token() -> str:
    return hashlib.sha256(config.TELEGRAM_BOT_TOKEN.encode()).hexdigest()[:32]


@router.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    await message.answer(WELCOME_TEXT, reply_markup=kb.main_menu())


@router.callback_query(F.data == "menu:home")
async def cb_home(call: types.CallbackQuery) -> None:
    await call.message.edit_text(WELCOME_TEXT, reply_markup=kb.main_menu())
    await call.answer()


# --- Картинка ---------------------------------------------------------------


@router.callback_query(F.data == "menu:image")
async def cb_image(call: types.CallbackQuery) -> None:
    await call.message.answer(
        IMAGE_PROMPT, reply_markup=ForceReply(input_field_placeholder="Опишите картинку")
    )
    await call.answer()


@router.message(F.text, F.reply_to_message.text.startswith("🎨 Опишите картинку"))
async def process_image_prompt(message: types.Message) -> None:
    status = await message.answer("🎨 Рисую, подождите несколько секунд...")
    try:
        image_bytes = await images.generate_image(message.text)
        photo = BufferedInputFile(image_bytes, filename="image.jpg")
        await message.answer_photo(
            photo=photo, caption="Готово!", reply_markup=kb.result_keyboard("menu:image")
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(
            f"❌ Не получилось создать картинку: {exc}", reply_markup=kb.main_menu()
        )
    finally:
        await status.delete()


# --- Выплата дизайнеру (диалог: вопрос -> ответ) ----------------------------


class PaymentForm(StatesGroup):
    designer_name = State()
    order = State()
    amount = State()
    date = State()
    bank_details = State()
    confirm = State()


async def _payment_directions() -> list[str]:
    found: list[str] = []
    for url in (config.FABRICS_CSV_URL, config.PRODUCTS_CSV_URL):
        try:
            for name in sheets.unique_directions(await sheets.fetch_rows(url)):
                if name not in found:
                    found.append(name)
        except sheets.SheetError:
            continue
    return found


@router.callback_query(F.data == "menu:payment")
async def cb_payment(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    directions = await _payment_directions()
    if not directions:
        await state.update_data(direction="")
        await state.set_state(PaymentForm.designer_name)
        await call.message.edit_text(
            "💵 <b>Выплата дизайнеру</b>\n\nВведите ФИО дизайнера:",
            reply_markup=kb.cancel_keyboard(),
        )
    else:
        await state.update_data(directions=directions)
        await call.message.edit_text(
            "💵 <b>Выплата дизайнеру</b>\n\nВыберите направление:",
            reply_markup=kb.directions_keyboard("paydir", directions),
        )
    await call.answer()


@router.callback_query(F.data.startswith("paydir:"))
async def payment_direction(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    directions = data.get("directions") or await _payment_directions()
    try:
        direction = directions[int(call.data.split(":")[1])]
    except (IndexError, ValueError):
        await call.message.edit_text("Список изменился, начните заново.", reply_markup=kb.main_menu())
        await call.answer()
        return
    await state.update_data(direction=direction)
    await state.set_state(PaymentForm.designer_name)
    await call.message.edit_text(
        f"💵 <b>Выплата дизайнеру</b>\nНаправление: <b>{direction}</b>\n\nВведите ФИО дизайнера:",
        reply_markup=kb.cancel_keyboard(),
    )
    await call.answer()


@router.message(PaymentForm.designer_name, F.text)
async def payment_name(message: types.Message, state: FSMContext) -> None:
    await state.update_data(designer_name=message.text.strip())
    await state.set_state(PaymentForm.order)
    await message.answer("Номер или название заказа/проекта:", reply_markup=kb.cancel_keyboard())


@router.message(PaymentForm.order, F.text)
async def payment_order(message: types.Message, state: FSMContext) -> None:
    await state.update_data(order=message.text.strip())
    await state.set_state(PaymentForm.amount)
    await message.answer("Сумма выплаты (руб.):", reply_markup=kb.cancel_keyboard())


@router.message(PaymentForm.amount, F.text)
async def payment_amount(message: types.Message, state: FSMContext) -> None:
    await state.update_data(amount=message.text.strip())
    await state.set_state(PaymentForm.date)
    await message.answer(
        "Дата выплаты (нажмите «Сегодня» или введите вручную, напр. 20.09.2026):",
        reply_markup=kb.date_keyboard(),
    )


async def _ask_bank(target: types.Message, state: FSMContext) -> None:
    await state.set_state(PaymentForm.bank_details)
    await target.answer(
        "Банковские реквизиты дизайнера (карта или счёт):", reply_markup=kb.cancel_keyboard()
    )


@router.callback_query(F.data == "pay:today", PaymentForm.date)
async def payment_date_today(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(date=dt.datetime.now(MSK).strftime("%d.%m.%Y"))
    await call.answer()
    await _ask_bank(call.message, state)


@router.message(PaymentForm.date, F.text)
async def payment_date_text(message: types.Message, state: FSMContext) -> None:
    await state.update_data(date=message.text.strip())
    await _ask_bank(message, state)


@router.message(PaymentForm.bank_details, F.text)
async def payment_bank(message: types.Message, state: FSMContext) -> None:
    await state.update_data(bank_details=message.text.strip())
    d = await state.get_data()
    summary = (
        "Проверьте данные перед созданием документа:\n\n"
        + (f"🧭 Направление: {d['direction']}\n" if d.get("direction") else "")
        + f"👤 Дизайнер: {d['designer_name']}\n"
        f"📁 Заказ: {d['order']}\n"
        f"💰 Сумма: {d['amount']} ₽\n"
        f"📅 Дата: {d['date']}\n"
        f"🏦 Реквизиты: {d['bank_details']}"
    )
    await state.set_state(PaymentForm.confirm)
    await message.answer(summary, reply_markup=kb.confirm_keyboard())


@router.callback_query(F.data == "pay:confirm", PaymentForm.confirm)
async def payment_confirm(call: types.CallbackQuery, state: FSMContext) -> None:
    d = await state.get_data()
    payment = PaymentData(
        designer_name=d["designer_name"],
        order=d["order"],
        amount=d["amount"],
        date=d["date"],
        bank_details=d["bank_details"],
        direction=d.get("direction", ""),
    )
    pdf_buffer = generate_payment_pdf(payment)
    document = BufferedInputFile(pdf_buffer.read(), filename="vyplata_dizayneru.pdf")
    await call.message.answer_document(
        document=document, caption="Документ готов!", reply_markup=kb.main_menu()
    )
    await state.clear()
    await call.answer()


# --- Ткани и продукция (Google Таблица) -------------------------------------


async def _show_directions(call: types.CallbackQuery, url: str, title: str, prefix: str) -> None:
    try:
        rows = await sheets.fetch_rows(url)
    except sheets.SheetError as exc:
        await call.message.edit_text(f"❌ {exc}", reply_markup=kb.back_to_menu())
        await call.answer()
        return
    directions = sheets.unique_directions(rows)
    await call.message.edit_text(title, reply_markup=kb.directions_keyboard(prefix, directions))
    await call.answer()


async def _show_direction(call, url: str, formatter, back: str) -> None:
    idx = int(call.data.split(":")[1])
    try:
        rows = await sheets.fetch_rows(url)
        direction = sheets.unique_directions(rows)[idx]
    except (sheets.SheetError, IndexError):
        await call.message.edit_text(
            "❌ Список изменился, откройте раздел заново.", reply_markup=kb.back_to_menu()
        )
        await call.answer()
        return
    await call.message.edit_text(
        formatter(rows, direction), reply_markup=kb.result_keyboard(back)
    )
    await call.answer()


@router.callback_query(F.data == "menu:stock")
async def cb_stock(call: types.CallbackQuery) -> None:
    await _show_directions(call, config.FABRICS_CSV_URL, "📦 Выберите направление:", "stockdir")


@router.callback_query(F.data.startswith("stockdir:"))
async def cb_stock_direction(call: types.CallbackQuery) -> None:
    await _show_direction(call, config.FABRICS_CSV_URL, sheets.format_stock_list, "menu:stock")


@router.callback_query(F.data == "menu:price")
async def cb_price(call: types.CallbackQuery) -> None:
    await _show_directions(call, config.FABRICS_CSV_URL, "💰 Выберите направление:", "pricedir")


@router.callback_query(F.data.startswith("pricedir:"))
async def cb_price_direction(call: types.CallbackQuery) -> None:
    await _show_direction(call, config.FABRICS_CSV_URL, sheets.format_price_list, "menu:price")


@router.callback_query(F.data == "menu:products")
async def cb_products(call: types.CallbackQuery) -> None:
    await _show_directions(call, config.PRODUCTS_CSV_URL, "👗 Выберите направление:", "proddir")


@router.callback_query(F.data.startswith("proddir:"))
async def cb_product_direction(call: types.CallbackQuery) -> None:
    await _show_direction(
        call, config.PRODUCTS_CSV_URL, sheets.format_products_list, "menu:products"
    )
