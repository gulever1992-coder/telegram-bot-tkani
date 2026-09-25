import config
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔎 Найти ткань (цена и остаток)", callback_data="menu:search"))
    builder.row(InlineKeyboardButton(text="📄 Открыть прайс онлайн", url=config.PRICE_ONLINE_URL))
    builder.row(InlineKeyboardButton(text="🎨 Создать картинку", callback_data="menu:image"))
    builder.row(InlineKeyboardButton(text="💵 Выплата дизайнеру", callback_data="menu:payment"))
    builder.row(InlineKeyboardButton(text="📝 Заявление на возврат", callback_data="menu:refund"))
    builder.row(InlineKeyboardButton(text="🏷 Ценник на мебель", callback_data="menu:tag"))
    builder.row(InlineKeyboardButton(text="📋 Коммерческое предложение", callback_data="menu:kp"))
    return builder.as_markup()


def search_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📄 Открыть прайс онлайн", url=config.PRICE_ONLINE_URL))
    builder.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
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


def choice_keyboard(label: str, callback: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=label, callback_data=callback))
    builder.row(InlineKeyboardButton(text="✖ Отмена", callback_data="menu:home"))
    return builder.as_markup()


def image_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🛋 Поменять обивку мебели", callback_data="img:upholstery"))
    builder.row(InlineKeyboardButton(text="🏠 Поставить мебель в интерьер", callback_data="img:interior"))
    builder.row(InlineKeyboardButton(text="✍ Картинка по описанию", callback_data="img:text"))
    builder.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    return builder.as_markup()
