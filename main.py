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
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials

# -------------------- ЛОГИ --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SHEET_NAME = os.getenv("SHEET_NAME", "Лист 2")
PORT = int(os.getenv("PORT", 10000))

if not all([BOT_TOKEN, SPREADSHEET_ID, GOOGLE_CREDENTIALS_JSON]):
    raise RuntimeError("❌ Не все ENV переменные заданы")

logger.info("✅ ENV OK")

# -------------------- GOOGLE SHEETS --------------------
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)

spreadsheet = gc.open_by_key(SPREADSHEET_ID)
sheet = spreadsheet.worksheet(SHEET_NAME)

logger.info(f"📄 Подключен лист: {SHEET_NAME}")

# -------------------- СОСТОЯНИЯ --------------------
WAIT_FIO, WAIT_HOME, WAIT_PHONE = range(3)

# -------------------- /start --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("🚀 Начать")]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Здравствуйте! 👋\n"
        "Я помогу вам заполнить данные.\n\n"
        "Нажмите «Начать», чтобы продолжить.",
        reply_markup=keyboard,
    )

# -------------------- НАЧАТЬ --------------------
async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✍️ Пожалуйста, введите ФИО:")
    context.user_data["state"] = WAIT_FIO

# -------------------- ОБРАБОТКА ТЕКСТА --------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = update.message.text.strip()

    if state == WAIT_FIO:
        context.user_data["fio"] = text
        await update.message.reply_text("🏠 Укажите дом:")
        context.user_data["state"] = WAIT_HOME

    elif state == WAIT_HOME:
        context.user_data["home"] = text
        await update.message.reply_text(
            "📞 Укажите номер телефона\n"
            "Пример: +79261234567"
        )
        context.user_data["state"] = WAIT_PHONE

    elif state == WAIT_PHONE:
        context.user_data["phone"] = text

        user = update.effective_user
        fio = context.user_data["fio"]

        row = [
            user.id,                     # telegram_id
            user.username or "",         # username
            fio,                          # ФИО
            context.user_data["home"],   # Дом
            context.user_data["phone"],  # Телефон
            "",                           # Ссылка_на_чек
            "",                           # Сумма_по_чеку
            datetime.now().strftime("%Y-%m-%d"),  # Дата_чека
            "",                           # OCR
            "",                           # Дубль_чека
            "",                           # File_Unique_ID
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")

        await update.message.reply_text(
            f"✅ {fio}, спасибо!\n"
            "Ваши данные успешно сохранены.\n\n"
            "Если нужно — можно начать заново.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🚀 Начать")]],
                resize_keyboard=True,
            ),
        )

        context.user_data.clear()

# -------------------- WEBHOOK --------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Начать$"), begin))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}"

    logger.info("🚀 Запуск webhook")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="",
        webhook_url=webhook_url,
    )

# --------------------
if __name__ == "__main__":
    main()
