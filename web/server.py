"""Сайт Creatica для менеджеров: те же функции, что в Telegram-боте, но в браузере.

Запуск:  python -m web.server   (порт — из PORT, по умолчанию 8080)

Переменные окружения:
  PRIVATE_BUNDLE_B64  — тот же архив с реквизитами, печатью и токеном админ-бота, что у бота
  WEB_STORE_CHAT      — (необязательно) другой чат для данных сайта; по умолчанию — чат владельца с админ-ботом
  WEB_BASE_URL        — адрес сайта для ссылок «Пустить / Отклонить» (на Render берётся сам)
  WEB_SECRET          — ключ подписи cookie (по умолчанию выводится из токена админ-бота)
"""

from __future__ import annotations

import base64
import io
import logging
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _unpack_bundle() -> None:
    """Приватные файлы (реквизиты, печать, токен админ-бота) — из переменной окружения, как в облаке у бота."""
    raw = os.getenv("PRIVATE_BUNDLE_B64", "").strip()
    if not raw or os.path.exists(os.path.join(ROOT, "principal.py")):
        return
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(raw))) as z:
        z.extractall(ROOT)


_unpack_bundle()

import asyncio  # noqa: E402
import html  # noqa: E402
import json  # noqa: E402

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiohttp import web  # noqa: E402

import config  # noqa: E402
import profiles  # noqa: E402
from web import api, auth, notify  # noqa: E402
from web.store import Store, now  # noqa: E402

log = logging.getLogger("web")
STATIC = os.path.join(ROOT, "web", "static")
STORE: Store
BASE_URL = ""
PUBLIC_API = {"/api/register", "/api/login", "/api/logout", "/api/me", "/api/showrooms"}
_attempts: dict[str, list[float]] = {}


def _users() -> dict:
    return STORE.data["users"]


def _public(u: dict) -> dict:
    return {k: u.get(k, "") for k in ("id", "name", "phone", "showroom", "telegram", "status")}


def _too_many(key: str, limit: int = 8, window: int = 900) -> bool:
    t = time.time()
    hits = [x for x in _attempts.get(key, []) if t - x < window]
    _attempts[key] = hits
    return len(hits) >= limit


def _hit(key: str) -> None:
    _attempts.setdefault(key, []).append(time.time())


def _ip(request: web.Request) -> str:
    return (request.headers.get("X-Forwarded-For") or request.remote or "").split(",")[0].strip()


# --- доступ ---------------------------------------------------------------------------------------


@web.middleware
async def guard(request: web.Request, handler):
    request["user"] = None
    s = auth.read_session(request.cookies.get(auth.COOKIE))
    if s:
        u = _users().get(s[0])
        if u and u.get("ver", 0) == s[1]:
            request["user"] = u
    path = request.path
    if path.startswith("/api/") and path not in PUBLIC_API:
        u = request["user"]
        if u is None:
            return web.json_response({"error": "Войдите заново."}, status=401)
        if u.get("status") != "approved":
            return web.json_response({"error": "Доступ ещё не подтверждён владельцем."}, status=403)
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except json.JSONDecodeError:
        return web.json_response({"error": "Неверный запрос."}, status=400)
    except Exception:  # noqa: BLE001
        log.exception("ошибка %s %s", request.method, path)
        return web.json_response({"error": "Что-то пошло не так. Попробуйте ещё раз."}, status=500)


def _set_session(resp: web.Response, u: dict, secure: bool) -> None:
    resp.set_cookie(auth.COOKIE, auth.make_session(u["id"], u.get("ver", 0)), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure, path="/")


def _secure(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto") == "https"


# --- регистрация и вход ---------------------------------------------------------------------------


@api.routes.get("/api/showrooms")
async def showrooms(request: web.Request) -> web.Response:
    return web.json_response({"showrooms": profiles.SHOWROOMS})


def _owner_buttons(uid: str) -> list[tuple[str, str]]:
    def link(action: str) -> str:
        return f"{BASE_URL}/owner/{action}?u={uid}&s={auth.action_sig(action, uid)}"
    return [("✅ Пустить", link("approve")), ("❌ Отклонить", link("reject"))]


@api.routes.post("/api/register")
async def register(request: web.Request) -> web.Response:
    b = await request.json()
    if _too_many("reg:" + _ip(request), 6, 3600):
        return web.json_response({"error": "Слишком много попыток. Попробуйте через час."}, status=429)
    name = " ".join(str(b.get("name") or "").split())[:120]
    phone = auth.norm_phone(str(b.get("phone") or ""))
    password = str(b.get("password") or "")
    showroom = profiles.norm_showroom(str(b.get("showroom") or ""))
    telegram = str(b.get("telegram") or "").strip()[:60]
    errors = {}
    if len(name.split()) < 2:
        errors["name"] = "Укажите фамилию и имя."
    if not phone:
        errors["phone"] = "Телефон: 10 цифр после +7."
    elif any(u["phone"] == phone for u in _users().values()):
        errors["phone"] = "Этот телефон уже зарегистрирован — войдите."
    if len(password) < 6:
        errors["password"] = "Пароль — минимум 6 символов."
    if not showroom:
        errors["showroom"] = "Выберите шоу-рум."
    if errors:
        return web.json_response({"errors": errors}, status=422)
    _hit("reg:" + _ip(request))
    uid = f"u{STORE.next_id()}"
    u = {"id": uid, "name": name, "phone": phone, "showroom": showroom, "telegram": telegram,
         "pw": auth.hash_password(password), "status": "pending", "ver": 0, "created": now()}
    _users()[uid] = u
    await STORE.flush()  # регистрацию сохраняем сразу
    esc = html.escape
    notify.text(
        "🆕 <b>Заявка на доступ к сайту</b>\n"
        f"ФИО: {esc(name)}\nТелефон: {auth.pretty_phone(phone)}\nШоу-рум: {esc(showroom)}"
        + (f"\nTelegram: {esc(telegram)}" if telegram else "") + "\n\nПустить на сайт?",
        _owner_buttons(uid))
    resp = web.json_response({"user": _public(u)})
    _set_session(resp, u, _secure(request))
    return resp


@api.routes.post("/api/login")
async def login(request: web.Request) -> web.Response:
    b = await request.json()
    phone = auth.norm_phone(str(b.get("phone") or ""))
    key = f"login:{_ip(request)}:{phone}"
    if _too_many(key):
        return web.json_response({"error": "Слишком много попыток. Подождите 15 минут."}, status=429)
    u = next((x for x in _users().values() if x["phone"] == phone), None) if phone else None
    if u is None or not auth.check_password(str(b.get("password") or ""), u["pw"]):
        _hit(key)
        return web.json_response({"error": "Неверный телефон или пароль."}, status=401)
    if u["status"] in ("rejected", "blocked"):
        return web.json_response({"error": "Доступ закрыт. Обратитесь к руководителю."}, status=403)
    resp = web.json_response({"user": _public(u)})
    _set_session(resp, u, _secure(request))
    return resp


@api.routes.post("/api/logout")
async def logout(request: web.Request) -> web.Response:
    resp = web.json_response({"ok": True})
    resp.del_cookie(auth.COOKIE, path="/")
    return resp


@api.routes.get("/api/me")
async def me(request: web.Request) -> web.Response:
    u = request["user"]
    return web.json_response({"user": _public(u) if u else None})


@api.routes.put("/api/profile")
async def profile_update(request: web.Request) -> web.Response:
    u = request["user"]
    b = await request.json()
    name = " ".join(str(b.get("name") or u["name"]).split())[:120]
    showroom = profiles.norm_showroom(str(b.get("showroom") or u["showroom"])) or u["showroom"]
    u.update(name=name, showroom=showroom, telegram=str(b.get("telegram") or "").strip()[:60])
    if b.get("password"):
        if len(b["password"]) < 6:
            return web.json_response({"errors": {"password": "Минимум 6 символов."}}, status=422)
        u["pw"], u["ver"] = auth.hash_password(b["password"]), u.get("ver", 0) + 1
    STORE.changed()
    resp = web.json_response({"user": _public(u)})
    if b.get("password"):
        _set_session(resp, u, _secure(request))
    return resp


# --- решения владельца по ссылкам из админ-бота ---------------------------------------------------


def _page(title: str, body: str) -> web.Response:
    return web.Response(content_type="text/html", text=(
        "<!doctype html><html lang=ru><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><link rel=stylesheet href=/static/style.css></head>"
        f"<body class=plain><main class=notice><img src=/static/logo.png alt=Creatica class=logo>"
        f"<h1>{html.escape(title)}</h1><p>{body}</p></main></body></html>"))


@api.routes.get("/owner/{action}")
async def owner_action(request: web.Request) -> web.Response:
    action = request.match_info["action"]
    uid, sig = request.query.get("u", ""), request.query.get("s", "")
    if action not in ("approve", "reject", "block") or not auth.check_action(action, uid, sig):
        return _page("Ссылка недействительна", "Откройте ссылку из сообщения админ-бота ещё раз.")
    u = _users().get(uid)
    if u is None:
        return _page("Пользователь не найден", "Возможно, заявку уже удалили.")
    who = f"{html.escape(u['name'])}, {auth.pretty_phone(u['phone'])}"
    status = {"approve": "approved", "reject": "rejected", "block": "blocked"}[action]
    if u["status"] == status:
        return _page("Уже сделано", f"{who} — решение уже принято раньше.")
    u["status"] = status
    if status != "approved":
        u["ver"] = u.get("ver", 0) + 1  # выкидываем из всех открытых сессий
    await STORE.flush()
    if action == "approve":
        notify.text(f"✅ Доступ к сайту открыт: {who}", [("🚫 Заблокировать", f"{BASE_URL}/owner/block?u={uid}"
                                                        f"&s={auth.action_sig('block', uid)}")])
        return _page("Доступ открыт", f"{who} теперь может работать на сайте.")
    notify.text(f"{'❌ Заявка отклонена' if action == 'reject' else '🚫 Заблокирован'}: {who}")
    return _page("Готово", f"{who} — доступ закрыт.")


# --- страницы ---------------------------------------------------------------------------------------


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(STATIC, "index.html"))


async def app_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(STATIC, "app.html"))


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


@web.middleware
async def no_cache_html(request: web.Request, handler):
    resp = await handler(request)
    if request.path in ("/", "/app") or request.path.endswith((".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# --- запуск -----------------------------------------------------------------------------------------


async def on_startup(app: web.Application) -> None:
    global STORE, BASE_URL
    BASE_URL = (os.getenv("WEB_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8080").rstrip("/")
    admin = Bot(config.ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML")) \
        if config.ADMIN_BOT_TOKEN and not os.getenv("WEB_NO_ADMIN") else None  # WEB_NO_ADMIN=1 — локальные тесты
    app["admin_bot"] = admin
    # данные сайта — в чате владельца с админ-ботом (или в отдельном чате, если задан WEB_STORE_CHAT)
    chat = os.getenv("WEB_STORE_CHAT", "").strip()
    store_chat = int(chat) if chat else (config.ADMIN_CHAT_IDS[0] if config.ADMIN_CHAT_IDS else None)
    STORE = Store(admin, store_chat)
    api.STORE = STORE
    await STORE.load()
    notify.configure(admin, config.ADMIN_CHAT_IDS)
    auth.init(os.getenv("WEB_SECRET") or config.ADMIN_BOT_TOKEN or "local-dev-secret")
    log.info("сайт запущен: %s, хранилище: %s", BASE_URL, "Telegram" if STORE.remote else "web_data/")


async def on_shutdown(app: web.Application) -> None:
    await STORE.flush()
    await asyncio.sleep(0.5)  # дать уйти последним уведомлениям
    if app.get("admin_bot"):
        await app["admin_bot"].session.close()


def make_app() -> web.Application:
    app = web.Application(middlewares=[guard, no_cache_html], client_max_size=20 * 1024 * 1024)
    app.add_routes(api.routes)
    app.router.add_get("/", index)
    app.router.add_get("/app", app_page)
    app.router.add_get("/health", health)
    app.router.add_static("/static/", STATIC)
    app.router.add_static("/fonts/", os.path.join(ROOT, "fonts"))
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    web.run_app(make_app(), port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    main()
