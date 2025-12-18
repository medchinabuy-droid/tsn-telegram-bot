import os
import json
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, ContextTypes
import gspread
from google.oauth2.service_account import Credentials

# ========= НАСТРОЙКИ =========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_NAME = "НАЗВАНИЕ_ТВОЕЙ_ТАБЛИЦЫ"
CREDENTIALS_FILE = "telegram-bot-481523-05b49bfbb763.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ========= GOOGLE SHEETS =========
creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sheet = gc.open(SPREADSHEET_NAME).sheet1

# ========= TELEGRAM =========
bot = Bot(token=BOT_TOKEN)

# ========= FLASK =========
app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    return "Bot is running"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.json, bot)

    if update.message:
        user = update.message.from_user
        text = update.message.text or ""

        telegram_id = user.id
        username = user.username or ""
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()

        # ИЩЕМ ПО TELEGRAM_ID
        records = sheet.get_all_records()
        row_number = None

        for i, row in enumerate(records, start=2):
            if str(row.get("Telegram_ID")) == str(telegram_id):
                row_number = i
                break

        if not row_number:
            sheet.append_row([
                "",               # Участок
                full_name,        # ФИО
                telegram_id,      # Telegram_ID
                "", "", "", "", ""
            ])
            bot.send_message(
                chat_id=telegram_id,
                text="Вы зарегистрированы в системе."
            )
        else:
            bot.send_message(
                chat_id=telegram_id,
                text="Вы уже есть в системе."
            )

    return "ok"

# ========= ЗАПУСК =========
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
