"""Заявления покупателя (возврат денег, выплата пени) в фирменном стиле Creatica — как КП и карточка:
монохром, тонкие линии, IBM Plex Sans, подписи мелкими прописными, сумма — на светлой плашке.
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
from utils.kp import BRAND, GREY, HAIR, INK, MR, ML, PANEL, PH, SHOWROOM, SITE

CW = MR - ML


@dataclass
class Statement:
    title: str  # «Заявление о возврате денежных средств»
    to_lines: list[str]  # кому (организация)
    from_lines: list[str]  # от кого (покупатель)
    paragraphs: list[str]  # основной текст
    amount: float | None = None
    amount_words: str = ""
    amount_label: str = "Сумма"
    sections: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)  # (заголовок, [(подпись, значение)])
    signer: str = ""  # расшифровка подписи
    date: dt.date | None = None
    attachment: str = ""


def money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


class _R:
    def __init__(self, title: str):
        tagmod._register_fonts()
        self.REG, self.BOLD = tagmod.REG, tagmod.BOLD
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4)
        self.c.setTitle(title)
        self.c.setAuthor("Creatica")
        self.logo = ImageReader(os.path.join(BRAND, "creatica_logo.png"))
        self.page = 0
        self.new_page()

    def Y(self, y):
        return PH - y

    def text(self, x, y, s, size=10.5, bold=False, color=INK, align="l", space=0.0):
        c = self.c
        font = self.BOLD if bold else self.REG
        c.setFillColorRGB(*color)
        if space:
            w = c.stringWidth(s, font, size) + space * max(len(s) - 1, 0)
            if align == "r":
                x -= w
            t = c.beginText(x, self.Y(y))
            t.setFont(font, size)
            t.setCharSpace(space)
            t.textOut(s)
            t.setCharSpace(0)
            c.drawText(t)
            return
        c.setFont(font, size)
        {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[align](x, self.Y(y), s)

    def label(self, x, y, s, align="l"):
        self.text(x, y, s.upper(), size=6.8, color=GREY, space=1.1, align=align)

    def hair(self, y, x0=ML, x1=MR, color=HAIR, width=0.5):
        self.c.setStrokeColorRGB(*color)
        self.c.setLineWidth(width)
        self.c.line(x0, self.Y(y), x1, self.Y(y))

    def wrap(self, s, size, width, bold=False):
        font = self.BOLD if bold else self.REG
        out = []
        for part in (s or "").split("\n"):
            out += simpleSplit(part, font, size, width) or [""]
        return out

    def new_page(self):
        if self.page:
            self.c.showPage()
        self.page += 1
        h = 17.0
        w = h * self.logo.getSize()[0] / self.logo.getSize()[1]
        self.c.drawImage(self.logo, ML, self.Y(34 + h), w, h, mask="auto")
        self.text(MR, 45, SITE, size=8.5, color=GREY, align="r")
        self.hair(PH - 38)
        self.text(ML, PH - 26, SHOWROOM, size=7, color=GREY)
        self.y = 84.0

    def need(self, h):
        if self.y + h > PH - 56:
            self.new_page()

    def finish(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def build_statement_pdf(s: Statement) -> bytes:
    r = _R(s.title)

    # кому / от кого — две колонки, как «Заказчик / Менеджер» в КП
    y = r.y + 14
    r.label(ML, y, "Заявление")
    y += 14
    r.hair(y, color=INK, width=0.8)
    y += 18
    col_w = CW / 2 - 14
    cols = [(ML, "Кому", s.to_lines), (ML + CW / 2 + 14, "От кого", s.from_lines)]
    tallest = 0
    for x, lab, lines in cols:
        r.label(x, y, lab)
        yy = y + 15
        for ln in lines:
            for part in r.wrap(ln, 10, col_w):
                r.text(x, yy, part, size=10)
                yy += 13
        tallest = max(tallest, yy - y)
    y += tallest + 12
    r.hair(y)

    # заголовок
    y += 34
    for ln in r.wrap(s.title, 17, CW, bold=True):
        r.text(ML, y, ln, size=17, bold=True)
        y += 21
    y += 6
    r.y = y

    # текст
    for p in s.paragraphs:
        lines = r.wrap(p, 10.5, CW)
        r.need(len(lines) * 15 + 6)
        for ln in lines:
            r.text(ML, r.y, ln, size=10.5)
            r.y += 15
        r.y += 7

    # сумма — светлая плашка
    if s.amount is not None:
        words = r.wrap(s.amount_words, 9.5, CW - 32)
        panel_h = 52 + 13 * len(words)
        r.need(panel_h + 16)
        top = r.y + 4
        r.c.setFillColorRGB(*PANEL)
        r.c.rect(ML, r.Y(top + panel_h), CW, panel_h, stroke=0, fill=1)
        r.label(ML + 16, top + 18, s.amount_label)
        r.text(ML + 16, top + 40, money(s.amount), size=18, bold=True)
        yy = top + 40 + 15
        for ln in words:
            r.text(ML + 16, yy, ln, size=9.5, color=GREY)
            yy += 13
        r.y = top + panel_h + 22

    # реквизиты — строки с тонкими линиями, как в карточке организации
    col = ML + 170
    for head, rows in s.sections:
        r.need(40)
        r.label(ML, r.y, head)
        r.y += 8
        r.hair(r.y)
        for lab, value in rows:
            lines = []
            for part in str(value or "—").split("\n"):
                lines += simpleSplit(part, r.REG, 10.5, MR - col) or [""]
            row_h = 12 + 14 * len(lines)
            r.need(row_h + 4)
            r.label(ML, r.y + 15, lab)
            for i, ln in enumerate(lines):
                r.text(col, r.y + 16 + i * 14, ln, size=10.5)
            r.y += row_h + 4
            r.hair(r.y)
        r.y += 22

    # подпись и дата
    r.need(80)
    y = r.y + 26
    r.hair(y, x0=ML, x1=ML + 150, color=INK, width=0.6)
    r.hair(y, x0=ML + 180, x1=ML + 380, color=INK, width=0.6)
    r.hair(y, x0=ML + 410, x1=MR, color=INK, width=0.6)
    if s.signer:
        r.text(ML + 180, y - 5, s.signer, size=10.5)
    if s.date:
        r.text(ML + 410, y - 5, f"{s.date:%d.%m.%Y}", size=10.5)
    r.label(ML, y + 13, "Подпись")
    r.label(ML + 180, y + 13, "Расшифровка")
    r.label(ML + 410, y + 13, "Дата")
    if s.attachment:
        r.text(ML, y + 40, s.attachment, size=9, color=GREY)
    return r.finish()


def build_statement_docx(s: Statement) -> bytes:
    """То же заявление в Word (.docx) — чтобы можно было поправить текст перед печатью."""
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    doc = Document()
    sec = doc.sections[0]
    sec.left_margin, sec.right_margin = Cm(2.2), Cm(1.8)
    sec.top_margin = sec.bottom_margin = Cm(1.8)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Times New Roman", Pt(12)
    st.paragraph_format.space_after = Pt(4)

    def para(text="", bold=False, size=None, align=None, after=None, indent=None):
        p = doc.add_paragraph()
        if align is not None:
            p.alignment = align
        if after is not None:
            p.paragraph_format.space_after = Pt(after)
        if indent:
            p.paragraph_format.first_line_indent = Cm(indent)
        run = p.add_run(text)
        run.bold = bold
        if size:
            run.font.size = Pt(size)
        return p

    head = doc.add_table(rows=1, cols=2)
    head.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, (lab, lines) in zip(head.rows[0].cells, [("Кому:", s.to_lines), ("От кого:", s.from_lines)]):
        cell.paragraphs[0].add_run(lab).bold = True
        for ln in lines:
            for part in str(ln).split("\n"):
                cell.add_paragraph(part).paragraph_format.space_after = Pt(0)
    para("", after=6)
    para(s.title, bold=True, size=14, align=WD_ALIGN_PARAGRAPH.CENTER, after=10)
    for p in s.paragraphs:
        para(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, indent=1.25)
    if s.amount is not None:
        p = para("", after=2)
        p.add_run(f"{s.amount_label}: ")
        p.add_run(money(s.amount)).bold = True
        if s.amount_words:
            para(f"({s.amount_words})", after=8)
    for title, rows in s.sections:
        para(title + ":", bold=True, after=2)
        tbl = doc.add_table(rows=0, cols=2)
        tbl.style = "Table Grid"
        for lab, value in rows:
            c1, c2 = tbl.add_row().cells
            c1.text, c2.text = str(lab), str(value or "—")
            c1.width, c2.width = Cm(5.2), Cm(11.0)
        para("", after=6)
    para("", after=10)
    sign = doc.add_table(rows=2, cols=3)
    vals = ["______________", s.signer or "______________", f"{s.date:%d.%m.%Y}" if s.date else "«___» __________ 20__ г."]
    caps = ["подпись", "расшифровка", "дата"]
    for i in range(3):
        sign.rows[0].cells[i].text = vals[i]
        sign.rows[1].cells[i].paragraphs[0].add_run(caps[i]).font.size = Pt(8)
    if s.attachment:
        para("", after=4)
        para(s.attachment, size=10)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
