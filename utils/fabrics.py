"""Поиск ткани по фабрике: цена за отрез, категория и цвета в наличии.

Данные берутся из Google Таблицы «Ткани и продукция — бот»:
- вкладка «Прайс»:   A название ткани, B цена за погонный метр (отрез), C тип ткани,
                      D поставщик (фабрика), E примечание, F категория ткани;
- вкладка «Остатки»: A название (коллекция + цвет), B остаток, м, C наличие,
                      D примечание, E поставщик (фабрика), F статус коллекции.

Колонки ищутся по заголовкам (без учёта регистра); если заголовок не найден —
берётся колонка по её месту, как описано выше.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from utils.sheets import _download_csv

# --- чтение таблицы ----------------------------------------------------------

PRICE_COLUMNS = {
    "name": (["название"], 0),
    "price": (["цена"], 1),
    "kind": (["тип"], 2),
    "supplier": (["поставщик", "фабрика"], 3),
    "note": (["примечание"], 4),
    "category": (["категория"], 5),
}
STOCK_COLUMNS = {
    "name": (["название"], 0),
    "meters": (["остаток"], 1),
    "availability": (["наличие"], 2),
    "note": (["примечание"], 3),
    "supplier": (["поставщик", "фабрика"], 4),
    "status": (["статус"], 5),
}


@dataclass
class Fabric:
    name: str
    price: str
    kind: str
    supplier: str
    note: str
    category: str
    keys: list[list[str]] = field(default_factory=list)  # варианты названия коллекции (слова)


@dataclass
class StockItem:
    name: str
    meters: str
    availability: str
    note: str
    supplier: str
    status: str
    words: list[str] = field(default_factory=list)


def _column_index(headers: list[str], aliases: list[str], fallback: int) -> int:
    low = [h.strip().lower() for h in headers]
    for i, h in enumerate(low):
        if any(a in h for a in aliases):
            return i
    return fallback


def _parse(text: str, columns: dict) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    headers = rows[0]
    idx = {key: _column_index(headers, aliases, fb) for key, (aliases, fb) in columns.items()}
    out = []
    for row in rows[1:]:
        item = {key: (row[i].strip() if i < len(row) else "") for key, i in idx.items()}
        if item["name"]:
            out.append(item)
    return out


def parse_price(text: str) -> list[Fabric]:
    fabrics = []
    for r in _parse(text, PRICE_COLUMNS):
        f = Fabric(**r)
        f.keys = collection_keys(f.name)
        fabrics.append(f)
    return fabrics


def parse_stock(text: str) -> list[StockItem]:
    items = []
    for r in _parse(text, STOCK_COLUMNS):
        s = StockItem(**r)
        s.words = words(s.name)
        items.append(s)
    return items


async def load(price_url: str, stock_url: str) -> tuple[list[Fabric], list[StockItem]]:
    import asyncio

    price_text, stock_text = await asyncio.gather(_download_csv(price_url), _download_csv(stock_url))
    return parse_price(price_text), parse_stock(stock_text)


# --- нормализация названий --------------------------------------------------

# латинские буквы, которые пишут вместо похожих русских («Aкцент», «Taкт»)
_LAT2CYR = str.maketrans("aceopxytkmhbABCEHKMOPTXY", "асеорхуткмнвАВСЕНКМОРТХУ")
_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)

# слова, которые не являются названием коллекции
STOP_WORDS = {
    "new", "новинка", "easy", "clean", "waterproof", "ткань", "кож", "зам", "кожа",
    "натуральная", "искусственная", "экокожа", "и", "the",
}


def _fix_mixed(word: str) -> str:
    if _CYR.search(word) and _LAT.search(word):
        return word.translate(_LAT2CYR)
    return word


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(_fix_mixed(w) for w in text.split())


def words(text: str) -> list[str]:
    return normalize(text).split()


def collection_keys(name: str) -> list[list[str]]:
    """Варианты названия коллекции: «Alfa / Альфа» -> [["alfa"], ["альфа"]],
    «Лейс (Lace)» -> [["лейс"], ["lace"]], «LOFT Easy Clean / ЛОФТ» -> [["loft"], ["лофт"]].
    Многословные названия дают и укороченные ключи: «Velutto Soft» -> ["velutto", "soft"], ["velutto"].
    Ключи идут от самого точного к самому общему."""
    variants: list[list[str]] = []
    for part in re.split(r"[/()]|,", name):
        w = [x for x in words(part) if x not in STOP_WORDS and not x.isdigit()]
        if w and len(w[0]) >= 3:
            variants.append(w)
    # «Ализэ полоса (Alize stripe)»: в остатках пишут «Alize полоса» — смешиваем варианты
    mixed = [[a[0]] + b[1:] for a in variants for b in variants if a is not b and len(b) > 1]
    keys: list[list[str]] = []
    for w in variants + mixed:
        for n in range(len(w), 0, -1):
            if w[:n] not in keys:
                keys.append(w[:n])
    keys.sort(key=len, reverse=True)
    return keys


def _name_len(fabric: "Fabric") -> int:
    """Длина самого короткого варианта названия — при равенстве совпадений строка остатков
    достаётся более «общей» ткани («Велютто 01» — это VELUTTO, а не «Велютто Софт»)."""
    lengths = [
        len([x for x in words(part) if x not in STOP_WORDS and not x.isdigit()])
        for part in re.split(r"[/()]|,", fabric.name)
    ]
    return min([n for n in lengths if n] or [99])


# --- поиск -------------------------------------------------------------------


def suppliers(fabrics: list[Fabric]) -> list[str]:
    seen: list[str] = []
    for f in fabrics:
        if f.supplier and f.supplier not in seen:
            seen.append(f.supplier)
    return seen


def find_fabrics(fabrics: list[Fabric], supplier: str, query: str) -> list[Fabric]:
    q = words(query)
    if not q:
        return []
    pool = [f for f in fabrics if f.supplier == supplier]
    # 1) точное совпадение с одним из вариантов названия
    exact = [f for f in pool if any(key == q[: len(key)] and len(q) == len(key) for key in f.keys)]
    if exact:
        return exact
    # 2) все слова запроса есть в названии (можно часть слова: «вел» -> «велютто»)
    res = []
    for f in pool:
        name_words = words(f.name)
        if all(any(nw.startswith(w) for nw in name_words) for w in q):
            res.append(f)
    return res


def similar(fabrics: list[Fabric], supplier: str, query: str, n: int = 6) -> list[str]:
    """Похожие названия у фабрики — если точного совпадения нет (опечатка, другая раскладка)."""
    import difflib

    q = normalize(query)
    scored = []
    for f in fabrics:
        if f.supplier != supplier:
            continue
        best = max((difflib.SequenceMatcher(None, q, " ".join(k)).ratio() for k in f.keys), default=0)
        scored.append((best, f.name))
    scored.sort(reverse=True)
    return [name for score, name in scored[:n] if score >= 0.6]


def _match_pos(item_words: list[str], key: list[str]) -> tuple[int, int]:
    """(позиция, сколько слов заняла коллекция) в названии остатка, или (-1, 0).
    «VelvetLUX» в прайсе находит «Velvet Lux 69» в остатках (слова склеиваются)."""
    n = len(key)
    for i in range(len(item_words) - n + 1):
        if item_words[i : i + n] == key:
            return i, n
    if n == 1:
        for i in range(len(item_words) - 1):
            if item_words[i] + item_words[i + 1] == key[0]:
                return i, 2
    return -1, 0


def _best_match(s: StockItem, fabric: Fabric) -> tuple[int, int, int]:
    """(длина совпавшего ключа, позиция, слов в названии) — (0, -1, 0), если ткань не подходит."""
    best = (0, -1, 0)
    for key in fabric.keys:
        pos, span = _match_pos(s.words, key)
        if pos >= 0 and len(key) > best[0]:
            best = (len(key), pos, span)
    return best


def stock_for(fabric: Fabric, stock: list[StockItem], fabrics: list[Fabric]) -> list[tuple[StockItem, str]]:
    """Строки остатков той же фабрики, относящиеся к ткани, и название цвета.
    Если строка подходит нескольким тканям фабрики («Alize» и «Alize полоса»),
    она достаётся той, чьё название совпало длиннее."""
    rivals = [f for f in fabrics if f.supplier == fabric.supplier and f is not fabric]
    out = []
    for s in stock:
        if s.supplier != fabric.supplier:
            continue
        length, pos, span = _best_match(s, fabric)
        if not length:
            continue
        mine = (length, -_name_len(fabric))
        if any((_best_match(s, r)[0], -_name_len(r)) > mine for r in rivals if _best_match(s, r)[0]):
            continue
        # цвет — то, что стоит после названия коллекции (слова считаем так же, как при поиске)
        color = " ".join(s.words[pos + span :]).strip()
        raw = s.name.split()
        if len(raw) == len(s.words):  # сохраняем исходное написание, если разбивка совпала
            color = " ".join(raw[pos + span :]).strip()
        out.append((s, color or s.name))
    return out


def stock_only(stock: list[StockItem], supplier: str, query: str) -> Fabric | None:
    """Ткани нет в прайсе, но она есть в остатках фабрики: возвращаем «ткань» без цены."""
    q = [w for w in words(query) if w not in STOP_WORDS]
    if not q:
        return None
    probe = Fabric(name=query.strip(), price="", kind="", supplier=supplier, note="", category="", keys=[q])
    return probe if any(_best_match(s, probe)[0] for s in stock if s.supplier == supplier) else None


# --- ответ -------------------------------------------------------------------


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_available(s: StockItem) -> str:
    """'yes' — есть, 'ask' — мало / по запросу, 'no' — нет."""
    a = s.availability.lower()
    if a.startswith("есть"):
        return "yes"
    if a.startswith("нет"):
        return "no"
    if "мало" in a or "запрос" in a or "звон" in a or "уточн" in a:
        return "ask"
    if s.meters and s.meters not in ("0", "0,0"):
        return "yes"
    return "no" if not a else "ask"


def format_fabric(fabric: Fabric, stock_rows: list[tuple[StockItem, str]], has_stock_data: bool,
                  limit: int = 3500) -> str:
    lines = [f"🧵 <b>{esc(fabric.name)}</b> — {esc(fabric.supplier)}"]
    price = f"{esc(fabric.price)} ₽ за погонный метр" if fabric.price else "нет в прайсе"
    lines.append(f"💰 Цена (отрез): <b>{price}</b>")
    lines.append(f"🏷 Категория: <b>{esc(fabric.category) or '—'}</b>")
    if fabric.kind:
        lines.append(f"📋 Тип: {esc(fabric.kind)}")
    if fabric.note:
        lines.append(f"ℹ️ {esc(fabric.note)}")

    if not has_stock_data:
        lines.append("\n📦 Остатков по этой фабрике в таблице нет — уточните наличие у поставщика.")
        return "\n".join(lines)
    if not stock_rows:
        lines.append("\n📦 В остатках фабрики эта ткань не найдена — уточните наличие у поставщика.")
        return "\n".join(lines)

    yes, ask, no = [], [], []
    for s, color in stock_rows:
        label = esc(color)
        if s.meters:
            label += f" (более {s.meters} м)" if "более" in s.note.lower() else f" ({s.meters} м)"
        elif s.note and _is_available(s) != "no" and len(s.note) <= 40:
            label += f" ({esc(s.note)})"
        {"yes": yes, "ask": ask, "no": no}[_is_available(s)].append(label)

    lines.append(f"\n✅ <b>В наличии ({len(yes)}):</b> " + (", ".join(yes) if yes else "нет"))
    if ask:
        lines.append(f"❓ <b>Мало / по запросу ({len(ask)}):</b> " + ", ".join(ask))
    if no:
        lines.append(f"❌ <b>Нет в наличии ({len(no)}):</b> " + ", ".join(no))

    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit - 60].rsplit(",", 1)[0] + "…\n<i>Список длинный — полный в таблице.</i>"
    return text
