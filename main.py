import os
import json
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials


# ---------------- НАСТРОЙКИ ----------------

SPREADSHEET_NAME = "ИМЯ_ТАБЛИЦЫ"
SHEET_NAME = "Лист1"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ---------------- ЛОГИ ----------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ---------------- GOOGLE AUTH ----------------

creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])

creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sheet = gc.open(SPREADSHEET_NAME).worksheet(SHEET_NAME)

# ---------------- TELEGRAM ----------------

TOKEN = os.environ["TELEGRAM_TOKEN"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот запущен и подключён к Google Sheets"
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)

    if not text:
        await update.message.reply_text("❌ Напиши текст: /add что-то")
        return

    sheet.append_row([text])
    await update.message.reply_text("✅ Добавлено в таблицу")


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))

    print("🤖 Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
