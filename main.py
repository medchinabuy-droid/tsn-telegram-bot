import os
import logging
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
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
STATE_NONE = None
STATE_FIO = "fio"
STATE_PHONE = "phone"
STATE_CHECK = "check"

# ================= KEYBOARDS =================
def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📤 Отправить чек")],
            [KeyboardButton("ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "🤖 Бот запущен.\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard(),
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Инструкция:\n"
        "1️⃣ Нажмите «Отправить чек»\n"
        "2️⃣ Введите данные\n"
        "3️⃣ Отправьте фото чека",
        reply_markup=main_keyboard(),
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    state = context.user_data.get("state", STATE_NONE)

    # --- КНОПКИ ---
    if text == "📤 Отправить чек":
        context.user_data.clear()
        context.user_data["state"] = STATE_FIO

        await update.message.reply_text(
            "✍️ Введите ФИО:"
        )
        return

    if text == "ℹ️ Помощь":
        await help_cmd(update, context)
        return

    # --- СОСТОЯНИЯ ---
    if state == STATE_FIO:
        context.user_data["fio"] = text
        context.user_data["state"] = STATE_PHONE

        await update.message.reply_text(
            "📞 Введите номер телефона:"
        )
        return

    if state == STATE_PHONE:
        context.user_data["phone"] = text
        context.user_data["state"] = STATE_CHECK

        await update.message.reply_text(
            "📷 Отправьте фото чека:"
        )
        return

    # --- ЕСЛИ НЕПОНЯТНО ---
    await update.message.reply_text(
        "❗ Пожалуйста, используйте кнопки ниже.",
        reply_markup=main_keyboard(),
    )

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != STATE_CHECK:
        await update.message.reply_text(
            "❗ Сначала нажмите «Отправить чек».",
            reply_markup=main_keyboard(),
        )
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    fio = context.user_data.get("fio")
    phone = context.user_data.get("phone")

    logger.info(f"📥 ЧЕК | ФИО={fio} | ТЕЛ={phone} | file_id={file_id}")

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Чек принят!\nСпасибо.",
        reply_markup=main_keyboard(),
    )

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("🚀 Запуск webhook")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=f"{BASE_URL}/webhook",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
