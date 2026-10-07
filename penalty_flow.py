"""Заявление о выплате неустойки (пени) за просрочку передачи товара: по шагам, как возврат -> PDF в стиле КП.

Пени считаем сами: срок доставки = дата готовности + 7 рабочих дней; дни просрочки (до фактической доставки)
× сумма договора × 0,1% в день. Менеджер может согласиться с расчётом или вписать согласованную сумму.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import analytics
from refund_flow import CARD, Step, _parse
from utils.refund import MONTHS, REFUND_COMPANIES, buyer_lines, money_words, payout_section, signer_name
from utils.statement import Statement, build_statement_pdf, money
from utils.ui import show

router = Router()
MSK = dt.timezone(dt.timedelta(hours=3))


class PenaltyForm(StatesGroup):
    ask = State()


_HEAD = [
    Step("order", "Договор / заказ клиента (номер или название):"),
    Step("contract_date", "Дата договора (ДД.ММ.ГГГГ):", "date"),
]
_FIZ = [
    Step("name", "ФИО покупателя (полностью):"),
    Step("passport", "Паспорт: серия и номер (10 цифр, например 4510 123456):", "passport"),
    Step("passport_issuer", "Кем выдан паспорт:"),
    Step("passport_date", "Когда выдан паспорт (ДД.ММ.ГГГГ):", "date"),
]
_YUR = [
    Step("org", "Название организации-покупателя (например: ООО «Ромашка»):"),
    Step("director", "Руководитель в родительном падеже (например: генерального директора Петрова Петра Петровича):"),
    Step("director_name", "ФИО руководителя для подписи (например: Петров Пётр Петрович):"),
]
_CALC = [
    Step("contract_sum", "Сумма договора, руб.:", "amount"),
    Step("ready_date", "Дата готовности по договору (ДД.ММ.ГГГГ):", "date"),
    Step("fact_date", "Фактическая дата доставки (ДД.ММ.ГГГГ):", "date",
         buttons=(("Ещё не доставлен — считать по сегодня", "=today"),)),
    Step("amount", "Сумма пени к выплате, руб.:", "amount"),  # кнопка с расчётом добавляется на лету
]
_PAY_FIZ = [
    Step("method", "Как выплатить пени?", "choice",
         buttons=(("💵 Из кассы магазина", "cash"), ("💳 На карту / счёт", "card"))),
    Step("recipient", "Получатель (ФИО полностью):", buttons=(("Тот же, что покупатель", "=name"),), when=CARD),
    Step("bank", "Банк получателя:", when=CARD),
    Step("bik", "БИК банка (9 цифр):", "digits", (9,), when=CARD),
    Step("bank_inn", "ИНН банка (10 цифр):", "digits", (10, 12), when=CARD),
    Step("ks", "Корреспондентский счёт банка (20 цифр):", "digits", (20,), when=CARD),
    Step("account", "№ лицевого или карточного счёта (20 цифр):", "digits", (20,), when=CARD),
    Step("card", "№ карты (16 цифр) — или нажмите «Нет»:", "card", buttons=(("Нет", "-"),), when=CARD),
]
_PAY_YUR = [
    Step("pay_org", "Реквизиты для выплаты. Наименование организации:", buttons=(("Как выше", "=org"),)),
    Step("pay_inn", "ИНН / КПП организации (например: 7701234567/770101001):"),
    Step("pay_ogrn", "ОГРН (13 или 15 цифр):", "digits", (13, 15)),
    Step("pay_rs", "Расчётный счёт (20 цифр):", "digits", (20,)),
    Step("pay_ls", "Лицевой счёт (если нет — нажмите «Нет»):", buttons=(("Нет", "-"),)),
    Step("bank", "Банк получателя:"),
    Step("bik", "БИК банка (9 цифр):", "digits", (9,)),
    Step("bank_inn", "ИНН банка (10 цифр):", "digits", (10, 12)),
    Step("ks", "Корреспондентский счёт банка (20 цифр):", "digits", (20,)),
]
_TAIL = [Step("doc_date", "Дата заявления (ДД.ММ.ГГГГ):", "date", buttons=(("📅 Сегодня", "=today"),))]
REQUIRED = {"contract_sum", "ready_date", "fact_date", "amount", "method"}
RATE = 0.1  # % от суммы договора за каждый день просрочки
DELIVERY_WORKDAYS = 7  # на доставку после даты готовности


def _steps(kind: str) -> list[Step]:
    return _HEAD + (_FIZ if kind == "fiz" else _YUR) + _CALC + (_PAY_FIZ if kind == "fiz" else _PAY_YUR) + _TAIL


def _next_step(kind: str, answers: dict) -> Step | None:
    for step in _steps(kind):
        if step.key in answers or (step.when and not step.when(answers)):
            continue
        return step
    return None


def _today() -> dt.date:
    return dt.datetime.now(MSK).date()


def add_workdays(d: dt.date, n: int) -> dt.date:
    """Дата через n рабочих дней (пн–пт, праздники не учитываются)."""
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def delivery_deadline(a: dict) -> dt.date | None:
    ready = a.get("ready_date")
    return add_workdays(ready, DELIVERY_WORKDAYS) if isinstance(ready, dt.date) else None


def penalty_calc(a: dict) -> tuple[int, float]:
    """(дней просрочки, сумма пени): срок = готовность + 7 раб. дней; дни × сумма договора × 0,1%."""
    deadline, fact, total = delivery_deadline(a), a.get("fact_date"), a.get("contract_sum")
    if not (deadline and isinstance(fact, dt.date) and total):
        return 0, 0.0
    days = max((fact - deadline).days, 0)
    return days, round(total * RATE / 100 * days, 2)


def _markup(step: Step, answers: dict) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    buttons = list(step.buttons)
    if step.key == "amount":
        days, calc = penalty_calc(answers)
        if calc:
            buttons.insert(0, (f"✅ По расчёту: {money(calc)} ({days} дн.)", "=calc"))
    for label, value in buttons:
        b.row(InlineKeyboardButton(text=label[:60], callback_data=f"pen:v:{value}"))
    if step.key not in REQUIRED:
        b.row(InlineKeyboardButton(text="⏭ Пропустить", callback_data="pen:skip"))
    b.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return b.as_markup()


async def _ask(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    answers = data["answers"]
    step = _next_step(data["kind"], answers)
    if step is None:
        await _finish(target, state)
        return
    text = f"{len(answers) + 1}. {step.prompt}"
    if step.key == "amount":
        days, calc = penalty_calc(answers)
        fact = answers["fact_date"]
        text = (f"🧮 Готовность {answers['ready_date']:%d.%m.%Y} + {DELIVERY_WORKDAYS} раб. дней = срок доставки "
                f"<b>{delivery_deadline(answers):%d.%m.%Y}</b>\n"
                f"{'Не доставлен, считаем по' if answers.get('not_delivered') else 'Доставлено'} {fact:%d.%m.%Y} → "
                f"просрочка <b>{days} дн.</b>\n"
                f"{days} дн. × 0,1% × {money(answers['contract_sum'])} = <b>{money(calc)}</b>"
                + f"\n\n{text}\nНажмите «По расчёту» или впишите согласованную сумму.")
    await target.answer(text, reply_markup=_markup(step, answers))


async def _store(target: types.Message, state: FSMContext, step: Step, value) -> None:
    data = await state.get_data()
    answers = dict(data["answers"])
    answers[step.key] = value
    await state.update_data(answers=answers)
    await _ask(target, state)


def _statement(company: str, kind: str, a: dict) -> Statement:
    days, calc = penalty_calc(a)
    cd = a.get("contract_date")
    contract = f"договору / заказу «{a.get('order') or '____'}»" + (
        f" от {cd.day} {MONTHS[cd.month - 1]} {cd.year} г." if isinstance(cd, dt.date) else "")
    ready, fact, deadline = a.get("ready_date"), a.get("fact_date"), delivery_deadline(a)
    fact_txt = "на дату заявления товар не доставлен" if a.get("not_delivered") or not isinstance(fact, dt.date) \
        else f"фактически товар доставлен {fact:%d.%m.%Y}"
    amount = a.get("amount") or calc
    paragraphs = [
        f"Сумма по {contract} составляет {money(a.get('contract_sum') or 0)}."
        + (f" Дата готовности товара — {ready:%d.%m.%Y}, срок доставки — {DELIVERY_WORKDAYS} рабочих дней, "
           f"т. е. до {deadline:%d.%m.%Y}; {fact_txt}." if deadline else ""),
        f"Просрочка доставки товара составила {days} дн. Прошу выплатить неустойку (пени) в размере "
        f"0,1% от суммы договора за каждый день просрочки.",
    ]
    return Statement(
        title="Заявление о выплате неустойки (пени)",
        to_lines=list(REFUND_COMPANIES[company]["header_lines"]),
        from_lines=buyer_lines(kind, a),
        paragraphs=paragraphs,
        amount=amount,
        amount_words=money_words(amount),
        amount_label="Сумма неустойки (пени) к выплате",
        sections=[payout_section(kind, a, "Выплату прошу осуществить")],
        signer=signer_name(kind, a),
        date=a.get("doc_date") if isinstance(a.get("doc_date"), dt.date) else None,
    )


async def _finish(target: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    a = dict(data["answers"])
    for key in ("card", "pay_ls"):
        if a.get(key) == "-":
            a[key] = ""
    status = await target.answer("⏳ Готовлю заявление...")
    pdf = await asyncio.to_thread(build_statement_pdf, _statement(data["company"], data["kind"], a))
    label = REFUND_COMPANIES[data["company"]]["label"]
    kind_ru = "физлицо" if data["kind"] == "fiz" else "юрлицо"
    stamp = a.get("doc_date") or _today()
    name = f"Zayavlenie_peni_{data['company']}_{stamp:%Y%m%d}.pdf"
    analytics.track(target.chat.id, "penalty", f"{data['company']} / {data['kind']}", document=(pdf, name),
                    caption=f"⚖ Заявление на выплату пени: {label}, {kind_ru}, {money(a.get('amount') or 0)}\n"
                            f"{analytics.who(target.chat.id)}")
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="⚖ Новое заявление", callback_data="menu:penalty"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await target.answer_document(BufferedInputFile(pdf, filename=name),
                                 caption=f"Готово! Заявление на выплату пени: {label}, {kind_ru}.",
                                 reply_markup=b.as_markup())
    await status.delete()
    await state.clear()


# --- выбор кнопками ------------------------------------------------------------------


@router.callback_query(F.data == "menu:penalty")
async def cb_penalty(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    b = InlineKeyboardBuilder()
    for key, co in REFUND_COMPANIES.items():
        b.row(InlineKeyboardButton(text=co["label"], callback_data=f"pen:co:{key}"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await show(call.message, "⚖ <b>Заявление на выплату пени</b> (неустойка за просрочку передачи товара)\n"
                             "На какую организацию оформляем?", reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("pen:co:"))
async def cb_company(call: types.CallbackQuery, state: FSMContext) -> None:
    company = call.data.split(":")[2]
    if company not in REFUND_COMPANIES:
        await call.answer("Неизвестная организация", show_alert=True)
        return
    await state.update_data(company=company)
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="👤 Физическое лицо", callback_data="pen:kind:fiz"))
    b.row(InlineKeyboardButton(text="🏢 Юридическое лицо", callback_data="pen:kind:yur"))
    b.row(InlineKeyboardButton(text="⬅ Назад", callback_data="menu:penalty"))
    await show(call.message, f"⚖ Выплата пени — <b>{REFUND_COMPANIES[company]['label']}</b>\nКто покупатель?",
               reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("pen:kind:"))
async def cb_kind(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "company" not in data:
        await call.answer("Начните заново: ⚖ Выплата пени", show_alert=True)
        return
    await state.update_data(kind=call.data.split(":")[2], answers={})
    await state.set_state(PenaltyForm.ask)
    await show(call.message, "⚖ Отвечайте на вопросы по очереди — пени посчитаю сам, в конце пришлю PDF.")
    await call.answer()
    await _ask(call.message, state)


@router.callback_query(F.data.startswith("pen:v:"), PenaltyForm.ask)
async def cb_value(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    answers = data["answers"]
    step = _next_step(data["kind"], answers)
    value = call.data[len("pen:v:"):]
    allowed = set()
    if step is not None:
        allowed = {v for _, v in step.buttons} | ({"=calc"} if step.key == "amount" else set())
    if step is None or value not in allowed:
        await call.answer("Этот вопрос уже пройден")
        return
    await call.answer()
    if value == "=today":
        if step.key == "fact_date":  # товар ещё не передан — считаем просрочку по сегодня
            await state.update_data(answers={**answers, "not_delivered": True})
        value = _today()
    elif value == "=calc":
        value = penalty_calc(answers)[1]
    elif value in ("=name", "=org"):
        value = answers.get(value[1:], "")
    await _store(call.message, state, step, value)


@router.callback_query(F.data == "pen:skip", PenaltyForm.ask)
async def cb_skip(call: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    step = _next_step(data["kind"], data["answers"])
    if step is None or step.key in REQUIRED:
        await call.answer("Этот вопрос нужно заполнить", show_alert=True)
        return
    await call.answer("Пропущено")
    await _store(call.message, state, step, None)


@router.message(PenaltyForm.ask, F.text)
async def msg_answer(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    step = _next_step(data["kind"], data["answers"])
    if step is None:
        await _finish(message, state)
        return
    if step.kind == "choice":
        await message.answer("Выберите вариант кнопкой ниже.", reply_markup=_markup(step, data["answers"]))
        return
    value, error = _parse(step, message.text.replace("%", ""))
    if error:
        await message.answer(error, reply_markup=_markup(step, data["answers"]))
        return
    await _store(message, state, step, value)
