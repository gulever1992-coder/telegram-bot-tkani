import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

FABRICS_CSV_URL = os.getenv("FABRICS_CSV_URL", "")
PRODUCTS_CSV_URL = os.getenv("PRODUCTS_CSV_URL", "")

COMPANY_NAME = os.getenv("COMPANY_NAME", "")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "Не найден TELEGRAM_BOT_TOKEN. Откройте файл .env и вставьте туда токен бота."
    )
