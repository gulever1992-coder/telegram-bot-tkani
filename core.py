"""Логика бота.

Картинка работает через ForceReply (без памяти). Выплата дизайнеру — диалог
вопрос-ответ на FSM (память в процессе): для облака без постоянного процесса
(Vercel) нужно внешнее хранилище состояний.
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
from aiogram.types import BufferedInputFile, ForceReply

import config
import keyboards as kb
from utils import images, sheets
from utils.agency import AgencyData, build_agency_package

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
    await call.message.edit_text(
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
    await call.message.edit_text(SEARCH_HINT, reply_markup=kb.search_keyboard())
    await call.answer()


@router.message(SearchForm.query, F.text)
async def search_fabric(message: types.Message, state: FSMContext) -> None:
    query = message.text.strip()
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
