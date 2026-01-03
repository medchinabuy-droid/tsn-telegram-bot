import os
import logging
from flask import Flask, request, abort

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ================== ЛОГИ ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not BOT_TOKEN or not WEBHOOK_URL or not WEBHOOK_SECRET:
    raise RuntimeError("❌ Не заданы BOT_TOKEN / WEBHOOK_URL / WEBHOOK_SECRET")

# ================== FLASK ==================
flask_app = Flask(__name__)

# ================== TELEGRAM ==================
application = Application.builder().token(BOT_TOKEN).build()

# ---------- handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "🤖 Бот запущен через WEBHOOK и работает!\n\nВыберите действие:",
        reply_markup=keyboard,
    )

application.add_handler(CommandHandler("start", start))

# ================== WEBHOOK ROUTE ==================
@flask_app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)

    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "OK"

# ================== INIT ==================
async def setup_webhook():
    await application.bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}",
        drop_pending_updates=True,
    )
    logging.info("✅ Webhook установлен")

# ================== ENTRY ==================
if __name__ == "__main__":
    import asyncio

    async def main():
        await application.initialize()
        await setup_webhook()
        await application.start()

    asyncio.run(main())

    # Render сам прокидывает PORT
    flask_app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
