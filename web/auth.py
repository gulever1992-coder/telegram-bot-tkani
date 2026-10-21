"""Пароли, сессии (подписанная cookie) и подписи ссылок «Пустить / Отклонить» для владельца."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time

COOKIE = "creatica_session"
SESSION_DAYS = 30
_SECRET: bytes = b""


def init(secret: str) -> None:
    global _SECRET
    _SECRET = hashlib.sha256(("creatica-web:" + secret).encode()).digest()


def _sign(text: str) -> str:
    return hmac.new(_SECRET, text.encode(), hashlib.sha256).hexdigest()[:32]


# --- пароли ------------------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), 200_000)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# --- сессия ------------------------------------------------------------------------------------


def make_session(uid: str, ver: int) -> str:
    """ver — номер «поколения» пароля: смена пароля/блокировка выкидывает старые сессии."""
    exp = int(time.time()) + SESSION_DAYS * 86400
    body = f"{uid}|{ver}|{exp}"
    return base64.urlsafe_b64encode(f"{body}|{_sign(body)}".encode()).decode()


def read_session(value: str | None) -> tuple[str, int] | None:
    if not value:
        return None
    try:
        uid, ver, exp, sig = base64.urlsafe_b64decode(value.encode()).decode().split("|")
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(sig, _sign(f"{uid}|{ver}|{exp}")) or int(exp) < time.time():
        return None
    return uid, int(ver)


# --- ссылки владельцу ---------------------------------------------------------------------------


def action_sig(action: str, uid: str) -> str:
    return _sign(f"{action}:{uid}")


def check_action(action: str, uid: str, sig: str) -> bool:
    return hmac.compare_digest(sig or "", action_sig(action, uid))


# --- телефон как логин ---------------------------------------------------------------------------


def norm_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return "+7" + digits


def pretty_phone(p: str) -> str:
    d = p[2:]
    return f"+7 {d[:3]} {d[3:6]}-{d[6:8]}-{d[8:]}"


def new_token() -> str:
    return secrets.token_urlsafe(24)
