import asyncio
import datetime as dt
import logging
import os

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

import config
import keyboards as kb
from utils import images, sheets
from utils.pdf import PaymentData, generate_payment_pdf

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Render задаёт RENDER_EXTERNAL_URL и PORT автоматически — по их наличию бот
# понимает, что работает в облаке, и переключается с polling на webhook,
# чтобы отвечать на HTTP-пинги (не даёт бесплатному сервису "уснуть").
_koyeb_domain = os.getenv("KOYEB_PUBLIC_DOMAIN")
RENDER_EXTERNAL_URL = (
    os.getenv("WEBHOOK_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or (f"https://{_koyeb_domain}" if _koyeb_domain else None)
)
PORT = int(os.getenv("PORT", "8000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "local-secret")
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

WELCOME_TEXT = (
    "👋 Привет! Я рабочий бот-помощник.\n\n"
    "Выберите действие в меню ниже:"
)


class ImageForm(StatesGroup):
    prompt = State()


class PaymentForm(StatesGroup):
    designer_name = State()
    order = State()
    amount = State()
    date = State()
    bank_details = State()
    confirm = State()


# ---------------------------------------------------------------------------
# Старт и главное меню
# ---------------------------------------------------------------------------


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=kb.main_menu())


@dp.callback_query(F.data == "menu:home")
async def cb_home(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(WELCOME_TEXT, reply_markup=kb.main_menu())
    await call.answer()


# ---------------------------------------------------------------------------
# 1. Генерация картинки
# ---------------------------------------------------------------------------


@dp.callback_query(F.data == "menu:image")
async def cb_image(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ImageForm.prompt)
    await call.message.edit_text(
        "🎨 Опишите картинку, которую нужно нарисовать (можно по-русски):",
        reply_markup=kb.cancel_keyboard(),
    )
    await call.answer()


@dp.message(ImageForm.prompt)
async def process_image_prompt(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer(
            "Пришлите, пожалуйста, текстовое описание картинки.",
            reply_markup=kb.cancel_keyboard(),
        )
        return

    status = await message.answer("🎨 Рисую, подождите несколько секунд...")
    try:
        image_bytes = await images.generate_image(message.text)
        photo = BufferedInputFile(image_bytes, filename="image.jpg")
        await message.answer_photo(
            photo=photo, caption="Готово!", reply_markup=kb.result_keyboard("menu:image")
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"❌ Не получилось создать картинку: {exc}", reply_markup=kb.main_menu())
    finally:
        await status.delete()
        await state.clear()


# ---------------------------------------------------------------------------
# 2. Документ на выплату дизайнеру
# ---------------------------------------------------------------------------


@dp.callback_query(F.data == "menu:payment")
async def cb_payment(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PaymentForm.designer_name)
    await call.message.edit_text(
        "💵 <b>Выплата дизайнеру</b>\n\nВведите ФИО дизайнера:",
        reply_markup=kb.cancel_keyboard(),
    )
    await call.answer()


@dp.message(PaymentForm.designer_name)
async def payment_name(message: types.Message, state: FSMContext) -> None:
    await state.update_data(designer_name=message.text)
    await state.set_state(PaymentForm.order)
    await message.answer("Номер или название заказа/проекта:", reply_markup=kb.cancel_keyboard())


@dp.message(PaymentForm.order)
async def payment_order(message: types.Message, state: FSMContext) -> None:
    await state.update_data(order=message.text)
    await state.set_state(PaymentForm.amount)
    await message.answer("Сумма выплаты (руб.):", reply_markup=kb.cancel_keyboard())


@dp.message(PaymentForm.amount)
async def payment_amount(message: types.Message, state: FSMContext) -> None:
    await state.update_data(amount=message.text)
    await state.set_state(PaymentForm.date)
    await message.answer(
        "Дата выплаты (нажмите «Сегодня» или введите вручную, напр. 17.09.2026):",
        reply_markup=kb.date_keyboard(),
    )


async def _ask_bank_details(target: types.Message, state: FSMContext) -> None:
    await state.set_state(PaymentForm.bank_details)
    await target.answer(
        "Банковские реквизиты дизайнера (карта или счёт):", reply_markup=kb.cancel_keyboard()
    )


@dp.callback_query(F.data == "pay:today", StateFilter(PaymentForm.date))
async def payment_date_today(call: types.CallbackQuery, state: FSMContext) -> None:
    today = dt.date.today().strftime("%d.%m.%Y")
    await state.update_data(date=today)
    await call.answer()
    await _ask_bank_details(call.message, state)


@dp.message(PaymentForm.date)
async def payment_date_text(message: types.Message, state: FSMContext) -> None:
    await state.update_data(date=message.text)
    await _ask_bank_details(message, state)


@dp.message(PaymentForm.bank_details)
async def payment_bank(message: types.Message, state: FSMContext) -> None:
    await state.update_data(bank_details=message.text)
    data = await state.get_data()
    summary = (
        "Проверьте данные перед созданием документа:\n\n"
        f"👤 Дизайнер: {data['designer_name']}\n"
        f"📁 Заказ: {data['order']}\n"
        f"💰 Сумма: {data['amount']} ₽\n"
        f"📅 Дата: {data['date']}\n"
        f"🏦 Реквизиты: {data['bank_details']}"
    )
    await state.set_state(PaymentForm.confirm)
    await message.answer(summary, reply_markup=kb.confirm_keyboard())


@dp.callback_query(F.data == "pay:confirm", StateFilter(PaymentForm.confirm))
async def payment_confirm(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    payment = PaymentData(
        designer_name=data["designer_name"],
        order=data["order"],
        amount=data["amount"],
        date=data["date"],
        bank_details=data["bank_details"],
    )
    pdf_buffer = generate_payment_pdf(payment)
    document = BufferedInputFile(pdf_buffer.read(), filename="vyplata_dizayneru.pdf")
    await call.message.answer_document(
        document=document, caption="Документ готов!", reply_markup=kb.main_menu()
    )
    await state.clear()
    await call.answer()


# ---------------------------------------------------------------------------
# 3-5. Ткани и продукция (данные из Google Таблицы)
# ---------------------------------------------------------------------------


async def _load_fabric_rows(state: FSMContext) -> list[dict]:
    data = await state.get_data()
    rows = data.get("fabric_rows")
    if rows is None:
        rows = await sheets.fetch_rows(config.FABRICS_CSV_URL)
        await state.update_data(fabric_rows=rows)
    return rows


async def _load_product_rows(state: FSMContext) -> list[dict]:
    data = await state.get_data()
    rows = data.get("product_rows")
    if rows is None:
        rows = await sheets.fetch_rows(config.PRODUCTS_CSV_URL)
        await state.update_data(product_rows=rows)
    return rows


@dp.callback_query(F.data == "menu:stock")
async def cb_stock(call: types.CallbackQuery, state: FSMContext) -> None:
    try:
        rows = await _load_fabric_rows(state)
    except sheets.SheetError as exc:
        await call.message.edit_text(f"❌ {exc}", reply_markup=kb.back_to_menu())
        await call.answer()
        return
    directions = sheets.unique_directions(rows)
    await call.message.edit_text(
        "📦 Выберите направление:", reply_markup=kb.directions_keyboard("stockdir", directions)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("stockdir:"))
async def cb_stock_direction(call: types.CallbackQuery, state: FSMContext) -> None:
    idx = int(call.data.split(":")[1])
    rows = await _load_fabric_rows(state)
    directions = sheets.unique_directions(rows)
    direction = directions[idx]
    text = sheets.format_stock_list(rows, direction)
    await call.message.edit_text(text, reply_markup=kb.result_keyboard("menu:stock"))
    await call.answer()


@dp.callback_query(F.data == "menu:price")
async def cb_price(call: types.CallbackQuery, state: FSMContext) -> None:
    try:
        rows = await _load_fabric_rows(state)
    except sheets.SheetError as exc:
        await call.message.edit_text(f"❌ {exc}", reply_markup=kb.back_to_menu())
        await call.answer()
        return
    directions = sheets.unique_directions(rows)
    await call.message.edit_text(
        "💰 Выберите направление:", reply_markup=kb.directions_keyboard("pricedir", directions)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("pricedir:"))
async def cb_price_direction(call: types.CallbackQuery, state: FSMContext) -> None:
    idx = int(call.data.split(":")[1])
    rows = await _load_fabric_rows(state)
    directions = sheets.unique_directions(rows)
    direction = directions[idx]
    text = sheets.format_price_list(rows, direction)
    await call.message.edit_text(text, reply_markup=kb.result_keyboard("menu:price"))
    await call.answer()


@dp.callback_query(F.data == "menu:products")
async def cb_products(call: types.CallbackQuery, state: FSMContext) -> None:
    try:
        rows = await _load_product_rows(state)
    except sheets.SheetError as exc:
        await call.message.edit_text(f"❌ {exc}", reply_markup=kb.back_to_menu())
        await call.answer()
        return
    directions = sheets.unique_directions(rows)
    await call.message.edit_text(
        "👗 Выберите направление:", reply_markup=kb.directions_keyboard("proddir", directions)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("proddir:"))
async def cb_product_direction(call: types.CallbackQuery, state: FSMContext) -> None:
    idx = int(call.data.split(":")[1])
    rows = await _load_product_rows(state)
    directions = sheets.unique_directions(rows)
    direction = directions[idx]
    text = sheets.format_products_list(rows, direction)
    await call.message.edit_text(text, reply_markup=kb.result_keyboard("menu:products"))
    await call.answer()


async def health(request: web.Request) -> web.Response:
    return web.Response(text="OK — бот жив")


async def on_startup(app: web.Application) -> None:
    await bot.set_webhook(f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}", drop_pending_updates=True)


async def on_shutdown(app: web.Application) -> None:
    await bot.delete_webhook()


def build_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


async def run_polling() -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


def main() -> None:
    if RENDER_EXTERNAL_URL:
        web.run_app(build_web_app(), host="0.0.0.0", port=PORT)
    else:
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
