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
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from io import BytesIO

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
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
PORT = int(os.getenv("PORT", 10000))

if not all([BOT_TOKEN, SPREADSHEET_ID, GOOGLE_CREDENTIALS_JSON, DRIVE_FOLDER_ID]):
    raise RuntimeError("❌ Не все ENV переменные заданы")

logger.info("✅ ENV OK")

# ================== GOOGLE AUTH ==================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDENTIALS_JSON),
    scopes=SCOPES,
)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sheet_users = gc.open_by_key(SPREADSHEET_ID).worksheet("Лист 1")
sheet_checks = gc.open_by_key(SPREADSHEET_ID).worksheet("Лист 2")

logger.info("📄 Google Sheets подключены")
logger.info("📁 Google Drive подключен")

# ================== STATES ==================
WAIT_PLOT, WAIT_FIO, WAIT_PHONE, WAIT_FILE = range(4)

# ================== HELPERS ==================
def find_user(telegram_id: int):
    for row in sheet_users.get_all_records():
        if str(row.get("Telegram_ID")) == str(telegram_id):
            return row
    return None

def upload_to_drive(file_bytes: bytes, filename: str, mime_type: str) -> str:
    media = MediaIoBaseUpload(BytesIO(file_bytes), mimetype=mime_type)
    file_metadata = {
        "name": filename,
        "parents": [DRIVE_FOLDER_ID],
    }

    file = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    return file["webViewLink"]

# ================== /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    db_user = find_user(user.id)

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
            "Мы вас не нашли в базе.\n"
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
        data["state"] = WAIT_PLOT
        await update.message.reply_text("🏡 Укажите номер участка:")
        return

    if not data.get("ФИО"):
        data["state"] = WAIT_FIO
        await update.message.reply_text("✍️ Укажите ФИО:")
        return

    if not data.get("Телефон"):
        data["state"] = WAIT_PHONE
        await update.message.reply_text(
            "📞 Укажите телефон\n"
            "Формат: +7XXXXXXXXXX\n"
            "Пример: +79261234567"
        )
        return

    data["state"] = WAIT_FILE
    await update.message.reply_text(
        "📎 Отправьте фото или PDF чека",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("❌ Отмена")]],
            resize_keyboard=True,
        ),
    )

# ================== TEXT ==================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data

    if text == "❌ Отмена":
        await start(update, context)
        return

    state = data.get("state")

    if state == WAIT_PLOT:
        data["Участок"] = text
        data["state"] = WAIT_FIO
        await update.message.reply_text("✍️ Укажите ФИО:")

    elif state == WAIT_FIO:
        data["ФИО"] = text
        data["state"] = WAIT_PHONE
        await update.message.reply_text(
            "📞 Укажите телефон\n"
            "Формат: +7XXXXXXXXXX\n"
            "Пример: +79261234567"
        )

    elif state == WAIT_PHONE:
        data["Телефон"] = text
        data["state"] = WAIT_FILE
        await update.message.reply_text("📎 Отправьте фото или PDF чека")

# ================== FILE ==================
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    if data.get("state") != WAIT_FILE:
        return

    user = update.effective_user

    if update.message.photo:
        file = update.message.photo[-1]
        mime = "image/jpeg"
        ext = "jpg"
    elif update.message.document:
        file = update.message.document
        mime = file.mime_type
        ext = "pdf"
    else:
        return

    file_unique_id = file.file_unique_id
    existing_ids = sheet_checks.col_values(11)

    if file_unique_id in existing_ids:
        await update.message.reply_text(
            "❌ Этот чек уже был загружен ранее.\n\n"
            "📎 Отправьте **другой чек** или новый файл."
        )
        return

    tg_file = await file.get_file()
    file_bytes = await tg_file.download_as_bytearray()

    filename = f"check_{user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    drive_link = upload_to_drive(file_bytes, filename, mime)

    row = [
        user.id,
        user.username or "",
        data.get("ФИО"),
        data.get("Участок"),
        data.get("Телефон"),
        drive_link,
        "",
        datetime.now().strftime("%Y-%m-%d"),
        "",
        "Нет",
        file_unique_id,
    ]

    sheet_checks.append_row(row, value_input_option="USER_ENTERED")

    await update.message.reply_text(
        f"✅ {data.get('ФИО')}, чек успешно сохранён!\n"
        "Спасибо 🙌",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("🚀 Начать")]],
            resize_keyboard=True,
        ),
    )

    context.user_data.clear()

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Начать$"), begin))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file))

    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}"

    logger.info("🚀 Webhook запущен")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url,
    )

if __name__ == "__main__":
    main()
