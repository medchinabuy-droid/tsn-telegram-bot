import os
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)
from google.oauth2.service_account import Credentials
import gspread

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

CREDENTIALS_FILE = "telegram-bot-481523-b9c69d6d59fb.json"

SPREADSHEET_NAME = "ВАШЕ_НАЗВАНИЕ_ТАБЛИЦЫ"  # ← замени
SHEET_NAME = "Лист1"                        # ← или другое имя

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PORT = int(os.environ.get("PORT", 10000))

# ================= GOOGLE SHEETS =================

creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sheet = gc.open(SPREADSHEET_NAME).worksheet(SHEET_NAME)

# ================= TELEGRAM =================

app_tg = ApplicationBuilder().token(BOT_TOKEN).build()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    tg_id = user.id
    username = user.username or ""
    phone = user.phone_number if hasattr(user, "phone_number") else ""

    rows = sheet.get_all_values()

    for i, row in enumerate(rows[1:], start=2):
        if len(row) < 3:
            continue

        if str(row[2]) == str(tg_id) or row[2] == f"@{username}":
            sheet.update(f"C{i}", [[tg_id]])
            await update.message.reply_text("✅ Вы успешно привязаны")
            return

    await update.message.reply_text(
        "❗ Вас нет в таблице. Обратитесь к администратору."
    )

app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ================= FLASK (WEBHOOK) =================

flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return "Bot is running", 200

@flask_app.route("/webhook", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, app_tg.bot)
    await app_tg.process_update(update)
    return "ok", 200

# ================= ЗАПУСК =================

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=PORT)
