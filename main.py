import os
import logging
from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")

PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================= GOOGLE =================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(
    "service_account.json", scopes=SCOPES
)
gc = gspread.authorize(creds)
sheet = gc.open(GOOGLE_SHEET_NAME).sheet2  # ЛИСТ 2

# ================= СОСТОЯНИЯ =================

(
    FIO,
    HOUSE,
    PHONE,
    CHECK_PHOTO,
    SUM,
    DATE,
) = range(6)

# ================= КЛАВИАТУРЫ =================

start_kb = ReplyKeyboardMarkup(
    [[KeyboardButton("📎 Отправить чек")]],
    resize_keyboard=True,
)

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Бот запущен ✅\n\nНажмите кнопку ниже, чтобы отправить чек.",
        reply_markup=start_kb,
    )


async def send_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ФИО:")
    return FIO


async def get_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["fio"] = update.message.text
    await update.message.reply_text("Введите номер дома:")
    return HOUSE


async def get_house(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["house"] = update.message.text
    await update.message.reply_text("Введите телефон:")
    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    await update.message.reply_text("Отправьте фото чека:")
    return CHECK_PHOTO


async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    context.user_data["file_unique_id"] = photo.file_unique_id

    file = await photo.get_file()
    context.user_data["file_url"] = file.file_path

    await update.message.reply_text("Введите сумму по чеку:")
    return SUM


async def get_sum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["sum"] = update.message.text
    await update.message.reply_text("Введите дату чека (например 05.01.2026):")
    return DATE


async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    date_text = update.message.text

    file_uid = context.user_data["file_unique_id"]
    existing = sheet.findall(file_uid)
    is_duplicate = "TRUE" if existing else "FALSE"

    row = [
        user.id,
        user.username or "",
        context.user_data["fio"],
        context.user_data["house"],
        context.user_data["phone"],
        context.user_data["file_url"],
        context.user_data["sum"],
        date_text,
        "",
        is_duplicate,
        file_uid,
    ]

    sheet.append_row(row)

    await update.message.reply_text(
        "✅ Чек принят и сохранён.\nСпасибо!",
        reply_markup=start_kb,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Операция отменена")
    return ConversationHandler.END

# ================= MAIN =================

def main():
    logger.info("✅ ENV OK")
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📎 Отправить чек$"), send_check)],
        states={
            FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_fio)],
            HOUSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_house)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            CHECK_PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
            SUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_sum)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    logger.info("🚀 Запуск webhook")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
    )


if __name__ == "__main__":
    main()
