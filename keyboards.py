import config
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


MENU_ITEMS = [
    ("🔎 Найти ткань", "search"),
    ("🎨 Создать картинку", "image"), ("💵 Выплата дизайнеру", "payment"),
    ("📝 Заявление на возврат", "refund"), ("🏷 Ценник на мебель", "tag"),
    ("📋 Коммерческое предложение", "kp"), ("📄 Мои КП", "mykp"),
    ("📈 Планируемые продажи", "pipeline"), ("🚚 Стоимость доставки", "delivery"),
    ("🏢 Карточка организации", "cards"), ("👤 Мой профиль", "profile"),
]
MENU_TEXT = dict(MENU_ITEMS)

WELCOME_FULL = (
    "👋 <b>Привет! Это рабочий бот-помощник Creatica.</b>\n"
    "\n"
    "Он экономит время менеджеров: всё нужное для работы — в одном месте.\n"
    "\n"
    "<b>Что умеет:</b>\n"
    "🔎 <b>Найти ткань</b> — цена, категория и цвета в наличии\n"
    "📋 <b>Коммерческое предложение</b> — готовый PDF для клиента\n"
    "📄 <b>Мои КП</b> — поправить и выпустить КП заново\n"
    "🏷 <b>Ценник на мебель</b> — готовый ценник\n"
    "📝 <b>Заявление на возврат</b> и 💵 <b>выплата дизайнеру</b> — документы по шагам\n"
    "📈 <b>Планируемые продажи</b> — ваши сделки; ✅ продажа состоялась, ❌ сорвалась (с причиной)\n"
    "🚚 <b>Стоимость доставки</b> — Москва, СПб и регионы, сборка из прайса\n"
    "🏢 <b>Карточка организации</b> — реквизиты ООО «Феникс» и ИП Сабиров в Word\n"
    "👤 <b>Мой профиль</b> — ваши контакты для КП\n"
    "\n"
    "<b>🛠 В разработке (ещё не закончено):</b>\n"
    "🎨 Создание картинок\n"

    "💰 Актуальный прайс ткани — на финальной стадии\n"
    "\n"
    "Выберите действие в меню внизу 👇\n"
)


def is_menu_text(text: str | None) -> bool:
    return (text or "").strip() in MENU_TEXT


def reply_menu() -> ReplyKeyboardMarkup:
    """Постоянное меню под полем ввода: две колонки, всегда на виду."""
    rows = [list(pair) for pair in zip(MENU_ITEMS[::2], MENU_ITEMS[1::2])]
    if len(MENU_ITEMS) % 2:
        rows.append([MENU_ITEMS[-1]])
    keyboard = [[KeyboardButton(text=item[0]) for item in row] for row in rows]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, is_persistent=True,
                               input_field_placeholder="Выберите действие в меню")


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔎 Найти ткань (цена, категория, цвета)", callback_data="menu:search"))
    builder.row(InlineKeyboardButton(text="🎨 Создать картинку", callback_data="menu:image"))
    builder.row(InlineKeyboardButton(text="💵 Выплата дизайнеру", callback_data="menu:payment"))
    builder.row(InlineKeyboardButton(text="📝 Заявление на возврат", callback_data="menu:refund"))
    builder.row(InlineKeyboardButton(text="🏷 Ценник на мебель", callback_data="menu:tag"))
    builder.row(InlineKeyboardButton(text="📋 Коммерческое предложение", callback_data="menu:kp"))
    builder.row(InlineKeyboardButton(text="📄 Мои КП", callback_data="menu:mykp"))
    builder.row(InlineKeyboardButton(text="📈 Планируемые продажи", callback_data="menu:pipeline"))
    builder.row(InlineKeyboardButton(text="🚚 Стоимость доставки", callback_data="menu:delivery"))
    builder.row(InlineKeyboardButton(text="🏢 Карточка организации", callback_data="menu:cards"))
    builder.row(InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu:profile"))
    return builder.as_markup()


def suppliers_keyboard(suppliers: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, name in enumerate(suppliers):
        builder.button(text=name, callback_data=f"fab:{i}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    return builder.as_markup()


def search_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏭 Другая фабрика", callback_data="menu:search"))
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
