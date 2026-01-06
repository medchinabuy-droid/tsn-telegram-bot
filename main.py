import os
import json
import logging
from io import BytesIO
from datetime import datetime

from telegram import Update
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

# -------------------- LOGGING --------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not all([BOT_TOKEN, WEBHOOK_SECRET, RENDER_URL, CREDS_JSON]):
    raise RuntimeError("❌ ENV переменные не заданы")

logger.info("✅ ENV OK")

# -------------------- GOOGLE --------------------
creds = Credentials.from_service_account_info(
    json.loads(CREDS_JSON),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
)

gc = gspread.authorize(creds)
spreadsheet = gc.open("TSN")
sheet_users = spreadsheet.worksheet("Лист 1")
sheet_checks = spreadsheet.worksheet("Лист 2")

drive = build("drive", "v3", credentials=creds)

logger.info("📄 Google Sheets подключены")
logger.info("📁 Google Drive подключен")

# -------------------- HELPERS --------------------
def find_user(telegram_id: int):
    rows = sheet_users.get_all_records()
    for r in rows:
        if str(r.get("Telegram_ID")) == str(telegram_id):
            return r
    return None


def is_duplicate(file_unique_id: str) -> bool:
    col = sheet_checks.col_values(11)
    return file_unique_id in col


def upload_to_drive(filename: str, file_bytes: bytes) -> str:
    media = MediaIoBaseUpload(BytesIO(file_bytes), resumable=True)
    file = drive.files().create(
        body={"name": filename},
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return file["webViewLink"]

# -------------------- HANDLERS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    user = find_user(tg_id)

    if user:
        await update.message.reply_text(
            f"👋 Здравствуйте, {user['ФИО']}!\n\n"
            "📎 Пожалуйста, отправьте фото или PDF чека."
        )
    else:
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\n"
            "📎 Пожалуйста, отправьте фото или PDF чека."
        )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    tg_id = msg.from_user.id
    username = msg.from_user.username or ""

    await msg.reply_text("⏳ Чек получен. Проверяю...")

    if msg.photo:
        file = msg.photo[-1]
        ext = "jpg"
    elif msg.document:
        file = msg.document
        ext = msg.document.file_name.split(".")[-1]
    else:
        await msg.reply_text("❌ Это не файл и не фото.")
        return

    if is_duplicate(file.file_unique_id):
        await msg.reply_text(
            "⚠️ Этот чек уже был загружен ранее.\n\n"
            "📎 Отправьте **другой чек**."
        )
        return

    try:
        tg_file = await file.get_file()
        bio = BytesIO()
        await tg_file.download_to_memory(out=bio)
        bio.seek(0)

        await msg.reply_text("☁️ Загружаю чек в Google Drive...")

        link = upload_to_drive(
            f"check_{tg_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}",
            bio.read(),
        )

        user = find_user(tg_id)
        fio = user["ФИО"] if user else ""

        sheet_checks.append_row([
            tg_id,
            username,
            fio,
            "",
            user.get("Телефон", "") if user else "",
            link,
            "",
            datetime.now().strftime("%d.%m.%Y"),
            "",
            "Нет",
            file.file_unique_id,
        ])

        await msg.reply_text(
            "✅ Чек успешно сохранён!\n\n"
            "📂 Файл загружен на Google Drive\n"
            "📄 Данные записаны в таблицу\n\n"
            "Спасибо!"
        )

    except Exception as e:
        logger.exception("Ошибка при обработке файла")
        await msg.reply_text(
            "❌ Произошла ошибка при обработке чека.\n"
            "Попробуйте отправить файл ещё раз."
        )

# -------------------- APP --------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file))

    app.run_webhook(
        listen="0.0.0.0",
        port=10000,
        secret_token=WEBHOOK_SECRET,
        webhook_url=f"{RENDER_URL}/{WEBHOOK_SECRET}",
    )

    logger.info("🚀 Webhook запущен")

if __name__ == "__main__":
    main()
