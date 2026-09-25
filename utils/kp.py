"""Коммерческое предложение Creatica (А4, PDF): строгий монохром, тонкие линии, IBM Plex Sans.

Два варианта:
  standard — позиции с фото, размерами, материалом, ценой, скидкой;
  visual   — индивидуальная позиция (визуализация) + «наша альтернатива» из коллекции.
"""

from __future__ import annotations

import datetime as dt
import io
import os
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

from utils import tag as tagmod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND = os.path.join(ROOT, "brand")

PW, PH = A4
ML, MR = 40.0, PW - 40.0
CW = MR - ML

INK = (0.07, 0.07, 0.07)
GREY = (0.53, 0.51, 0.48)
HAIR = (0.85, 0.83, 0.80)
PANEL = (0.965, 0.955, 0.94)

SHOWROOM = "Флагманский шоу-рум Creatica · г. Москва, Новодевичий проезд, д. 2"
SITE = "creatica.shop"

PAYMENTS = [
    "Наличными в магазине",
    "По карте в магазине",
    "Переводом на карту (удалённо)",
    "По QR-коду (удалённо)",
    "По выставленному счёту (удалённо)",
]

THANKS = (
    "Покупка мебели — ответственный момент. Мы подберём модели, которые соответствуют вашему вкусу "
    "и точно впишутся в интерьер: выверенные пропорции, лаконичный дизайн, натуральные материалы.\n"
    "Creatica — для тех, кто ценит качество и спокойную красоту вещей."
)


@dataclass
class KPItem:
    title: str = ""
    qty: int = 1
    size: str = ""
    material: str = ""
    link: str = ""
    unit_price: int = 0
    discount: float = 0.0  # проценты
    photo: bytes | None = None
    alt: "KPItem | None" = None  # альтернатива из коллекции (вариант visual)

    def total(self, qty: int | None = None) -> int:
        q = self.qty if qty is None else qty
        return int(round(self.unit_price * (1 - self.discount / 100) * q))

    @property
    def gross(self) -> int:
        return int(round(self.unit_price * self.qty))


@dataclass
class KP:
    kind: str = "standard"  # standard | visual
    customer: str = ""
    manager: str = ""
    phone: str = ""
    telegram: str = ""
    showroom: str = ""
    date: dt.date = field(default_factory=dt.date.today)
    valid_days: int = 7
    number: str = ""
    items: list[KPItem] = field(default_factory=list)
    production: str = "55 рабочих дней"
    services: str = "По согласованию"
    payments: list[str] = field(default_factory=lambda: list(PAYMENTS))

    @property
    def valid_until(self) -> dt.date:
        return self.date + dt.timedelta(days=self.valid_days)

    def total(self) -> int:
        return sum(i.total() for i in self.items)

    def gross(self) -> int:
        return sum(i.gross for i in self.items)

    def alt_total(self) -> int:
        """Итого, если по каждой позиции выбрана альтернатива из коллекции (где она есть)."""
        return sum(i.alt.total(i.qty) if i.alt else i.total() for i in self.items)


def rub(value: int | float) -> str:
    return f"{tagmod.money(value)} ₽"


def date_ru(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def prep_photo(data: bytes | None, max_side: int = 1100) -> bytes | None:
    """Приводит присланное фото к аккуратному JPEG (или None, если файл не картинка)."""
    if not data:
        return None
    try:
        from PIL import Image, ImageOps

        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=88)
        return out.getvalue()
    except Exception:
        return None


class _Renderer:
    def __init__(self, kp: KP, total_pages: int):
        tagmod._register_fonts()
        self.REG, self.BOLD = tagmod.REG, tagmod.BOLD
        self.kp = kp
        self.total = total_pages
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4)
        self.c.setTitle(f"Коммерческое предложение {kp.number}".strip())
        self.c.setAuthor("Creatica")
        self.page = 0
        self.y = 0.0
        self._logo = ImageReader(os.path.join(BRAND, "creatica_logo.png"))
        self.new_page()

    # ---- примитивы -------------------------------------------------------

    def Y(self, y: float) -> float:
        return PH - y

    def text(self, x, y, s, size=9.5, bold=False, color=INK, align="l", space=0.0):
        c = self.c
        font = self.BOLD if bold else self.REG
        c.setFillColorRGB(*color)
        if space:
            width = c.stringWidth(s, font, size) + space * max(len(s) - 1, 0)
            if align == "r":
                x -= width
            elif align == "c":
                x -= width / 2
            t = c.beginText(x, self.Y(y))
            t.setFont(font, size)
            t.setCharSpace(space)
            t.textOut(s)
            t.setCharSpace(0)  # Tc живёт в графическом состоянии и «протекает» на следующий текст
            c.drawText(t)
            return
        c.setFont(font, size)
        {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[align](x, self.Y(y), s)

    def label(self, x, y, s, align="l"):
        self.text(x, y, s.upper(), size=6.8, color=GREY, space=1.1, align=align)

    def wrap(self, s: str, size: float, width: float, bold=False) -> list[str]:
        font = self.BOLD if bold else self.REG
        lines: list[str] = []
        for part in (s or "").split("\n"):
            lines += simpleSplit(part, font, size, width) or [""]
        return lines

    def hair(self, y, x0=ML, x1=MR, color=HAIR, width=0.5):
        self.c.setStrokeColorRGB(*color)
        self.c.setLineWidth(width)
        self.c.line(x0, self.Y(y), x1, self.Y(y))

    def photo_box(self, x, y, w, h, photo: bytes | None):
        c = self.c
        c.setStrokeColorRGB(*HAIR)
        c.setLineWidth(0.5)
        c.setFillColorRGB(1, 1, 1)
        c.rect(x, self.Y(y + h), w, h, stroke=1, fill=1)
        if photo:
            try:
                img = ImageReader(io.BytesIO(photo))
                iw, ih = img.getSize()
                k = min((w - 8) / iw, (h - 8) / ih)
                dw, dh = iw * k, ih * k
                c.drawImage(img, x + (w - dw) / 2, self.Y(y + h) + (h - dh) / 2, dw, dh)
                return
            except Exception:
                pass
        # заглушка: знак Creatica бледным
        s = min(w, h) * 0.22
        c.saveState()
        c.setFillColorRGB(*HAIR)
        c.rect(x + (w - s) / 2, self.Y(y + h / 2 + s / 2), s, s, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.circle(x + w / 2 - s * 0.0, self.Y(y + h / 2), s * 0.32, stroke=0, fill=1)
        c.restoreState()

    # ---- страница --------------------------------------------------------

    def new_page(self):
        if self.page:
            self.c.showPage()
        self.page += 1
        c = self.c
        h = 17.0
        w = h * self._logo.getSize()[0] / self._logo.getSize()[1]
        c.drawImage(self._logo, ML, self.Y(34 + h), w, h, mask="auto")
        self.text(MR, 45, SITE, size=8.5, color=GREY, align="r")
        # подвал
        self.hair(PH - 38)
        self.text(ML, PH - 26, self.kp.showroom or SHOWROOM, size=7, color=GREY)
        self.text(MR, PH - 26, f"{self.page} / {self.total}", size=7, color=GREY, align="r")
        self.y = 78.0

    def need(self, h: float):
        if self.y + h > PH - 52:
            self.new_page()

    # ---- шапка первой страницы --------------------------------------------

    def cover(self):
        kp = self.kp
        y = self.y + 14
        self.label(ML, y, "Коммерческое предложение" + (f" № {kp.number}" if kp.number else ""))
        y += 14
        self.hair(y, color=INK, width=0.8)
        y += 18
        cols = [(ML, "Заказчик", kp.customer or "—", 205), (260, "Дата", date_ru(kp.date), 90),
                (365, "Действует до", date_ru(kp.valid_until), 90), (470, "Менеджер", kp.manager or "—", 85)]
        tallest = 0
        for x, lab, val, wd in cols:
            self.label(x, y, lab)
            lines = self.wrap(val, 10.5, wd)
            for i, ln in enumerate(lines[:3]):
                self.text(x, y + 15 + i * 13, ln, size=10.5)
            tallest = max(tallest, len(lines[:3]))
        y += 15 + tallest * 13 + 14
        self.hair(y)
        self.y = y + 26

    # ---- позиции ----------------------------------------------------------

    def item_standard(self, n: int, it: KPItem):
        mid_x, mid_w = 224.0, 172.0
        rows: list[tuple[str, list[str]]] = []
        if it.size:
            rows.append(("Размеры, мм", self.wrap(it.size, 9.5, mid_w)))
        if it.material:
            rows.append(("Материал", self.wrap(it.material, 9.5, mid_w)))
        if it.link:
            rows.append(("Ссылка", [_short(it.link)]))
        title_lines = self.wrap(it.title, 14, mid_w)
        mid_h = 12 + len(title_lines) * 17 + 8 + sum(9 + len(v) * 12 + 8 for _, v in rows)
        h = max(128.0, mid_h + 8)
        self.need(h + 6)
        y0 = self.y
        self.text(ML, y0 + 18, f"{n:02d}", size=8.5, color=GREY)
        self.photo_box(ML + 22, y0 + 6, 142, 108, it.photo)
        y = y0 + 22
        for ln in title_lines:
            self.text(mid_x, y, ln, size=14)
            y += 17
        y += 4
        for lab, vals in rows:
            self.label(mid_x, y + 6, lab)
            y += 9
            for ln in vals:
                self.text(mid_x, y + 12, ln, size=9.5)
                if lab == "Ссылка":
                    self.c.linkURL(it.link, (mid_x, self.Y(y + 15), mid_x + mid_w, self.Y(y + 3)), relative=0)
                y += 12
            y += 8
        # правая колонка
        rx0, rx1 = 418.0, MR
        ry = y0 + 22
        for lab, val in (("Цена за ед.", rub(it.unit_price)), ("Количество", f"{it.qty} шт")):
            self.label(rx0, ry, lab)
            self.text(rx1, ry, val, size=9.5, align="r")
            ry += 16
        if it.discount:
            self.label(rx0, ry, "Скидка")
            self.text(rx1, ry, f"{_pct(it.discount)}%", size=9.5, align="r")
            ry += 16
        self.hair(y0 + h - 40, rx0, rx1, color=INK, width=0.6)
        self.label(rx0, y0 + h - 22, "Итого")
        self.text(rx1, y0 + h - 12, rub(it.total()), size=15, bold=True, align="r")
        self.y = y0 + h
        self.hair(self.y)
        self.y += 8

    def item_visual(self, n: int, it: KPItem):
        colw, gap = 250.0, 15.0
        cols = [("Индивидуальное изготовление", it, ML), ("Наша альтернатива · из коллекции", it.alt, ML + colw + gap)]

        def block_h(p: KPItem | None) -> float:
            if not p:
                return 40
            t = len(self.wrap(p.title, 13, colw)) * 16
            s = 0
            for v in (p.size, p.material):
                s += len(self.wrap(v, 9.5, colw - 70)) * 12 + 4 if v else 0
            if p.link:
                s += 16
            return 20 + 150 + 12 + t + 6 + s + 58

        h = max(block_h(it), block_h(it.alt)) + 10
        self.need(h)
        y0 = self.y
        for idx, (lab, p, x) in enumerate(cols):
            self.label(x, y0 + 8, (f"{n:02d} · " if idx == 0 else "") + lab)
            if not p:
                self.text(x, y0 + 36, "Альтернатива не подобрана", size=9.5, color=GREY)
                continue
            self.photo_box(x, y0 + 20, colw, 150, p.photo)
            y = y0 + 20 + 150 + 22
            for ln in self.wrap(p.title, 13, colw):
                self.text(x, y, ln, size=13)
                y += 16
            y += 4
            for lab2, v in (("Размеры, мм", p.size), ("Материал", p.material)):
                if not v:
                    continue
                lines = self.wrap(v, 9.5, colw - 70)
                self.label(x, y, lab2)
                for i, ln in enumerate(lines):
                    self.text(x + 70, y + i * 12, ln, size=9.5)
                y += len(lines) * 12 + 4
            if p.link:
                self.label(x, y, "Ссылка")
                self.text(x + 70, y, _short(p.link), size=9.5)
                self.c.linkURL(p.link, (x + 70, self.Y(y + 3), x + colw, self.Y(y - 9)), relative=0)
                y += 16
            yy = y0 + h - 48
            self.hair(yy, x, x + colw, color=INK, width=0.6)
            qty = it.qty
            self.label(x, yy + 16, f"{rub(p.unit_price)} × {qty} шт" + (f" · −{_pct(p.discount)}%" if p.discount else ""))
            self.text(x + colw, yy + 32, rub(p.total(qty)), size=15, bold=True, align="r")
        self.y = y0 + h
        self.hair(self.y)
        self.y += 10

    # ---- итоги -------------------------------------------------------------

    def summary(self):
        kp = self.kp
        self.need(150)
        y = self.y + 26
        if kp.kind == "visual":
            self.label(ML, y, "Индивидуальное изготовление")
            self.text(MR, y + 6, rub(kp.total()), size=22, align="r")
            y += 34
            if any(i.alt for i in kp.items):
                self.label(ML, y, "Альтернатива из коллекции")
                self.text(MR, y + 6, rub(kp.alt_total()), size=22, align="r", color=GREY)
                y += 26
                diff = kp.total() - kp.alt_total()
                if diff > 0:
                    self.text(MR, y, f"Разница — {rub(diff)}", size=9, color=GREY, align="r")
                    y += 14
        else:
            self.label(ML, y, "Итого стоимость предметов")
            self.text(MR, y + 10, rub(kp.total()), size=30, align="r")
            y += 40
            saved = kp.gross() - kp.total()
            if saved > 0:
                self.text(MR, y, f"Скидка по позициям — {rub(saved)}", size=9, color=GREY, align="r")
                y += 14
        y += 8
        self.hair(y, color=INK, width=0.8)
        y += 22
        left = [("Сроки изготовления", kp.production), ("Дополнительные услуги", kp.services),
                ("Предложение действительно до", date_ru(kp.valid_until))]
        ly = y
        for lab, val in left:
            self.label(ML, ly, lab)
            lines = self.wrap(val, 10.5, 215)
            for i, ln in enumerate(lines):
                self.text(ML, ly + 15 + i * 13, ln, size=10.5)
            ly += 15 + len(lines) * 13 + 14
        self.label(305, y, "Способы оплаты")
        py = y + 15
        for p in kp.payments:
            self.text(305, py, "—  " + p, size=10)
            py += 15
        self.y = max(ly, py) + 8
        self.hair(self.y)
        self.y += 24
        # менеджер
        self.need(70)
        self.label(ML, self.y, "Ответственный менеджер")
        self.text(ML, self.y + 18, kp.manager or "—", size=13)
        contacts = "  ·  ".join(x for x in (kp.phone, kp.telegram) if x)
        if contacts:
            self.text(ML, self.y + 34, contacts, size=10, color=GREY)
        self.text(MR, self.y + 18, "С уважением,", size=10, color=GREY, align="r")
        self.text(MR, self.y + 34, "команда Creatica", size=10, align="r")
        self.y += 62

    def closing(self):
        c = self.c
        panel_h = 172.0
        clients = [os.path.join(BRAND, n) for n in ("clients_1.png", "clients_2.png") if os.path.exists(os.path.join(BRAND, n))]
        need_h = panel_h + 30 + (30 + 54 * len(clients) if clients else 0)
        self.need(need_h)
        y = self.y + 6
        # чёрная плашка на всю ширину — как знак Creatica
        c.setFillColorRGB(*INK)
        c.rect(0, self.Y(y + panel_h), PW, panel_h, stroke=0, fill=1)
        self.text(ML, y + 52, "Спасибо, что выбрали", size=24, color=(1, 1, 1))
        self.text(ML, y + 82, "Creatica", size=24, bold=True, color=(1, 1, 1))
        ty = y + 56
        for ln in self.wrap(THANKS, 9.5, 235):
            self.text(320, ty, ln, size=9.5, color=(0.86, 0.85, 0.83))
            ty += 13
        self.y = y + panel_h + 26
        if clients:
            self.label(ML, self.y, "Нам доверяют")
            self.y += 12
            for path in clients:
                img = ImageReader(path)
                iw, ih = img.getSize()
                h = CW * ih / iw
                c.drawImage(img, ML, self.Y(self.y + h), CW, h)
                self.y += h + 8

    def finish(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def _short(url: str) -> str:
    u = url.replace("https://", "").replace("http://", "").rstrip("/")
    return u if len(u) <= 34 else u[:31] + "…"


def _pct(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def _render(kp: KP, total: int) -> tuple[bytes, int]:
    r = _Renderer(kp, total)
    r.cover()
    for n, it in enumerate(kp.items, 1):
        (r.item_visual if kp.kind == "visual" else r.item_standard)(n, it)
    r.summary()
    r.closing()
    return r.finish(), r.page


def build_kp_pdf(kp: KP) -> bytes:
    _, pages = _render(kp, 1)
    data, pages2 = _render(kp, pages)
    return data


def build_kp_preview(kp: KP, page: int = 0, dpi: int = 70) -> bytes:
    import pymupdf

    doc = pymupdf.open(stream=build_kp_pdf(kp), filetype="pdf")
    return doc[min(page, len(doc) - 1)].get_pixmap(dpi=dpi).tobytes("png")
