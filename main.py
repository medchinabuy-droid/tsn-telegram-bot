import os
import json
import asyncio
import logging

from aiohttp import web

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import gspread
from google.oauth2.service_account import Credentials

# -------------------- ЛОГИ --------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN отсутствует")

if not WEBHOOK_URL:
    raise RuntimeError("❌ WEBHOOK_URL отсутствует")

# -------------------- GOOGLE --------------------
gc = None

def init_google():
    global gc

    if not GOOGLE_CREDENTIALS_JSON:
        logger.error("❌ GOOGLE_CREDENTIALS_JSON отсутствует")
        return

    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)

    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )

    gc = gspread.authorize(creds)
    logger.info("✅ Google Sheets инициализирован")

# -------------------- HANDLERS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Бот запущен и работает")

# -------------------- WEBHOOK --------------------
async def handle_webhook(request: web.Request):
    app: Application = request.app["telegram_app"]
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="ok")

# -------------------- MAIN --------------------
async def main():
    init_google()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))

    # 🔴 ВАЖНО: initialize()
    await application.initialize()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    await application.start()

    logger.info(f"🌍 Webhook установлен: {WEBHOOK_URL}/webhook")

    # HTTP SERVER (Render требует порт)
    aio_app = web.Application()
    aio_app["telegram_app"] = application
    aio_app.router.add_post("/webhook", handle_webhook)

    runner = web.AppRunner(aio_app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"🚀 HTTP сервер запущен на порту {PORT}")

    # Бесконечный цикл
    await asyncio.Event().wait()

# -------------------- ENTRY --------------------
if __name__ == "__main__":
    asyncio.run(main())
