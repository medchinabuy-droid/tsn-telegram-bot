import os
import logging
from flask import Flask, request, abort

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
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

if not BOT_TOKEN or not WEBHOOK_URL or not WEBHOOK_SECRET:
    raise RuntimeError("❌ Не заданы BOT_TOKEN / WEBHOOK_URL / WEBHOOK_SECRET")

# ================= APP =================
telegram_app = Application.builder().token(BOT_TOKEN).build()
flask_app = Flask(__name__)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "🤖 Бот запущен и реагирует на /start\n\nВыберите действие:",
        reply_markup=keyboard,
    )

telegram_app.add_handler(CommandHandler("start", start))

# ================= WEBHOOK ROUTE =================
@flask_app.post(f"/webhook/{WEBHOOK_SECRET}")
async def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)

    data = request.get_json(force=True)
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return "OK"

# ================= STARTUP =================
async def setup_webhook():
    await telegram_app.bot.set_webhook(
        url=f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}",
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )
    logger.info("✅ Webhook установлен")

# ================= ENTRY =================
if __name__ == "__main__":
    import asyncio

    async def main():
        await telegram_app.initialize()
        await setup_webhook()
        await telegram_app.start()

    asyncio.run(main())

    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
