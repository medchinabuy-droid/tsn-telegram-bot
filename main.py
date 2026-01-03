import os
import asyncio
import logging
from flask import Flask, request, abort
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------------- ЛОГИ ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not BOT_TOKEN or not BASE_URL or not WEBHOOK_SECRET:
    raise RuntimeError("❌ BOT_TOKEN / BASE_URL / WEBHOOK_SECRET не заданы")

WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

logger.info("ENV OK")

# ---------------- APPS ----------------
flask_app = Flask(__name__)
telegram_app = Application.builder().token(BOT_TOKEN).build()

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "🤖 Бот запущен!\n\nНажмите кнопку ниже 👇",
        reply_markup=keyboard,
    )

telegram_app.add_handler(CommandHandler("start", start))

# ---------------- WEBHOOK (SYNC!) ----------------
@flask_app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)

    data = request.get_json(force=True)

    update = Update.de_json(data, telegram_app.bot)

    asyncio.run(telegram_app.process_update(update))
    return "OK"

# ---------------- STARTUP ----------------
async def setup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
    )
    await telegram_app.start()
    logger.info("✅ Webhook установлен")

# ---------------- ENTRY ----------------
if __name__ == "__main__":
    asyncio.run(setup())
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
