import os
import logging

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

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

# ================= STATES =================
WAIT_FIO = "wait_fio"
WAIT_PHONE = "wait_phone"
WAIT_CHECK = "wait_check"

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔥 /start получен")

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "🤖 Бот запущен и готов к работе.\n\n"
        "Нажмите кнопку ниже 👇",
        reply_markup=keyboard,
    )

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📤 Отправить чек":
        context.user_data.clear()
        context.user_data["state"] = WAIT_FIO

        await update.message.reply_text(
            "✍️ Введите ваше ФИО:"
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state == WAIT_FIO:
        context.user_data["fio"] = update.message.text
        context.user_data["state"] = WAIT_PHONE

        await update.message.reply_text(
            "📞 Введите номер телефона:"
        )
        return

    if state == WAIT_PHONE:
        context.user_data["phone"] = update.message.text
        context.user_data["state"] = WAIT_CHECK

        await update.message.reply_text(
            "📷 Отправьте фото чека:"
        )
        return

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state != WAIT_CHECK:
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    fio = context.user_data.get("fio")
    phone = context.user_data.get("phone")

    logger.info(
        f"📥 Чек получен | ФИО={fio} | Телефон={phone} | file_id={file_id}"
    )

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Чек получен!\n"
        "Спасибо, данные сохранены."
    )

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("🚀 Запуск webhook")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=f"{BASE_URL}/webhook",
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
