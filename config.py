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

COMPANY_NAME = os.getenv("COMPANY_NAME", "")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "Не найден TELEGRAM_BOT_TOKEN. Задайте его в файле .env (локально) "
        "или в настройках проекта на Vercel (Environment Variables)."
    )
