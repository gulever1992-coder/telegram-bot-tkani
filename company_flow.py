"""Карточка организации: выбор юрлица -> PDF в фирменном стиле + реквизиты текстом для копирования."""

from __future__ import annotations

import asyncio
import html

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import analytics
from utils import cards
from utils.ui import show

router = Router()


@router.callback_query(F.data == "menu:cards")
async def cb_cards(call: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    b = InlineKeyboardBuilder()
    for key, card in cards.load_cards().items():
        b.row(InlineKeyboardButton(text=f"🏢 {card['short']}", callback_data=f"card:{key}"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    await show(call.message, "🏢 <b>Карточка организации</b>\nВыберите юрлицо:", reply_markup=b.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("card:"))
async def cb_card(call: types.CallbackQuery) -> None:
    key = call.data.split(":", 1)[1]
    card = cards.load_cards().get(key)
    if not card:
        await call.answer("Карточка не найдена", show_alert=True)
        return
    if all(value == "—" for _, value in card["rows"]):  # в облаке нет приватного файла с реквизитами
        await call.answer("Реквизиты ещё не загружены на сервер — обратитесь к владельцу бота.", show_alert=True)
        return
    await call.answer("Готовлю карточку…")
    pdf = await asyncio.to_thread(cards.build_card_pdf, card)
    analytics.track(call.message.chat.id, "card", card["short"])
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🏢 Другая организация", callback_data="menu:cards"))
    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    name = "Karta_" + ("Fenix" if key == "fenix" else "IP_Sabirov" if key == "sabirov" else key)
    await call.message.answer_document(BufferedInputFile(pdf, filename=f"{name}.pdf"),
                                       caption=f"🏢 Карточка предприятия — {card['short']}")
    await call.message.answer(f"<code>{html.escape(cards.card_text(card))}</code>\n\n<i>Нажмите на текст, чтобы скопировать.</i>",
                              reply_markup=b.as_markup())
