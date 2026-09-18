import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram.types import Update  # noqa: E402

from core import dp, make_bot, secret_token  # noqa: E402


async def process(payload: dict) -> None:
    bot = make_bot()
    try:
        update = Update.model_validate(payload, context={"bot": bot})
        await dp.feed_update(bot, update)
    finally:
        await bot.session.close()


class handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, text: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def do_GET(self) -> None:
        self._reply(200, "OK - бот жив")

    def do_POST(self) -> None:
        if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret_token():
            self._reply(403, "forbidden")
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            asyncio.run(process(payload))
        except Exception as exc:  # noqa: BLE001
            print("update failed:", repr(exc))
        self._reply(200, "ok")
