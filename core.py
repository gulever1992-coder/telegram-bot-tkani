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
import profiles
import config
from utils.ui import show
import keyboards as kb
from refund_flow import router as refund_router
from tag_flow import router as tag_router
from kp_flow import router as kp_router
from onboarding import ProfileGate, router as onb_router
from pipeline_flow import router as pipeline_router
from delivery_flow import router as delivery_router
from quickmenu import router as quick_router
from utils import images as legacy_images
from utils import fabrics, nano, sheets
from utils.agency import AgencyData, build_agency_package

WELCOME_TEXT = "👋 Привет! Я рабочий бот-помощник.\n\nВыберите действие в меню внизу 👇"
router = Router()
dp = Dispatcher()
dp.message.outer_middleware(ProfileGate())
dp.callback_query.outer_middleware(ProfileGate())
dp.include_router(quick_router)
dp.include_router(onb_router)
dp.include_router(router)
dp.include_router(refund_router)
dp.include_router(tag_router)
dp.include_router(kp_router)
dp.include_router(pipeline_router)
dp.include_router(delivery_router)

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
    await message.answer(kb.WELCOME_FULL, reply_markup=kb.reply_menu())


@router.callback_query(F.data == "menu:home")
async def cb_home(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await call.message.delete()
    except Exception:  # noqa: BLE001 — сообщение уже удалено или слишком старое
        pass
    await call.message.answer(WELCOME_TEXT, reply_markup=kb.reply_menu())
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
    if markup is None and str(new_state) in PAY_SKIPPABLE:
        markup = _skip_keyboard()
    await target.answer(question, reply_markup=markup or kb.cancel_keyboard())


def _skip_keyboard():
    return kb.choice_keyboard("⏭ Пропустить", "pay:skip")


@router.callback_query(F.data == "menu:payment")
async def cb_payment(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🏢 Юр. лицо — выплата по счёту", callback_data="pay:type:legal")],
        [types.InlineKeyboardButton(text="👤 Физ. лицо — перевод на карту", callback_data="pay:type:person")],
        [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")],
    ])
    await show(call.message, "💵 <b>Выплата дизайнеру</b>\nВыберите тип дизайнера:", reply_markup=markup)
    await call.answer()


@router.callback_query(F.data == "pay:type:legal")
async def pay_type_legal(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PaymentForm.agent_name)
    await show(call.message,
        "💵 <b>Выплата по счёту (юр. лицо)</b>\n"
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
    await _ask(message, state, PaymentForm.phone, "9. Телефон дизайнера:", kb.choice_keyboard("⏭ Пропустить", "pay:skip"))


@router.message(PaymentForm.phone, F.text)
async def pay_phone(message: types.Message, state: FSMContext) -> None:
    await state.update_data(phone=message.text.strip())
    await _ask(message, state, PaymentForm.email, "10. Email дизайнера:", kb.choice_keyboard("⏭ Пропустить", "pay:skip"))


@router.message(PaymentForm.email, F.text)
async def pay_email(message: types.Message, state: FSMContext) -> None:
    if "@" not in message.text:
        await message.answer("Похоже, это не email. Введите ещё раз:", reply_markup=kb.choice_keyboard("⏭ Пропустить", "pay:skip"))
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
    pdf_bytes = pdf.read()
    prof = profiles.get(target.chat.id) or {}
    analytics.track(
        target.chat.id, "payout", data.agent_name,
        document=(pdf_bytes, f"Agency_Package_{data.inn}.pdf"),
        caption=(
            f"💵 Выплата дизайнеру — договор №{data.number}\n"
            f"👤 Менеджер: {prof.get('name') or '—'}\n"
            f"Исполнитель: {data.agent_name} (ИНН {data.inn})\n"
            f"Заказ: {data.order_name}\n"
            f"Вознаграждение: {data.reward}"
        ),
    )
    document = BufferedInputFile(pdf_bytes, filename=f"Agency_Package_{data.inn}.pdf")
    await target.answer_document(
        document=document,
        caption=f"Готово! Договор № {data.number}: договор, реквизиты, отчёт, счёт и акт.",
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


# --- Выплата физ. лицу: перевод на карту, расчёт с налогом 13% за вывод -----


class PersonPayForm(StatesGroup):
    order_name = State()
    designer = State()
    bank = State()
    amount = State()
    discount = State()


PERSON_TAX = 0.13  # налог за вывод денег физ. лица


def _money(value: float) -> str:
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return text[:-3] if text.endswith(",00") else text


def person_payout(amount: float, discount: float) -> tuple[float, float, float]:
    """Вознаграждение дизайнера = сумма заказа × его процент; возвращает (процент, налог, к выплате)."""
    reward = amount * discount / 100
    tax = reward * PERSON_TAX
    return reward, tax, reward - tax


@router.callback_query(F.data == "pay:type:person")
async def pay_type_person(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PersonPayForm.order_name)
    await show(call.message,
        "👤 <b>Перевод на карту (физ. лицо)</b>\n\n"
        "1. ФИО клиента или название заказа:",
        reply_markup=kb.choice_keyboard("⏭ Пропустить", "pay:skip"),
    )
    await call.answer()


# «Пропустить»: поле -> "-", переход к следующему шагу. Ключи — состояния (юр. и физ. лицо).
PAY_SKIP_NEXT = {
    PaymentForm.inn: ("inn", PaymentForm.ogrnip, "3. ОГРНИП / ОГРН (если нет — нажмите «Нет»):", kb.choice_keyboard("Нет", "pay:none")),
    PaymentForm.address: ("address", PaymentForm.rs, "5. Расчётный счёт (Р/с, 20 цифр):", None),
    PaymentForm.rs: ("rs", PaymentForm.bank, "6. Название банка (например: Филиал № 7701 Банка ВТБ (ПАО) в г. Москве):", None),
    PaymentForm.bank: ("bank", PaymentForm.ks, "7. Корреспондентский счёт (К/с, 20 цифр):", None),
    PaymentForm.ks: ("ks", PaymentForm.bik, "8. БИК банка (9 цифр):", None),
    PaymentForm.bik: ("bik", PaymentForm.phone, "9. Телефон дизайнера:", None),
    PaymentForm.phone: ("phone", PaymentForm.email, "10. Email дизайнера:", None),
    PaymentForm.email: ("email", PaymentForm.order_name,
                        "11. Название заказа / клиента (например: ИП Иванов Иван Иванович ИИИ-01):", None),
    PaymentForm.order_name: ("order_name", PaymentForm.sale_amount, "12. Сумма заказа (стоимость товара), руб.:", None),
    PersonPayForm.order_name: ("order_name", PersonPayForm.designer, "2. ФИО дизайнера (полностью):", None),
    PersonPayForm.bank: ("bank", PersonPayForm.amount, "4. Сумма заказа, руб.:", None),
}
PAY_SKIPPABLE = {str(state) for state in PAY_SKIP_NEXT}


@router.callback_query(F.data == "pay:skip")
async def pay_skip(call: types.CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    step = next((v for k, v in PAY_SKIP_NEXT.items() if str(k) == current), None)
    if step is None:
        await call.answer()
        return
    field, next_state, question, markup = step
    await state.update_data({field: "-"})
    await call.answer()
    await _ask(call.message, state, next_state, question, markup)


@router.message(PersonPayForm.order_name, F.text)
async def person_order(message: types.Message, state: FSMContext) -> None:
    await state.update_data(order_name=message.text.strip())
    await _ask(message, state, PersonPayForm.designer, "2. ФИО дизайнера (полностью):")


@router.message(PersonPayForm.designer, F.text)
async def person_designer(message: types.Message, state: FSMContext) -> None:
    await state.update_data(designer=message.text.strip())
    await _ask(
        message, state, PersonPayForm.bank, "3. Банк получателя (например: Сбербанк):",
        kb.choice_keyboard("⏭ Пропустить", "pay:skip"),
    )


@router.message(PersonPayForm.bank, F.text)
async def person_bank(message: types.Message, state: FSMContext) -> None:
    await state.update_data(bank=message.text.strip())
    await _ask(message, state, PersonPayForm.amount, "4. Сумма заказа, руб.:")


@router.message(PersonPayForm.amount, F.text)
async def person_amount(message: types.Message, state: FSMContext) -> None:
    value = _amount(message.text)
    if value is None:
        await message.answer("Введите сумму числом, например 111868. Ещё раз:", reply_markup=kb.cancel_keyboard())
        return
    await state.update_data(amount=value)
    await _ask(message, state, PersonPayForm.discount, "5. Процент дизайнера (скидка), %:")


@router.message(PersonPayForm.discount, F.text)
async def person_discount(message: types.Message, state: FSMContext) -> None:
    value = _amount(message.text.strip().replace("%", "").replace(",", "."))
    if value is None or value > 100:
        await message.answer("Введите процент числом от 1 до 100, например 15. Ещё раз:", reply_markup=kb.cancel_keyboard())
        return
    d = await state.get_data()
    reward, tax, payout = person_payout(d["amount"], value)
    await state.clear()
    analytics.track(message.chat.id, "payout", d["designer"])
    await message.answer(
        "✅ <b>Расчёт выплаты (физ. лицо)</b>\n\n"
        f"Заказ / клиент: {d['order_name']}\n"
        f"Дизайнер: {d['designer']}\n"
        f"Банк получателя: {d['bank']}\n"
        f"Сумма заказа: {_money(d['amount'])} ₽\n\n"
        f"Процент дизайнера: {_money(value)} %\n"
        f"Вознаграждение: {_money(reward)} ₽\n"
        f"Налог за вывод (13%): −{_money(tax)} ₽\n"
        f"<b>К переводу на карту: {_money(payout)} ₽</b>",
    )


# --- Поиск ткани: фабрика -> ткань -> цена, категория, цвета в наличии ------


class SearchForm(StatesGroup):
    supplier = State()
    query = State()


SEARCH_HINT = (
    "🔎 <b>Поиск ткани</b>\n"
    "Шаг 1 из 2. Выберите <b>фабрику</b>:"
)


async def _load_fabrics():
    return await fabrics.load(config.PRICE_CSV_URL, config.STOCK_CSV_URL)


@router.callback_query(F.data == "menu:search")
async def cb_search(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        price, _ = await _load_fabrics()
    except sheets.SheetError as exc:
        await show(call.message, f"❌ {exc}", reply_markup=kb.search_keyboard())
        await call.answer()
        return
    suppliers = fabrics.suppliers(price)
    await state.set_state(SearchForm.supplier)
    await state.update_data(suppliers=suppliers)
    await show(call.message, SEARCH_HINT, reply_markup=kb.suppliers_keyboard(suppliers))
    await call.answer()


@router.callback_query(F.data.startswith("fab:"))
async def cb_pick_supplier(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    suppliers = data.get("suppliers") or []
    try:
        supplier = suppliers[int(call.data.split(":", 1)[1])]
    except (ValueError, IndexError):
        await call.answer("Список фабрик устарел — откройте поиск заново.", show_alert=True)
        return
    await state.set_state(SearchForm.query)
    await state.update_data(supplier=supplier)
    await show(
        call.message,
        f"🏭 Фабрика: <b>{fabrics.esc(supplier)}</b>\n\n"
        "Шаг 2 из 2. Напишите <b>название ткани</b> (можно часть названия), например: <i>velutto</i>.\n"
        "Покажу цену за отрез, категорию и какие цвета есть в наличии.",
        reply_markup=kb.search_keyboard(),
    )
    await call.answer()


@router.message(SearchForm.supplier, F.text)
async def search_need_supplier(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    await message.answer("Сначала выберите фабрику кнопкой 👇",
                         reply_markup=kb.suppliers_keyboard(data.get("suppliers") or []))


@router.message(SearchForm.query, F.text)
async def search_fabric(message: types.Message, state: FSMContext) -> None:
    query = message.text.strip()
    supplier = (await state.get_data()).get("supplier", "")
    analytics.track(message.chat.id, "search", f"{supplier}: {query}"[:60])
    try:
        price, stock = await _load_fabrics()
    except sheets.SheetError as exc:
        await message.answer(f"❌ {exc}", reply_markup=kb.search_keyboard())
        return

    has_stock = any(s.supplier == supplier for s in stock)
    found = fabrics.find_fabrics(price, supplier, query)
    if not found:
        probe = fabrics.stock_only(stock, supplier, query)
        if probe:
            found = [probe]
    if not found:
        hint = fabrics.similar(price, supplier, query)
        text = f"Ткань «{fabrics.esc(query)}» у фабрики {fabrics.esc(supplier)} не нашёл."
        if hint:
            text += "\nПохожие: " + ", ".join(f"<b>{fabrics.esc(h)}</b>" for h in hint)
        text += "\n\nНапишите другое название или выберите другую фабрику."
        await message.answer(text, reply_markup=kb.search_keyboard())
        return

    for fabric in found[:5]:
        rows = fabrics.stock_for(fabric, stock, price)
        await message.answer(fabrics.format_fabric(fabric, rows, has_stock))
    tail = f"Найдено {len(found)}, показаны первые 5 — уточните название.\n" if len(found) > 5 else ""
    await message.answer(tail + f"Можно написать следующую ткань фабрики <b>{fabrics.esc(supplier)}</b>.",
                         reply_markup=kb.search_keyboard())
