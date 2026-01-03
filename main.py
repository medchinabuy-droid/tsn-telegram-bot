import os
import logging
from flask import Flask, request, abort
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ================= ЛОГИ =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

logger.info(f"BOT_TOKEN set: {bool(BOT_TOKEN)}")
logger.info(f"WEBHOOK_URL set: {bool(WEBHOOK_URL)}")
logger.info(f"WEBHOOK_SECRET set: {bool(WEBHOOK_SECRET)}")

if not BOT_TOKEN or not WEBHOOK_URL or not WEBHOOK_SECRET:
    raise RuntimeError("❌ Не заданы BOT_TOKEN / WEBHOOK_URL / WEBHOOK_SECRET")

WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

# ================= FLASK =================
flask_app = Flask(__name__)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Бот запущен и работает!\n/start получен ✅"
    )

# ================= TELEGRAM APP =================
telegram_app = Application.builder().token(BOT_TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))

# ================= WEBHOOK =================
@flask_app.post(WEBHOOK_PATH)
async def webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)

    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    await telegram_app.process_update(update)
    return "OK"

# ================= STARTUP =================
async def setup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)
    logger.info("✅ Webhook установлен")

@flask_app.before_first_request
def startup():
    flask_app.loop.create_task(setup())

# ================= ENTRY =================
if __name__ == "__main__":
    flask_app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
