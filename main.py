import os
import json
import logging
from datetime import datetime
from io import BytesIO

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ----------------- ЛОГИ -----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- ENV -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not all([BOT_TOKEN, WEBHOOK_URL, SPREADSHEET_ID, DRIVE_FOLDER_ID, CREDS_JSON]):
    raise RuntimeError("❌ Не все ENV заданы")

logger.info("✅ ENV OK")

# ----------------- GOOGLE -----------------
creds_dict = json.loads(CREDS_JSON)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)

gc = gspread.authorize(credentials)
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
sheet1 = spreadsheet.worksheet("Лист 1")
sheet2 = spreadsheet.worksheet("Лист 2")

drive = build("drive", "v3", credentials=credentials)

logger.info("📄 Google Sheets подключены")
logger.info("📁 Google Drive подключен")

# ----------------- STATES -----------------
FIO, PHONE, CHECK = range(3)

# ----------------- HELPERS -----------------
def find_user_row(telegram_id):
    rows = sheet1.get_all_records()
    for idx, row in enumerate(rows, start=2):
        if str(row.get("Telegram_ID")) == str(telegram_id):
            return idx, row
    return None, None


def is_duplicate(file_unique_id):
    ids = sheet2.col_values(11)  # File_Unique_ID
    return file_unique_id in ids


def upload_to_drive(filename, content, mime):
    media = MediaIoBaseUpload(BytesIO(content), mimetype=mime)
    body = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
    file = drive.files().create(
        body=body,
        media_body=media,
        fields="id, webViewLink"
    ).execute()
    logger.info(f"📤 Загружен файл: {file['webViewLink']}")
    return file["webViewLink"]


# ----------------- HANDLERS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    row_num, row = find_user_row(tg.id)

    if row:
        context.user_data["fio"] = row.get("ФИО")
        text = f"👋 Здравствуйте, {row.get('ФИО')}!\n\nПришлите фото или PDF чека."
        return CHECK
    else:
        await update.message.reply_text("Введите ФИО:")
        return FIO


async def get_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["fio"] = update.message.text.strip()
    await update.message.reply_text(
        "Введите телефон\nПример: +79261234567"
    )
    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()

    sheet1.append_row([
        "", context.user_data["fio"], update.effective_user.id,
        context.user_data["phone"], "", "", "", "", "Новый", "Житель",
        "", "Telegram", "", "", "Telegram"
    ])

    await update.message.reply_text(
        f"✅ Данные сохранены, {context.user_data['fio']}!\n\nТеперь отправьте чек (фото или PDF)."
    )
    return CHECK


async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    tg = update.effective_user

    if message.photo:
        file = message.photo[-1]
        mime = "image/jpeg"
        filename = f"check_{tg.id}_{file.file_unique_id}.jpg"
    elif message.document:
        file = message.document
        mime = file.mime_type
        filename = file.file_name
    else:
        await message.reply_text("❌ Отправьте фото или PDF.")
        return CHECK

    if is_duplicate(file.file_unique_id):
        await message.reply_text(
            "⚠️ Этот чек уже был загружен ранее.\n\n📎 Отправьте другой чек."
        )
        return CHECK

    tg_file = await context.bot.get_file(file.file_id)
    content = await tg_file.download_as_bytearray()

    link = upload_to_drive(filename, content, mime)

    sheet2.append_row([
        tg.id,
        tg.username,
        context.user_data.get("fio"),
        "",
        context.user_data.get("phone"),
        link,
        "",
        datetime.now().strftime("%d.%m.%Y"),
        "",
        "Нет",
        file.file_unique_id,
        "",
    ])

    await message.reply_text(
        "✅ Чек принят и сохранён.\nСпасибо!"
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено")
    return ConversationHandler.END


# ----------------- APP -----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_fio)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            CHECK: [MessageHandler(filters.ALL, handle_check)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )


if __name__ == "__main__":
    main()
