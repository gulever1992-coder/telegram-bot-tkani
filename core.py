"""Логика бота без сохранения состояния между запросами (нужно для Vercel).

Вместо пошаговых форм бот присылает сообщение с ForceReply — и ждёт, что вы
ответите именно на него. По тексту исходного сообщения бот понимает, что
делать с ответом, поэтому память между запросами не нужна.
"""

import datetime as dt
import hashlib

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, ForceReply

import config
import keyboards as kb
from utils import images, sheets
from utils.pdf import PaymentData, generate_payment_pdf

WELCOME_TEXT = "👋 Привет! Я рабочий бот-помощник.\n\nВыберите действие в меню ниже:"
IMAGE_PROMPT = "🎨 Опишите картинку, которую нужно нарисовать (можно по-русски):"
PAYMENT_PROMPT = (
    "💵 Выплата дизайнеру\n\n"
    "Ответьте на это сообщение одним сообщением, каждая строка — отдельный пункт:\n"
    "1. ФИО дизайнера\n"
    "2. Номер или название заказа\n"
    "3. Сумма (руб.)\n"
    "4. Дата (или слово «сегодня»)\n"
    "5. Банковские реквизиты (можно в несколько строк)\n\n"
    "Пример:\n"
    "Иванова Мария Сергеевна\n"
    "Заказ №42, платья Ирис\n"
    "25000\n"
    "сегодня\n"
    "Сбербанк, карта 2202 0000 0000 1234"
)

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


# --- Выплата дизайнеру ------------------------------------------------------


@router.callback_query(F.data == "menu:payment")
async def cb_payment(call: types.CallbackQuery) -> None:
    await call.message.answer(
        PAYMENT_PROMPT, reply_markup=ForceReply(input_field_placeholder="ФИО, заказ, сумма...")
    )
    await call.answer()


@router.message(F.text, F.reply_to_message.text.startswith("💵 Выплата дизайнеру"))
async def process_payment(message: types.Message) -> None:
    lines = [ln.strip() for ln in message.text.splitlines() if ln.strip()]
    if len(lines) < 5:
        await message.answer(
            "Нужно минимум 5 строк: ФИО, заказ, сумма, дата, реквизиты. "
            "Нажмите «💵 Выплата дизайнеру» и попробуйте ещё раз.",
            reply_markup=kb.main_menu(),
        )
        return

    date = lines[3]
    if date.lower() in {"сегодня", "today"}:
        date = dt.datetime.now(MSK).strftime("%d.%m.%Y")

    payment = PaymentData(
        designer_name=lines[0],
        order=lines[1],
        amount=lines[2],
        date=date,
        bank_details="\n".join(lines[4:]),
    )
    pdf_buffer = generate_payment_pdf(payment)
    document = BufferedInputFile(pdf_buffer.read(), filename="vyplata_dizayneru.pdf")
    await message.answer_document(
        document=document, caption="Документ готов!", reply_markup=kb.main_menu()
    )


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
