"""Стоимость доставки по Москве и Санкт-Петербургу (тарифы ООО «Феникс», приложение №3 к договору).

Для других регионов расчёт не делаем — тариф даёт транспортная компания, ссылка на таблицу городов
и ТК: см. delivery_flow.REGIONS_URL.
"""

from __future__ import annotations

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


def delivery_price(category: str, qty: int, long_case: bool = False) -> int:
    """Доставка до подъезда, в пределах МКАД (без подъёма и сборки)."""
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


# --- подъём (лифт / вручную) ---------------------------------------------------------------

LIFT = {
    "armchair": {"lift": 1250, "lift_units": 2, "manual_per_floor": 300},
    "small": {"lift": 1200, "lift_units": 3, "lift_over": 2000, "lift_over_units": 6, "manual_per_floor": 300},
    "big": {"lift": 2550, "lift_units": 2, "manual_per_floor": 400},
}


def lift_cost_elevator(category: str, qty: int) -> int | None:
    """Занос в помещение + подъём на лифте. None — тариф не задан (комод/тумба/консоль — индивидуально)."""
    t = LIFT.get(category)
    if not t:
        return None
    if category == "small" and qty > t["lift_units"]:
        return t.get("lift_over", t["lift"])
    return t["lift"]


def lift_cost_manual(category: str, floors: int) -> int | None:
    t = LIFT.get(category)
    if not t:
        return None
    return t["manual_per_floor"] * max(floors, 0)


# --- сборка --------------------------------------------------------------------------------

ASSEMBLY_ARMCHAIR = 1250
ASSEMBLY_SOFA = {"legs": 1300, "full": 2700}
ASSEMBLY_BED = {"regular": 2900, "special": 4900}  # special: Некст, Либерти, Бостон
CALLOUT_FEE = 3000  # выезд на сборку — один раз, если выбрана любая сборка

TIME_SLOT_FEE = 2000
CARRY_EXTRA_UNIT_M = 10
CARRY_EXTRA_PRICE = 200
CARRY_FREE_M = 15


def carry_extra_cost(meters: int) -> int:
    extra = max(0, meters - CARRY_FREE_M)
    if not extra:
        return 0
    import math

    return math.ceil(extra / CARRY_EXTRA_UNIT_M) * CARRY_EXTRA_PRICE


@dataclass
class Quote:
    category: str
    qty: int = 1
    long_case: bool = False
    distance_km: int = 0
    lift_mode: str = "none"  # none | elevator | manual
    floors: int = 0
    assembly: str = "none"  # none | armchair | sofa_legs | sofa_full | bed_regular | bed_special
    time_slot: bool = False
    carry_extra_m: int = 0
    lines: list[tuple[str, int]] = field(default_factory=list)

    def compute(self) -> int:
        self.lines = []
        base = delivery_price(self.category, self.qty, self.long_case)
        self.lines.append((f"Доставка до подъезда, {CATEGORY_LABEL[self.category]} × {self.qty}", base))
        km = extra_km_cost(self.distance_km)
        if km:
            self.lines.append((f"Свыше {MKAD_FREE_KM} км от МКАД ({self.distance_km} км)", km))
        if self.lift_mode == "elevator":
            c = lift_cost_elevator(self.category, self.qty)
            if c:
                self.lines.append(("Занос и подъём на лифте", c))
            else:
                self.lines.append(("Подъём — рассчитывается индивидуально, уточните у логистики", 0))
        elif self.lift_mode == "manual":
            c = lift_cost_manual(self.category, self.floors)
            if c:
                self.lines.append((f"Ручной подъём, {self.floors} эт.", c))
            else:
                self.lines.append(("Подъём — рассчитывается индивидуально, уточните у логистики", 0))
        assembly_price = {
            "armchair": ASSEMBLY_ARMCHAIR, "sofa_legs": ASSEMBLY_SOFA["legs"], "sofa_full": ASSEMBLY_SOFA["full"],
            "bed_regular": ASSEMBLY_BED["regular"], "bed_special": ASSEMBLY_BED["special"],
        }.get(self.assembly)
        if assembly_price:
            mult = self.qty if self.assembly == "armchair" else 1
            self.lines.append(("Сборка" + (f" × {self.qty}" if mult > 1 else ""), assembly_price * mult))
            self.lines.append(("Выезд на сборку", CALLOUT_FEE))
        if self.time_slot:
            self.lines.append(("Доставка ко времени", TIME_SLOT_FEE))
        carry = carry_extra_cost(self.carry_extra_m)
        if carry:
            self.lines.append((f"Ручной пронос свыше {CARRY_FREE_M} м ({self.carry_extra_m} м)", carry))
        return sum(v for _, v in self.lines)
