import os
import json
import logging

from aiohttp import web

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials

# =======================
# ENV
# =======================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not all([BOT_TOKEN, WEBHOOK_URL, GOOGLE_CREDENTIALS_JSON]):
    raise RuntimeError("❌ Не заданы ENV переменные")

# =======================
# LOGGING
# =======================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# =======================
# GOOGLE SHEETS
# =======================

def init_google():
    creds = json.loads(GOOGLE_CREDENTIALS_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        creds,
        scopes=scopes,
    )

    gc = gspread.authorize(credentials)
    logger.info("✅ Google Sheets инициализирован")
    return gc


gc = init_google()

# =======================
# KEYBOARDS
# =======================

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("🚀 Начать")]],
    resize_keyboard=True,
)

# =======================
# HANDLERS
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать!\nНажмите «🚀 Начать»",
        reply_markup=MAIN_KEYBOARD,
    )


async def handle_start_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Кнопка «🚀 Начать» нажата!\n\nДальше будет логика бота.",
    )


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Я не понял сообщение.\nНажмите «🚀 Начать».",
        reply_markup=MAIN_KEYBOARD,
    )

# =======================
# TELEGRAM APP
# =======================

application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(
    MessageHandler(filters.TEXT & filters.Regex("^🚀 Начать$"), handle_start_button)
)
application.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text)
)

# =======================
# AIOHTTP SERVER
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

    webhook_url = f"{WEBHOOK_URL}/webhook"
    await application.bot.set_webhook(webhook_url)

    logger.info(f"🌍 Webhook установлен: {webhook_url}")
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
