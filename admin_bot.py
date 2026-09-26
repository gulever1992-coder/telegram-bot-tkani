"""Админ-бот для владельца: сюда приходят КП менеджеров, а по запросу — статистика.

Работает в том же процессе, что и основной бот (см. bot.py). Доступ — только id из ADMIN_CHAT_IDS.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import zlib
from collections import Counter, defaultdict
from html import escape

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import analytics
import config
from analytics import KINDS, MSK, fmt_time, since

router = Router()
admin_dp = Dispatcher()
admin_dp.include_router(router)

PERIODS = {"1": ("Сегодня", 1), "7": ("7 дней", 7), "30": ("30 дней", 30), "all": ("Всё время", None)}


def make_admin_bot() -> Bot:
    return Bot(token=config.ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def _allowed(user: types.User | None) -> bool:
    return bool(user) and user.id in config.ADMIN_CHAT_IDS


def rub(v: float | int) -> str:
    return f"{int(round(v)):,}".replace(",", " ") + " ₽"


def who(e: dict) -> str:
    return escape(e.get("name") or f"id {e['uid']}")


# --- меню -----------------------------------------------------------------------------------


def menu_markup(period: str = "7") -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(*[
        InlineKeyboardButton(text=("• " if k == period else "") + label, callback_data=f"a:p:{k}")
        for k, (label, _) in PERIODS.items()
    ])
    b.row(InlineKeyboardButton(text="📊 Сводка", callback_data=f"a:sum:{period}"),
          InlineKeyboardButton(text="👥 Менеджеры", callback_data=f"a:mgr:{period}"))
    b.row(InlineKeyboardButton(text="🏬 Шоу-румы", callback_data=f"a:room:{period}"),
          InlineKeyboardButton(text="📋 Последние КП", callback_data=f"a:kp:{period}"))
    b.row(InlineKeyboardButton(text="🕒 Лента действий", callback_data=f"a:feed:{period}"),
          InlineKeyboardButton(text="📈 По дням", callback_data=f"a:days:{period}"))
    b.row(InlineKeyboardButton(text="🔍 КП по менеджеру", callback_data=f"a:fm:{period}"),
          InlineKeyboardButton(text="🔍 КП по шоу-руму", callback_data=f"a:fr:{period}"))
    return b.as_markup()


HELP = ("Выберите период и раздел ниже. Сами КП приходят сюда сразу, как менеджер их оформил.\n"
        "🔍 <b>Поиск КП:</b> кнопки «КП по менеджеру» / «КП по шоу-руму» — либо просто напишите слово "
        "(например: <i>новодевичий</i>, <i>румер</i>, фамилию менеджера или заказчика).\n"
        "Команды: /stats, /managers, /rooms, /kp, /feed.")


@router.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    if not config.ADMIN_CHAT_IDS:
        await message.answer(
            f"Админ-бот включён, но владелец ещё не задан.\nВаш Telegram ID: <code>{message.from_user.id}</code>\n"
            "Передайте его разработчику — после этого сюда пойдут КП и статистика."
        )
        return
    if not _allowed(message.from_user):
        await message.answer("Доступ закрыт.")
        return
    await message.answer("👋 <b>Creatica · панель владельца</b>\n" + HELP, reply_markup=reply_menu())


async def _guard(message: types.Message) -> bool:
    if not _allowed(message.from_user):
        await message.answer("Доступ закрыт.")
        return False
    return True


# --- отчёты ---------------------------------------------------------------------------------


def report_summary(period: str) -> str:
    label, days = PERIODS[period]
    ev = since(days)
    counts = Counter(e["kind"] for e in ev)
    kps = [e for e in ev if e["kind"] == "kp"]
    total = sum(e["data"].get("sum", 0) for e in kps)
    managers = {e["uid"] for e in ev if e["kind"] not in ("register", "profile")}
    lines = [f"📊 <b>Сводка — {label}</b>", f"Активных менеджеров: <b>{len(managers)}</b>", ""]
    for k, title in KINDS.items():
        if counts.get(k):
            lines.append(f"{title}: <b>{counts[k]}</b>")
    if kps:
        lines += ["", f"Сумма оформленных КП: <b>{rub(total)}</b>", f"Средний чек КП: {rub(total / len(kps))}"]
    if not ev:
        lines.append("За этот период действий не было.")
    return "\n".join(lines)


def report_managers(period: str) -> str:
    label, days = PERIODS[period]
    ev = [e for e in since(days) if e["kind"] not in ("register", "profile")]
    if not ev:
        return f"👥 <b>Менеджеры — {label}</b>\nДействий не было."
    by: dict[int, list[dict]] = defaultdict(list)
    for e in ev:
        by[e["uid"]].append(e)
    rows = sorted(by.items(), key=lambda kv: -len(kv[1]))
    lines = [f"👥 <b>Менеджеры — {label}</b>"]
    for uid, items in rows:
        last = max(items, key=lambda e: e["ts"])
        c = Counter(e["kind"] for e in items)
        parts = [f"{KINDS[k].split(' ', 1)[1]} {n}" for k, n in c.most_common()]
        kp_sum = sum(e["data"].get("sum", 0) for e in items if e["kind"] == "kp")
        room = last.get("showroom") or "—"
        lines += ["", f"<b>{who(last)}</b> · {escape(room[:40])}",
                  f"  {' · '.join(parts)}"]
        if kp_sum:
            lines.append(f"  сумма КП: {rub(kp_sum)}")
        lines.append(f"  последнее: {fmt_time(last['ts'])}")
    return "\n".join(lines)[:4000]


def report_rooms(period: str) -> str:
    label, days = PERIODS[period]
    ev = [e for e in since(days) if e["kind"] not in ("register", "profile")]
    if not ev:
        return f"🏬 <b>Шоу-румы — {label}</b>\nДействий не было."
    by: dict[str, list[dict]] = defaultdict(list)
    for e in ev:
        by[e.get("showroom") or "Не указан"].append(e)
    lines = [f"🏬 <b>Шоу-румы — {label}</b>"]
    for room, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        kps = [e for e in items if e["kind"] == "kp"]
        mgrs = {e["uid"] for e in items}
        lines += ["", f"<b>{escape(room[:60])}</b>",
                  f"  менеджеров: {len(mgrs)} · действий: {len(items)} · КП: {len(kps)}"
                  + (f" на {rub(sum(e['data'].get('sum', 0) for e in kps))}" if kps else "")]
    return "\n".join(lines)[:4000]


def report_kp(period: str) -> str:
    label, days = PERIODS[period]
    kps = sorted((e for e in since(days) if e["kind"] == "kp"), key=lambda e: -e["ts"])[:15]
    if not kps:
        return f"📋 <b>КП — {label}</b>\nКП не оформляли."
    lines = [f"📋 <b>Последние КП — {label}</b>"]
    for e in kps:
        d = e["data"]
        lines.append(
            f"\n{fmt_time(e['ts'])} · <b>{who(e)}</b>\n"
            f"  №{escape(str(d.get('number', '')))} · {escape(str(d.get('customer') or 'без заказчика'))} · "
            f"{d.get('items', 0)} поз. · {rub(d.get('sum', 0))}"
        )
    lines.append("\n<i>Сами файлы КП — в этом чате выше.</i>")
    return "\n".join(lines)[:4000]


def report_feed(period: str) -> str:
    label, days = PERIODS[period]
    ev = sorted((e for e in since(days) if e["kind"] != "search"), key=lambda e: -e["ts"])[:25]
    if not ev:
        return f"🕒 <b>Лента — {label}</b>\nПока пусто."
    lines = [f"🕒 <b>Лента действий — {label}</b> <i>(без поиска ткани)</i>"]
    for e in ev:
        detail = f" — {escape(e['detail'][:40])}" if e.get("detail") else ""
        lines.append(f"{fmt_time(e['ts'])} · {who(e)} · {KINDS.get(e['kind'], e['kind'])}{detail}")
    return "\n".join(lines)[:4000]


def report_days(period: str) -> str:
    label, days = PERIODS[period]
    n = min(days or 14, 14) if days != 1 else 7
    today = dt.datetime.now(MSK).date()
    lines = [f"📈 <b>Активность по дням</b> <i>(последние {n} дн.)</i>", "<i>КП / все действия</i>"]
    ev = since(None)
    for i in range(n - 1, -1, -1):
        day = today - dt.timedelta(days=i)
        items = [e for e in ev if dt.datetime.fromtimestamp(e["ts"], MSK).date() == day]
        kp = sum(1 for e in items if e["kind"] == "kp")
        bar = "█" * min(len(items), 20)
        lines.append(f"<code>{day:%d.%m} {kp:>2} / {len(items):>3}</code> {bar}")
    return "\n".join(lines)


REPORTS = {"sum": report_summary, "mgr": report_managers, "room": report_rooms,
           "kp": report_kp, "feed": report_feed, "days": report_days}


# --- фильтры КП: по менеджеру, по шоу-руму, по слову -----------------------------------------------

LAST_QUERY: dict[int, str] = {}  # последний текстовый поиск владельца (для кнопки «прислать файлы»)
MAX_FILES = 10


def room_key(name: str) -> str:
    return format(zlib.crc32((name or "").encode("utf-8")) & 0xFFFFFFFF, "08x")


def kp_events(period: str, *, uid: int | None = None, room: str | None = None, query: str | None = None) -> list[dict]:
    days = PERIODS.get(period, PERIODS["all"])[1]
    out = [e for e in since(days) if e["kind"] == "kp"]
    if uid is not None:
        out = [e for e in out if e["uid"] == uid]
    if room is not None:
        out = [e for e in out if room_key(e.get("showroom", "")) == room]
    if query:
        q = query.lower().strip()
        out = [e for e in out if q in " ".join([
            e.get("name", ""), e.get("showroom", ""), str(e["data"].get("customer", "")), str(e["data"].get("number", "")),
        ]).lower()]
    return sorted(out, key=lambda e: -e["ts"])


def kp_list_text(title: str, events: list[dict], period: str) -> str:
    label = PERIODS[period][0]
    if not events:
        return f"{title} — {label}\n\nКП не найдено."
    total = sum(e["data"].get("sum", 0) for e in events)
    lines = [f"{title} — {label}", f"Найдено КП: <b>{len(events)}</b> на <b>{rub(total)}</b>"]
    for e in events[:15]:
        d = e["data"]
        room = (e.get("showroom") or "—").split("·")[-1].strip()[:36]
        lines.append(
            f"\n{fmt_time(e['ts'])} · <b>{who(e)}</b> · {escape(room)}\n"
            f"  №{escape(str(d.get('number', '')))} · {escape(str(d.get('customer') or 'без заказчика'))} · "
            f"{d.get('items', 0)} поз. · {rub(d.get('sum', 0))}"
        )
    if len(events) > 15:
        lines.append(f"\n<i>Показаны последние 15 из {len(events)}.</i>")
    return "\n".join(lines)[:4000]


def kp_list_markup(events: list[dict], send_cb: str, back_cb: str, period: str) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    with_files = [e for e in events if e["data"].get("msg")]
    if with_files:
        n = min(len(with_files), MAX_FILES)
        b.row(InlineKeyboardButton(text=f"📎 Прислать файлы КП ({n})", callback_data=send_cb))
    b.row(InlineKeyboardButton(text="⬅ Назад", callback_data=back_cb),
          InlineKeyboardButton(text="🏠 Меню", callback_data=f"a:p:{period}"))
    return b.as_markup()


def _people(period: str) -> list[tuple[int, str, int]]:
    by: dict[int, list[dict]] = defaultdict(list)
    for e in kp_events(period):
        by[e["uid"]].append(e)
    return sorted(((uid, who(max(v, key=lambda e: e["ts"])), len(v)) for uid, v in by.items()), key=lambda r: -r[2])


def _rooms(period: str) -> list[tuple[str, str, int]]:
    by: dict[str, list[dict]] = defaultdict(list)
    for e in kp_events(period):
        by[e.get("showroom") or ""].append(e)
    return sorted(((room_key(r), r or "Не указан", len(v)) for r, v in by.items()), key=lambda x: -x[2])


@router.callback_query(F.data.regexp(r"^a:f[mr]:"))
async def cb_pick_filter(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    _, kind, period = call.data.split(":")
    period = period if period in PERIODS else "all"
    text, markup = _picker(kind, period)
    await call.message.edit_text(text, reply_markup=markup)
    await call.answer()


def _picker(kind: str, period: str) -> tuple[str, types.InlineKeyboardMarkup]:
    b = InlineKeyboardBuilder()
    if kind == "fm":
        rows = _people(period)
        for uid, name, n in rows[:30]:
            b.row(InlineKeyboardButton(text=f"{name} — {n}", callback_data=f"a:km:{uid}:{period}"))
        text = f"🔍 <b>КП по менеджеру — {PERIODS[period][0]}</b>\nВыберите менеджера:" if rows else "КП пока не оформляли."
    else:
        rows = _rooms(period)
        for key, name, n in rows[:30]:
            b.row(InlineKeyboardButton(text=f"{name[:50]} — {n}", callback_data=f"a:kr:{key}:{period}"))
        text = f"🔍 <b>КП по шоу-руму — {PERIODS[period][0]}</b>\nВыберите шоу-рум:" if rows else "КП пока не оформляли."
    b.row(InlineKeyboardButton(text="⬅ Назад", callback_data=f"a:p:{period}"))
    return text, b.as_markup()


# --- постоянное меню под полем ввода (две колонки) -------------------------------------------------

ADMIN_MENU = [
    ("📊 Сводка", "sum"), ("👥 Менеджеры", "mgr"),
    ("🏬 Шоу-румы", "room"), ("📋 Последние КП", "kp"),
    ("🔍 КП по менеджеру", "fm"), ("🔍 КП по шоу-руму", "fr"),
    ("🕒 Лента действий", "feed"), ("📈 По дням", "days"),
    ("📅 Период отчётов", "period"), ("ℹ Помощь", "help"),
]
ADMIN_TEXT = dict(ADMIN_MENU)
PERIOD: dict[int, str] = {}  # выбранный период отчётов у каждого владельца


def reply_menu() -> types.ReplyKeyboardMarkup:
    rows = [[types.KeyboardButton(text=a[0]), types.KeyboardButton(text=b[0])] for a, b in zip(ADMIN_MENU[::2], ADMIN_MENU[1::2])]
    return types.ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True,
                                     input_field_placeholder="Выберите отчёт или напишите слово для поиска КП")


async def on_admin_button(message: types.Message) -> None:
    if not await _guard(message):
        return
    kind = ADMIN_TEXT[message.text.strip()]
    period = PERIOD.get(message.from_user.id, "7")
    if kind == "help":
        await message.answer(HELP, reply_markup=reply_menu())
    elif kind == "period":
        await message.answer(f"📅 Период отчётов сейчас: <b>{PERIODS[period][0]}</b>. Выберите другой:",
                             reply_markup=menu_markup(period))
    elif kind in ("fm", "fr"):
        text, markup = _picker(kind, period)
        await message.answer(text, reply_markup=markup)
    else:
        await message.answer(REPORTS[kind](period), reply_markup=menu_markup(period))


@router.callback_query(F.data.regexp(r"^a:k[mr]:"))
async def cb_kp_filtered(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    _, kind, key, period = call.data.split(":")
    period = period if period in PERIODS else "all"
    if kind == "km":
        events = kp_events(period, uid=int(key))
        title = f"📋 <b>КП менеджера {who(events[0]) if events else ''}</b>"
        send, back = f"a:sm:{key}:{period}", f"a:fm:{period}"
    else:
        events = kp_events(period, room=key)
        room = events[0].get("showroom") if events else ""
        title = f"📋 <b>КП шоу-рума {escape(room or 'не указан')}</b>"
        send, back = f"a:sr:{key}:{period}", f"a:fr:{period}"
    await call.message.edit_text(kp_list_text(title, events, period), reply_markup=kp_list_markup(events, send, back, period))
    await call.answer()


async def _send_files(call_or_msg, events: list[dict]) -> int:
    """Пересылает владельцу файлы КП (копией сообщений из этого же чата)."""
    bot = call_or_msg.bot
    chat = call_or_msg.from_user.id
    sent = 0
    for e in [e for e in events if e["data"].get("msg")][:MAX_FILES][::-1]:  # старые -> новые
        try:
            await bot.copy_message(chat, chat, e["data"]["msg"])
            sent += 1
            await asyncio.sleep(0.4)
        except Exception:  # noqa: BLE001 — сообщение могли удалить из чата
            continue
    return sent


@router.callback_query(F.data.regexp(r"^a:s[mrq]:"))
async def cb_send(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    parts = call.data.split(":")
    kind, period = parts[1], parts[-1]
    period = period if period in PERIODS else "all"
    if kind == "sm":
        events = kp_events(period, uid=int(parts[2]))
    elif kind == "sr":
        events = kp_events(period, room=parts[2])
    else:
        events = kp_events(period, query=LAST_QUERY.get(call.from_user.id, ""))
    await call.answer("Присылаю файлы…")
    n = await _send_files(call, events)
    if not n:
        await call.message.answer("Файлы этих КП недоступны (старые записи или сообщения удалены из чата).")


async def _search(message: types.Message) -> None:
    if not await _guard(message):
        return
    query = message.text.strip()
    LAST_QUERY[message.from_user.id] = query
    events = kp_events("all", query=query)
    title = f"🔍 <b>КП по запросу «{escape(query[:40])}»</b>"
    await message.answer(kp_list_text(title, events, "all"),
                         reply_markup=kp_list_markup(events, "a:sq:all", "a:p:all", "all"))


@router.callback_query(F.data.regexp(r"^a:(p|sum|mgr|room|kp|feed|days):"))
async def cb_report(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    _, kind, period = call.data.split(":")
    if kind == "p":  # смена периода — перерисовать сводку
        kind = "sum"
    if period not in PERIODS:
        period = "7"
    PERIOD[call.from_user.id] = period
    text = REPORTS[kind](period)
    try:
        await call.message.edit_text(text, reply_markup=menu_markup(period))
    except Exception:  # то же самое содержимое — Telegram не даёт отредактировать
        pass
    await call.answer()


def _cmd(kind: str):
    async def handler(message: types.Message) -> None:
        if not await _guard(message):
            return
        await message.answer(REPORTS[kind]("7"), reply_markup=menu_markup("7"))
    return handler


for _name, _kind in (("stats", "sum"), ("managers", "mgr"), ("rooms", "room"), ("kp", "kp"), ("feed", "feed")):
    router.message.register(_cmd(_kind), Command(_name))

# кнопки меню — раньше поиска
router.message.register(on_admin_button, F.text.in_(ADMIN_TEXT.keys()))
# любой другой текст (не команда) — поиск КП по менеджеру / шоу-руму / заказчику / номеру
router.message.register(_search, F.text, ~F.text.startswith("/"))
