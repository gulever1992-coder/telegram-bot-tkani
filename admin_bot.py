"""Админ-бот для владельца: сюда приходят КП менеджеров, а по запросу — статистика.

Работает в том же процессе, что и основной бот (см. bot.py). Доступ — только id из ADMIN_CHAT_IDS.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import secrets
import time
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
import claims
import config
import keyboards as kb
from analytics import KINDS, MSK, fmt_time, since

router = Router()
admin_dp = Dispatcher()
admin_dp.include_router(router)

PERIODS = {"1": ("Сегодня", 1), "7": ("7 дней", 7), "30": ("30 дней", 30), "all": ("Всё время", None)}
DIGEST_HOUR = 9  # утренняя сводка — по Москве


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
    b.row(InlineKeyboardButton(text="📂 КП менеджера: в работе / купили / отказ", callback_data="a:stl"))
    return b.as_markup()


HELP = ("Выберите период и раздел ниже. Сами КП приходят сюда сразу, как менеджер их оформил.\n"
        "🔍 <b>Поиск КП:</b> кнопки «КП по менеджеру» / «КП по шоу-руму» — либо просто напишите слово "
        "(например: <i>новодевичий</i>, <i>румер</i>, фамилию менеджера или заказчика).\n"
        f"🌅 Каждое утро в {DIGEST_HOUR}:00 по Москве сюда приходит сводка за прошедший день — "
        "кто чем пользовался и сколько раз.\n"
        "Команды: /stats, /managers, /rooms, /kp, /feed.")


@router.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    if not config.ADMIN_CHAT_IDS:
        await message.answer(
            f"Админ-бот включён, но владелец ещё не задан.\nВаш Telegram ID: <code>{message.from_user.id}</code>\n"
            "Передайте его разработчику — после этого сюда пойдут КП и статистика."
        )
        return
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if arg[:4] in ("rec_", "log_"):  # ссылка-приглашение рекламациониста / логиста от владельца
        await _join_recipient(message, arg[4:], "claim" if arg.startswith("rec_") else "log")
        return
    if not _allowed(message.from_user):
        if _is_recipient(message.from_user.id):
            await message.answer(_hello(message.from_user.id))
        else:
            await message.answer("Доступ закрыт.")
        return
    await message.answer("👋 <b>Creatica · панель владельца</b>\n" + HELP, reply_markup=reply_menu())


HELLO = {
    "claim": ("👋 Вы подключены как <b>рекламационист</b> Creatica.\n"
              "Заявки от менеджеров будут приходить сюда. Под каждой заявкой кнопки: "
              "«🛠 Взял в работу» и «✅ Решена» — менеджер и руководитель получат отбивку."),
    "log": ("👋 Вы подключены как <b>логист</b> Creatica.\n"
            "Запросы от менеджеров (доставка, сроки, забор) будут приходить сюда. Под каждым запросом кнопки: "
            "«🛠 Взял в работу» и «✅ Решена» — менеджер и руководитель получат отбивку."),
}


def _hello(uid: int) -> str:
    return "\n\n".join(HELLO[k] for k in claims.KINDS if _is_recipient(uid, k)) or HELLO["claim"]


async def _guard(message: types.Message) -> bool:
    if not _allowed(message.from_user):
        await message.answer(_hello(message.from_user.id) if _is_recipient(message.from_user.id) else "Доступ закрыт.")
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


# --- утренняя сводка (раз в сутки, автоматически) -------------------------------------------


def daily_digest_text(day: dt.date) -> str:
    start = dt.datetime.combine(day, dt.time.min, MSK).timestamp()
    end = start + 86400
    ev = [e for e in analytics.EVENTS if start <= e["ts"] < end]
    label = day.strftime("%d.%m.%Y")
    counts = Counter(e["kind"] for e in ev)
    kps = [e for e in ev if e["kind"] == "kp"]
    total = sum(e["data"].get("sum", 0) for e in kps)
    managers = {e["uid"] for e in ev if e["kind"] not in ("register", "profile", "digest")}
    lines = [f"🌅 <b>Сводка за {label}</b>", f"Активных менеджеров: <b>{len(managers)}</b>", ""]
    for k, title in KINDS.items():
        if counts.get(k):
            lines.append(f"{title}: <b>{counts[k]}</b>")
    if kps:
        lines.append(f"Сумма оформленных КП: <b>{rub(total)}</b>")
    if not ev:
        lines.append("Действий не было.")
    by: dict[int, list[dict]] = defaultdict(list)
    for e in ev:
        if e["kind"] not in ("register", "profile", "digest"):
            by[e["uid"]].append(e)
    if by:
        lines += ["", "👥 <b>По менеджерам:</b>"]
        for uid, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
            c = Counter(e["kind"] for e in items)
            parts = [f"{KINDS[k].split(' ', 1)[1]} {n}" for k, n in c.most_common()]
            lines.append(f"  {who(items[0])}: {' · '.join(parts)}")
    return "\n".join(lines)[:4000]


def _digest_sent(day: dt.date) -> bool:
    tag = day.isoformat()
    return any(e["kind"] == "digest" and e.get("detail") == tag for e in analytics.EVENTS)


async def _send_digest(bot: Bot, day: dt.date) -> None:
    text = daily_digest_text(day)
    for chat in config.ADMIN_CHAT_IDS:
        try:
            await bot.send_message(chat, text)
        except Exception:  # noqa: BLE001
            pass
    analytics.track(0, "digest", day.isoformat())


async def digest_loop(bot: Bot) -> None:
    """Раз в сутки, около 9:00 по Москве, шлёт владельцу сводку за прошедший день.

    Проверяем каждые 10 минут (а не спим точно до 9:00), чтобы пережить перезапуски бота
    на GitHub Actions без риска пропустить момент отправки.
    """
    while True:
        try:
            now = dt.datetime.now(MSK)
            if now.hour == DIGEST_HOUR:
                yesterday = (now - dt.timedelta(days=1)).date()
                if not _digest_sent(yesterday):
                    await _send_digest(bot, yesterday)
        except Exception:  # noqa: BLE001 — сводка не должна ронять бота
            pass
        await asyncio.sleep(600)


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


# --- КП менеджера по статусам: в работе / купили / отказались ------------------------------------

STATUS_LABEL = {"open": "🟡 В работе", "won": "✅ Купили", "lost": "❌ Отказались"}


def _kp_status(e: dict) -> str:
    closed = e["data"].get("closed")
    return closed if closed in ("won", "lost") else "open"


def _status_picker() -> tuple[str, types.InlineKeyboardMarkup]:
    by: dict[int, list[dict]] = defaultdict(list)
    for e in kp_events("all"):
        by[e["uid"]].append(e)
    b = InlineKeyboardBuilder()
    rows = sorted(by.items(), key=lambda kv: -sum(_kp_status(e) == "open" for e in kv[1]))
    for uid, evs in rows[:40]:
        c = Counter(_kp_status(e) for e in evs)
        b.row(InlineKeyboardButton(text=f"{who(evs[0])} — 🟡{c['open']} ✅{c['won']} ❌{c['lost']}",
                                   callback_data=f"a:st:{uid}"))
    text = ("📂 <b>КП менеджеров по статусам</b>\nВыберите менеджера — покажу все его КП: "
            "🟡 в работе (клиент думает), ✅ купили, ❌ отказались." if rows else "КП пока не оформляли.")
    return text, b.as_markup()


def _status_text(uid: int) -> str:
    evs = kp_events("all", uid=uid)
    if not evs:
        return "У этого менеджера нет КП."
    groups = {k: [e for e in evs if _kp_status(e) == k] for k in STATUS_LABEL}
    lines = [f"📂 <b>КП менеджера {who(evs[0])}</b>"]
    for key, label in STATUS_LABEL.items():
        g = groups[key]
        lines.append(f"\n<b>{label}: {len(g)}</b> на {rub(sum(e['data'].get('sum', 0) for e in g))}")
        limit = 20 if key == "open" else 10
        for e in g[:limit]:
            d = e["data"]
            when = fmt_time(d["closed_ts"]) if d.get("closed_ts") else fmt_time(e["ts"])
            line = (f"• {when} · №{escape(str(d.get('number', '')))} · "
                    f"{escape(str(d.get('customer') or 'без заказчика'))} · {rub(d.get('sum', 0))}")
            if key == "lost" and d.get("lost_reason"):
                line += f"\n   причина: {escape(str(d['lost_reason']))[:120]}"
            lines.append(line)
        if len(g) > limit:
            lines.append(f"<i>…и ещё {len(g) - limit}</i>")
    lines.append("\n<i>Дата у закрытых — когда менеджер отметил результат, у остальных — когда выпущено КП.</i>")
    return "\n".join(lines)[:4000]


def _status_markup(uid: int) -> types.InlineKeyboardMarkup:
    evs = kp_events("all", uid=uid)
    b = InlineKeyboardBuilder()
    for key, label in STATUS_LABEL.items():
        n = sum(1 for e in evs if _kp_status(e) == key and e["data"].get("msg"))
        if n:
            b.row(InlineKeyboardButton(text=f"📎 Файлы: {label.lower()} ({min(n, MAX_FILES)})",
                                       callback_data=f"a:sf:{uid}:{key}"))
    b.row(InlineKeyboardButton(text="⬅ Менеджеры", callback_data="a:stl"))
    return b.as_markup()


@router.callback_query(F.data.regexp(r"^a:st"))
async def cb_status(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    if call.data == "a:stl":
        text, markup = _status_picker()
    else:
        uid = int(call.data.split(":")[2])
        text, markup = _status_text(uid), _status_markup(uid)
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001 — содержимое не изменилось
        pass
    await call.answer()


@router.callback_query(F.data.regexp(r"^a:sf:"))
async def cb_status_files(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    _, _, uid, key = call.data.split(":")
    events = [e for e in kp_events("all", uid=int(uid)) if _kp_status(e) == key]
    await call.answer("Присылаю файлы…")
    if not await _send_files(call, events):
        await call.message.answer("Файлы этих КП недоступны (старые записи или сообщения удалены из чата).")


# --- рекламации: кому приходят, кнопки «в работе / решена», отбивки ---------------------------------


def _recipients(kind: str = "claim") -> list[dict]:
    return analytics.SETTINGS.setdefault(claims.KINDS[kind]["users"], [])


def _is_recipient(uid: int, kind: str | None = None) -> bool:
    kinds = [kind] if kind else list(claims.KINDS)
    return any(u["id"] == uid for k in kinds for u in _recipients(k))


def _can_act(user: types.User | None) -> bool:
    return bool(user) and (_allowed(user) or _is_recipient(user.id))


def claim_chats(kind: str = "claim") -> list[int]:
    """Кому уходит заявка: рекламационисты (или логисты) + владельцы (владелец получает всё)."""
    return [u["id"] for u in _recipients(kind)] + list(config.ADMIN_CHAT_IDS)


async def push_recipients() -> None:
    """Сообщить сайту, кому слать заявки с сайта (сайт — отдельная программа, сам этого не знает)."""
    if config.ADMIN_BOT_TOKEN:
        await claims.web_call(config.ADMIN_BOT_TOKEN, "/internal/recipients",
                              {"users": _recipients("claim"), "log_users": _recipients("log"),
                               "owners": list(config.ADMIN_CHAT_IDS)})


async def sync_loop() -> None:
    await asyncio.sleep(90)
    while True:
        try:
            await push_recipients()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(6 * 3600)


async def _owners_say(bot: Bot, text: str, skip: int | None = None) -> None:
    for chat in config.ADMIN_CHAT_IDS:
        if chat == skip:
            continue
        try:
            await bot.send_message(chat, text)
        except Exception:  # noqa: BLE001
            pass


async def _join_recipient(message: types.Message, token: str, kind: str = "claim") -> None:
    k = claims.KINDS[kind]
    tokens = analytics.SETTINGS.setdefault(k["tokens"], {})
    ts = tokens.get(token)
    if ts is None or time.time() - ts > 3 * 86400:
        await message.answer("Эта ссылка недействительна или устарела. Попросите руководителя прислать новую.")
        return
    tokens.pop(token, None)
    user = message.from_user
    if not _is_recipient(user.id, kind):
        _recipients(kind).append({"id": user.id, "name": user.full_name, "username": user.username or ""})
    analytics.save_state()
    await message.answer(HELLO[kind])
    await _owners_say(message.bot, f"✅ Подключён {k['who']}: <b>{escape(user.full_name)}</b>"
                                   + (f" (@{escape(user.username)})" if user.username else ""))
    asyncio.create_task(push_recipients())


CFG_HEAD = {"claim": "📣 <b>Рекламации — кому приходят заявки от менеджеров:</b>",
            "log": "📦 <b>Логистика — кому приходят запросы логисту:</b>"}


def _cfg_view() -> tuple[str, types.InlineKeyboardMarkup]:
    lines = ["⚙ <b>Настройки</b>"]
    b = InlineKeyboardBuilder()
    me = config.ADMIN_CHAT_IDS[0] if config.ADMIN_CHAT_IDS else None
    for kind, k in claims.KINDS.items():
        users = _recipients(kind)
        lines += ["", CFG_HEAD[kind]]
        lines += [f"• {escape(u['name'])}" + (f" (@{escape(u['username'])})" if u.get("username") else "")
                  for u in users] or ["пока никому — заявки приходят только вам"]
        for u in users:
            b.row(InlineKeyboardButton(text=f"❌ Убрать ({k['who']}): {u['name'][:26]}",
                                       callback_data=f"a:cfg:del:{kind}:{u['id']}"))
        row = [InlineKeyboardButton(text=f"➕ Пригласить: {k['who']}", callback_data=f"a:cfg:inv:{kind}")]
        if me and not _is_recipient(me, kind):
            row.append(InlineKeyboardButton(text="➕ Себя", callback_data=f"a:cfg:me:{kind}"))
        b.row(*row)
    lines += ["", "Вы в любом случае получаете копию каждой заявки и каждой отбивки (взяли в работу / решена)."]
    b.row(InlineKeyboardButton(text="📋 Порядок кнопок в боте-помощнике", callback_data="a:menu:show"))
    return "\n".join(lines), b.as_markup()


def _menu_view() -> tuple[str, types.InlineKeyboardMarkup]:
    items = kb.menu_items()
    lines = ["📋 <b>Порядок кнопок в меню бота-помощника</b>",
             "Меню — две колонки, кнопки идут слева направо, сверху вниз. "
             "Без прокрутки обычно видно первые 6 рядов (12 кнопок).", ""]
    b = InlineKeyboardBuilder()
    for i, (label, key) in enumerate(items):
        if i % 2 == 0:
            lines.append(f"<b>Ряд {i // 2 + 1}:</b> " + "  |  ".join(escape(x[0]) for x in items[i:i + 2]))
        b.row(InlineKeyboardButton(text="⬆", callback_data=f"a:menu:up:{key}"),
              InlineKeyboardButton(text=label, callback_data="a:menu:nop"),
              InlineKeyboardButton(text="⬇", callback_data=f"a:menu:down:{key}"))
    b.row(InlineKeyboardButton(text="↩ Порядок по умолчанию", callback_data="a:menu:reset"))
    lines += ["", "Сотрудники увидят новый порядок, когда меню обновится: после /start или «🏠 В главное меню»."]
    return "\n".join(lines), b.as_markup()


@router.callback_query(F.data.startswith("a:menu:"))
async def cb_menu(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    parts = call.data.split(":")
    action = parts[2]
    if action == "nop":
        await call.answer("Двигайте кнопками ⬆ / ⬇")
        return
    if action == "show":
        text, markup = _menu_view()
        await call.message.answer(text, reply_markup=markup)
        await call.answer()
        return
    keys = [key for _, key in kb.menu_items()]
    if action == "reset":
        analytics.SETTINGS.pop("menu_order", None)
    elif action in ("up", "down") and len(parts) > 3 and parts[3] in keys:
        i = keys.index(parts[3])
        j = i - 1 if action == "up" else i + 1
        if 0 <= j < len(keys):
            keys[i], keys[j] = keys[j], keys[i]
        analytics.SETTINGS["menu_order"] = keys
    analytics.save_state()
    text, markup = _menu_view()
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        pass
    await call.answer("Готово")


@router.callback_query(F.data.startswith("a:cfg:"))
async def cb_cfg(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    parts = call.data.split(":")
    action = parts[2]
    rest = parts[3:]
    kind = rest.pop(0) if rest and rest[0] in claims.KINDS else "claim"  # старые кнопки — без вида
    k = claims.KINDS[kind]
    if action == "inv":
        token = secrets.token_urlsafe(8)
        analytics.SETTINGS.setdefault(k["tokens"], {})[token] = int(time.time())
        analytics.save_state()
        me = await call.bot.me()
        prefix = "rec" if kind == "claim" else "log"
        await call.message.answer(
            f"🔗 <b>Ссылка для: {k['who']}</b> (одноразовая, действует 3 дня):\n"
            f"https://t.me/{me.username}?start={prefix}_{token}\n\n"
            "Перешлите её сотруднику: он откроет ссылку, нажмёт «Старт» — и заявки пойдут ему.")
        await call.answer()
        return
    if action == "me":
        user = call.from_user
        if not _is_recipient(user.id, kind):
            _recipients(kind).append({"id": user.id, "name": user.full_name, "username": user.username or ""})
    elif action == "del" and rest:
        analytics.SETTINGS[k["users"]] = [u for u in _recipients(kind) if u["id"] != int(rest[0])]
    analytics.save_state()
    asyncio.create_task(push_recipients())
    text, markup = _cfg_view()
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        pass
    await call.answer("Готово")


PENDING_COMMENT: dict[int, tuple[str, float]] = {}  # кто сейчас пишет комментарий к заявке


async def _get_claim(cid: str) -> dict | None:
    if cid.startswith("b"):
        return analytics.CLAIMS.get(cid)
    res = await claims.web_call(config.ADMIN_BOT_TOKEN, "/internal/claim", {"id": cid})
    return (res or {}).get("claim")


async def _set_status(cid: str, status: str, actor: str) -> tuple[dict | None, bool, bool]:
    """-> (заявка, изменилась ли, ответил ли сайт)."""
    if cid.startswith("b"):
        c = analytics.CLAIMS.get(cid)
        if c is None:
            return None, False, True
        changed = claims.apply_status(c, status, actor)
        if changed:
            analytics.save_state()
        return c, changed, True
    res = await claims.web_call(config.ADMIN_BOT_TOKEN, "/internal/status", {"id": cid, "status": status, "by": actor})
    if res is None:
        return None, False, False
    return res.get("claim"), bool(res.get("changed")), True


@router.callback_query(F.data.regexp(r"^rc:[bw]\d+:[wd]$"))
async def cb_claim(call: types.CallbackQuery) -> None:
    if not _can_act(call.from_user):
        await call.answer("Нет доступа", show_alert=True)
        return
    _, cid, act = call.data.split(":")
    status = "work" if act == "w" else "done"
    actor = call.from_user.full_name
    await call.answer()
    c, changed, online = await _set_status(cid, status, actor)
    if not online:
        await call.message.answer("⚠ Сайт сейчас не отвечает — нажмите кнопку ещё раз через минуту.")
        return
    if c is None:
        await call.message.answer("Заявка не найдена.")
        return
    try:  # кнопка могла быть на копии, которой нет в списке сообщений заявки
        await call.message.edit_text(claims.claim_text(c), reply_markup=claims.claim_markup(c))
    except Exception:  # noqa: BLE001
        pass
    if not changed:
        return  # уже было нажато — просто показали актуальное состояние
    await claims.refresh(call.bot, c)
    n = claims.number(c)
    order = claims.esc(c.get("order"))
    title = claims.kind(c)["title"]
    if status == "work":
        msg = f"🛠 <b>{title} №{n} — взято в работу</b>\nЗаказ: {order}\nЗаявку ведёт: {claims.esc(actor)}"
    else:
        msg = f"✅ <b>{title} №{n} — решено</b>\nЗаказ: {order}\nОтметил: {claims.esc(actor)}"
    await claims.notify_requester(c, msg)  # менеджеру, который подал заявку
    await _owners_say(call.bot, msg + f"\nМенеджер: {claims.esc((c.get('by') or {}).get('name'))}", skip=call.from_user.id)
    PENDING_COMMENT[call.from_user.id] = (cid, time.time())
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="Без комментария", callback_data=f"rcx:{cid}"))
    await call.message.answer(f"💬 Хотите добавить комментарий для менеджера по заявке №{n}? "
                              "Просто напишите его сообщением — я перешлю менеджеру и руководителю.",
                              reply_markup=b.as_markup())


@router.callback_query(F.data.startswith("rcx:"))
async def cb_claim_nocomment(call: types.CallbackQuery) -> None:
    PENDING_COMMENT.pop(call.from_user.id, None)
    await call.answer("Хорошо")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass


def _is_comment(message: types.Message) -> bool:
    pending = PENDING_COMMENT.get(message.from_user.id) if message.from_user else None
    if not pending or time.time() - pending[1] > 900:
        return False
    text = message.text or ""
    return bool(text) and not text.startswith("/") and text not in ADMIN_TEXT


@router.message(F.text, _is_comment)
async def msg_claim_comment(message: types.Message) -> None:
    cid, _ = PENDING_COMMENT.pop(message.from_user.id)
    actor = message.from_user.full_name
    text = message.text.strip()
    if cid.startswith("b"):
        c = analytics.CLAIMS.get(cid)
        if c is not None:
            claims.add_comment(c, actor, text)
            analytics.save_state()
    else:
        res = await claims.web_call(config.ADMIN_BOT_TOKEN, "/internal/comment", {"id": cid, "by": actor, "text": text})
        c = (res or {}).get("claim")
        if res is None:
            await message.answer("⚠ Сайт не ответил — комментарий не сохранился, напишите ещё раз позже.")
            return
    if c is None:
        await message.answer("Заявка не найдена.")
        return
    await claims.refresh(message.bot, c)
    out = (f"💬 <b>Комментарий {claims.kind(c)['of']} №{claims.number(c)}</b> ({claims.esc(actor)}):\n{claims.esc(text)}")
    await claims.notify_requester(c, out)
    await _owners_say(message.bot, out, skip=message.from_user.id)
    await message.answer("✔ Комментарий отправлен менеджеру и руководителю.")


async def claims_report(kind: str = "claim") -> tuple[str, types.InlineKeyboardMarkup]:
    k = claims.KINDS[kind]
    items = list(analytics.CLAIMS.values())
    res = await claims.web_call(config.ADMIN_BOT_TOKEN, "/internal/claims", {})
    if res and res.get("claims"):
        items += res["claims"]
    items = [c for c in items if claims.kind_of(c) == kind and c.get("status") != "cancelled"]
    items.sort(key=lambda c: -c["ts"])
    cnt = Counter(c["status"] for c in items)
    if not items:
        return f"{k['icon']} {k['plural']}: пока ни одной заявки.", types.InlineKeyboardMarkup(inline_keyboard=[])
    lines = [f"{k['icon']} <b>{k['plural']}</b>: 🆕 {cnt['new']} · 🛠 {cnt['work']} · ✅ {cnt['done']}"
             + ("" if res is not None else "\n<i>(сайт не ответил — заявки с сайта не показаны)</i>")]
    b = InlineKeyboardBuilder()
    for c in items[:15]:
        lines.append("\n" + claims.short_line(c))
        if c["status"] != "done":
            b.row(InlineKeyboardButton(text=f"Открыть №{claims.number(c)}", callback_data=f"rv:{c['id']}"))
    if len(items) > 15:
        lines.append(f"\n<i>Показаны последние 15 из {len(items)}.</i>")
    return "\n".join(lines)[:4000], b.as_markup()


@router.callback_query(F.data.regexp(r"^rv:[bw]\d+$"))
async def cb_claim_view(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    c = await _get_claim(call.data.split(":")[1])
    await call.answer()
    if c is None:
        await call.message.answer("Заявка не найдена (или сайт не отвечает).")
        return
    await call.message.answer(claims.claim_text(c), reply_markup=claims.claim_markup(c))


# --- постоянное меню под полем ввода (две колонки) -------------------------------------------------

ADMIN_MENU = [
    ("📊 Сводка", "sum"), ("👥 Менеджеры", "mgr"),
    ("🏬 Шоу-румы", "room"), ("📋 Последние КП", "kp"),
    ("🔍 КП по менеджеру", "fm"), ("🔍 КП по шоу-руму", "fr"),
    ("🕒 Лента действий", "feed"), ("📈 По дням", "days"),
    ("📅 Период отчётов", "period"), ("🌅 Сводка за вчера", "digest"),
    ("📂 КП менеджера: статусы", "status"), ("📣 Рекламации", "claims"),
    ("📦 Логистика", "logist"), ("⚙ Настройки", "settings"),
    ("ℹ Помощь", "help"),
]
ADMIN_TEXT = dict(ADMIN_MENU)
PERIOD: dict[int, str] = {}  # выбранный период отчётов у каждого владельца


def reply_menu() -> types.ReplyKeyboardMarkup:
    rows = [[types.KeyboardButton(text=t) for t, _ in ADMIN_MENU[i:i + 2]] for i in range(0, len(ADMIN_MENU), 2)]
    return types.ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True,
                                     input_field_placeholder="Выберите отчёт или напишите слово для поиска КП")


async def on_admin_button(message: types.Message) -> None:
    if not await _guard(message):
        return
    kind = ADMIN_TEXT[message.text.strip()]
    period = PERIOD.get(message.from_user.id, "7")
    if kind == "help":
        await message.answer(HELP, reply_markup=reply_menu())
    elif kind == "digest":
        yesterday = (dt.datetime.now(MSK) - dt.timedelta(days=1)).date()
        await message.answer(daily_digest_text(yesterday), reply_markup=reply_menu())
    elif kind == "period":
        await message.answer(f"📅 Период отчётов сейчас: <b>{PERIODS[period][0]}</b>. Выберите другой:",
                             reply_markup=menu_markup(period))
    elif kind == "status":
        text, markup = _status_picker()
        await message.answer(text, reply_markup=markup)
    elif kind in ("claims", "logist"):
        text, markup = await claims_report("claim" if kind == "claims" else "log")
        await message.answer(text, reply_markup=markup)
    elif kind == "settings":
        text, markup = _cfg_view()
        await message.answer(text, reply_markup=markup)
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


# --- канал для данных сайта: владелец добавил админ-бота администратором — сообщаем id канала ------


@router.my_chat_member()
async def on_added_to_chat(event: types.ChatMemberUpdated, bot: Bot) -> None:
    if event.chat.type not in ("channel", "group", "supergroup") or not _allowed(event.from_user):
        return
    if event.new_chat_member.status != "administrator":
        return
    await bot.send_message(
        event.from_user.id,
        f"✅ Админ-бот добавлен администратором в «{escape(event.chat.title or '')}».\n\n"
        f"ID для сайта (переменная <b>WEB_STORE_CHAT</b>):\n<code>{event.chat.id}</code>\n\n"
        "Здесь сайт будет хранить свои данные — не удаляйте канал и закреплённое в нём сообщение.",
    )
