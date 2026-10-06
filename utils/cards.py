"""Карточка организации (реквизиты) в фирменном стиле Creatica — как КП: монохром, тонкие линии, IBM Plex Sans.

Реквизиты не хранятся в публичном репозитории: они лежат в company_cards.json (приватный пакет
PRIVATE_BUNDLE_B64, как principal.py). Если файла нет — берётся company_cards_example.json с заглушками.
"""

from __future__ import annotations

import io
import json
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

from utils import tag as tagmod
from utils.kp import BRAND, GREY, HAIR, INK, MR, ML, PH, PW, SHOWROOM, SITE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cards() -> dict[str, dict]:
    """{ключ: {"short": ..., "full": ..., "rows": [[подпись, значение], ...]}} в порядке файла."""
    for name in ("company_cards.json", "company_cards_example.json"):
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return {}


def card_text(card: dict) -> str:
    """Реквизиты текстом — чтобы скопировать и переслать клиенту одним сообщением."""
    lines = [card["full"], ""] + [f"{label}: {value}" for label, value in card["rows"]]
    return "\n".join(lines)


def build_card_pdf(card: dict) -> bytes:
    tagmod._register_fonts()
    reg, bold = tagmod.REG, tagmod.BOLD
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Карточка предприятия — {card['short']}")
    c.setAuthor("Creatica")

    def Y(y: float) -> float:
        return PH - y

    def text(x, y, s, size=9.5, is_bold=False, color=INK, space=0.0, align="l"):
        font = bold if is_bold else reg
        c.setFillColorRGB(*color)
        if space:
            t = c.beginText(x, Y(y))
            t.setFont(font, size)
            t.setCharSpace(space)
            t.textOut(s)
            t.setCharSpace(0)
            c.drawText(t)
            return
        c.setFont(font, size)
        (c.drawRightString if align == "r" else c.drawString)(x, Y(y), s)

    def label(x, y, s):
        text(x, y, s.upper(), size=6.8, color=GREY, space=1.1)

    def hair(y, color=HAIR, width=0.5):
        c.setStrokeColorRGB(*color)
        c.setLineWidth(width)
        c.line(ML, Y(y), MR, Y(y))

    # шапка — как в КП: логотип слева, сайт справа
    logo = ImageReader(os.path.join(BRAND, "creatica_logo.png"))
    h = 17.0
    w = h * logo.getSize()[0] / logo.getSize()[1]
    c.drawImage(logo, ML, Y(34 + h), w, h, mask="auto")
    text(MR, 45, SITE, size=8.5, color=GREY, align="r")

    y = 106.0
    label(ML, y, "Карточка предприятия")
    y += 14
    hair(y, color=INK, width=0.8)
    y += 40
    text(ML, y, card["short"], size=22, is_bold=True)
    y += 20
    for ln in simpleSplit(card["full"], reg, 10.5, MR - ML):
        text(ML, y, ln, size=10.5, color=GREY)
        y += 14
    y += 18
    hair(y)

    # реквизиты: подпись слева, значение справа, тонкая линия между строками
    col = ML + 170
    for lab, value in card["rows"]:
        lines = []
        for part in str(value).split("\n"):
            lines += simpleSplit(part, reg, 10.5, MR - col) or [""]
        row_h = 16 + 14 * len(lines)
        label(ML, y + 19, lab)
        for i, ln in enumerate(lines):
            text(col, y + 20 + i * 14, ln, size=10.5)
        y += row_h + 4
        hair(y)

    # чёрная плашка внизу — фирменный акцент, как в конце КП
    panel_h = 70.0
    top = PH - 52 - panel_h
    c.setFillColorRGB(*INK)
    c.rect(0, Y(top + panel_h), PW, panel_h, stroke=0, fill=1)
    text(ML, top + 32, "Creatica", size=18, is_bold=True, color=(1, 1, 1))
    text(ML, top + 50, SITE, size=9.5, color=(0.86, 0.85, 0.83))
    text(MR, top + 32, card["short"], size=9.5, color=(0.86, 0.85, 0.83), align="r")

    # подвал — как в КП
    hair(PH - 38)
    text(ML, PH - 26, SHOWROOM, size=7, color=GREY)
    c.showPage()
    c.save()
    return buf.getvalue()


def build_card_preview(card: dict, dpi: int = 70) -> bytes:
    import pymupdf

    doc = pymupdf.open(stream=build_card_pdf(card), filetype="pdf")
    return doc[0].get_pixmap(dpi=dpi).tobytes("png")
