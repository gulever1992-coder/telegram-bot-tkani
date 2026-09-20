"""Чтение данных о тканях и продукции из Google Таблицы, опубликованной как CSV.

Как это устроено (см. README.md):
1. В Google Таблице делается вкладка "Ткани" и вкладка "Продукция".
2. Каждая вкладка публикуется в интернет как CSV (Файл -> Поделиться -> Опубликовать в интернете).
3. Ссылки на CSV вставляются в .env (FABRICS_CSV_URL, PRODUCTS_CSV_URL).
4. Бот при каждом нажатии кнопки скачивает свежий CSV и показывает актуальные данные.

Названия колонок ищутся "нестрого" (без учёта регистра и лишних пробелов),
чтобы бот не ломался из-за мелких отличий в заголовках таблицы.
"""

from __future__ import annotations

import asyncio
import csv
import io

import aiohttp

COLUMN_ALIASES = {
    "direction": ["направление", "категория", "напр"],
    "name": ["название", "наименование", "ткань", "товар", "продукция"],
    "stock": ["остаток", "остатки", "кол-во", "количество"],
    "price": ["цена", "стоимость"],
    "availability": ["наличие", "статус", "готовность"],
}


class SheetError(RuntimeError):
    pass


async def _download_csv(url: str) -> str:
    if not url:
        raise SheetError(
            "Ссылка на таблицу не настроена. Заполните FABRICS_CSV_URL / PRODUCTS_CSV_URL в файле .env."
        )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise SheetError(f"Не удалось загрузить таблицу (код {resp.status}).")
                raw = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        raise SheetError("Нет связи с Google Таблицей. Попробуйте ещё раз через минуту.") from None
    return raw.decode("utf-8-sig")


def _find_column(headers: list[str], kind: str) -> str | None:
    normalized = {h: h.strip().lower() for h in headers}
    for header, low in normalized.items():
        for alias in COLUMN_ALIASES[kind]:
            if alias in low:
                return header
    return None


async def fetch_rows(url: str) -> list[dict]:
    text = await _download_csv(url)
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []

    col_direction = _find_column(headers, "direction")
    col_name = _find_column(headers, "name")
    col_stock = _find_column(headers, "stock")
    col_price = _find_column(headers, "price")
    col_availability = _find_column(headers, "availability")

    if not col_direction or not col_name:
        raise SheetError(
            "В таблице не найдены колонки 'Направление' и 'Название'. Проверьте заголовки первой строки."
        )

    rows = []
    for raw_row in reader:
        direction = (raw_row.get(col_direction) or "").strip()
        name = (raw_row.get(col_name) or "").strip()
        if not direction or not name:
            continue
        rows.append(
            {
                "direction": direction,
                "name": name,
                "stock": (raw_row.get(col_stock) or "").strip() if col_stock else "",
                "price": (raw_row.get(col_price) or "").strip() if col_price else "",
                "availability": (raw_row.get(col_availability) or "").strip()
                if col_availability
                else "",
            }
        )
    return rows


def unique_directions(rows: list[dict]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        if row["direction"] not in seen:
            seen.append(row["direction"])
    return seen


def rows_for_direction(rows: list[dict], direction: str) -> list[dict]:
    return [r for r in rows if r["direction"] == direction]


def format_stock_list(rows: list[dict], direction: str) -> str:
    lines = [f"📦 <b>Остатки тканей — {direction}</b>", ""]
    for row in rows_for_direction(rows, direction):
        stock = row["stock"] or "—"
        lines.append(f"• {row['name']}: <b>{stock} м</b>")
    if len(lines) == 2:
        lines.append("Нет данных по этому направлению.")
    return "\n".join(lines)


def format_price_list(rows: list[dict], direction: str) -> str:
    lines = [f"💰 <b>Прайс тканей — {direction}</b>", ""]
    for row in rows_for_direction(rows, direction):
        price = row["price"] or "—"
        lines.append(f"• {row['name']}: <b>{price} ₽/м</b>")
    if len(lines) == 2:
        lines.append("Нет данных по этому направлению.")
    return "\n".join(lines)


def format_products_list(rows: list[dict], direction: str) -> str:
    lines = [f"👗 <b>Готовая продукция — {direction}</b>", ""]
    for row in rows_for_direction(rows, direction):
        price = row["price"] or "—"
        availability = row["availability"] or "—"
        lines.append(f"• {row['name']} — {price} ₽ ({availability})")
    if len(lines) == 2:
        lines.append("Нет данных по этому направлению.")
    return "\n".join(lines)


async def fetch_pairs(url: str) -> list[tuple[str, str]]:
    """Простая таблица: первая колонка — название, вторая — значение (цена / остаток)."""
    text = await _download_csv(url)
    rows = list(csv.reader(io.StringIO(text)))
    pairs = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        name, value = row[0].strip(), row[1].strip()
        if name:
            pairs.append((name, value))
    return pairs


def normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def name_matches(query: str, name: str) -> bool:
    n = normalize(name)
    return all(word in n for word in normalize(query).split())
