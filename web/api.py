"""Функции бота для сайта: те же расчёты и документы, что в Telegram (общий код из utils/ и *_flow.py)."""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import logging
import re

from aiohttp import web

import config
import delivery as dl
import delivery_flow
import kp_flow
import penalty_flow
import pipeline
import refund_flow
from utils import cards, fabrics, pricelist, regions
from utils.agency import AgencyData, build_agency_package
from utils.kp import PAYMENTS, build_kp_pdf, build_kp_preview, prep_photo, rub
from utils.refund import REFUND_COMPANIES, build_refund_branded
from utils.statement import money
from utils.tag import Tag, build_tag_docx, build_tag_pdf, build_tag_preview
from web import notify
from web.store import Store, now

log = logging.getLogger("web.api")
MSK = dt.timezone(dt.timedelta(hours=3))
routes = web.RouteTableDef()
STORE: Store  # задаётся в server.py


def _user(request: web.Request) -> dict:
    return request["user"]


def _uid(request: web.Request) -> str:
    return request["user"]["id"]


def err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


def file_response(data: bytes, filename: str, ctype: str) -> web.Response:
    return web.Response(body=data, content_type=ctype,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _today() -> dt.date:
    return dt.datetime.now(MSK).date()


def _int(value, default=None):
    try:
        return int(round(float(str(value).replace(" ", "").replace("\xa0", "").replace(",", "."))))
    except (TypeError, ValueError):
        return default


def _float(value, default=None):
    try:
        return float(str(value).replace(" ", "").replace("\xa0", "").replace(",", ".").replace("%", ""))
    except (TypeError, ValueError):
        return default


# --- справочники для интерфейса ----------------------------------------------------------------


@routes.get("/api/meta")
async def meta(request: web.Request) -> web.Response:
    card_list = [{"key": k, "short": c["short"]} for k, c in cards.load_cards().items()]
    return web.json_response({
        "payments": PAYMENTS,
        "companies": [{"key": k, "label": c["label"]} for k, c in REFUND_COMPANIES.items()],
        "stages": pipeline.STAGES,
        "statuses": [{"key": k, "label": v} for k, v in pipeline.STATUSES],
        "categories": [{"key": k, "label": v} for k, v in dl.CATEGORIES],
        "difficulty": [{"key": k, "label": v, "rate": dl.MANUAL_BIG_RATES[k]} for k, v in dl.MANUAL_BIG_LABELS.items()],
        "cards": card_list,
        "delivery": {"free_km": dl.MKAD_FREE_KM, "km_price": dl.EXTRA_KM_PRICE, "time_fee": dl.TIME_SLOT_FEE,
                     "carry_free": dl.CARRY_FREE_M},
    })


# --- прайс: поиск позиции ------------------------------------------------------------------------


@routes.get("/api/price/search")
async def price_search(request: web.Request) -> web.Response:
    q = (request.query.get("q") or "").strip()
    if len(q) < 2:
        return web.json_response({"items": []})
    try:
        items = await asyncio.wait_for(pricelist.search(q), timeout=25)
    except (pricelist.PriceError, asyncio.TimeoutError):
        return err("Прайс (Google Таблица) сейчас не отвечает — попробуйте через минуту или заполните вручную.", 503)
    out = []
    for it in items:
        try:
            it = await pricelist.enrich(it)
        except pricelist.PriceError:
            pass
        out.append({
            "title": it.title, "size": kp_flow._dims(it), "price_from": int(round(it.price_from)) if it.price_from else 0,
            "article": it.article or "", "source": it.source,
            "dims": list(it.dims) if it.dims else None, "size_raw": it.size_raw or "",
        })
    return web.json_response({"items": out})


# --- фото ------------------------------------------------------------------------------------------


@routes.post("/api/photo")
async def photo_upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    part = await reader.next()
    if part is None:
        return err("Файл не получен")
    raw = await part.read(decode=False)
    if len(raw) > 15 * 1024 * 1024:
        return err("Файл больше 15 МБ")
    jpeg = await asyncio.to_thread(prep_photo, bytes(raw))
    if not jpeg:
        return err("Не получилось прочитать картинку — нужен JPG или PNG.")
    key = await STORE.put_photo(jpeg)
    return web.json_response({"id": key})


@routes.get("/api/photo")
async def photo_get(request: web.Request) -> web.Response:
    data = await STORE.get_photo(request.query.get("id"))
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# --- КП ------------------------------------------------------------------------------------------


def _kp_clean(raw: dict) -> dict:
    """Проверка и нормализация КП из браузера (никаких лишних полей)."""
    def item(it: dict, alt: bool = False) -> dict:
        out = {
            "title": str(it.get("title") or "").strip()[:200],
            "size": str(it.get("size") or "").strip()[:100],
            "material": str(it.get("material") or "").strip()[:500],
            "link": str(it.get("link") or "").strip()[:500],
            "unit_price": max(_int(it.get("unit_price"), 0), 0),
            "photo_id": it.get("photo_id") or None,
            "default_price": _int(it.get("default_price"), 0) or 0,
        }
        if out["link"] and not re.match(r"^https?://", out["link"]):
            out["link"] = "https://" + out["link"]
        if not alt:
            out["qty"] = max(_int(it.get("qty"), 1) or 1, 1)
            disc = _float(it.get("discount"), 0) or 0
            out["discount"] = min(max(disc, 0), 99)
            a = it.get("alt")
            out["alt"] = item(a, True) if isinstance(a, dict) and (a.get("title") or "").strip() else None
        return out

    pays = raw.get("payments") or []
    return {
        "kind": "vis" if raw.get("kind") == "vis" else "std",
        "customer": str(raw.get("customer") or "").strip()[:200],
        "customer_phone": str(raw.get("customer_phone") or "").strip()[:40],
        "items": [item(it) for it in (raw.get("items") or [])[:40] if isinstance(it, dict)],
        "production": str(raw.get("production") or "55 рабочих дней")[:100],
        "services": str(raw.get("services") or "По согласованию")[:200],
        "valid_days": min(max(_int(raw.get("valid_days"), 7) or 7, 1), 90),
        "payments": [bool(pays[i]) if i < len(pays) else True for i in range(len(PAYMENTS))],
    }


async def _kp_build(user: dict, w: dict):
    """Веб-КП -> объект КП бота (с фото)."""
    k = kp_flow._kp_new({"name": user["name"], "phone": user["phone"], "telegram": user.get("telegram", ""),
                         "showroom": user.get("showroom", "")})
    for f in ("kind", "customer", "customer_phone", "production", "services", "valid_days", "payments"):
        k[f] = w[f]
    items = []
    for it in w["items"]:
        item = {**kp_flow._item_new(), **{f: it[f] for f in ("title", "qty", "size", "material", "link",
                                                              "unit_price", "discount")}}
        item["photo"] = await STORE.get_photo(it.get("photo_id"))
        if it.get("alt"):
            a = it["alt"]
            item["alt"] = {"title": a["title"], "size": a["size"], "material": a["material"], "link": a["link"],
                           "unit_price": a["unit_price"], "photo": await STORE.get_photo(a.get("photo_id")),
                           "default": {}}
        items.append(item)
    k["items"] = items
    return kp_flow._build(k), k


def _kp_problem(w: dict) -> str | None:
    if not w["customer"]:
        return "Укажите заказчика."
    if not w["customer_phone"]:
        return "Укажите телефон заказчика."
    if not w["items"]:
        return "Добавьте хотя бы одну позицию."
    for n, it in enumerate(w["items"], 1):
        if not it["title"]:
            return f"У позиции {n} нет названия."
    return None


@routes.get("/api/kp/draft")
async def kp_draft_get(request: web.Request) -> web.Response:
    return web.json_response(STORE.data["drafts"].get(_uid(request)) or {})


@routes.put("/api/kp/draft")
async def kp_draft_put(request: web.Request) -> web.Response:
    body = await request.json()
    STORE.data["drafts"][_uid(request)] = {"ts": now(), "kp": _kp_clean(body.get("kp") or {}),
                                          "source": body.get("source")}
    STORE.changed(5)
    return web.json_response({"ok": True, "ts": now()})


@routes.delete("/api/kp/draft")
async def kp_draft_del(request: web.Request) -> web.Response:
    if STORE.data["drafts"].pop(_uid(request), None) is not None:
        STORE.changed()
    return web.json_response({"ok": True})


@routes.post("/api/kp/preview")
async def kp_preview(request: web.Request) -> web.Response:
    w = _kp_clean((await request.json()).get("kp") or {})
    if not w["items"]:
        return err("Добавьте хотя бы одну позицию.")
    kp, _ = await _kp_build(_user(request), w)
    png = await asyncio.to_thread(build_kp_preview, kp, 0, 80)
    return web.Response(body=png, content_type="image/png")


@routes.post("/api/kp/pdf")
async def kp_pdf(request: web.Request) -> web.Response:
    user = _user(request)
    w = _kp_clean((await request.json()).get("kp") or {})
    problem = _kp_problem(w)
    if problem:
        return err(problem)
    kp, _ = await _kp_build(user, w)
    pdf = await asyncio.to_thread(build_kp_pdf, kp)
    filename = f"KP_Creatica_{kp.number}.pdf"
    hist = STORE.data["kp"].setdefault(user["id"], [])
    hist.insert(0, {"ts": now(), "number": kp.number, "customer": kp.customer, "sum": kp.total(), "kp": w})
    del hist[50:]
    STORE.data["drafts"].pop(user["id"], None)
    STORE.changed()
    notify.document(pdf, filename, (
        f"📋 КП №{kp.number}\n{notify.who(user)}\n"
        f"Заказчик: {html.escape(kp.customer)}"
        + (f" · {html.escape(kp.client_phone)}" if kp.client_phone else "") + "\n"
        f"Позиций: {len(kp.items)} · Итого: {rub(kp.total())}"))
    return file_response(pdf, filename, "application/pdf")


@routes.get("/api/kp/history")
async def kp_history(request: web.Request) -> web.Response:
    hist = STORE.data["kp"].get(_uid(request)) or []
    return web.json_response({"items": [{"i": i, "ts": h["ts"], "number": h["number"], "customer": h["customer"],
                                         "sum": h["sum"], "closed": h.get("closed")} for i, h in enumerate(hist)]})


@routes.get("/api/kp/history/{i}")
async def kp_history_get(request: web.Request) -> web.Response:
    hist = STORE.data["kp"].get(_uid(request)) or []
    i = _int(request.match_info["i"], -1)
    if not 0 <= i < len(hist):
        return err("КП не найдено", 404)
    return web.json_response({"kp": hist[i]["kp"]})


@routes.post("/api/kp/history/{i}/close")
async def kp_history_close(request: web.Request) -> web.Response:
    """Клиент купил (won) или отказался (lost + причина) — как ✅/❌ в «Мои КП» бота."""
    user = _user(request)
    hist = STORE.data["kp"].get(user["id"]) or []
    i = _int(request.match_info["i"], -1)
    if not 0 <= i < len(hist):
        return err("КП не найдено", 404)
    body = await request.json()
    outcome = "won" if body.get("outcome") == "won" else "lost"
    reason = str(body.get("reason") or "").strip()[:500]
    if outcome == "lost" and not reason:
        return err("Напишите причину отказа.")
    h = hist[i]
    h["closed"] = {"outcome": outcome, "reason": reason, "ts": now()}
    STORE.changed()
    head = "✅ Клиент купил по КП" if outcome == "won" else "❌ Клиент отказался от КП"
    notify.text(f"{head} №{h['number']}\n{notify.who(user)}\nЗаказчик: {html.escape(h['customer'])} · {rub(h['sum'])}"
                + (f"\nПричина: {html.escape(reason)}" if reason else ""))
    return web.json_response({"ok": True})


# --- доставка --------------------------------------------------------------------------------------


@routes.get("/api/delivery/search")
async def delivery_search(request: web.Request) -> web.Response:
    q = (request.query.get("q") or "").strip()
    if len(q) < 2:
        return web.json_response({"items": []})
    try:
        items = await asyncio.wait_for(pricelist.search_catalog(q), timeout=25)
    except (pricelist.PriceError, asyncio.TimeoutError):
        return err("Прайс сейчас не отвечает — выберите тип изделия вручную.", 503)
    out = []
    for it in items:
        category = delivery_flow.SOURCE_CATEGORY.get(it.source, "big")
        dims = list(it.dims) if it.dims else None
        out.append({
            "name": it.title, "category": category, "asm": int(it.assembly or 0),
            "size": "×".join(str(v) for v in dims) if dims else (it.size_raw or ""),
            "parts": it.parts or delivery_flow._parts_from_title(it.title) or 1,
            "long_case": bool(dims) and category == "case" and max(dims[:2] or [0]) > 1000,
            "dims_known": bool(dims),
        })
    return web.json_response({"items": out})


@routes.post("/api/delivery/quote")
async def delivery_quote(request: web.Request) -> web.Response:
    user = _user(request)
    b = await request.json()
    city = "Санкт-Петербургу" if b.get("city") == "spb" else "Москве"
    with_asm = bool(b.get("assembly"))
    items = []
    for it in (b.get("items") or [])[:30]:
        category = it.get("category") if it.get("category") in dl.CATEGORY_LABEL else "big"
        items.append(dl.Item(category=category, name=str(it.get("name") or "")[:120],
                             qty=max(_int(it.get("qty"), 1) or 1, 1), long_case=bool(it.get("long_case")),
                             assembly_price=(_int(it.get("asm"), 0) or 0) if with_asm else 0,
                             parts=max(_int(it.get("parts"), 1) or 1, 1)))
    if not items:
        return err("Добавьте хотя бы одну позицию.")
    lift = b.get("lift_mode") if b.get("lift_mode") in ("none", "elevator", "manual") else "none"
    floors = max(_int(b.get("floors"), 0) or 0, 0)
    if lift == "manual" and not floors:
        return err("Укажите этаж для ручного подъёма.")
    q = dl.Quote(items=items, distance_km=max(_int(b.get("distance_km"), 0) or 0, 0), lift_mode=lift,
                 floors=floors, lift_difficulty=b.get("lift_difficulty") if b.get("lift_difficulty") in dl.MANUAL_BIG_RATES
                 else "normal", time_slot=bool(b.get("time_slot")),
                 carry_extra_m=max(_int(b.get("carry_extra_m"), 0) or 0, 0))
    total = q.compute()
    lines = [{"label": label, "price": price} for label, price in q.lines]
    text = "\n".join([f"🚚 <b>Расчёт доставки — {city}</b>", ""] +
                     [f"{html.escape(x['label'])} — <b>{rub(x['price'])}</b>" if x["price"] else html.escape(x["label"])
                      for x in lines] + ["", f"<b>Итого: {rub(total)}</b>"])
    notify.text(f"{text}\n\n{notify.who(user)}")
    return web.json_response({"lines": lines, "total": total, "city": city})


def _entry(e: dict) -> dict:
    return {"city": e["city"], "from": e["from"], "tk": e["tk"], "ati": e["ati"],
            "asked": e.get("asked"), "km": e.get("km")}


@routes.get("/api/delivery/region")
async def delivery_region(request: web.Request) -> web.Response:
    q = (request.query.get("city") or "").strip()
    if len(q) < 2:
        return err("Напишите город.")
    try:
        matches = await regions.find(q)
    except regions.RegionError as exc:
        return err(f"Таблица регионов не открылась: {exc}", 503)
    if matches:
        return web.json_response({"type": "found", "entries": [_entry(m) for m in matches[:15]]})
    home = await regions.home_city(q)
    if home:
        return web.json_response({"type": "home", "city": home[0], "km": home[1],
                                  "code": "msk" if home[0] == "Москва" else "spb"})
    near = await regions.nearest(q)
    if near:
        entry, km = near
        return web.json_response({"type": "nearest", "entries": [_entry({**entry, "asked": q, "km": km})]})
    return web.json_response({"type": "none"})


@routes.post("/api/delivery/region")
async def delivery_region_result(request: web.Request) -> web.Response:
    user = _user(request)
    b = await request.json()
    e = b.get("entry") or {}
    # всё, что пришло из браузера, экранируем: текст уходит и на страницу, и в Telegram как HTML
    entry = {k: html.escape(str(e.get(k) or ""))[:2000] for k in ("city", "from", "tk", "ati")}
    if e.get("asked"):
        entry["asked"], entry["km"] = html.escape(str(e["asked"])[:80]), _int(e.get("km"), 0)
    items = [(html.escape(n[:120]), q) for n, q in regions.parse_items(str(b.get("items") or ""))[:30]]
    if not entry["city"] or not items:
        return err("Укажите позиции, например: Диван 1 шт, Кресло 2 шт")
    text = delivery_flow._region_result(entry, items)
    notify.text(f"🚚 <b>Доставка в регион</b>\n{notify.who(user)}\n\n{text}")
    return web.json_response({"html": text})


# --- документы: возврат и пени (поля — из тех же шагов, что в боте) ---------------------------------


def _schema(steps, required: set[str]) -> list[dict]:
    out = []
    for s in steps:
        when = {"key": "method", "eq": "card"} if s.when is refund_flow.CARD else None
        out.append({"key": s.key, "label": s.prompt, "kind": s.kind,
                    "buttons": [{"label": lbl, "value": val} for lbl, val in s.buttons],
                    "when": when, "required": s.key in required})
    return out


def _doc_steps(doc: str, kind: str):
    if doc == "refund":
        return refund_flow._steps(kind), set()
    return penalty_flow._steps(kind), penalty_flow.REQUIRED


@routes.get("/api/docs/schema/{doc}")
async def docs_schema(request: web.Request) -> web.Response:
    doc, kind = request.match_info["doc"], request.query.get("kind", "fiz")
    if doc not in ("refund", "penalty") or kind not in ("fiz", "yur"):
        raise web.HTTPNotFound()
    steps, required = _doc_steps(doc, kind)
    return web.json_response({"fields": _schema(steps, required)})


def _collect(doc: str, kind: str, raw: dict) -> tuple[dict, dict]:
    """Проверяет ответы так же, как бот. -> (ответы, ошибки по полям)."""
    steps, required = _doc_steps(doc, kind)
    answers: dict = {}
    errors: dict = {}
    for s in steps:
        if s.when and not s.when(answers):
            continue
        text = str(raw.get(s.key) or "").strip()
        if s.kind == "choice":
            values = {v for _, v in s.buttons}
            if text in values:
                answers[s.key] = text
            elif s.key in required:
                errors[s.key] = "Выберите вариант."
            continue
        if not text:
            if s.key in required:
                errors[s.key] = "Обязательное поле."
            continue
        if text in ("-", "Нет", "нет"):
            answers[s.key] = "-"
            continue
        value, problem = refund_flow._parse(s, text)
        if problem:
            errors[s.key] = problem
        else:
            answers[s.key] = value
    if raw.get("not_delivered"):
        answers["not_delivered"] = True
    return answers, errors


@routes.post("/api/docs/penalty/calc")
async def penalty_calc(request: web.Request) -> web.Response:
    raw = await request.json()
    a: dict = {}
    for key in ("ready_date", "fact_date"):
        try:
            a[key] = dt.datetime.strptime(str(raw.get(key) or "").strip(), "%d.%m.%Y").date()
        except ValueError:
            pass
    a["contract_sum"] = _float(raw.get("contract_sum"))
    days, calc = penalty_flow.penalty_calc(a)
    deadline = penalty_flow.delivery_deadline(a)
    return web.json_response({"days": days, "amount": calc,
                              "deadline": f"{deadline:%d.%m.%Y}" if deadline else None})


@routes.post("/api/docs/{doc}")
async def docs_make(request: web.Request) -> web.Response:
    user = _user(request)
    doc = request.match_info["doc"]
    if doc not in ("refund", "penalty"):
        raise web.HTTPNotFound()
    b = await request.json()
    company, kind = b.get("company"), b.get("kind")
    if company not in REFUND_COMPANIES or kind not in ("fiz", "yur"):
        return err("Выберите организацию и тип покупателя.")
    a, errors = _collect(doc, kind, b.get("answers") or {})
    if errors:
        return web.json_response({"errors": errors}, status=422)
    for key in ("card", "pay_ls"):
        if a.get(key) == "-":
            a[key] = ""
    label = REFUND_COMPANIES[company]["label"]
    kind_ru = "физлицо" if kind == "fiz" else "юрлицо"
    stamp = a.get("doc_date") or _today()
    if doc == "refund":
        if a.get("passport"):
            a["passport_series"], a["passport_number"] = a["passport"][:4], a["passport"][4:]
        pdf = await asyncio.to_thread(build_refund_branded, company, kind, a)
        name = f"Zayavlenie_vozvrat_{company}_{'fizlico' if kind == 'fiz' else 'yurlico'}_{stamp:%Y%m%d}.pdf"
        caption = f"📝 Заявление на возврат: {label}, {kind_ru}\n{notify.who(user)}"
    else:
        from utils.statement import build_statement_pdf
        pdf = await asyncio.to_thread(build_statement_pdf, penalty_flow._statement(company, kind, a))
        name = f"Zayavlenie_peni_{company}_{stamp:%Y%m%d}.pdf"
        caption = f"⚖ Заявление на выплату пени: {label}, {kind_ru}, {money(a.get('amount') or 0)}\n{notify.who(user)}"
    notify.document(pdf, name, caption)
    return file_response(pdf, name, "application/pdf")


# --- выплата дизайнеру -----------------------------------------------------------------------------

PAYOUT_FIELDS = [
    ("agent_name", "ФИО дизайнера (полностью)", "text", True),
    ("inn", "ИНН дизайнера (10 или 12 цифр)", "inn", True),
    ("ogrnip", "ОГРНИП / ОГРН (если нет — оставьте пустым)", "text", False),
    ("address", "Юридический адрес (например: г. Москва)", "text", False),
    ("rs", "Расчётный счёт (20 цифр)", "d20", False),
    ("bank", "Название банка", "text", False),
    ("ks", "Корреспондентский счёт (20 цифр)", "d20", False),
    ("bik", "БИК банка (9 цифр)", "d9", False),
    ("phone", "Телефон дизайнера", "text", False),
    ("email", "Email дизайнера", "email", False),
    ("order_name", "Название заказа / клиента", "text", False),
    ("sale_amount", "Сумма заказа, руб.", "amount", True),
    ("reward", "Вознаграждение дизайнера, руб.", "amount", True),
    ("tax", "Налогообложение, % (0–100; пусто — 0)", "tax", False),
    ("date", "Дата документов (ДД.ММ.ГГГГ; пусто — сегодня)", "date", False),
]


@routes.get("/api/payout/schema")
async def payout_schema(request: web.Request) -> web.Response:
    return web.json_response({"fields": [{"key": k, "label": lbl, "kind": kind, "required": req}
                                         for k, lbl, kind, req in PAYOUT_FIELDS]})


@routes.post("/api/payout/legal")
async def payout_legal(request: web.Request) -> web.Response:
    user = _user(request)
    raw = (await request.json()).get("answers") or {}
    d: dict = {}
    errors: dict = {}
    for key, _, kind, req in PAYOUT_FIELDS:
        text = str(raw.get(key) or "").strip()
        if not text:
            if req:
                errors[key] = "Обязательное поле."
            d[key] = {"tax": "0", "date": _today()}.get(key, "-")
            continue
        digits = re.sub(r"\D", "", text)
        if kind == "inn":
            if len(digits) not in (10, 12):
                errors[key] = "ИНН — 10 или 12 цифр."
            d[key] = digits
        elif kind in ("d20", "d9"):
            need = 20 if kind == "d20" else 9
            if len(digits) != need:
                errors[key] = f"Нужно ровно {need} цифр."
            d[key] = digits
        elif kind == "email":
            if "@" not in text:
                errors[key] = "Похоже, это не email."
            d[key] = text
        elif kind == "amount":
            v = _float(text)
            if not v or v <= 0:
                errors[key] = "Введите сумму числом."
            d[key] = v
        elif kind == "tax":
            v = _float(text)
            if v is None or not 0 <= v <= 100:
                errors[key] = "Процент от 0 до 100."
            d[key] = f"{v:g}" if v is not None else "0"
        elif kind == "date":
            try:
                d[key] = dt.datetime.strptime(text, "%d.%m.%Y").date()
            except ValueError:
                errors[key] = "Формат ДД.ММ.ГГГГ."
        else:
            d[key] = text
    if errors:
        return web.json_response({"errors": errors}, status=422)
    data = AgencyData(**d)
    pdf = (await asyncio.to_thread(build_agency_package, data)).read()
    name = f"Agency_Package_{data.inn}.pdf"
    notify.document(pdf, name, (
        f"💵 Выплата дизайнеру — по счёту, договор №{data.number}\n{notify.who(user)}\n"
        f"Исполнитель: {html.escape(data.agent_name)} (ИНН {data.inn})\nЗаказ: {html.escape(data.order_name)}\n"
        f"Вознаграждение: {data.reward}"))
    return file_response(pdf, name, "application/pdf")


@routes.post("/api/payout/person")
async def payout_person(request: web.Request) -> web.Response:
    user = _user(request)
    b = await request.json()
    amount, pct = _float(b.get("amount")), _float(b.get("discount"))
    if not amount or amount <= 0:
        return web.json_response({"errors": {"amount": "Введите сумму заказа."}}, status=422)
    if pct is None or not 0 < pct <= 100:
        return web.json_response({"errors": {"discount": "Процент от 1 до 100."}}, status=422)
    reward = amount * pct / 100
    tax = reward * 0.13
    res = {"reward": round(reward, 2), "tax": round(tax, 2), "payout": round(reward - tax, 2)}
    esc = html.escape
    notify.text(
        f"💵 <b>Выплата дизайнеру — перевод на карту</b>\n{notify.who(user)}\n\n"
        f"Заказ / клиент: {esc(str(b.get('order_name') or '-'))}\nДизайнер: {esc(str(b.get('designer') or '-'))}\n"
        f"Банк получателя: {esc(str(b.get('bank') or '-'))}\nСумма заказа: {money(amount)}\n\n"
        f"Процент дизайнера: {pct:g} %\nВознаграждение: {money(reward)}\nНалог за вывод (13%): −{money(tax)}\n"
        f"<b>К переводу на карту: {money(reward - tax)}</b>")
    return web.json_response(res)


# --- карточки организаций ------------------------------------------------------------------------


@routes.get("/api/cards/{key}")
async def card_get(request: web.Request) -> web.Response:
    card = cards.load_cards().get(request.match_info["key"])
    if not card:
        raise web.HTTPNotFound()
    if all(value == "—" for _, value in card["rows"]):
        return err("Реквизиты ещё не загружены на сервер — обратитесь к владельцу.", 503)
    if request.query.get("format") == "docx":
        doc = await asyncio.to_thread(cards.build_card_docx, card)
        notify.text(f"🏢 Скачана карточка организации: {html.escape(card['short'])}\n{notify.who(_user(request))}")
        name = "Karta_" + re.sub(r"\W+", "_", card["short"]).strip("_") + ".docx"
        return file_response(doc, name, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    return web.json_response({"short": card["short"], "full": card["full"], "rows": card["rows"]})


# --- поиск ткани -----------------------------------------------------------------------------------


async def _fabrics():
    return await fabrics.load(config.PRICE_CSV_URL, config.STOCK_CSV_URL)


@routes.get("/api/fabrics/suppliers")
async def fabric_suppliers(request: web.Request) -> web.Response:
    try:
        price, _ = await _fabrics()
    except Exception as exc:  # noqa: BLE001
        return err(f"Таблица тканей не открылась: {exc}", 503)
    return web.json_response({"suppliers": fabrics.suppliers(price)})


@routes.get("/api/fabrics/search")
async def fabric_search(request: web.Request) -> web.Response:
    supplier, q = request.query.get("supplier", ""), (request.query.get("q") or "").strip()
    if not q:
        return err("Напишите название ткани.")
    try:
        price, stock = await _fabrics()
    except Exception as exc:  # noqa: BLE001
        return err(f"Таблица тканей не открылась: {exc}", 503)
    has_stock = any(s.supplier == supplier for s in stock)
    found = fabrics.find_fabrics(price, supplier, q)
    if not found:
        probe = fabrics.stock_only(stock, supplier, q)
        found = [probe] if probe else []
    if not found:
        return web.json_response({"items": [], "similar": fabrics.similar(price, supplier, q)})
    blocks = [fabrics.format_fabric(f, fabrics.stock_for(f, stock, price), has_stock) for f in found[:8]]
    return web.json_response({"items": blocks, "total": len(found)})


# --- ценник ----------------------------------------------------------------------------------------


def _tag(b: dict) -> Tag:
    return Tag(title=str(b.get("title") or "")[:120], length=str(b.get("length") or ""), depth=str(b.get("depth") or ""),
               height=str(b.get("height") or ""), frame=str(b.get("frame") or "")[:120],
               filler=str(b.get("filler") or "")[:120], article=str(b.get("article") or "")[:40],
               price=_int(b.get("price_from")), sample_price=_int(b.get("price")))


@routes.post("/api/tag/{fmt}")
async def tag_make(request: web.Request) -> web.Response:
    fmt = request.match_info["fmt"]
    b = await request.json()
    tag = _tag(b)
    if not tag.title:
        return err("Укажите название.")
    if fmt == "preview":
        return web.Response(body=await asyncio.to_thread(build_tag_preview, tag), content_type="image/png")
    user = _user(request)
    safe = re.sub(r"\W+", "_", tag.title).strip("_")[:40] or "tag"
    if fmt == "pdf":
        data, name, ctype = await asyncio.to_thread(build_tag_pdf, tag), f"Cennik_{safe}.pdf", "application/pdf"
    elif fmt == "docx":
        data, name = await asyncio.to_thread(build_tag_docx, tag), f"Cennik_{safe}.docx"
        ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        raise web.HTTPNotFound()
    notify.document(data, name, f"🏷 Ценник: {html.escape(tag.title)}\n{notify.who(user)}")
    return file_response(data, name, ctype)


# --- планируемые продажи ---------------------------------------------------------------------------

DEAL_FIELDS = ("client", "product", "amount", "arrived", "stage", "status", "planned_date", "blocker", "lost_reason")


def _deal_text(d: dict) -> str:
    esc = html.escape
    status = pipeline.STATUS_LABEL.get(d.get("status"), "")
    lines = [f"<b>{esc(d.get('client') or '—')}</b> — {rub(d.get('amount') or 0)} · {status}"]
    if d.get("product"):
        lines.append(f"Покупает: {esc(d['product'])}")
    if d.get("stage"):
        lines.append(f"Стадия: {esc(d['stage'])}")
    if d.get("planned_date"):
        lines.append(f"План продажи: {esc(d['planned_date'])}")
    if d.get("blocker"):
        lines.append(f"Что мешает: {esc(d['blocker'])}")
    if d.get("lost_reason"):
        lines.append(f"Причина срыва: {esc(d['lost_reason'])}")
    return "\n".join(lines)


def _deals_notify(user: dict, head: str, deal: dict) -> None:
    items = STORE.data["deals"].get(user["id"]) or []
    active = [d for d in items if d.get("status") not in ("done", "lost")]
    notify.text(f"📈 <b>Планируемые продажи</b> — {head}\n{notify.who(user)}\n\n{_deal_text(deal)}\n\n"
                f"В работе: {len(active)} сделок на {rub(pipeline.total(active))}")


def _deal_clean(b: dict, base: dict) -> dict:
    d = dict(base)
    for f in DEAL_FIELDS:
        if f in b:
            d[f] = _int(b[f]) if f == "amount" else str(b[f] or "").strip()[:500]
    if d.get("status") not in pipeline.STATUS_LABEL:
        d["status"] = "green"
    return d


@routes.get("/api/deals")
async def deals_list(request: web.Request) -> web.Response:
    return web.json_response({"items": STORE.data["deals"].get(_uid(request)) or []})


@routes.post("/api/deals")
async def deals_add(request: web.Request) -> web.Response:
    user = _user(request)
    d = _deal_clean(await request.json(), pipeline.new_deal())
    if not d["client"] or not d["product"]:
        return err("Укажите клиента и что он покупает.")
    items = STORE.data["deals"].setdefault(user["id"], [])
    d["id"] = max((x["id"] for x in items), default=0) + 1
    items.append(d)
    STORE.changed()
    _deals_notify(user, "новая сделка", d)
    return web.json_response(d)


@routes.put("/api/deals/{id}")
async def deals_update(request: web.Request) -> web.Response:
    user = _user(request)
    items = STORE.data["deals"].get(user["id"]) or []
    deal_id = _int(request.match_info["id"], -1)
    idx = next((i for i, x in enumerate(items) if x["id"] == deal_id), None)
    if idx is None:
        return err("Сделка не найдена", 404)
    items[idx] = _deal_clean(await request.json(), items[idx])
    STORE.changed()
    _deals_notify(user, "изменена сделка", items[idx])
    return web.json_response(items[idx])


@routes.delete("/api/deals/{id}")
async def deals_delete(request: web.Request) -> web.Response:
    user = _user(request)
    items = STORE.data["deals"].get(user["id"]) or []
    deal_id = _int(request.match_info["id"], -1)
    deal = next((x for x in items if x["id"] == deal_id), None)
    if deal is None:
        return err("Сделка не найдена", 404)
    items.remove(deal)
    STORE.changed()
    _deals_notify(user, "удалена сделка", deal)
    return web.json_response({"ok": True})
