import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

_SHEET_ID = "1emQk0xq3tnTSYsljZ25bVpE_GX35xpwZMckq74U8r-I"
_SHEET = f"https://docs.google.com/spreadsheets/d/{_SHEET_ID}/export?format=csv"
PRICE_CSV_URL = os.getenv("PRICE_CSV_URL") or f"{_SHEET}&gid=0"
STOCK_CSV_URL = os.getenv("STOCK_CSV_URL") or f"{_SHEET}&gid=1964038919"
PRICE_ONLINE_URL = os.getenv("PRICE_ONLINE_URL") or (
    f"https://docs.google.com/spreadsheets/d/{_SHEET_ID}/edit?gid=0#gid=0"
)

PRICELIST_SHEET_ID = os.getenv("PRICELIST_SHEET_ID") or "1d3UN8U5H-SEFK_xApNV34b5PWk4G8ah3-NUuYz_3cxQ"

COMPANY_NAME = os.getenv("COMPANY_NAME", "")


def _admin_settings() -> tuple[str, list[int]]:
    """Токен админ-бота и id владельцев: из переменных окружения или из файла admin_secrets.json
    (файл не попадает в репозиторий; в облаке приезжает в закрытом архиве PRIVATE_BUNDLE_B64)."""
    import json

    data: dict = {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_secrets.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
    token = os.getenv("ADMIN_BOT_TOKEN") or data.get("ADMIN_BOT_TOKEN", "")
    raw = os.getenv("ADMIN_CHAT_IDS") or data.get("ADMIN_CHAT_IDS", [])
    if isinstance(raw, str):
        raw = [x for x in raw.replace(";", ",").split(",") if x.strip()]
    ids = []
    for x in raw if isinstance(raw, list) else [raw]:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            pass
    return token, ids


ADMIN_BOT_TOKEN, ADMIN_CHAT_IDS = _admin_settings()

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "Не найден TELEGRAM_BOT_TOKEN. Задайте его в файле .env (локально) "
        "или в настройках проекта на Vercel (Environment Variables)."
    )
