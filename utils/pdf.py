"""Генерация PDF-документа на выплату дизайнеру (ReportLab).

Отдельная головная боль в прошлой версии бота — кириллица в PDF (Helvetica её не
умеет). Здесь шрифт с поддержкой кириллицы ищется автоматически в стандартных
папках Windows, ничего скачивать вручную не нужно.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from config import COMPANY_NAME

FONT_NAME = "Helvetica"
_FONT_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts", "DejaVuSans.ttf"),
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _ensure_font() -> str:
    global FONT_NAME
    if FONT_NAME != "Helvetica":
        return FONT_NAME
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("MainFont", path))
                FONT_NAME = "MainFont"
                break
            except Exception:
                continue
    return FONT_NAME


@dataclass
class PaymentData:
    designer_name: str
    inn: str
    order: str
    amount: str
    date: str
    bank_details: str
    direction: str = ""


def generate_payment_pdf(data: PaymentData) -> io.BytesIO:
    font = _ensure_font()
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 60

    if COMPANY_NAME:
        c.setFont(font, 11)
        c.drawString(50, y, COMPANY_NAME)
        y -= 30

    c.setFont(font, 16)
    c.drawCentredString(width / 2, y, "ВЕДОМОСТЬ НА ВЫПЛАТУ ДИЗАЙНЕРУ")
    y -= 10
    c.setLineWidth(1)
    c.line(50, y, width - 50, y)
    y -= 35

    def field(label: str, value: str, line_height: float = 28) -> None:
        nonlocal y
        c.setFont(font, 11)
        c.drawString(50, y, f"{label}:")
        c.setFont(font, 12)
        c.drawString(220, y, value or "—")
        y -= line_height

    field("Дата", data.date)
    if data.direction:
        field("Направление", data.direction)
    field("Дизайнер", data.designer_name)
    field("ИНН", data.inn)
    field("Заказ / проект", data.order)

    c.setFont(font, 11)
    c.drawString(50, y, "Банковские реквизиты дизайнера:")
    y -= 20
    c.setFont(font, 11)
    text_obj = c.beginText(50, y)
    text_obj.setFont(font, 11)
    for line in (data.bank_details or "—").split("\n"):
        text_obj.textLine(line)
    c.drawText(text_obj)
    y -= 20 * max(1, len((data.bank_details or "").split("\n"))) + 20

    c.setLineWidth(1)
    c.line(50, y, width - 50, y)
    y -= 30

    c.setFont(font, 14)
    c.drawString(50, y, f"ИТОГО К ВЫПЛАТЕ: {data.amount} ₽")
    y -= 80

    c.setFont(font, 11)
    c.drawString(50, y, "Выдал: ____________________")
    c.drawString(320, y, "Получил: ____________________")
    y -= 25
    c.setFont(font, 9)
    c.drawString(50, y, "(подпись, ФИО)")
    c.drawString(320, y, "(подпись, ФИО)")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer
