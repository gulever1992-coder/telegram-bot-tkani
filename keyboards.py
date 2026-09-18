from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎨 Создать картинку", callback_data="menu:image"))
    builder.row(InlineKeyboardButton(text="💵 Выплата дизайнеру", callback_data="menu:payment"))
    builder.row(
        InlineKeyboardButton(text="📦 Остатки тканей", callback_data="menu:stock"),
        InlineKeyboardButton(text="💰 Прайс тканей", callback_data="menu:price"),
    )
    builder.row(InlineKeyboardButton(text="👗 Готовая продукция", callback_data="menu:products"))
    return builder.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    return builder.as_markup()


def directions_keyboard(prefix: str, directions: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, name in enumerate(directions):
        builder.row(InlineKeyboardButton(text=name, callback_data=f"{prefix}:{i}"))
    builder.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return builder.as_markup()


def date_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📅 Сегодня", callback_data="pay:today"))
    builder.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return builder.as_markup()


def confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Создать PDF", callback_data="pay:confirm"))
    builder.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return builder.as_markup()


def result_keyboard(back_callback: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅ Другое направление", callback_data=back_callback))
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:home"))
    return builder.as_markup()
