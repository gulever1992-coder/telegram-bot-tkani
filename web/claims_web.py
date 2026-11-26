"""Рекламации на сайте: заявка менеджера с сайта, статусы, связь с ботом (админ-бот на GitHub Actions).

Заявки с сайта хранятся в хранилище сайта. Рекламационист нажимает кнопки в админ-боте — бот вызывает сайт
(/internal/…, подпись общего секрета), сайт меняет статус; менеджер видит его на странице «Рекламации».
"""

from __future__ import annotations

import html
import json
import logging
import time

from aiohttp import web

import claims
import config
from web import auth
from web.store import Store

log = logging.getLogger("web.claims")
routes = web.RouteTableDef()
STORE: Store
ADMIN_BOT = None  # aiogram.Bot админ-бота (только отправка), задаётся в server.py


def _claims() -> dict:
    return STORE.data.setdefault("claims", {})


def public(c: dict, full: bool = False) -> dict:
    """Что отдаём в браузер менеджеру (без служебных id сообщений)."""
    out = {k: c.get(k) for k in ("id", "ts", "status", "order", "client", "client_phone", "item", "problem", "photos_n",
                                 "log", "comments")}
    out["number"] = claims.number(c)
    out["kind"] = claims.kind_of(c)
    return out


def _kind(x) -> str:
    return x if x in claims.KINDS else "claim"


def _users(kind: str) -> list[dict]:
    return STORE.data.get(claims.KINDS[kind]["users"]) or []


def _norm_users(raw) -> list[dict]:
    return [{"id": int(u["id"]), "name": str(u.get("name") or "")[:80]} for u in raw or []]


# --- менеджер на сайте ---------------------------------------------------------------------------------


@routes.get("/api/claims")
async def my_claims(request: web.Request) -> web.Response:
    uid = request["user"]["id"]
    kind = _kind(request.query.get("kind"))
    mine = [c for c in _claims().values() if (c.get("by") or {}).get("web") == uid and claims.kind_of(c) == kind]
    mine.sort(key=lambda c: -c["ts"])
    return web.json_response({"items": [public(c) for c in mine], "recipients": [u["name"] for u in _users(kind)]})


@routes.post("/api/claims")
async def create_claim(request: web.Request) -> web.Response:
    user = request["user"]
    b = await request.json()
    kind = _kind(b.get("kind"))
    errors = {}
    f = {k: " ".join(str(b.get(k) or "").split())[:1500] for k in ("order", "client", "item")}
    f["problem"] = str(b.get("problem") or "").strip()[:1500]
    for key, label in (("order", "Укажите договор или заказ."), ("client", "Укажите клиента."),
                       ("item", "Укажите изделие." if kind == "claim" else "Укажите адрес доставки."),
                       ("problem", "Опишите проблему." if kind == "claim" else "Опишите запрос.")):
        if len(f[key]) < 2:
            errors[key] = label
    phone = auth.norm_phone(str(b.get("client_phone") or ""))
    if not phone:
        errors["client_phone"] = "Телефон клиента: 10 цифр после +7."
    if errors:
        return web.json_response({"errors": errors}, status=422)
    photos = [str(x) for x in (b.get("photos") or [])[:8] if x]
    STORE.data["claim_seq"] = int(STORE.data.get("claim_seq", 0)) + 1
    c = {
        "id": f"w{STORE.data['claim_seq']}", "kind": kind, "ts": int(time.time()), "status": "new", "log": [], "comments": [],
        "by": {"web": user["id"], "name": user["name"], "showroom": user.get("showroom", ""), "phone": user["phone"]},
        "order": f["order"], "client": f["client"], "client_phone": auth.pretty_phone(phone), "item": f["item"],
        "problem": f["problem"], "photos_n": len(photos),
    }
    _claims()[c["id"]] = c
    if ADMIN_BOT is not None:
        chats = [u["id"] for u in _users(kind)] + list(config.ADMIN_CHAT_IDS)
        await claims.deliver(ADMIN_BOT, c, chats, [p for p in photos if not p.startswith("local:")])
    await STORE.flush()
    return web.json_response({"claim": public(c), "recipients": [u["name"] for u in _users(kind)]})


# --- вызовы от бота (подпись, без сессии) ----------------------------------------------------------------


async def _signed(request: web.Request) -> dict | None:
    body = await request.read()
    if not config.ADMIN_BOT_TOKEN or not claims.check(config.ADMIN_BOT_TOKEN, request.headers.get("X-Ts", ""), body,
                                                      request.headers.get("X-Sig", "")):
        return None
    try:
        return json.loads(body or b"{}")
    except json.JSONDecodeError:
        return {}


def _forbidden() -> web.Response:
    return web.json_response({"error": "bad signature"}, status=403)


@routes.post("/internal/recipients")
async def int_recipients(request: web.Request) -> web.Response:
    data = await _signed(request)
    if data is None:
        return _forbidden()
    STORE.data["rec_users"] = _norm_users(data.get("users"))
    if "log_users" in data:
        STORE.data["log_users"] = _norm_users(data.get("log_users"))
    STORE.changed()
    return web.json_response({"ok": True})


@routes.post("/internal/claim")
async def int_claim(request: web.Request) -> web.Response:
    data = await _signed(request)
    if data is None:
        return _forbidden()
    return web.json_response({"claim": _claims().get(str(data.get("id")))})


@routes.post("/internal/claims")
async def int_claims(request: web.Request) -> web.Response:
    data = await _signed(request)
    if data is None:
        return _forbidden()
    return web.json_response({"claims": list(_claims().values())})


@routes.post("/internal/status")
async def int_status(request: web.Request) -> web.Response:
    data = await _signed(request)
    if data is None:
        return _forbidden()
    c = _claims().get(str(data.get("id")))
    if c is None:
        return web.json_response({"claim": None, "changed": False})
    changed = claims.apply_status(c, str(data.get("status")), str(data.get("by") or "")[:80])
    if changed:
        await STORE.flush()
    return web.json_response({"claim": c, "changed": changed})


@routes.post("/internal/comment")
async def int_comment(request: web.Request) -> web.Response:
    data = await _signed(request)
    if data is None:
        return _forbidden()
    c = _claims().get(str(data.get("id")))
    if c is None:
        return web.json_response({"claim": None})
    claims.add_comment(c, str(data.get("by") or "")[:80], str(data.get("text") or ""))
    await STORE.flush()
    return web.json_response({"claim": c})
