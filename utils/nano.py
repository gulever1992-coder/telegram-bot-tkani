"""Nano Banana (Gemini image) — редактирование и генерация картинок.

Использует Gemini API по ключу из aistudio.google.com (GEMINI_API_KEY в .env).
Подписка Google AI Pro в приложении Gemini — это другое, для бота нужен именно API-ключ.
Модель можно сменить переменной NANO_BANANA_MODEL (например gemini-3-pro-image-preview —
Nano Banana Pro).
"""

from __future__ import annotations

import base64
import os

import aiohttp

import config

MODEL = os.getenv("NANO_BANANA_MODEL", "gemini-2.5-flash-image")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class NanoError(RuntimeError):
    pass


def _explain(status: int, body: str) -> str:
    low = body.lower()
    if "location is not supported" in low:
        return (
            "Google не принимает запросы из вашего региона. Включите VPN на компьютере, "
            "где работает бот, и повторите."
        )
    if status in (400, 401, 403) and ("api key" in low or "api_key" in low or "permission" in low):
        return "Ключ Gemini не подходит. Проверьте GEMINI_API_KEY в файле .env."
    if status == 429:
        return "Лимит запросов Gemini исчерпан. Подождите минуту (или до завтра, если дневной лимит)."
    if status == 404:
        return f"Модель {MODEL} недоступна для вашего ключа. Смените NANO_BANANA_MODEL в .env."
    return f"Gemini вернул ошибку {status}."


async def generate(prompt: str, images: list[tuple[bytes, str]] | None = None) -> bytes:
    if not config.GEMINI_API_KEY:
        raise NanoError(
            "Не задан ключ Gemini. Получите его на aistudio.google.com и впишите в файл .env "
            "в строку GEMINI_API_KEY=..."
        )
    parts: list[dict] = [{"text": prompt}]
    for data, mime in images or []:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    body = {"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150)) as session:
            async with session.post(
                URL.format(model=MODEL), json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY}
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise NanoError(_explain(resp.status, text))
                data = await resp.json(content_type=None)
    except NanoError:
        raise
    except Exception:
        raise NanoError("Нет связи с Gemini. Проверьте интернет/VPN и повторите.") from None

    if data.get("promptFeedback", {}).get("blockReason"):
        raise NanoError("Запрос заблокирован фильтром безопасности Gemini. Попробуйте другое фото.")

    said = ""
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
            said += part.get("text", "")
    raise NanoError("Gemini не вернул картинку." + (f" Ответ: {said[:300]}" if said else ""))
