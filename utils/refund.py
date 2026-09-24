"""Заявление покупателя о возврате денежных средств (PDF).

Вёрстка повторяет Word-шаблоны: координаты сняты с готовых макетов.
Варианты: организация (ИП Сабиров / ООО «Феникс») x покупатель (физлицо / юрлицо).
"""

from __future__ import annotations

import datetime as dt
import io
import os
from collections import defaultdict

from num2words import num2words
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:
    from principal import REFUND_COMPANIES
except ImportError:  # свежий клон репозитория без личных данных
    from principal_example import REFUND_COMPANIES  # type: ignore  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = A4[1]
FONT = "Helvetica"
LINE_STEP = 20.7


def _font() -> str:
    global FONT
    if FONT == "Helvetica":
        for path in (
            os.path.join(ROOT, "fonts", "LiberationSerif-Regular.ttf"),
            r"C:\Windows\Fonts\times.ttf",
        ):
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont("Serif", path))
                FONT = "Serif"
                break
    return FONT


MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def fmt_money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def _plural(n: int, one: str, two: str, five: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return five
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return two
    return five


def money_words(x: float) -> str:
    rub = int(x)
    kop = int(round((x - rub) * 100))
    if kop == 100:
        rub, kop = rub + 1, 0
    words = num2words(rub, lang="ru")
    text = f"{words} {_plural(rub, 'рубль', 'рубля', 'рублей')} {kop:02d} {_plural(kop, 'копейка', 'копейки', 'копеек')}"
    return text[:1].upper() + text[1:]


def short_name(full: str) -> str:
    parts = full.split()
    if not parts:
        return ""
    out = parts[0]
    for p in parts[1:3]:
        out += f" {p[0].upper()}."
    return out


class Page:
    def __init__(self, c: canvas.Canvas):
        self.c = c
        self.f = _font()

    def base(self, y0: float, size: float) -> float:
        return H - (y0 + 0.891 * size)

    def text(self, x: float, y0: float, s: str, size: float = 12) -> float:
        self.c.setFont(self.f, size)
        self.c.drawString(x, self.base(y0, size), s)
        return x + stringWidth(s, self.f, size)

    def rtext(self, x: float, y0: float, s: str, size: float = 12) -> None:
        self.c.setFont(self.f, size)
        self.c.drawRightString(x, self.base(y0, size), s)

    def ctext(self, x: float, y0: float, s: str, size: float = 12) -> None:
        self.c.setFont(self.f, size)
        self.c.drawCentredString(x, self.base(y0, size), s)

    def line(self, x0: float, x1: float, y: float, w: float = 0.5) -> None:
        self.c.setLineWidth(w)
        self.c.line(x0, H - y, x1, H - y)

    def fit_size(self, s: str, width: float, size: float = 11.5, low: float = 7) -> float:
        while size > low and stringWidth(s, self.f, size) > width:
            size -= 0.25
        return size

    def value(self, x0: float, x1: float, line_y: float, s: str, size: float = 11.5, center: bool = False) -> None:
        if not s:
            return
        size = self.fit_size(s, x1 - x0 - 2, size)
        self.c.setFont(self.f, size)
        y = H - (line_y - 2.2)
        if center:
            self.c.drawCentredString((x0 + x1) / 2, y, s)
        else:
            self.c.drawString(x0 + 1.5, y, s)

    def wrap(self, s: str, width: float, size: float, first_width: float | None = None) -> list[str]:
        words, lines, cur = s.split(), [], ""
        limit = first_width or width
        for w in words:
            trial = (cur + " " + w).strip()
            if stringWidth(trial, self.f, size) <= limit:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
                limit = width
        if cur:
            lines.append(cur)
        return lines

    def boxes(self, x0: float, y0: float, n: int, pitch: float, h: float, digits: str, groups: int = 0) -> None:
        for i in range(n):
            x = x0 + i * pitch
            self.c.setLineWidth(0.5)
            self.c.rect(x, H - (y0 + h), pitch, h, stroke=1, fill=0)
            if i < len(digits):
                self.c.setFont(self.f, 11)
                self.c.drawCentredString(x + pitch / 2, H - (y0 + h / 2 + 4), digits[i])

    def checkbox(self, x: float, y0: float, checked: bool) -> None:
        self.c.setLineWidth(0.6)
        self.c.rect(x, H - (y0 + 9.5), 9, 9, stroke=1, fill=0)
        if checked:
            self.c.setLineWidth(1.2)
            self.c.line(x + 1.5, H - (y0 + 5), x + 3.8, H - (y0 + 8.2))
            self.c.line(x + 3.8, H - (y0 + 8.2), x + 8.5, H - (y0 + 1.2))


def build_refund_pdf(company: str, kind: str, a: dict) -> io.BytesIO:
    """company: ключ из REFUND_COMPANIES; kind: 'fiz' | 'yur'; a: ответы менеджера."""
    co = REFUND_COMPANIES[company]
    a = defaultdict(str, {k: ("" if v is None else v) for k, v in a.items()})
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Заявление о возврате денежных средств")
    p = Page(c)

    # --- шапка: кому
    y = 55.7
    for line in co["header_lines"]:
        p.rtext(550.0, y, line)
        y += LINE_STEP
    ot = y  # позиция строки «От»

    p.text(353.0, ot, "От")
    if kind == "fiz":
        name_lines = p.wrap(a["name"], 174, 11.5)
        p.line(369.9, 544, ot + 12)
        p.value(369.9, 544, ot + 12, name_lines[0] if name_lines else "")
        p.line(369.9, 544, ot + 32.8)
        p.value(369.9, 544, ot + 32.8, " ".join(name_lines[1:]))
        # паспорт
        yy = ot + 41.4
        x = p.text(352.9, yy, "Паспорт серия")
        sw = stringWidth("_" * 7, p.f, 12)
        p.line(x, x + sw, yy + 12)
        p.value(x, x + sw, yy + 12, a["passport_series"], center=True)
        x2 = p.text(x + sw, yy, "№")
        nw = stringWidth("_" * 11, p.f, 12)
        p.line(x2, x2 + nw, yy + 12)
        p.value(x2, x2 + nw, yy + 12, a["passport_number"], center=True)
        yy = ot + 62.2
        x = p.text(353.4, yy, "выдан")
        p.line(x, 547, yy + 12)
        pdate = a["passport_date"]
        pdate = f"{pdate:%d.%m.%Y}" if isinstance(pdate, dt.date) else str(pdate)
        issued_txt = ", ".join(x for x in (a["passport_issuer"], f"{pdate} г." if pdate else "") if x)
        issued = p.wrap(issued_txt, 175, 11, first_width=160)
        p.value(x, 547, yy + 12, issued[0] if issued else "", size=11)
        yy = ot + 82.8
        p.line(354.9, 547, yy + 12)
        p.value(354.9, 547, yy + 12, " ".join(issued[1:]), size=11)
        title_y = ot + 112.9
    else:
        full = ", ".join(x for x in (a["org"], f"в лице {a['director']}" if a["director"] else "") if x)
        for size in (11.5, 10.5, 9.5, 8.5):
            lines = p.wrap(full, 174, size)
            if len(lines) <= 3:
                break
        ys3 = (ot + 12, ot + 32.8, ot + 53.4)
        xs = ((369.9, 544), (369.9, 544), (366.9, 541))
        for i, (yline, (xa, xb)) in enumerate(zip(ys3, xs)):
            p.line(xa, xb, yline)
            if i < len(lines):
                p.value(xa, xb, yline, lines[i], size=size)
        title_y = ot + 71.5

    T = title_y
    p.text(142.3, T, "Заявление покупателя о возврате денежных средств", size=14)

    # --- основной текст
    d = a["purchase_date"]
    date_txt = f'"{d:%d}" {MONTHS[d.month - 1]} {d.year} г.' if d else '"____" _____________20___ г.'
    p.text(83.7, T + 24.0, f"{date_txt} я приобрел(а) в вашем магазине товар по Договору / Заказу")
    x = p.text(48.2, T + 44.7, "клиента")
    order = a["order"]
    size = p.fit_size(order, 420, 11.5)
    end = max(244.8, min(545.0, x + 6 + stringWidth(order, p.f, size) + 4))
    p.line(91.9, end, T + 56.7)
    p.value(93, end, T + 56.7, order, size=11.5)
    p.text(83.7, T + 65.5, "Прошу принять у меня указанный товар и вернуть мне уплаченные за него деньги в сумме")

    amount = a["amount"]
    p.line(47.5, 544.6, T + 109.3)
    p.value(48.2, 544.6, T + 109.3, f"{fmt_money(amount)} руб." if amount else "", center=True)
    p.ctext(299.0, T + 110.3, "(цифрами)", size=10)
    words = money_words(amount) if amount else ""
    wl = p.wrap(words, 490, 11.5)
    p.line(48.2, 544.0, T + 144.9)
    p.line(47.5, 544.0, T + 169.2)
    p.value(48.2, 544.0, T + 144.9, wl[0] if wl else "")
    p.value(48.2, 544.0, T + 169.2, " ".join(wl[1:]))
    p.ctext(298.7, T + 170.1, "(прописью)", size=10)

    p.text(53.6, T + 192.6, "Причина возврата:", size=11)
    rl = p.wrap(a["reason"], 490, 11.5, first_width=390)
    ys = [T + 204.8, T + 225.4, T + 246.2]
    p.line(48.2, 544.0, ys[0])
    p.line(48.2, 544.0, ys[1])
    p.line(47.5, 544.0, ys[2])
    p.value(145, 544.0, ys[0], rl[0] if rl else "")
    if len(rl) > 1:
        rest = p.wrap(" ".join(rl[1:]), 490, 11.5)
        p.value(48.2, 544.0, ys[1], rest[0])
        if len(rest) > 1:
            p.value(48.2, 544.0, ys[2], " ".join(rest[1:]))
    p.ctext(298.8, T + 247.2, "(указать причину)", size=10)

    # --- реквизиты
    def pair(y0, left, lx0, lx1, right, rx0, rx1, lval, rval):
        """строка вида: «Метка____ Метка2____»"""
        x = p.text(48.2, y0, left, size=11)
        p.line(x, lx1, y0 + 11.1)
        p.value(x, lx1, y0 + 11.1, lval, size=10.5)
        x = p.text(lx1, y0, right, size=11)
        p.line(x, rx1, y0 + 11.1)
        p.value(x, rx1, y0 + 11.1, rval, size=10.5)

    if kind == "yur":
        p.text(48.2, T + 258.7, "Возврат прошу осуществить", size=12)
        p.text(194.3, T + 259.6, " на счет по реквизитам:", size=11)
        x = p.text(48.2, T + 283.4, "Наименования организации", size=11)
        p.line(184.5, 531.0, T + 294.5)
        p.value(184.5, 531.0, T + 294.5, a["pay_org"])
        x = p.text(48.2, T + 307.1, "ИНН / КПП ", size=11)
        p.line(x, 290.9, T + 318.3)
        p.value(x, 290.9, T + 318.3, a["pay_inn"])
        x = p.text(48.2, T + 330.7, "ОГРН ", size=11)
        p.line(x, 291.2, T + 341.9)
        p.value(x, 291.2, T + 341.9, a["pay_ogrn"])
        x = p.text(48.2, T + 354.5, "Расчетный счет:", size=12)
        p.line(x, 548.5, T + 366.6)
        p.value(x, 548.5, T + 366.6, a["pay_rs"], size=12)
        x = p.text(48.2, T + 379.2, "Лицевой счет: ", size=12)
        p.line(x, 543.9, T + 391.3)
        p.value(x, 543.9, T + 391.3, a.get("pay_ls", ""), size=12)
        pair(T + 403.9, "Банк получателя", 128.9, 300.7, "БИК банка", 351.4, 544.1, a["bank"], a["bik"])
        pair(T + 427.6, "ИНН банка", 104.1, 250.4, "Кор.счет банка", 321.0, 544.2, a["bank_inn"], a["ks"])
        sig_y = T + 476.1
        cap_y = T + 488.7
        date_y = T + 508.3
    else:
        p.text(48.2, T + 258.7, "Возврат прошу осуществить: ", size=12)
        cash = a["method"] == "cash"
        card_mode = a["method"] == "card"
        p.checkbox(66.3, T + 284.0, cash)
        x = p.text(84.3, T + 284.2, "Из кассы магазина:", size=11)
        p.line(177.6, 502.1, T + 295.2)
        p.checkbox(66.3, T + 308.4, card_mode)
        p.text(84.3, T + 308.6, "На карточный или лицевой счет по реквизитам: ", size=11)
        x = p.text(48.2, T + 332.3, "Получатель (ФИО полностью)", size=11)
        p.line(194.8, 541.2, T + 343.3)
        pair(T + 355.9, "Банк получателя", 128.9, 300.7, "БИК банка", 351.4, 544.1, "", "")
        pair(T + 379.6, "ИНН банка", 104.1, 250.4, "Кор.счет банка", 321.0, 544.2, "", "")
        p.text(48.2, T + 403.2, "№ лицевого или карточного ", size=11)
        p.text(48.2, T + 415.8, "счета  ", size=11)
        p.text(48.2, T + 439.6, " № карты ", size=11)
        p.boxes(206.3, T + 405.7, 20, 17.025, 24.6, "")
        for gx in (206.6, 291.5, 376.6, 461.6):
            p.boxes(gx, T + 442.0, 4, 17.1, 24.6, "")
        if card_mode:
            p.value(194.8, 541.2, T + 343.3, a["recipient"])
            p.value(128.9, 300.7, T + 367.0, a["bank"], size=10.5)
            p.value(351.4, 544.1, T + 367.0, a["bik"], size=10.5)
            p.value(104.1, 250.4, T + 390.6, a["bank_inn"], size=10.5)
            p.value(321.0, 544.2, T + 390.6, a["ks"], size=10.5)
            p.boxes(206.3, T + 405.7, 20, 17.025, 24.6, a["account"])
            card = a.get("card", "")
            for gi, gx in enumerate((206.6, 291.5, 376.6, 461.6)):
                p.boxes(gx, T + 442.0, 4, 17.1, 24.6, card[gi * 4: gi * 4 + 4])
        sig_y = T + 478.0
        cap_y = T + 490.7
        date_y = T + 510.3

    # --- подпись
    p.text(48.2, sig_y, "_____________________________/___________________________________ ", size=11)
    who = short_name(a["name"] if kind == "fiz" else a["director_name"])
    p.value(210.0, 406.0, sig_y + 11.5, who, size=11, center=True)
    p.text(48.2, cap_y, "               подпись                                                     расшифровка ", size=9)
    x = p.text(48.2, date_y, "Дата: ", size=10)
    if a["doc_date"]:
        p.text(x, date_y, f"{a['doc_date']:%d.%m.%Y}", size=10)
    p.text(48.2, date_y + 11.6, "Приложение: Акт на возврат", size=10)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf
