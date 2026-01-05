import os
import json
import logging
from datetime import datetime

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
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

# ================= ЛОГИ =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Render автоматически
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")

if not BOT_TOKEN or not SPREADSHEET_ID or not GOOGLE_CREDENTIALS_JSON:
    raise RuntimeError("❌ Не все ENV переменные заданы (BOT_TOKEN / SPREADSHEET_ID / GOOGLE_CREDENTIALS_JSON)")

logger.info("✅ ENV OK")

# ================= GOOGLE SHEETS =================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).worksheet("Лист2")

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\nНажмите кнопку ниже, чтобы отправить чек.",
        reply_markup=keyboard,
    )

async def handle_check_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✍️ Введите ваше ФИО:")
    context.user_data["step"] = "fio"

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step")

    if step == "fio":
        context.user_data["fio"] = update.message.text
        await update.message.reply_text("🏠 Введите номер дома:")
        context.user_data["step"] = "house"

    elif step == "house":
        context.user_data["house"] = update.message.text
        await update.message.reply_text("📞 Введите телефон:")
        context.user_data["step"] = "phone"

    elif step == "phone":
        context.user_data["phone"] = update.message.text

        sheet.append_row([
            update.effective_user.id,
            update.effective_user.username,
            context.user_data["fio"],
            context.user_data["house"],
            context.user_data["phone"],
            "", "", datetime.now().strftime("%Y-%m-%d"), "", "", ""
        ])

        await update.message.reply_text("✅ Данные сохранены. Спасибо!")
        context.user_data.clear()

# ================= APP =================
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📤 Отправить чек$"), handle_check_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    return app

# ================= ENTRY =================
if __name__ == "__main__":
    app = build_app()

    logger.info("🚀 Запуск webhook")

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        url_path="",
        webhook_url=WEBHOOK_URL,
    )
