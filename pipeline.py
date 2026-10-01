"""План продаж менеджера (сделки): хранится в памяти процесса и в общем журнале событий.

Журнал (analytics/admin-бот) переживает перезапуски бота, поэтому список сделок
восстанавливается оттуда при старте — отдельная база данных не нужна.
"""

from __future__ import annotations

import analytics

STAGES = [
    "Первый контакт", "Выезд / замер", "КП отправлено",
    "Согласование", "Договор / предоплата", "Ожидает поставки", "Доставка / сборка",
]

STATUSES = [
    ("green", "🟢 В работе"),
    ("yellow", "🟡 Пауза / думает"),
    ("red", "🔴 Под угрозой срыва"),
    ("done", "✅ Сделка закрыта"),
    ("lost", "❌ Сорвалась"),
]
STATUS_LABEL = dict(STATUSES)

_cache: dict[int, list[dict]] = {}
_loaded = False


def _ensure_loaded() -> None:
    """Восстанавливает последний снимок сделок каждого менеджера из журнала (один раз за запуск)."""
    global _loaded
    if _loaded:
        return
    for e in analytics.EVENTS:
        if e.get("kind") == "pipeline":
            _cache[e["uid"]] = e["data"].get("deals", [])
    _loaded = True


def deals(uid: int) -> list[dict]:
    _ensure_loaded()
    return _cache.get(uid, [])


def _save(uid: int, items: list[dict]) -> None:
    _cache[uid] = items
    analytics.track(uid, "pipeline", "", data={"deals": items})


def new_deal() -> dict:
    return {"id": 0, "client": "", "amount": None, "arrived": "", "stage": "", "status": "green",
            "planned_date": "", "blocker": "", "lost_reason": ""}


def add(uid: int, deal: dict) -> dict:
    items = list(deals(uid))
    deal = dict(deal)
    deal["id"] = max((d["id"] for d in items), default=0) + 1
    items.append(deal)
    _save(uid, items)
    return deal


def update(uid: int, deal_id: int, **patch) -> None:
    items = [dict(d, **patch) if d["id"] == deal_id else d for d in deals(uid)]
    _save(uid, items)


def remove(uid: int, deal_id: int) -> None:
    items = [d for d in deals(uid) if d["id"] != deal_id]
    _save(uid, items)


def get(uid: int, deal_id: int) -> dict | None:
    return next((d for d in deals(uid) if d["id"] == deal_id), None)


def total(items: list[dict]) -> int:
    return sum(d.get("amount") or 0 for d in items)
