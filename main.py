import os
import logging
import asyncio
from flask import Flask, request, abort

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes

# ================= ЛОГИ =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")  # без /webhook
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN or not BASE_URL or not WEBHOOK_SECRET:
    raise RuntimeError("❌ ENV variables missing")

logger.info("✅ ENV OK")

# ================= ASYNC LOOP =================
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# ================= TELEGRAM =================
telegram_app = Application.builder().token(BOT_TOKEN).build()

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ /start received")

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True
    )

    await update.message.reply_text(
        "🤖 Бот запущен и работает.\n\nНажмите кнопку ниже 👇",
        reply_markup=keyboard
    )

telegram_app.add_handler(CommandHandler("start", start))

# ================= FLASK =================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    logger.info("📩 Webhook POST received")

    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        logger.warning("❌ Wrong secret token")
        abort(403)

    update = Update.de_json(request.get_json(force=True), telegram_app.bot)

    asyncio.run_coroutine_threadsafe(
        telegram_app.process_update(update),
        loop
    )

    return "ok"

@app.route("/", methods=["GET", "HEAD"])
def index():
    return "OK"

# ================= START =================
async def startup():
    await telegram_app.initialize()
    await telegram_app.bot.delete_webhook(drop_pending_updates=True)

    await telegram_app.bot.set_webhook(
        url=f"{BASE_URL}/webhook",
        secret_token=WEBHOOK_SECRET
    )

    await telegram_app.start()
    logger.info("✅ Webhook установлен корректно")

if __name__ == "__main__":
    loop.run_until_complete(startup())
    app.run(host="0.0.0.0", port=PORT)
