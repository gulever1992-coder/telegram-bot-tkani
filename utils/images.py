"""Генерация картинок: Gemini улучшает и переводит промпт, Pollinations рисует.

Pollinations.ai — бесплатный сервис без ключей и лимитов (проверено в вашем боте
раньше). Gemini API нужен только для перевода/улучшения текста промпта на английский
— так картинки получаются точнее.
"""

from __future__ import annotations

import urllib.parse

import aiohttp

from config import GEMINI_API_KEY

_client = None
if GEMINI_API_KEY:
    from google import genai

    _client = genai.Client(api_key=GEMINI_API_KEY)


async def _translate_prompt(prompt: str) -> str:
    if not _client:
        return prompt
    try:
        response = _client.models.generate_content(
            model="gemini-2.5-flash",
            contents=(
                "Translate this image description to English for an image generator. "
                "Keep it short and concise, output only the translated prompt, no quotes: "
                f"{prompt}"
            ),
        )
        text = (response.text or "").strip()
        return text or prompt
    except Exception:
        # Если Gemini недоступен — рисуем по исходному тексту, ничего страшного.
        return prompt


async def generate_image(prompt: str) -> bytes:
    prompt_en = await _translate_prompt(prompt)
    encoded = urllib.parse.quote(prompt_en)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Сервис генерации картинок ответил ошибкой ({resp.status}).")
            return await resp.read()
