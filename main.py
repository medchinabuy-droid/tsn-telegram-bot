import os
import json
import logging
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
PORT = int(os.getenv("PORT", 10000))

if not all([BOT_TOKEN, SPREADSHEET_ID, GOOGLE_CREDENTIALS_JSON]):
    raise RuntimeError("❌ Не все ENV переменные заданы")

logger.info("✅ ENV OK")

# ================== GOOGLE ==================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDENTIALS_JSON),
    scopes=SCOPES,
)
gc = gspread.authorize(creds)

sheet_users = gc.open_by_key(SPREADSHEET_ID).worksheet("Лист 1")
sheet_checks = gc.open_by_key(SPREADSHEET_ID).worksheet("Лист 2")

logger.info("📄 Google Sheets подключены")

# ================== STATES ==================
WAIT_PLOT, WAIT_FIO, WAIT_PHONE, WAIT_PHOTO = range(4)

# ================== HELPERS ==================
def find_user(telegram_id: int):
    users = sheet_users.get_all_records()
    for u in users:
        if str(u.get("Telegram_ID")) == str(telegram_id):
            return u
    return None

# ================== /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = find_user(user.id)
    context.user_data.clear()

    if db_user:
        context.user_data.update(db_user)
        await update.message.reply_text(
            f"👋 Здравствуйте, {db_user.get('ФИО')}!\n"
            "Мы вас узнали ✅\n\n"
            "Нажмите «🚀 Начать», чтобы загрузить чек.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🚀 Начать")]],
                resize_keyboard=True,
            ),
        )
    else:
        await update.message.reply_text(
            "👋 Здравствуйте!\n"
            "Мы вас пока не нашли в базе.\n"
            "Давайте заполним данные.\n\n"
            "Нажмите «🚀 Начать».",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🚀 Начать")]],
                resize_keyboard=True,
            ),
        )

# ================== BEGIN ==================
async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data

    if not data.get("Участок"):
        await update.message.reply_text("🏡 Укажите номер участка:")
        data["state"] = WAIT_PLOT
        return

    if not data.get("ФИО"):
        await update.message.reply_text("✍️ Укажите ФИО:")
        data["state"] = WAIT_FIO
        return

    if not data.get("Телефон"):
        await update.message.reply_text(
            "📞 Укажите номер телефона\n"
            "Формат: +7XXXXXXXXXX\n"
            "Пример: +79261234567"
        )
        data["state"] = WAIT_PHONE
        return

    await update.message.reply_text(
        "📸 Отправьте фото чека",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("❌ Отмена")]],
            resize_keyboard=True,
        ),
    )
    data["state"] = WAIT_PHOTO

# ================== TEXT ==================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data
    state = data.get("state")

    if text == "❌ Отмена":
        await start(update, context)
        return

    if state == WAIT_PLOT:
        data["Участок"] = text
        await update.message.reply_text("✍️ Укажите ФИО:")
        data["state"] = WAIT_FIO

    elif state == WAIT_FIO:
        data["ФИО"] = text
        await update.message.reply_text(
            "📞 Укажите номер телефона\n"
            "Формат: +7XXXXXXXXXX\n"
            "Пример: +79261234567"
        )
        data["state"] = WAIT_PHONE

    elif state == WAIT_PHONE:
        data["Телефон"] = text
        await update.message.reply_text("📸 Отправьте фото чека")
        data["state"] = WAIT_PHOTO

# ================== PHOTO ==================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    if data.get("state") != WAIT_PHOTO:
        return

    photo = update.message.photo[-1]
    file_unique_id = photo.file_unique_id

    existing_ids = sheet_checks.col_values(11)

    if file_unique_id in existing_ids:
        await update.message.reply_text(
            "❌ Этот чек уже был загружен ранее.\n\n"
            "📸 Чтобы загрузить **другой чек**, просто отправьте новое фото.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🚀 Начать")]],
                resize_keyboard=True,
            ),
        )
        data["state"] = WAIT_PHOTO
        return

    user = update.effective_user

    row = [
        user.id,
        user.username or "",
        data.get("ФИО"),
        data.get("Участок"),
        data.get("Телефон"),
        "",
        "",
        datetime.now().strftime("%Y-%m-%d"),
        "",
        "Нет",
        file_unique_id,
    ]

    sheet_checks.append_row(row, value_input_option="USER_ENTERED")

    await update.message.reply_text(
        f"✅ {data.get('ФИО')}, данные сохранены.\nСпасибо!",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("🚀 Начать")]],
            resize_keyboard=True,
        ),
    )

    context.user_data.clear()

# ================== WEBHOOK ==================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Начать$"), begin))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}"

    logger.info("🚀 Webhook запущен")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url,
    )

if __name__ == "__main__":
    main()
