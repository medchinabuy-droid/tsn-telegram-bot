import os
import json
import logging
import threading

from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import gspread
from google.oauth2.service_account import Credentials


# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"
SHEET_NAME = "Лист 1"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
]

PORT = int(os.environ.get("PORT", 10000))


# ================= ЛОГИ =================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# ================= GOOGLE SHEETS =================

creds_dict = json.loads(GOOGLE_CREDS_JSON)

creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

logger.info("📄 Подключение к Google Sheets успешно")


# ================= TELEGRAM =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущен и работает")

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)

    if not text:
        await update.message.reply_text("❌ Используй: /add текст")
        return

    sheet.append_row([text])
    await update.message.reply_text("✅ Записано в таблицу!")


def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))

    logger.info("🤖 Telegram-бот запущен")
    app.run_polling()


# ================= FLASK (ДЛЯ RENDER) =================

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "OK", 200


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)


# ================= MAIN =================

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    run_flask()
