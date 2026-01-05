import os
import json
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
    ConversationHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials

# ================= ЛОГИ =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://tsn-telegram-bot.onrender.com
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not all([BOT_TOKEN, WEBHOOK_URL, SPREADSHEET_ID, GOOGLE_CREDENTIALS_JSON]):
    raise RuntimeError("❌ Не все ENV переменные заданы")

logger.info("✅ ENV OK")

# ================= GOOGLE SHEETS =================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

service_info = json.loads(GOOGLE_CREDENTIALS_JSON)
creds = Credentials.from_service_account_info(service_info, scopes=SCOPES)
gc = gspread.authorize(creds)

sheet = gc.open_by_key(SPREADSHEET_ID).get_worksheet(1)  # ЛИСТ 2

# ================= STATES =================
(
    WAIT_FIO,
    WAIT_HOME,
    WAIT_PHONE,
    WAIT_RECEIPT,
) = range(4)

# ================= КНОПКИ =================
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📤 Отправить чек")]],
    resize_keyboard=True,
)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Бот запущен.\n\nНажмите кнопку ниже, чтобы отправить чек.",
        reply_markup=MAIN_KEYBOARD,
    )

async def start_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Введите ФИО:")
    return WAIT_FIO

async def get_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["fio"] = update.message.text.strip()
    await update.message.reply_text("Введите номер дома:")
    return WAIT_HOME

async def get_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["home"] = update.message.text.strip()
    await update.message.reply_text("Введите телефон:")
    return WAIT_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text("Отправьте фото чека 📸")
    return WAIT_RECEIPT

async def get_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]

    file_id = photo.file_id
    file_unique_id = photo.file_unique_id

    user = update.effective_user

    row = [
        user.id,                          # telegram_id
        user.username or "",              # username
        context.user_data.get("fio", ""), # ФИО
        context.user_data.get("home", ""),# Дом
        context.user_data.get("phone", ""),# Телефон
        file_id,                          # Ссылка_на_чек
        "",                               # Сумма_по_чеку
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        "",                               # OCR
        "",                               # Дубль_чека
        file_unique_id,                   # File_Unique_ID
    ]

    sheet.append_row(row, value_input_option="USER_ENTERED")

    await update.message.reply_text(
        "✅ Чек принят и сохранён.\nСпасибо!",
        reply_markup=MAIN_KEYBOARD,
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Отменено",
        reply_markup=MAIN_KEYBOARD,
    )
    return ConversationHandler.END

# ================= APP =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex("^📤 Отправить чек$"), start_receipt)
        ],
        states={
            WAIT_FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_fio)],
            WAIT_HOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_home)],
            WAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            WAIT_RECEIPT: [MessageHandler(filters.PHOTO, get_receipt)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    logger.info("🚀 Запуск webhook")

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
        drop_pending_updates=True,
    )

# ================= ENTRY =================
if __name__ == "__main__":
    main()
