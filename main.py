import os
import logging
from flask import Flask, request, abort

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")          # ← ВАЖНО
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

logger.info(f"BOT_TOKEN set: {bool(BOT_TOKEN)}")
logger.info(f"BASE_URL set: {bool(BASE_URL)}")
logger.info(f"WEBHOOK_SECRET set: {bool(WEBHOOK_SECRET)}")

if not BOT_TOKEN or not BASE_URL or not WEBHOOK_SECRET:
    raise RuntimeError("❌ Не заданы BOT_TOKEN / BASE_URL / WEBHOOK_SECRET")

# ================== FLASK ==================
app = Flask(__name__)

# ================== TELEGRAM ==================
application = Application.builder().token(BOT_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот работает через WEBHOOK на Render")

application.add_handler(CommandHandler("start", start))

# ================== WEBHOOK ==================
@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)

    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "OK"

# ================== INIT ==================
async def setup():
    await application.initialize()
    await application.bot.set_webhook(
        url=f"{BASE_URL}/webhook/{WEBHOOK_SECRET}",
        drop_pending_updates=True,
    )
    logger.info("✅ Webhook установлен")
    await application.start()

if __name__ == "__main__":
    import asyncio

    asyncio.run(setup())

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
