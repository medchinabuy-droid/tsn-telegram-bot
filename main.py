import os
import json
import logging
import asyncio

from aiohttp import web

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import gspread
from google.oauth2.service_account import Credentials

# =======================
# НАСТРОЙКИ / ENV
# =======================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://xxxx.onrender.com
PORT = int(os.getenv("PORT", 10000))

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не задан")

if not WEBHOOK_URL:
    raise RuntimeError("❌ WEBHOOK_URL не задан")

if not GOOGLE_CREDENTIALS_JSON:
    raise RuntimeError("❌ GOOGLE_CREDENTIALS_JSON не задан")

# =======================
# ЛОГИ
# =======================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# =======================
# GOOGLE SHEETS
# =======================

def init_google_sheets():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        creds_dict,
        scopes=scopes,
    )

    gc = gspread.authorize(credentials)
    logger.info("✅ Google Sheets инициализирован")
    return gc


gc = init_google_sheets()

# =======================
# TELEGRAM HANDLERS
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Бот запущен и работает через Webhook!"
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong")


# =======================
# APP TELEGRAM
# =======================

application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("ping", ping))

# =======================
# AIOHTTP WEB SERVER
# =======================

async def healthcheck(request):
    return web.Response(text="OK")


async def telegram_webhook(request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return web.Response(text="OK")


async def on_startup(app):
    await application.initialize()
    await application.start()

    webhook_full_url = f"{WEBHOOK_URL}/webhook"
    await application.bot.set_webhook(webhook_full_url)

    logger.info(f"🌍 Webhook установлен: {webhook_full_url}")
    logger.info(f"🚀 HTTP сервер запущен на порту {PORT}")


async def on_shutdown(app):
    await application.stop()
    await application.shutdown()
    logger.info("🛑 Application остановлен")


def main():
    app = web.Application()

    app.router.add_get("/", healthcheck)
    app.router.add_post("/webhook", telegram_webhook)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, port=PORT)


if __name__ == "__main__":
    main()
