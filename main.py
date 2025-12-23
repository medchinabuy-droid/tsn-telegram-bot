import os
import json
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials


# ================= НАСТРОЙКИ =================

TOKEN = os.environ.get("TELEGRAM_TOKEN")

GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"
SHEET_NAME = "Лист1"  # название вкладки

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
]

# ================= ЛОГИ =================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ================= GOOGLE SHEETS =================

creds_dict = json.loads(GOOGLE_CREDS_JSON)

creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=SCOPES
)

gc = gspread.authorize(creds)

sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)


# ================= TELEGRAM HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот активирован и подключён к таблице!"
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)

    if not text:
        await update.message.reply_text("❌ Используй так: /add текст")
        return

    sheet.append_row([text])
    await update.message.reply_text("✅ Записано в таблицу!")


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))

    logging.info("🤖 Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
