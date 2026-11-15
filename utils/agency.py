"""Пакет документов для выплаты дизайнеру (агенту): договор, реквизиты, отчёт, счёт, акт.

Оформление — в стиле КП и заявлений Creatica: монохром, тонкие линии, IBM Plex Sans, логотип сверху,
сумма на светлой плашке. Данные дизайнера (Агента) подставляются из ответов в боте/на сайте, данные
Принципала — из principal.py, печать и подпись — из папки assets/ (они не публикуются в GitHub).
"""

from __future__ import annotations

import datetime as dt
import io
import os
from dataclasses import dataclass

from num2words import num2words
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from utils import tag as tagmod
from utils.kp import BRAND, GREY, HAIR, INK, ML, MR, PANEL, PH, PW, SHOWROOM, SITE

try:
    from principal import PRINCIPAL
except ImportError:  # свежий клон репозитория без личных данных
    from principal_example import PRINCIPAL  # type: ignore  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

FONT, FONT_B = "Helvetica", "Helvetica-Bold"
NB = "\xa0"
C_INK, C_GREY, C_HAIR, C_PANEL = colors.Color(*INK), colors.Color(*GREY), colors.Color(*HAIR), colors.Color(*PANEL)
CW = MR - ML  # ширина рабочей области


def _register_fonts() -> None:
    global FONT, FONT_B
    tagmod._register_fonts()
    FONT, FONT_B = tagmod.REG, tagmod.BOLD
    if FONT != "Helvetica":
        pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_B, italic=FONT, boldItalic=FONT_B)


@dataclass
class AgencyData:
    agent_name: str
    inn: str
    ogrnip: str
    address: str
    rs: str
    bank: str
    ks: str
    bik: str
    phone: str
    email: str
    order_name: str
    sale_amount: float
    reward: float
    tax: str
    date: dt.date

    @property
    def number(self) -> str:
        return f"{self.date:%d%m}-{self.inn[-4:]}"


def fmt(x: float) -> str:
    """1 234,50 — как в КП и заявлениях."""
    return f"{x:,.2f}".replace(",", NB).replace(".", ",")


def tax_sum(data: "AgencyData") -> str:
    """Сумма налога по ставке из отчёта (от вознаграждения), руб. — при 0% будет 0,00."""
    try:
        rate = float(str(data.tax).replace(",", "."))
    except ValueError:
        rate = 0.0
    return fmt(round(data.reward * rate / 100, 2))


def words(x: float) -> str:
    """Сумма прописью — только рубли (копейки пишутся цифрами рядом)."""
    text = num2words(int(x), lang="ru")
    return text[:1].upper() + text[1:]


def date_long(d: dt.date) -> str:
    return f"«{d:%d}» {MONTHS[d.month - 1]} {d.year} г."


def rub_line(x: float, tail: str = "руб. {k:02d} коп.") -> str:
    kop = int(round((x - int(x)) * 100))
    return f"{fmt(x)} ({words(x)}) " + tail.format(k=kop)


def _spaced(c, x: float, y: float, s: str, size: float = 6.8, color=C_GREY, font: str | None = None, align: str = "l",
            space: float = 1.1) -> None:
    """Подпись мелкими прописными с разрядкой — как в КП."""
    font = font or FONT
    s = s.upper()
    w = c.stringWidth(s, font, size) + space * max(len(s) - 1, 0)
    if align == "r":
        x -= w
    t = c.beginText(x, y)
    t.setFont(font, size)
    t.setFillColor(color)
    t.setCharSpace(space)
    t.textOut(s)
    c.drawText(t)


# --- страница: логотип, сайт, подвал — как в КП -----------------------------------------------------


def _on_page(c, doc) -> None:
    c.saveState()
    logo = os.path.join(BRAND, "creatica_logo.png")
    if os.path.exists(logo):
        from reportlab.lib.utils import ImageReader

        img = ImageReader(logo)
        h = 17.0
        w = h * img.getSize()[0] / img.getSize()[1]
        c.drawImage(img, ML, PH - 34 - h, w, h, mask="auto")
    c.setFont(FONT, 8.5)
    c.setFillColor(C_GREY)
    c.drawRightString(MR, PH - 45, SITE)
    c.setStrokeColor(C_HAIR)
    c.setLineWidth(0.5)
    c.line(ML, 38, MR, 38)
    c.setFont(FONT, 7)
    c.drawString(ML, 26, SHOWROOM)
    c.drawRightString(MR, 26, f"стр. {doc.page}")
    c.restoreState()


class _Title(Flowable):
    """Подпись-разрядка, тонкая чёрная линия, крупный заголовок (и строка под ним)."""

    def __init__(self, label: str, title: str, sub: str = ""):
        super().__init__()
        self.label, self.title, self.sub = label, title, sub
        self.width, self.height = CW, (62 if sub else 50)

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        c = self.canv
        top = self.height
        _spaced(c, 0, top - 8, self.label)
        c.setStrokeColor(C_INK)
        c.setLineWidth(0.8)
        c.line(0, top - 16, self.width, top - 16)
        c.setFillColor(C_INK)
        c.setFont(FONT_B, 15)
        c.drawString(0, top - 38, self.title)
        if self.sub:
            c.setFont(FONT, 9)
            c.setFillColor(C_GREY)
            c.drawString(0, top - 54, self.sub)


class _Label(Flowable):
    """Мелкая серая подпись-разрядка + тонкая линия (заголовок раздела)."""

    def __init__(self, text: str):
        super().__init__()
        self.text = text
        self.width, self.height = CW, 20

    def wrap(self, aw, ah):
        self.width = aw
        return aw, self.height

    def draw(self):
        c = self.canv
        _spaced(c, 0, 10, self.text)
        c.setStrokeColor(C_HAIR)
        c.setLineWidth(0.5)
        c.line(0, 2, self.width, 2)


class _SignStamp(Flowable):
    """Строка «____ / Фамилия И. О. /» с подписью на линии и печатью, заходящей на подпись (М.П.)."""

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.width, self.height = 250, 78

    def draw(self):
        c = self.canv
        base_y = 30  # базовая линия строки подписи
        x0 = 56  # слева от линии место под печать (М.П.)
        line_w = 104
        c.setStrokeColor(C_INK)
        c.setLineWidth(0.6)
        c.line(x0, base_y - 1.5, x0 + line_w, base_y - 1.5)
        c.setFont(FONT, 8)
        c.setFillColor(C_INK)
        c.drawString(x0 + line_w + 6, base_y, f"/ {self.name} /")
        sig = os.path.join(ROOT, "assets", "signature.png")
        stamp = os.path.join(ROOT, "assets", "stamp.png")
        if os.path.exists(stamp):  # печать слева, заходит на начало линии подписи
            c.drawImage(stamp, 0, 0, 76, 76, mask="auto")
        if os.path.exists(sig):  # подпись — на линии, правее печати, расшифровка остаётся открытой
            c.drawImage(sig, x0 + line_w * 0.25, base_y - 8, 54, 38, mask="auto")


class _SignLine(Flowable):
    """Пустая строка подписи (для Агента): линия и расшифровка, ниже — «М.П. (при наличии)»."""

    def __init__(self, name: str, stamp_note: bool = True):
        super().__init__()
        self.name, self.note = name, stamp_note
        self.width, self.height = 250, 40

    def draw(self):
        c = self.canv
        c.setStrokeColor(C_INK)
        c.setLineWidth(0.6)
        c.line(0, 22, 104, 22)
        c.setFont(FONT, 8)
        c.setFillColor(C_INK)
        c.drawString(110, 23.5, f"/ {self.name} /")
        if self.note:
            c.setFont(FONT, 7)
            c.setFillColor(C_GREY)
            c.drawString(0, 8, "М.П. (при наличии)")


def _sign_block(name: str) -> Flowable:
    return _SignStamp(name)


CONTRACT = [
    ("h", "1. Термины и определения"),
    ("p", "Для целей настоящего Договора применяются следующие термины:"),
    ("b", "Клиент – любое физическое или юридическое лицо, желающее заключить с Принципалом Договор."),
    ("b", "Договор – договор оказания услуг, выполнения работ, реализации товара, который заключается с клиентом."),
    ("h", "2. Предмет Договора"),
    ("p", "2.1. По настоящему договору Агент обязуется за вознаграждение совершать по Поручению Принципала от его имени, в его интересах и за его счёт следующие фактические и юридические действия:"),
    ("b", "осуществлять поиск, привлечение Клиентов;"),
    ("b", "осуществлять иные фактические и юридические действия в случае получения от Принципала соответствующего указания."),
    ("p", "2.2. Агент вправе привлекать субагентов."),
    ("p", "2.3. Агент вправе заключать аналогичные настоящему договоры с третьими лицами."),
    ("p", "2.4. Принципал вправе заключать аналогичные настоящему договоры с третьими лицами."),
    ("h", "3. Обязанности Сторон"),
    ("p", "3.1. Агент обязуется:"),
    ("p", "3.1.1. Исполнять поручение в соответствии с указаниями Принципала."),
    ("p", "3.1.2. Сохранять коммерческую тайну при выполнении поручений."),
    ("p", "3.1.3. Доносить информацию до Принципала об изменении или дополнении различных условий в заключаемых сделках."),
    ("p", "3.1.4. Оказывать всяческое содействие контрагенту при ведении переговоров с третьими лицами и принимать участие в согласовании условий каких-либо сделок."),
    ("p", "3.2. Принципал обязуется:"),
    ("p", "3.2.1. Передать Агенту форму Договора."),
    ("p", "3.2.2. Передать Агенту иную информацию, необходимую последнему для эффективного выполнения поручения, содержащегося в настоящем договоре."),
    ("p", "3.2.3. Принимать документы об отчетности по исполнению поручений."),
    ("p", "3.2.4. Выплачивать Агенту вознаграждение в соответствии с условиями настоящего договора."),
    ("h", "4. Вознаграждение Агента и порядок расчетов"),
    ("p", "4.1. Выплата вознаграждения в сторону Агента осуществляется после утверждения Принципалом документов об отчетности. Расчеты между сторонами производятся в безналичной денежной форме, в валюте Российской Федерации (рубли РФ). Условия налогообложения вознаграждения: {tax}%."),
    ("p", "4.2. Отчеты о проделанной работе составляются и направляются в сторону Принципала в течение 30 рабочих дней с момента выполнения Агентом конкретного поручения. Если имеются какие-либо возражения по отчетности, Принципал обязан уведомить о них Агента в течение 5 рабочих дней с момента получения документов об отчетности. Поручения и отчетность являются неотъемлемой составляющей настоящего Договора."),
    ("p", "4.3. Обязательство Принципала по перечислению сумм вознаграждения считается исполненным в момент списания денежных средств с расчётного счёта банка Принципала."),
    ("h", "5. Ответственность Сторон"),
    ("p", "5.1. В случае неисполнения или ненадлежащего исполнения одной из Сторон обязательств по настоящему договору она обязана возместить другой стороне причинённые таким неисполнением документально подтвержденные убытки."),
    ("p", "5.2. Неисполнение одной из Сторон условий настоящего договора, приведшее к материальным потерям другой Стороны, влечёт за собой применение к виновной стороне штрафных санкций в размере нанесённого ущерба и может служить основанием досрочного прекращения договора по инициативе добросовестной стороны."),
    ("p", "5.3. Уплата штрафа и применение иных мер ответственности за нарушение обязательств не освобождает Стороны от исполнения обязательств по Договору."),
    ("h", "6. Срок действия Договора"),
    ("p", "6.1. Настоящий договор вступает в силу с момента его подписания и действует в течение 1 (Одного) года."),
    ("p", "6.2. Настоящий договор может быть пролонгирован на основании соглашения Сторон. Условия пролонгируемого договора определяются Сторонами за 1 (Один) месяц до истечения срока действия настоящего Договора."),
    ("p", "6.3. Принципал вправе в любое время в одностороннем внесудебном порядке отказаться от исполнения настоящего договора с обязательным письменным уведомлением Агента не менее чем за 10 (Десять) календарных дней."),
    ("h", "7. Форс-мажор и Конфиденциальность"),
    ("p", "7.1. Стороны освобождаются от ответственности за частичное или полное неисполнение обязательств при наступлении обстоятельств непреодолимой силы (форс-мажор), возникших помимо воли Сторон."),
    ("p", "7.2. Стороны обязуются не разглашать коммерческую тайну и конфиденциальные сведения, полученные в рамках исполнения настоящего Договора. Данное обязательство действует в течение срока действия Договора и в течение 3 (трех) лет после его прекращения."),
    ("h", "8. Разрешение споров и заключительные положения"),
    ("p", "8.1. Все споры и разногласия разрешаются путём переговоров с соблюдением обязательного досудебного (претензионного) порядка. Срок ответа на претензию — 10 (десять) календарных дней."),
    ("p", "8.2. При недостижении согласия споры передаются на рассмотрение в суд по месту нахождения Принципала в соответствии с законодательством РФ."),
    ("p", "8.3. Настоящий Договор составлен в двух экземплярах, имеющих одинаковую юридическую силу, по одному для каждой из Сторон."),
    ("h", "9. Реквизиты и подписи Сторон"),
]


def build_agency_package(data: AgencyData) -> io.BytesIO:
    _register_fonts()
    P = PRINCIPAL
    num, d_long = data.number, date_long(data.date)

    base = ParagraphStyle("base", fontName=FONT, fontSize=8.2, leading=11.4, alignment=TA_LEFT, textColor=C_INK)
    just = ParagraphStyle("just", parent=base, alignment=TA_JUSTIFY)
    small = ParagraphStyle("small", parent=base, fontSize=8, leading=10.8)
    muted = ParagraphStyle("muted", parent=base, fontSize=7.4, leading=10, textColor=C_GREY)
    head = ParagraphStyle("head", parent=base, fontName=FONT_B, fontSize=8.8, leading=11.5, spaceBefore=7, spaceAfter=1)
    bullet = ParagraphStyle("bul", parent=just, leftIndent=11, bulletIndent=2)
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    cell = ParagraphStyle("cell", parent=base, fontSize=8, leading=10.6)
    cellc = ParagraphStyle("cellc", parent=cell, alignment=1)
    cellr = ParagraphStyle("cellr", parent=cell, alignment=TA_RIGHT)
    th = ParagraphStyle("th", parent=cell, fontSize=6.8, leading=8.4, textColor=C_GREY)
    thr = ParagraphStyle("thr", parent=th, alignment=TA_RIGHT)
    thc = ParagraphStyle("thc", parent=th, alignment=1)
    big = ParagraphStyle("big", parent=base, fontName=FONT_B, fontSize=17, leading=21)

    def para(t, s=base):
        return Paragraph(t, s)

    def lab(t):
        return Paragraph(t.upper(), th)

    def rule_table(rows, widths, header=True, total_row=False, extra=()):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        st = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, C_HAIR),
        ]
        if header:
            st += [("LINEBELOW", (0, 0), (-1, 0), 0.8, C_INK), ("TOPPADDING", (0, 0), (-1, 0), 3)]
        if total_row:
            st += [("BACKGROUND", (0, -1), (-1, -1), C_PANEL), ("LINEBELOW", (0, -1), (-1, -1), 0, C_PANEL)]
        t.setStyle(TableStyle(st + list(extra)))
        return t

    def info_rows(pairs, lw=92, w=CW / 2 - 12):
        """Строки «ПОДПИСЬ — значение» с тонкими линиями (как реквизиты в заявлении)."""
        t = Table([[lab(k), para(v, cell)] for k, v in pairs if v], colWidths=[lw, w - lw])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.5, C_HAIR),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t

    def city_row():
        t = Table([[para(P["city"], muted), para(d_long, ParagraphStyle("r", parent=muted, alignment=TA_RIGHT))]],
                  colWidths=[CW / 2, CW / 2])
        t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        return t

    def amount_panel(label: str, amount: float, extra: str = ""):
        inner = [[lab(label)], [para(f"{fmt(amount)}{NB}₽", big)], [para(f"{words(amount)} руб. "
                 f"{int(round((amount - int(amount)) * 100)):02d} коп.", muted)]]
        if extra:
            inner.append([para(extra, muted)])
        t = Table(inner, colWidths=[CW])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), C_PANEL), ("LEFTPADDING", (0, 0), (-1, -1), 14),
                               ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
                               ("TOPPADDING", (0, 0), (-1, 0), 12)]))
        return t

    story: list = []

    # ------------------------------------------------------------ договор
    story += [_Title("Агентский договор", f"Договор № {num} от {data.date:%d.%m.%Y}", "на оказание агентских услуг"),
              city_row(), Spacer(1, 6)]
    story.append(
        para(
            f"{data.agent_name}, ОГРНИП / ОГРН {data.ogrnip}, ИНН {data.inn}, именуемый в дальнейшем «Агент», "
            f"действующий на основании Устава, с одной стороны, и {P['full']} в лице {P['director_post']} "
            f"{P['director_name']}, действующей на основании Устава, именуемое в дальнейшем «Принципал», с другой "
            "стороны, совместно именуемые «Стороны», заключили настоящий договор о нижеследующем:",
            just,
        )
    )
    last_head = CONTRACT[-1][1]  # «9. Реквизиты и подписи Сторон» — выводим вместе с таблицей реквизитов
    for kind, text in CONTRACT[:-1]:
        text = text.replace("{tax}%", f"{data.tax}% ({tax_sum(data)} руб.)")
        if kind == "h":
            story.append(para(text, head))
        elif kind == "b":
            story.append(Paragraph(text, bullet, bulletText="•"))
        else:
            story.append(para(text, just))

    # ------------------------------------------------------------ реквизиты
    gap = 24
    half = (CW - gap) / 2
    agent_rows = info_rows([
        ("Агент", data.agent_name), ("ИНН", data.inn), ("ОГРНИП / ОГРН", data.ogrnip), ("Юр. адрес", data.address),
        ("Р/с", data.rs), ("Банк", data.bank), ("К/с", data.ks), ("БИК", data.bik), ("Телефон", data.phone),
        ("Email", data.email),
    ], w=half)
    principal_rows = info_rows([
        ("Принципал", P["short_caps"]), ("ИНН / КПП", f"{P['inn']} / {P['kpp']}"), ("ОГРН", P["ogrn"]),
        ("Юр. адрес", P["address"]), ("Р/с", P["rs"]), ("Банк", P["bank"]), ("К/с", P["ks"]), ("БИК", P["bik"]),
        ("Телефон", P["phone"]),
    ], w=half)
    req = Table(
        [[agent_rows, "", principal_rows],
         [[Spacer(1, 6), para("Агент (Исполнитель):", muted), _SignLine(data.agent_name)], "",
          [Spacer(1, 6), para(f"Генеральный директор {P['short']}:", muted), _sign_block(P["director_short"])]]],
        colWidths=[half, gap, half],
    )
    req.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether([para(last_head, head), Spacer(1, 4), req]))
    story.append(PageBreak())

    # ------------------------------------------------------------ отчёт агента
    reward, sale = data.reward, data.sale_amount
    story += [
        _Title("Отчёт", "Отчёт агента", f"по агентскому договору № {num} от {d_long}"),
        city_row(), Spacer(1, 8),
    ]
    parties = Table([[info_rows([("Кому", f"{P['short']}, {P['address']}")], w=half),
                      "", info_rows([("От кого", data.agent_name), ("Адрес", data.address), ("Телефон", data.phone)], w=half)]],
                    colWidths=[half, gap, half])
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [
        parties, Spacer(1, 10),
        para(f"Направляю Отчёт по агентскому договору № {num} от {d_long} За отчётный период выполнены следующие "
             "действия (операции):", just),
        Spacer(1, 8),
        rule_table(
            [[Paragraph("№ П/П", thc), Paragraph("ДАТА (ПЕРИОД)", thc), Paragraph("НАИМЕНОВАНИЕ ТОВАРА / ЗАКАЗА", th),
              Paragraph("СТОИМОСТЬ ТОВАРА, РУБ.", thr), Paragraph("КОЛ-ВО", thc), Paragraph("СУММА РЕАЛИЗАЦИИ, РУБ.", thr),
              Paragraph("ВОЗНАГРАЖДЕНИЕ АГЕНТА, РУБ.", thr)],
             [para("1", cellc), para(f"{data.date:%d.%m.%Y}", cellc), para(data.order_name, cell), para(fmt(sale), cellr),
              para("1", cellc), para(fmt(sale), cellr), para(fmt(reward), cellr)],
             [para("<b>Итого:</b>", cell), "", "", para(f"<b>{fmt(sale)}</b>", cellr), para("<b>1</b>", cellc),
              para(f"<b>{fmt(sale)}</b>", cellr), para(f"<b>{fmt(reward)}</b>", cellr)]],
            [24, 56, 128, 70, 38, 90, 109], total_row=True, extra=[("SPAN", (0, 2), (2, 2))]),
        Spacer(1, 10),
        amount_panel("Сумма агентского вознаграждения", reward, f"Налогообложение: {data.tax}% ({tax_sum(data)} руб.)"),
        Spacer(1, 10),
        para("За отчётный период Агент надлежащим образом (качественно и в срок) выполнил свои обязательства по "
             "привлечению клиентов.", base),
        Spacer(1, 3),
        para("Настоящий Отчёт составлен в двух экземплярах, имеющих равную юридическую силу, по одному для каждой из "
             "Сторон. Возражения по Отчёту принимаются в течение 5 рабочих дней с момента получения.", just),
        Spacer(1, 16),
    ]
    sig = Table(
        [[[para("Отчёт сдал (Агент):", muted), para(data.agent_name, cell), para(f"ИНН: {data.inn}", cell),
           Spacer(1, 10), _SignLine(data.agent_name)],
          "",
          [para("Отчёт принял (Принципал):", muted), para(P["short"], cell), para("Генеральный директор", cell),
           _sign_block(P["director_short"])]]],
        colWidths=[half, gap, half])
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [KeepTogether(sig), PageBreak()]

    # ------------------------------------------------------------ счёт
    story += [_Title("Счёт", f"Счёт на оплату № {num} от {d_long}"), Spacer(1, 6)]
    bank_tbl = Table(
        [
            [[para("БАНК ПОЛУЧАТЕЛЯ", th), para(data.bank, small)], [para("БИК", th), para("СЧ. №", th)],
             [para(data.bik, small), para(data.ks, small)]],
            [[para(f"ИНН: {data.inn}", small), para("ПОЛУЧАТЕЛЬ", th), para(data.agent_name, small)],
             [para("КПП", th), para("СЧ. №", th)], [para("-", small), para(data.rs, small)]],
        ],
        colWidths=[290, 55, CW - 345],
    )
    bank_tbl.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, C_INK), ("INNERGRID", (0, 0), (-1, -1), 0.5, C_HAIR),
                                  ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [
        bank_tbl, Spacer(1, 10),
        info_rows([
            ("Поставщик", f"{data.agent_name}, ИНН {data.inn}, {data.ogrnip}, {data.address}, тел.: {data.phone}"),
            ("Покупатель", f"{P['short_caps']}, ИНН {P['inn']}, КПП {P['kpp']}, {P['address_invoice']}"),
            ("Основание", f"Агентский договор № {num} от {d_long} (Отчёт Агента от {d_long})"),
        ], lw=80, w=CW),
        Spacer(1, 12),
        rule_table(
            [[Paragraph("№", thc), Paragraph("ТОВАРЫ (РАБОТЫ, УСЛУГИ)", th), Paragraph("КОЛ-ВО", thc), Paragraph("ЕД.", thc),
              Paragraph("ЦЕНА, РУБ.", thr), Paragraph("СУММА, РУБ.", thr)],
             [para("1", cellc),
              para(f"Агентское вознаграждение за привлечение клиентов по заказу «{data.order_name}» по Договору № {num}", cell),
              para("1", cellc), para("усл. ед.", cellc), para(fmt(reward), cellr), para(fmt(reward), cellr)]],
            [24, CW - 24 - 44 - 50 - 82 - 88, 44, 50, 82, 88]),
        Spacer(1, 10),
        para(f"Всего наименований 1, на сумму {fmt(reward)} руб.", base),
        Spacer(1, 6),
        amount_panel("Итого к оплате", reward, f"Налогообложение: {data.tax}% — {tax_sum(data)} руб."),
        Spacer(1, 18),
        KeepTogether([
            para("Руководитель / Предприниматель:", muted), _SignLine(data.agent_name, stamp_note=False), Spacer(1, 8),
            para("Бухгалтер:", muted), _SignLine(data.agent_name, stamp_note=False), Spacer(1, 4),
            para("М.П. (при наличии)", muted),
        ]),
        PageBreak(),
    ]

    # ------------------------------------------------------------ акт
    story += [
        _Title("Акт", f"Акт № {num} от {d_long}", "об оказании агентских услуг"), Spacer(1, 6),
        info_rows([
            ("Исполнитель", f"{data.agent_name}, ИНН: {data.inn}, ОГРНИП / ОГРН: {data.ogrnip}, Адрес: {data.address}, "
                            f"Р/с: {data.rs} в {data.bank}, БИК: {data.bik}, К/с: {data.ks}"),
            ("Заказчик", f"{P['short_caps']}, ИНН: {P['inn']}, КПП: {P['kpp']}, Адрес: {P['address_invoice']}, "
                         f"Р/с: {P['rs']} в {P['bank_act']}, БИК: {P['bik']}, К/с: {P['ks']}"),
            ("Основание", f"Договор на оказание агентских услуг № {num} от {d_long} (Отчёт Агента от {d_long})"),
        ], lw=80, w=CW),
        Spacer(1, 12),
        rule_table(
            [[Paragraph("№", thc), Paragraph("НАИМЕНОВАНИЕ РАБОТ, УСЛУГ", th), Paragraph("КОЛ-ВО", thc), Paragraph("ЕД.", thc),
              Paragraph("ЦЕНА, РУБ.", thr), Paragraph("СУММА, РУБ.", thr)],
             [para("1", cellc),
              para(f"Оказание услуг по поиску и привлечению клиентов по Договору № {num}<br/>(проект: {data.order_name})", cell),
              para("1", cellc), para("усл. ед.", cellc), para(fmt(reward), cellr), para(fmt(reward), cellr)]],
            [24, CW - 24 - 44 - 50 - 82 - 88, 44, 50, 82, 88]),
        Spacer(1, 10),
        para(f"Всего оказано услуг 1, на сумму {fmt(reward)} руб. {int(round((reward - int(reward)) * 100)):02d} коп.", base),
        Spacer(1, 6),
        amount_panel("Итого", reward, f"Налогообложение: {data.tax}% ({tax_sum(data)} руб.)"),
        Spacer(1, 10),
        para("Вышеперечисленные услуги выполнены полностью и в срок. Заказчик претензий по объёму, качеству и срокам "
             "оказания услуг не имеет.", base),
        Spacer(1, 16),
    ]
    act_sig = Table(
        [[[para("Исполнитель:", muted), para(data.agent_name, cell), para(f"ИНН: {data.inn}", cell), Spacer(1, 10),
           _SignLine(data.agent_name)],
          "",
          [para("Заказчик:", muted), para(P["short_caps"], cell), para("Генеральный директор", cell),
           _sign_block(P["director_short"])]]],
        colWidths=[half, gap, half])
    act_sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether(act_sig))

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, title=f"Агентский договор № {num}", author="Creatica")
    doc.addPageTemplates([PageTemplate(
        id="main", onPage=_on_page,
        frames=[Frame(ML, 52, CW, PH - 52 - 66, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)])])
    doc.build(story)
    buf.seek(0)
    return buf
