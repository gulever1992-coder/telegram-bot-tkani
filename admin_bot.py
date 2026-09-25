"""Админ-бот для владельца: сюда приходят КП менеджеров, а по запросу — статистика.

Работает в том же процессе, что и основной бот (см. bot.py). Доступ — только id из ADMIN_CHAT_IDS.
"""

from __future__ import annotations

import datetime as dt
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
    return b.as_markup()


HELP = ("Выберите период и раздел ниже. Сами КП приходят сюда сразу, как менеджер их оформил.\n"
        "Команды: /stats — сводка, /managers, /rooms, /kp, /feed.")


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
    await message.answer("👋 <b>Creatica · панель владельца</b>\n" + HELP, reply_markup=menu_markup())


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


@router.callback_query(F.data.startswith("a:"))
async def cb_report(call: types.CallbackQuery) -> None:
    if not _allowed(call.from_user):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    _, kind, period = call.data.split(":")
    if kind == "p":  # смена периода — перерисовать сводку
        kind = "sum"
    if period not in PERIODS:
        period = "7"
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
