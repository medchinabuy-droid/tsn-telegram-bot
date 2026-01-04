import os
import logging
import asyncio

from flask import Flask, request
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")  # https://tsn-telegram-bot.onrender.com
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN or not BASE_URL:
    raise RuntimeError("❌ BOT_TOKEN или BASE_URL не заданы")

logger.info("✅ ENV OK")

# ================== TELEGRAM ==================
telegram_app = Application.builder().token(BOT_TOKEN).build()
loop = asyncio.get_event_loop()

# ================== HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("📨 /start received")

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "🤖 Бот ЗАПУЩЕН и отвечает на /start\n\nНажмите кнопку 👇",
        reply_markup=keyboard,
    )

telegram_app.add_handler(CommandHandler("start", start))

# ================== FLASK ==================
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET", "HEAD"])
def index():
    return "OK", 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    logger.info("🔥 WEBHOOK RECEIVED")
    logger.info(f"📦 Update: {data}")

    update = Update.de_json(data, telegram_app.bot)
    asyncio.run_coroutine_threadsafe(
        telegram_app.process_update(update),
        loop,
    )

    return "ok", 200

# ================== STARTUP ==================
async def startup():
    await telegram_app.initialize()
    await telegram_app.start()

    await telegram_app.bot.delete_webhook(drop_pending_updates=True)
    await telegram_app.bot.set_webhook(f"{BASE_URL}/webhook")

    logger.info(f"✅ Webhook установлен: {BASE_URL}/webhook")

# ================== ENTRY ==================
if __name__ == "__main__":
    loop.run_until_complete(startup())

    flask_app.run(
        host="0.0.0.0",
        port=PORT,
    )
