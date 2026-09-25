"""Прайс мебели (Google Таблица): поиск позиции, размеры, артикул и цена «от».

Правила цены «от» (всегда минимальная):
1. Лист «АКЦИОННЫЕ ПОЗИЦИИ» в приоритете: берём минимум из двух колонок «Цена в велюто 6 цветов».
2. Иначе, по листам «… new»: «Цена опт без ткани 2025» + «4 КАТЕГОРИЯ ТКАНИ».
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import time
import urllib.parse
from dataclasses import dataclass, field

import aiohttp

import config

PROMO_SHEET = "АКЦИОННЫЕ ПОЗИЦИИ"
NEW_SHEETS = [
    "Кресла new",
    "Диваны new price",
    "Кровати new price",
    "Стулья new",
    "Пуфы new",
    "Корпус new",
]
CACHE_TTL = 600

_cache: dict[str, tuple[float, list[list[str]]]] = {}


class PriceError(RuntimeError):
    pass


@dataclass
class Item:
    title: str
    source: str
    size_raw: str = ""
    dims: tuple[int, int, int] | None = None
    length: int | None = None
    article: str = ""
    price_from: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        size = "x".join(str(v) for v in self.dims) if self.dims else (self.size_raw or "")
        price = f" · от {int(self.price_from):,}".replace(",", " ") if self.price_from else ""
        return f"{self.title}" + (f" · {size}" if size else "") + price


def normalize(text: str) -> str:
    return " ".join(str(text).lower().replace("ё", "е").split())


_LAT2CYR = [
    ("shch", "щ"), ("sch", "щ"), ("zh", "ж"), ("kh", "х"), ("ch", "ч"), ("sh", "ш"), ("ts", "ц"),
    ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("dj", "дж"), ("ph", "ф"), ("ck", "к"), ("ee", "и"),
]
_LAT_SINGLE = dict(zip("abcdefghijklmnopqrstuvwxyz", "абкдефгхийклмнопкрстуввксиз"))


def latin_to_cyrillic(text: str) -> str:
    s = normalize(text)
    for lat, cyr in _LAT2CYR:
        s = s.replace(lat, cyr)
    return "".join(_LAT_SINGLE.get(ch, ch) for ch in s)


def to_number(value: str) -> float | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", str(value).replace("\xa0", "").replace(" ", ""))
    cleaned = cleaned.replace(",", ".")
    if not cleaned or cleaned in {".", "-"}:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if number > 0 else None


def parse_dims(text: str) -> tuple[int, int, int] | None:
    numbers = re.findall(r"\d+(?:[.,]\d+)?", str(text or ""))
    if len(numbers) == 3:
        return tuple(int(float(n.replace(",", "."))) for n in numbers)  # type: ignore[return-value]
    return None


def first_number(text: str) -> int | None:
    m = re.search(r"\d+", str(text or ""))
    return int(m.group()) if m else None


async def _fetch_sheet(name: str) -> list[list[str]]:
    cached = _cache.get(name)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    url = (
        f"https://docs.google.com/spreadsheets/d/{config.PRICELIST_SHEET_ID}/gviz/tq"
        f"?tqx=out:csv&sheet={urllib.parse.quote(name)}"
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise PriceError(f"Не удалось загрузить лист «{name}» (код {resp.status}).")
                raw = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        raise PriceError("Нет связи с прайсом (Google Таблицей). Попробуйте ещё раз через минуту.") from None
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    _cache[name] = (time.time(), rows)
    return rows


def _cell(row: list[str], i: int | None) -> str:
    if i is None or i >= len(row):
        return ""
    return row[i].strip()


def _parse_promo(rows: list[list[str]]) -> list[Item]:
    items = []
    for row in rows[1:]:
        title = _cell(row, 0)
        if not title:
            continue
        prices = [to_number(_cell(row, i)) for i in (2, 3)]
        prices = [p for p in prices if p]
        size_raw = _cell(row, 1)
        items.append(
            Item(
                title=title[:1].upper() + title[1:],
                source="акция",
                size_raw=size_raw,
                dims=parse_dims(size_raw),
                length=first_number(size_raw),
                price_from=min(prices) if prices else None,
            )
        )
    return items


def _parse_new(rows: list[list[str]], source: str) -> list[Item]:
    header_idx = None
    for i, row in enumerate(rows[:8]):
        joined = " ".join(normalize(c) for c in row[:6])
        if "артикул" in joined or "атикул" in joined or (row and normalize(row[0]).startswith("наимен")):
            header_idx = i
            break
    if header_idx is None:
        return []
    header = [normalize(c) for c in rows[header_idx]]
    art_col = next((i for i, h in enumerate(header) if "артикул" in h or "атикул" in h), 1)
    size_col = next((i for i, h in enumerate(header) if "габарит" in h or "размер" in h), 2)
    opt_cols = [i for i, h in enumerate(header) if h.startswith("цена опт")]
    opt_2025 = next((i for i in opt_cols if "2025" in header[i]), opt_cols[-1] if opt_cols else None)
    cat4 = next((i for i, h in enumerate(header) if h.startswith("4 кат")), None)

    items = []
    for row in rows[header_idx + 1:]:
        title = _cell(row, 0)
        if not title:
            continue
        opt = to_number(_cell(row, opt_2025))
        surcharge = to_number(_cell(row, cat4))
        price = opt + (surcharge or 0) if opt else None
        size_raw = _cell(row, size_col)
        items.append(
            Item(
                title=title[:1].upper() + title[1:],
                source=source,
                size_raw=size_raw,
                dims=parse_dims(size_raw),
                length=first_number(size_raw),
                article=_cell(row, art_col).replace(".0", ""),
                price_from=price,
            )
        )
    return items


async def _load_new_items() -> list[Item]:
    sheets = await asyncio.gather(*[_fetch_sheet(n) for n in NEW_SHEETS], return_exceptions=True)
    items: list[Item] = []
    for name, rows in zip(NEW_SHEETS, sheets):
        if isinstance(rows, PriceError):
            raise rows
        if isinstance(rows, Exception):
            continue
        items.extend(_parse_new(rows, name.replace(" new price", "").replace(" new", "")))
    return items


def _matches(query: str, title: str) -> bool:
    name = normalize(title)
    words = normalize(query).split()
    return bool(words) and all(w in name for w in words)


async def search(query: str) -> list[Item]:
    variants = [query]
    alt = latin_to_cyrillic(query)
    if alt != normalize(query):
        variants.append(alt)

    promo = _parse_promo(await _fetch_sheet(PROMO_SHEET))
    found = [i for i in promo if any(_matches(v, i.title) for v in variants)]
    if found:
        return found[:15]
    new_items = await _load_new_items()
    return [i for i in new_items if any(_matches(v, i.title) for v in variants)][:15]


async def enrich(item: Item) -> Item:
    """Для позиции из акций дополняет артикул и габариты из листов «new»."""
    if item.source != "акция":
        return item
    try:
        new_items = await _load_new_items()
    except PriceError:
        return item
    same = [i for i in new_items if normalize(i.title) == normalize(item.title)]
    if not same:
        same = [i for i in new_items if normalize(item.title) in normalize(i.title)]
    if not same:
        return item
    pick = None
    if item.length:
        pick = next((i for i in same if i.length == item.length), None)
    if pick is None and len(same) == 1:
        pick = same[0]
    if pick is None:
        return item
    if not item.dims and pick.dims:
        item.dims = pick.dims
    if not item.article:
        item.article = pick.article
    return item
