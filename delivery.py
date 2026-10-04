"""Стоимость доставки по Москве и Санкт-Петербургу (тарифы ООО «Феникс», приложение №3 к договору).

Один расчёт может включать несколько позиций (диван + кресла + тумба и т.п.) — доставка,
подъём и сборка считаются по каждой позиции отдельно и складываются.
Для других регионов расчёт не делаем — тариф даёт транспортная компания, ссылка на таблицу городов
и ТК: см. delivery_flow.REGIONS_URL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

MKAD_FREE_KM = 10
EXTRA_KM_PRICE = 70
OVER_LIMIT_UNIT = 500  # свыше прайса — за штуку сверх максимума в брекете

CATEGORIES = [
    ("armchair", "Кресло"),
    ("small", "Стул / Пуф / Банкетка / Стол"),
    ("case", "Комод / Тумба / Консоль"),
    ("big", "Кровать / Диван / Матрас"),
]
CATEGORY_LABEL = dict(CATEGORIES)


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def delivery_price(category: str, qty: int, long_case: bool = False) -> int:
    """Доставка до подъезда, в пределах МКАД (без подъёма и сборки), за все qty штук позиции."""
    if category in ("armchair", "small"):
        if qty <= 1:
            return 3800
        if qty <= 3:
            return 4900
        if qty <= 6:
            return 6500
        return 6500 + OVER_LIMIT_UNIT * (qty - 6)
    if category == "case":
        unit = 5500 if long_case else 4500
        return unit * qty
    if category == "big":
        if qty <= 1:
            return 5500
        if qty <= 2:
            return 6800
        return 6800 + OVER_LIMIT_UNIT * (qty - 2)
    return 0


def extra_km_cost(distance_km: int) -> int:
    return max(0, distance_km - MKAD_FREE_KM) * EXTRA_KM_PRICE


# --- подъём (лифт / вручную) — тариф на позицию, считаем по каждой отдельно -----------------

LIFT = {
    "armchair": {"lift": 1250, "lift_units": 2, "manual_per_floor": 300},
    "small": {"lift": 1200, "lift_units": 3, "lift_over": 2000, "lift_over_units": 6, "manual_per_floor": 300},
    "big": {"lift": 2550, "lift_units": 2, "manual_per_floor": 400},
}


def lift_cost_elevator(category: str, qty: int) -> int | None:
    """Занос в помещение + подъём на лифте, за все qty штук. None — тариф не задан (комод/тумба/консоль)."""
    t = LIFT.get(category)
    if not t:
        return None
    if category == "small":
        if qty <= t["lift_units"]:
            return t["lift"]
        if qty <= t["lift_over_units"]:
            return t["lift_over"]
        trips = _ceil_div(qty, t["lift_over_units"])
        return t["lift_over"] * trips
    trips = _ceil_div(qty, t["lift_units"])
    return t["lift"] * trips


def lift_cost_manual(category: str, floors: int, qty: int) -> int | None:
    """Ручной подъём тарифицируется «за 1 ед.» — умножаем на количество."""
    t = LIFT.get(category)
    if not t:
        return None
    return t["manual_per_floor"] * max(floors, 0) * qty


# --- ручной подъём по правилам логиста --------------------------------------------------------
# диван/кровать: за каждую часть за этаж, ставка зависит от сложности; кресло: 500 ₽ за шт за этаж

MANUAL_BIG_RATES = {"normal": 600, "hard": 700, "very_hard": 800}
MANUAL_BIG_LABELS = {
    "normal": "обычный",
    "hard": "сложный (пронос через этаж до следующего пролёта)",
    "very_hard": "очень сложный (узкая лестница)",
}
MANUAL_ARMCHAIR = 500


def lift_cost_manual_item(it: "Item", floors: int, difficulty: str = "normal") -> int | None:
    floors = max(floors, 0)
    if it.category == "big":
        return MANUAL_BIG_RATES.get(difficulty, 600) * max(it.parts, 1) * it.qty * floors
    if it.category == "armchair":
        return MANUAL_ARMCHAIR * it.qty * floors
    return lift_cost_manual(it.category, floors, it.qty)


# --- сборка: стоимость берётся из прайса по позиции; эти цены — только если позиции нет в прайсе ---

ASSEMBLY_ARMCHAIR = 1250
ASSEMBLY_SOFA = {"legs": 1300, "full": 2700}
ASSEMBLY_BED = {"regular": 2900, "special": 4900}  # special: Некст, Либерти, Бостон
ASSEMBLY_PRICE = {
    "armchair": ASSEMBLY_ARMCHAIR, "sofa_legs": ASSEMBLY_SOFA["legs"], "sofa_full": ASSEMBLY_SOFA["full"],
    "bed_regular": ASSEMBLY_BED["regular"], "bed_special": ASSEMBLY_BED["special"],
}

TIME_SLOT_FEE = 2000
CARRY_EXTRA_UNIT_M = 10
CARRY_EXTRA_PRICE = 200
CARRY_FREE_M = 15
SAME_CAR_UNIT = 500  # догруз в ту же машину к основной доставке, за 1 шт


def carry_extra_cost(meters: int, pieces: int = 1) -> int:
    """200 ₽ за каждые 10 м сверх 15 м (пропорционально: 35 м = 700 ₽) — за каждое место."""
    extra = max(0, meters - CARRY_FREE_M)
    return round(extra * CARRY_EXTRA_PRICE / CARRY_EXTRA_UNIT_M) * max(pieces, 0)


@dataclass
class Item:
    category: str
    name: str = ""
    qty: int = 1
    long_case: bool = False
    assembly_price: int = 0  # сборка за 1 шт, 0 — без сборки
    parts: int = 1  # из скольких частей диван/кровать (для подъёма и проноса)

    @property
    def label(self) -> str:
        return self.name.strip() or CATEGORY_LABEL.get(self.category, self.category)

    @property
    def pieces(self) -> int:
        return self.qty * (max(self.parts, 1) if self.category == "big" else 1)


def _names(items: list[Item]) -> str:
    return ", ".join(f"{it.label} × {it.qty}" for it in items)


def delivery_lines(items: list[Item]) -> list[tuple[str, int]]:
    """Доставка одной машиной: основной тариф по самой крупной группе, остальное — догруз по 500 ₽/шт.
    Пример: диван + кровать (крупногабарит, 2 шт) = 6800, + 2 кресла по 500 = 7800."""
    big = [i for i in items if i.category == "big"]
    seats = [i for i in items if i.category in ("armchair", "small")]
    cases = [i for i in items if i.category == "case"]
    qty = lambda group: sum(i.qty for i in group)
    lines: list[tuple[str, int]] = []
    if big:
        lines.append((f"Доставка, крупногабарит {qty(big)} шт: {_names(big)}", delivery_price("big", qty(big))))
        rest = seats + cases
    elif seats:
        lines.append((f"Доставка: {_names(seats)}", delivery_price("armchair", qty(seats))))
        rest = cases
    else:
        for it in cases:
            lines.append((f"Доставка: {it.label} × {it.qty}", delivery_price("case", it.qty, it.long_case)))
        rest = []
    if rest:
        lines.append((f"Догруз в ту же машину: {_names(rest)} ({SAME_CAR_UNIT} ₽/шт)", SAME_CAR_UNIT * qty(rest)))
    return lines


@dataclass
class Quote:
    items: list[Item] = field(default_factory=list)
    distance_km: int = 0
    lift_mode: str = "none"  # none | elevator | manual — один способ на весь заказ (один адрес, один заезд)
    floors: int = 0
    lift_difficulty: str = "normal"
    time_slot: bool = False
    carry_extra_m: int = 0
    lines: list[tuple[str, int]] = field(default_factory=list)

    def compute(self) -> int:
        self.lines = delivery_lines(self.items)
        for it in self.items:
            if self.lift_mode == "elevator":
                c = lift_cost_elevator(it.category, it.qty)
                if c:
                    self.lines.append((f"Подъём лифтом: {it.label}", c))
                else:
                    self.lines.append((f"Подъём: {it.label} — индивидуально, уточните у логистики", 0))
            elif self.lift_mode == "manual":
                c = lift_cost_manual_item(it, self.floors, self.lift_difficulty)
                if c:
                    detail = ""
                    if it.category == "big":
                        rate = MANUAL_BIG_RATES.get(self.lift_difficulty, 600)
                        detail = f" ({it.pieces} част. × {rate} ₽ × {self.floors} эт.)"
                    elif it.category == "armchair":
                        detail = f" ({it.qty} шт × {MANUAL_ARMCHAIR} ₽ × {self.floors} эт.)"
                    self.lines.append((f"Ручной подъём: {it.label}{detail}", c))
                else:
                    self.lines.append((f"Подъём: {it.label} — индивидуально, уточните у логистики", 0))

            if it.assembly_price:
                self.lines.append((f"Сборка: {it.label}" + (f" × {it.qty}" if it.qty > 1 else ""),
                                   it.assembly_price * it.qty))

        km = extra_km_cost(self.distance_km)
        if km:
            self.lines.append((f"Свыше {MKAD_FREE_KM} км от МКАД ({self.distance_km} км)", km))
        if self.time_slot:
            self.lines.append(("Доставка ко времени", TIME_SLOT_FEE))
        pieces = sum(it.pieces for it in self.items)
        carry = carry_extra_cost(self.carry_extra_m, pieces)
        if carry:
            self.lines.append((f"Ручной пронос {self.carry_extra_m} м (сверх {CARRY_FREE_M} м), мест: {pieces}", carry))
        return sum(v for _, v in self.lines)
