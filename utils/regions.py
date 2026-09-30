"""Доставка в регионы: справочник городов (ТК / частный перевозчик) из гугл-таблицы.

Это не расчёт по формуле — в таблице готовые ценовые диапазоны от разных ТК для двух эталонных
изделий (диван 2300×1060×850, кресло 870×930×1060). Мы находим город и подсказываем диапазон
для дивана/кресла; для остальных позиций или количества больше 1 таблица цифр не даёт.
"""

from __future__ import annotations

import csv
import io
import re
import time

import aiohttp

SHEET_ID = "1d3UN8U5H-SEFK_xApNV34b5PWk4G8ah3-NUuYz_3cxQ"
GID = "2086428513"
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"
CACHE_TTL = 600

_cache: dict[str, list[dict] | float] = {"ts": 0.0, "rows": []}


class RegionError(RuntimeError):
    pass


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower().replace("ё", "е"))


async def _fetch_rows() -> list[dict]:
    now = time.time()
    if _cache["rows"] and now - _cache["ts"] < CACHE_TTL:
        return _cache["rows"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    raise RegionError(f"таблица регионов ответила {resp.status}")
                text = await resp.text()
    except aiohttp.ClientError as exc:
        raise RegionError(f"не удалось открыть таблицу регионов: {exc}") from exc

    rows: list[dict] = []
    header_seen = False
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip():
            continue
        if _normalize(row[0]) == "город доставки":
            header_seen = True
            continue
        if not header_seen:
            continue
        rows.append({
            "city": row[0].strip(),
            "from": row[1].strip() if len(row) > 1 else "",
            "tk": row[2].strip() if len(row) > 2 else "",
            "ati": row[3].strip() if len(row) > 3 else "",
        })
    _cache["rows"], _cache["ts"] = rows, now
    return rows


async def find(query: str) -> list[dict]:
    """Города, чьё название (в любом написании в скобках) содержит запрос."""
    q = _normalize(query)
    if not q:
        return []
    rows = await _fetch_rows()
    return [r for r in rows if q in _normalize(r["city"])]


_PRICE_RE = {
    "sofa": re.compile(r"диван\D{0,4}([\d\s]{4,})\s*-\s*([\d\s]{4,})", re.I),
    "armchair": re.compile(r"кресл\w*\D{0,4}([\d\s]{3,})\s*-\s*([\d\s]{3,})", re.I),
}


def _nums(text: str, key: str) -> tuple[list[int], list[int]]:
    lows, highs = [], []
    for m in _PRICE_RE[key].finditer(text or ""):
        lo, hi = re.sub(r"\D", "", m.group(1)), re.sub(r"\D", "", m.group(2))
        if lo and hi:
            lows.append(int(lo))
            highs.append(int(hi))
    return lows, highs


def price_range(entry: dict, key: str) -> tuple[int, int] | None:
    """Диапазон цены за 1 штуку эталонного дивана/кресла по всем упомянутым ТК/АТИ. None — данных нет."""
    lows, highs = _nums(f"{entry.get('tk', '')}\n{entry.get('ati', '')}", key)
    return (min(lows), max(highs)) if lows else None


def item_key(name: str) -> str | None:
    low = (name or "").lower()
    if "див" in low:
        return "sofa"
    if "кресл" in low:
        return "armchair"
    return None


def parse_items(text: str) -> list[tuple[str, int]]:
    """«Диван 1 шт, кресло 2» -> [("Диван", 1), ("кресло", 2)]."""
    items = []
    for raw in re.split(r"[,\n;]+", text):
        raw = raw.strip()
        if not raw:
            continue
        m = re.search(r"(\d+)", raw)
        qty = int(m.group(1)) if m else 1
        name = re.sub(r"\d+\s*(шт\.?|штук\w*)?", "", raw, flags=re.I).strip(" -:.")
        if name:
            items.append((name, max(qty, 1)))
    return items
