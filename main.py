import os
import logging
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")  # https://tsn-telegram-bot.onrender.com
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN or not BASE_URL:
    raise RuntimeError("❌ BOT_TOKEN или BASE_URL не заданы")

logger.info("✅ ENV OK")

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔥 /start получен")

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "🤖 Бот ЗАПУЩЕН и РАБОТАЕТ\n\nНажмите кнопку 👇",
        reply_markup=keyboard,
    )

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    logger.info("🚀 Запуск webhook")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_path="/webhook",                 # 🔴 ВАЖНО
        webhook_url=f"{BASE_URL}/webhook",
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
