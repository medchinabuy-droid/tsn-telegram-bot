import os
import json
import logging

from flask import Flask, request, abort
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

import gspread
from google.oauth2.service_account import Credentials

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"
SHEET_NAME = "Лист 1"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ================= ПРОВЕРКИ =================

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не задан")

if not GOOGLE_CREDS_JSON:
    raise RuntimeError("❌ GOOGLE_CREDENTIALS_JSON не задан")

if not WEBHOOK_SECRET:
    raise RuntimeError("❌ WEBHOOK_SECRET не задан")

# ================= ЛОГИ =================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logging.info("✅ Переменные окружения загружены")

# ================= GOOGLE SHEETS =================

creds_dict = json.loads(GOOGLE_CREDS_JSON)

creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

logging.info("📄 Подключение к Google Sheets успешно")

# ================= TELEGRAM =================

app_tg = ApplicationBuilder().token(BOT_TOKEN).build()

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот работает через webhook"
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("❌ Используй: /add текст")
        return

    sheet.append_row([text])
    await update.message.reply_text("✅ Записано")

app_tg.add_handler(CommandHandler("start", start))
app_tg.add_handler(CommandHandler("add", add))

# ================= FLASK =================

flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return "OK", 200

@flask_app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
async def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)

    update = Update.de_json(request.get_json(force=True), app_tg.bot)
    await app_tg.process_update(update)
    return "OK", 200

# ================= ЗАПУСК =================

async def setup_webhook():
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_url:
        raise RuntimeError("❌ RENDER_EXTERNAL_URL не найден")

    webhook_url = f"{render_url}/webhook/{WEBHOOK_SECRET}"
    await app_tg.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET
    )
    logging.info(f"🔗 Webhook установлен: {webhook_url}")

if __name__ == "__main__":
    import asyncio

    asyncio.run(setup_webhook())

    flask_app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
