"""Кнопки постоянного меню под полем ввода: то же, что и inline-меню, но всегда на виду.

Кнопка нажимается в любой момент: текущий диалог сбрасывается и открывается выбранный раздел.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import keyboards as kb

router = Router()


class _Msg:
    """Сообщение пользователя, которое «редактируется» ответом (чужие сообщения редактировать нельзя)."""

    def __init__(self, message: types.Message):
        self._m = message

    def __getattr__(self, name):
        return getattr(self._m, name)

    async def edit_text(self, text: str, **kwargs):
        return await self._m.answer(text, **kwargs)


class _Call:
    """Минимальная замена CallbackQuery для обработчиков меню."""

    def __init__(self, message: types.Message):
        self.message = _Msg(message)
        self.from_user = message.from_user
        self.data = ""
        self.id = "0"

    async def answer(self, *args, **kwargs):
        return None


@router.message(F.text.in_(kb.MENU_TEXT.keys()))
async def on_menu_button(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current and current.startswith("Onb:"):
        await message.answer("Сначала закончим знакомство — ответьте на вопрос выше 🙂")
        return
    await state.clear()
    key = kb.MENU_TEXT[message.text.strip()]
    call = _Call(message)

    if key == "price":
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="📄 Открыть прайс онлайн", url=config.PRICE_ONLINE_URL))
        await message.answer("Прайс на ткани — по кнопке ниже:", reply_markup=b.as_markup())
        return

    # импорт внутри функции — чтобы не было циклов между модулями
    import core
    import kp_flow
    import onboarding
    import refund_flow
    import tag_flow

    handlers = {
        "search": core.cb_search,
        "image": core.cb_image,
        "payment": core.cb_payment,
        "refund": refund_flow.cb_refund,
        "tag": tag_flow.cb_tag,
        "kp": kp_flow.cb_kp,
        "profile": onboarding.cb_profile,
    }
    await handlers[key](call, state)
