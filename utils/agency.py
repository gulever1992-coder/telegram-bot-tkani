"""Пакет документов для выплаты дизайнеру (агенту): договор, реквизиты, отчёт, счёт, акт.

Данные дизайнера (Агента) подставляются из ответов в боте, данные Принципала —
из principal.py, печать и подпись — из папки assets/ (они не публикуются в GitHub).
"""

from __future__ import annotations

import datetime as dt
import io
import os
from dataclasses import dataclass

from num2words import num2words
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

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


def _register_fonts() -> None:
    global FONT, FONT_B
    if FONT != "Helvetica":
        return
    regular = os.path.join(ROOT, "fonts", "DejaVuSans.ttf")
    bold = os.path.join(ROOT, "fonts", "DejaVuSans-Bold.ttf")
    if not os.path.exists(regular):
        regular = r"C:\Windows\Fonts\arial.ttf"
        bold = r"C:\Windows\Fonts\arialbd.ttf"
    if os.path.exists(regular):
        pdfmetrics.registerFont(TTFont("Main", regular))
        FONT = "Main"
        if os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("Main-B", bold))
            FONT_B = "Main-B"
        else:
            FONT_B = "Main"


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
    return f"{x:,.2f}"


def words(x: float) -> str:
    text = num2words(round(x, 2), lang="ru")
    return text[:1].upper() + text[1:]


def date_long(d: dt.date) -> str:
    return f"«{d:%d}» {MONTHS[d.month - 1]} {d.year} г."


def rub_line(x: float, tail: str = "руб. {k:02d} коп.") -> str:
    kop = int(round((x - int(x)) * 100))
    return f"{fmt(x)} ({words(x)}) " + tail.format(k=kop)


def _image(name: str, width: float, height: float):
    path = os.path.join(ROOT, "assets", name)
    if os.path.exists(path):
        return Image(path, width=width, height=height)
    return Spacer(width, height)


def _sign_block() -> Table:
    t = Table([[_image("signature.png", 44, 30), _image("stamp.png", 65, 62)]], colWidths=[78, 70])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return t


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

    base = ParagraphStyle("base", fontName=FONT, fontSize=7.4, leading=9.2, alignment=TA_LEFT)
    just = ParagraphStyle("just", parent=base, alignment=TA_JUSTIFY)
    small = ParagraphStyle("small", parent=base, fontSize=7.5, leading=9.5)
    head = ParagraphStyle("head", parent=base, fontName=FONT_B, fontSize=8, spaceBefore=3, spaceAfter=0)
    bullet = ParagraphStyle("bul", parent=just, leftIndent=10, bulletIndent=2)
    title = ParagraphStyle("title", parent=base, fontName=FONT_B, fontSize=12, leading=15, alignment=TA_CENTER)
    title_n = ParagraphStyle("title_n", parent=title, fontName=FONT, fontSize=12)
    sub = ParagraphStyle("sub", parent=title, fontSize=10, leading=13)
    cell = ParagraphStyle("cell", parent=base, fontSize=8, leading=10)
    cellc = ParagraphStyle("cellc", parent=cell, alignment=TA_CENTER)
    cellb = ParagraphStyle("cellb", parent=cellc, fontName=FONT_B)

    def para(t, s=base):
        return Paragraph(t, s)

    grid = TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )
    head_bg = ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2"))

    story: list = []

    # ------------------------------------------------------------ договор
    story += [
        para(f"ДОГОВОР № {num} / {data.date:%d.%m.%Y}", title),
        para("на оказание агентских услуг", sub),
        Spacer(1, 4),
    ]
    city_row = Table(
        [[para(P["city"]), para(d_long, ParagraphStyle("r", parent=base, alignment=2))]],
        colWidths=[257, 258],
    )
    city_row.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [city_row, Spacer(1, 4)]
    story.append(
        para(
            f"{data.agent_name}, ОГРНИП / ОГРН {data.ogrnip}, ИНН {data.inn}, именуемый в дальнейшем «Агент», "
            f"действующий на основании Устава, с одной стороны, и {P['full']} в лице {P['director_post']} "
            f"{P['director_name']}, действующей на основании Устава, именуемое в дальнейшем «Принципал», с другой "
            "стороны, совместно именуемые «Стороны», заключили настоящий договор о нижеследующем:",
            just,
        )
    )
    for kind, text in CONTRACT:
        text = text.replace("{tax}", data.tax)
        if kind == "h":
            story.append(para(text, head))
        elif kind == "b":
            story.append(Paragraph(text, bullet, bulletText="•"))
        else:
            story.append(para(text, just))
    story.append(PageBreak())

    # ------------------------------------------------------------ реквизиты
    agent_cell = [
        para("АГЕНТ (Исполнитель):", cell),
        para(data.agent_name, cell),
        para(f"ИНН: {data.inn}", cell),
        para(f"ОГРНИП / ОГРН: {data.ogrnip}", cell),
        para(f"Юр. адрес: {data.address}", cell),
        para(f"Р/с: {data.rs}", cell),
        para(f"Банк: {data.bank}", cell),
        para(f"К/с: {data.ks}", cell),
        para(f"БИК: {data.bik}", cell),
        para(f"Тел.: {data.phone}", cell),
        para(f"Email: {data.email}", cell),
        Spacer(1, 10),
        para(f"Агент: ________________ / {data.agent_name} /", cell),
        para("М.П. (при наличии)", cell),
    ]
    principal_cell = [
        para("ПРИНЦИПАЛ (Заказчик):", cell),
        para(P["short_caps"], cell),
        para(f"ИНН / КПП: {P['inn']} / {P['kpp']}", cell),
        para(f"ОГРН: {P['ogrn']}", cell),
        para(f"Юр. адрес: {P['address']}", cell),
        para(f"Р/с: {P['rs']}", cell),
        para(f"Банк: {P['bank']}", cell),
        para(f"К/с: {P['ks']}", cell),
        para(f"БИК: {P['bik']}", cell),
        para(f"Тел.: {P['phone']}", cell),
        Spacer(1, 6),
        para(f"Генеральный директор {P['short']}:", cell),
        _sign_block(),
        para(f"________________ / {P['director_short']} /", cell),
    ]
    req = Table([[agent_cell, principal_cell]], colWidths=[257, 258])
    req.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [req, PageBreak()]

    # ------------------------------------------------------------ отчёт агента
    reward, sale = data.reward, data.sale_amount
    story += [
        para("ОТЧЕТ АГЕНТА", title_n),
        para(f"по агентскому договору № {num} от {d_long}", ParagraphStyle("t2", parent=title_n, fontSize=11, leading=14)),
        Spacer(1, 6),
        para(f"Кому: {P['short']}, {P['address']}", small),
        para(f"От кого: {data.agent_name}", small),
        para(f"Адрес: {data.address}&nbsp;&nbsp;&nbsp;&nbsp;Тел.: {data.phone}", small),
        Spacer(1, 6),
    ]
    city_row2 = Table(
        [[para(P["city"]), para(d_long, ParagraphStyle("r2", parent=base, alignment=2))]], colWidths=[257, 258]
    )
    city_row2.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [
        city_row2,
        Spacer(1, 4),
        para(f"Направляю Отчет по агентскому договору № {num} от {d_long} За отчетный период выполнены следующие действия (операции):", just),
        Spacer(1, 4),
    ]
    rep = Table(
        [
            [para("№<br/>п/п", cellb), para("Дата<br/>(период)", cellb), para("Наименование товара / заказа", cellb),
             para("Стоимость<br/>товара, руб.", cellb), para("Кол-во", cellb), para("Сумма реализации,<br/>руб.", cellb),
             para("Вознаграждение<br/>Агента, руб.", cellb)],
            [para("1", cellc), para(f"{data.date:%d.%m.%Y}", cellc), para(data.order_name, cellc),
             para(fmt(sale), cellc), para("1", cellc), para(fmt(sale), cellc), para(fmt(reward), cellc)],
            [para("Итого:", cell), "", "", para(fmt(sale), cellc), para("1", cellc), para(fmt(sale), cellc), para(fmt(reward), cellc)],
        ],
        colWidths=[26, 58, 150, 68, 34, 84, 95],
    )
    rep.setStyle(TableStyle(list(grid.getCommands()) + [head_bg, ("SPAN", (0, 2), (2, 2))]))
    story += [
        rep,
        Spacer(1, 6),
        para("За отчетный период Агент надлежащим образом (качественно и в срок) выполнил свои обязательства по привлечению клиентов.", base),
        para(f"Сумма агентского вознаграждения составляет: {rub_line(reward)}, налогообложение: {data.tax}%.", base),
        para("Настоящий Отчет составлен в двух экземплярах, имеющих равную юридическую силу, по одному для каждой из Сторон. Возражения по Отчету принимаются в течение 5 рабочих дней с момента получения.", just),
        Spacer(1, 14),
    ]
    sig = Table(
        [[
            [para("Отчет сдал (Агент):", cell), para(data.agent_name, cell), para(f"ИНН: {data.inn}", cell), Spacer(1, 26),
             para(f"________________ / {data.agent_name} /", cell), para("М.П.", cell)],
            [para("Отчет принял (Принципал):", cell), para(P["short"], cell), para("Генеральный директор", cell),
             Spacer(1, 4), _sign_block(), para(f"________________ / {P['director_short']} /", cell)],
        ]],
        colWidths=[257, 258],
    )
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [sig, PageBreak()]

    # ------------------------------------------------------------ счёт
    story += [para(f"СЧЕТ НА ОПЛАТУ № {num} от {d_long}", title_n), Spacer(1, 10)]
    bank_tbl = Table(
        [
            [[para("Банк получателя:", small), para(data.bank, small)], [para("БИК", small), para("Сч. №", small)],
             [para(data.bik, small), para(data.ks, small)]],
            [[para(f"ИНН: {data.inn}", small), para("Получатель:", small), para(data.agent_name, small)],
             [para("КПП", small), para("Сч. №", small)], [para("-", small), para(data.rs, small)]],
        ],
        colWidths=[300, 55, 160],
    )
    bank_tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    kpp_ogrn = data.ogrnip
    story += [
        bank_tbl,
        Spacer(1, 6),
        para(f"Поставщик (Исполнитель): {data.agent_name}, ИНН {data.inn}, {kpp_ogrn}, {data.address}, тел.: {data.phone}", small),
        para(f"Покупатель (Заказчик): {P['short_caps']}, ИНН {P['inn']}, КПП {P['kpp']}, {P['address_invoice']}", small),
        para(f"Основание: Агентский договор № {num} от {d_long} (Отчет Агента от {d_long})", small),
        Spacer(1, 6),
    ]
    items = Table(
        [
            [para("№", cellb), para("Товары (работы, услуги)", cellb), para("Кол-во", cellb), para("Ед.", cellb),
             para("Цена, руб.", cellb), para("Сумма, руб.", cellb)],
            [para("1", cellc),
             para(f"Агентское вознаграждение за привлечение клиентов по заказу «{data.order_name}» по Договору № {num}", cell),
             para("1", cellc), para("усл. ед.", cellc), para(fmt(reward), cellc), para(fmt(reward), cellc)],
        ],
        colWidths=[25, 225, 50, 50, 80, 85],
    )
    items.setStyle(TableStyle(list(grid.getCommands()) + [head_bg]))
    story += [
        items,
        Spacer(1, 6),
        para(f"Всего наименований 1, на сумму {fmt(reward)} руб.", base),
        para(f"Итого к оплате: {rub_line(reward)}", base),
        para(f"Налогообложение: {data.tax}%.", base),
        Spacer(1, 16),
        para(f"Руководитель / Предприниматель: ____________________ / {data.agent_name} /", base),
        Spacer(1, 10),
        para(f"Бухгалтер: ____________________ / {data.agent_name} /", base),
        Spacer(1, 10),
        para("М.П. (при наличии)", base),
        PageBreak(),
    ]

    # ------------------------------------------------------------ акт
    story += [
        para(f"АКТ № {num} от {d_long}", title_n),
        para("об оказании агентских услуг", title_n),
        Spacer(1, 8),
        para(
            f"Исполнитель: {data.agent_name}, ИНН: {data.inn}, ОГРНИП / ОГРН: {data.ogrnip}, Адрес: {data.address}, "
            f"Р/с: {data.rs} в {data.bank}, БИК: {data.bik}, К/с: {data.ks}",
            small,
        ),
        para(
            f"Заказчик: {P['short_caps']}, ИНН: {P['inn']}, КПП: {P['kpp']}, Адрес: {P['address_invoice']}, "
            f"Р/с: {P['rs']} в {P['bank_act']}, БИК: {P['bik']}, К/с: {P['ks']}",
            small,
        ),
        para(f"Основание: Договор на оказание агентских услуг № {num} от {d_long} (Отчет Агента от {d_long})", small),
        Spacer(1, 6),
    ]
    act = Table(
        [
            [para("№", cellb), para("Наименование работ, услуг", cellb), para("Кол-во", cellb), para("Ед.", cellb),
             para("Цена, руб.", cellb), para("Сумма, руб.", cellb)],
            [para("1", cellc),
             para(f"Оказание услуг по поиску и привлечению клиентов по Договору № {num}<br/>(проект: {data.order_name})", cell),
             para("1", cellc), para("усл. ед.", cellc), para(fmt(reward), cellc), para(fmt(reward), cellc)],
        ],
        colWidths=[25, 225, 50, 50, 80, 85],
    )
    act.setStyle(TableStyle(list(grid.getCommands()) + [head_bg]))
    story += [
        act,
        Spacer(1, 6),
        para(f"Всего оказано услуг 1, на сумму {fmt(reward)} руб. {int(round((reward - int(reward)) * 100)):02d} коп.", base),
        para(f"Итого: {rub_line(reward, 'рублей {k:02d} копеек')}. Налогообложение: {data.tax}%.", base),
        para("Вышеперечисленные услуги выполнены полностью и в срок. Заказчик претензий по объему, качеству и срокам оказания услуг не имеет.", base),
        Spacer(1, 16),
    ]
    act_sig = Table(
        [[
            [para("ИСПОЛНИТЕЛЬ:", cell), para(data.agent_name, cell), para(f"ИНН: {data.inn}", cell), Spacer(1, 26),
             para(f"________________ / {data.agent_name} /", cell), para("М.П.", cell)],
            [para("ЗАКАЗЧИК:", cell), para(P["short_caps"], cell), para("Генеральный директор", cell),
             Spacer(1, 4), _sign_block(), para(f"________________ / {P['director_short']} /", cell)],
        ]],
        colWidths=[257, 258],
    )
    act_sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether(act_sig))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=32, bottomMargin=32,
        title=f"Агентский договор № {num}", author=data.agent_name,
    )
    doc.build(story)
    buf.seek(0)
    return buf
