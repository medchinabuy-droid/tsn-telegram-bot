import os
import json
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials


# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"
SHEET_NAME = "Лист 1"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
]

# ================= ПРОВЕРКИ =================

if not BOT_TOKEN:
    raise RuntimeError("❌ Переменная BOT_TOKEN не задана в Render")

if not GOOGLE_CREDS_JSON:
    raise RuntimeError("❌ Переменная GOOGLE_CREDENTIALS_JSON не задана в Render")

# ================= ЛОГИ =================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logging.info("✅ Переменные окружения загружены")

# ================= GOOGLE SHEETS =================

try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
except json.JSONDecodeError:
    raise RuntimeError("❌ GOOGLE_CREDENTIALS_JSON — невалидный JSON")

creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=SCOPES
)

gc = gspread.authorize(creds)

sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

logging.info("📄 Подключение к Google Sheets успешно")

# ================= TELEGRAM HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот запущен и подключён к Google Таблице"
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)

    if not text:
        await update.message.reply_text("❌ Используй: /add текст")
        return

    sheet.append_row([text])
    await update.message.reply_text("✅ Записано в таблицу")


# ================= ЗАПУСК =================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))

    logging.info("🤖 Telegram-бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
