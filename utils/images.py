"""Генерация картинок: Gemini переводит промпт, Pollinations рисует.

Pollinations.ai — бесплатный сервис без ключей. Gemini нужен только для перевода
описания на английский (без ключа рисуем по исходному тексту).
"""

from __future__ import annotations

import urllib.parse

import aiohttp

from config import GEMINI_API_KEY

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
)


async def _translate_prompt(prompt: str) -> str:
    if not GEMINI_API_KEY:
        return prompt
    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": "Translate this image description to English for an image "
                        "generator. Keep it short, output only the translated prompt, "
                        f"no quotes: {prompt}"
                    }
                ]
            }
        ]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _GEMINI_URL,
                json=body,
                headers={"x-goog-api-key": GEMINI_API_KEY},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return prompt
                data = await resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text or prompt
    except Exception:
        return prompt


async def generate_image(prompt: str) -> bytes:
    prompt_en = await _translate_prompt(prompt)
    encoded = urllib.parse.quote(prompt_en)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=40)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Сервис генерации картинок ответил ошибкой ({resp.status}).")
            return await resp.read()
