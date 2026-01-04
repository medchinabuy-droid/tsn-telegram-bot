import os
import logging
import asyncio
from flask import Flask, request, abort

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ================= ЛОГИ =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not BOT_TOKEN or not BASE_URL or not WEBHOOK_SECRET:
    raise RuntimeError("ENV variables missing")

logger.info("ENV OK")

# ================= TELEGRAM =================
telegram_app = Application.builder().token(BOT_TOKEN).build()

# ================= FLASK =================
app = Flask(__name__)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ /start received from Telegram")

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True
    )

    await update.message.reply_text(
        "🤖 Бот запущен.\n\nНажмите кнопку ниже, чтобы начать.",
        reply_markup=keyboard
    )

telegram_app.add_handler(CommandHandler("start", start))

# ================= WEBHOOK =================
@app.route("/", methods=["POST"])
def webhook():
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != WEBHOOK_SECRET:
        abort(403)

    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    asyncio.get_event_loop().create_task(
        telegram_app.process_update(update)
    )
    return "ok"

@app.route("/", methods=["GET", "HEAD"])
def index():
    return "OK"

# ================= START =================
async def main():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(
        url=BASE_URL,
        secret_token=WEBHOOK_SECRET
    )
    await telegram_app.start()
    logger.info("✅ Webhook установлен НА /")

if __name__ == "__main__":
    asyncio.run(main())
    app.run(host="0.0.0.0", port=10000)
