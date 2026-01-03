import os
import logging
from flask import Flask, request, abort
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ---------------- ЛОГИ ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

logger.info(f"BOT_TOKEN set: {bool(BOT_TOKEN)}")
logger.info(f"BASE_URL set: {bool(BASE_URL)}")
logger.info(f"WEBHOOK_SECRET set: {bool(WEBHOOK_SECRET)}")

if not BOT_TOKEN or not BASE_URL or not WEBHOOK_SECRET:
    raise RuntimeError("❌ Не заданы BOT_TOKEN / BASE_URL / WEBHOOK_SECRET")

WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

# ---------------- APP ----------------
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

# ---------------- WEBHOOK ROUTE ----------------
@flask_app.post(WEBHOOK_PATH)
async def telegram_webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)

    data = request.get_json(force=True)
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return "OK"

# ---------------- STARTUP ----------------
async def setup_webhook():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
    )
    await telegram_app.start()
    logger.info("✅ Webhook установлен")

# ---------------- ENTRY ----------------
if __name__ == "__main__":
    import asyncio

    asyncio.run(setup_webhook())

    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
