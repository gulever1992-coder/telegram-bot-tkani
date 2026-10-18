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


# --- ближайший город из таблицы, если нужного города в ней нет ------------------------------------
# Координаты: utils/ru_cities.json (≈1100 городов РФ, данные hflabs/city); посёлки и сёла, которых там нет,
# ищем через OpenStreetMap Nominatim (1 запрос, результат кэшируем).

import asyncio
import json
import math
import os

_CITIES: dict[str, tuple[float, float, int]] = {}
_GEO_CACHE: dict[str, tuple[float, float] | None] = {}
_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_UA = "CreaticaBot/1.0 (delivery estimate)"
_geo_lock = asyncio.Lock()
_PLACES = {"city", "town", "village", "hamlet", "locality", "isolated_dwelling", "suburb", "quarter", "municipality"}


def _cities() -> dict[str, tuple[float, float, int]]:
    if not _CITIES:
        path = os.path.join(os.path.dirname(__file__), "ru_cities.json")
        with open(path, encoding="utf-8") as f:
            for name, _region, lat, lon, pop in json.load(f):
                key = _normalize(name)
                if key not in _CITIES or pop > _CITIES[key][2]:  # одноимённые — берём крупный
                    _CITIES[key] = (lat, lon, pop)
    return _CITIES


def _names(city: str) -> list[str]:
    """«ВОЛЖСКИЙ (Волгоград)» -> ["волжский", "волгоград"]; «Братск (Иркутская обл.)» -> ["братск", ...]."""
    main = re.sub(r"\(.*?\)", "", city)
    out = [_normalize(main)]
    for inner in re.findall(r"\((.*?)\)", city):
        out.append(_normalize(inner))
    return [n for n in out if n]


async def _nominatim(query: str) -> tuple[float, float] | None:
    key = _normalize(query)
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    params = {"q": query, "countrycodes": "ru", "format": "json", "limit": "10", "accept-language": "ru"}
    async with _geo_lock:  # правила Nominatim: не чаще 1 запроса в секунду
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
                async with session.get(_NOMINATIM, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    found = await resp.json() if resp.status == 200 else []
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None  # сеть — не кэшируем, попробуем в следующий раз
        await asyncio.sleep(1)
    # только населённые пункты (без магазинов и улиц); из одноимённых — самый крупный (меньший place_rank)
    found = [f for f in found if f.get("addresstype") in _PLACES]
    found.sort(key=lambda f: (f.get("place_rank", 99), -float(f.get("importance") or 0)))
    point = (float(found[0]["lat"]), float(found[0]["lon"])) if found else None
    _GEO_CACHE[key] = point
    return point


async def locate(name: str) -> tuple[float, float] | None:
    """Координаты города: сначала справочник, потом Nominatim."""
    for n in _names(name):
        hit = _cities().get(n)
        if hit:
            return hit[0], hit[1]
    return await _nominatim(re.sub(r"\(.*?\)", "", name).strip() or name)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


HOME = {"Москва": (55.7540, 37.6204), "Санкт-Петербург": (59.9390, 30.3158)}
HOME_RADIUS_KM = 70  # ≈ 50 км от МКАД/КАД — возим сами, считаем по городскому прайсу


async def home_city(query: str) -> tuple[str, int] | None:
    """Если адрес в зоне нашей доставки (Москва/СПб + ~50 км) — (город, км от центра)."""
    point = await locate(query)
    if point is None:
        return None
    for name, center in HOME.items():
        d = _km(point, center)
        if d <= HOME_RADIUS_KM:
            return name, round(d)
    return None


async def nearest(query: str) -> tuple[dict, int] | None:
    """Ближайший к запросу город из таблицы: (строка таблицы, км по прямой). None — запрос не нашли на карте."""
    point = await locate(query)
    if point is None:
        return None
    best: tuple[float, dict] | None = None
    for row in await _fetch_rows():
        p = await locate(row["city"])  # чего нет в справочнике — один раз через Nominatim, дальше из кэша
        if p is None:
            continue
        d = _km(point, p)
        if best is None or d < best[0]:
            best = (d, row)
    return (best[1], round(best[0])) if best else None
