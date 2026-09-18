import asyncio
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import make_bot, secret_token  # noqa: E402


async def register(url: str) -> str:
    bot = make_bot()
    try:
        await bot.set_webhook(url, secret_token=secret_token(), drop_pending_updates=True)
        me = await bot.get_me()
        info = await bot.get_webhook_info()
        return f"Готово! Бот @{me.username} подключён.\nWebhook: {info.url}\nОшибок: {info.last_error_message or 'нет'}"
    finally:
        await bot.session.close()


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host")
        try:
            text = asyncio.run(register(f"https://{host}/api/webhook"))
            code = 200
        except Exception as exc:  # noqa: BLE001
            text, code = f"Ошибка: {exc!r}", 500
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))
