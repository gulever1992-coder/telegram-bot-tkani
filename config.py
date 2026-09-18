import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

_SHEET = "https://docs.google.com/spreadsheets/d/1emQk0xq3tnTSYsljZ25bVpE_GX35xpwZMckq74U8r-I/export?format=csv"
FABRICS_CSV_URL = os.getenv("FABRICS_CSV_URL") or f"{_SHEET}&gid=0"
PRODUCTS_CSV_URL = os.getenv("PRODUCTS_CSV_URL") or f"{_SHEET}&gid=434466654"

COMPANY_NAME = os.getenv("COMPANY_NAME", "")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "Не найден TELEGRAM_BOT_TOKEN. Задайте его в файле .env (локально) "
        "или в настройках проекта на Vercel (Environment Variables)."
    )
