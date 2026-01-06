import os
import json
import logging
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
import io

# ================== ЛОГИ ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # ВАЖНО: ID, не имя
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # может быть пустым
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN or not GOOGLE_CREDENTIALS_JSON or not SPREADSHEET_ID:
    raise RuntimeError("❌ BOT_TOKEN / GOOGLE_CREDENTIALS_JSON / SPREADSHEET_ID обязательны")

logger.info("✅ ENV OK")

# ================== GOOGLE AUTH ==================
creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

gc = gspread.authorize(credentials)
spreadsheet = gc.open_by_key(SPREADSHEET_ID)

sheet_users = spreadsheet.worksheet("Лист1")
sheet_checks = spreadsheet.worksheet("Лист 2")

logger.info("📄 Google Sheets подключены")

drive_service = build("drive", "v3", credentials=credentials)
logger.info("📁 Google Drive подключен")

# ================== DRIVE ==================
DRIVE_FOLDER_NAME = "TSN_CHECKS"

def get_or_create_folder(name: str) -> str:
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id)").execute()
    if res["files"]:
        return res["files"][0]["id"]

    folder = drive_service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id"
    ).execute()
    return folder["id"]

FOLDER_ID = get_or_create_folder(DRIVE_FOLDER_NAME)

# ================== HELPERS ==================
def find_user_row(telegram_id: int):
    rows = sheet_users.get_all_records()
    for i, row in enumerate(rows, start=2):
        if str(row.get("Telegram_ID")) == str(telegram_id):
            return i, row
    return None, None

def check_duplicate(file_unique_id: str) -> bool:
    values = sheet_checks.col_values(1)
    return file_unique_id in values

def save_check(file_unique_id, user_id, fio, phone, file_url):
    sheet_checks.append_row([
        file_unique_id,
        user_id,
        fio,
        phone,
        file_url,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ])

# ================== BOT HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    row_num, user = find_user_row(tg_id)

    if user:
        fio = user.get("ФИО", "")
        phone = user.get("Телефон", "")
        text = f"👋 Привет, {fio}!\n"
        if not phone:
            text += "📞 Укажи телефон в формате +7926..."
        else:
            text += "📸 Пришли фото или PDF чека"
    else:
        text = (
            "👋 Привет!\n"
            "Я не нашёл тебя в базе.\n\n"
            "📞 Укажи телефон в формате +7926..."
        )

    await update.message.reply_text(text)

async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+7"):
        await update.message.reply_text("❌ Формат неверный. Пример: +79261234567")
        return

    context.user_data["phone"] = phone
    await update.message.reply_text("📸 Теперь пришли фото или PDF чека")

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    tg_id = update.effective_user.id

    if message.photo:
        file = message.photo[-1]
    elif message.document:
        file = message.document
    else:
        return

    file_unique_id = file.file_unique_id

    if check_duplicate(file_unique_id):
        await message.reply_text("⚠️ Этот чек уже был загружен")
        return

    tg_file = await context.bot.get_file(file.file_id)
    file_bytes = await tg_file.download_as_bytearray()

    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype="application/octet-stream",
        resumable=False,
    )

    drive_file = drive_service.files().create(
        media_body=media,
        body={
            "name": f"{file_unique_id}",
            "parents": [FOLDER_ID],
        },
        fields="id, webViewLink",
    ).execute()

    row_num, user = find_user_row(tg_id)
    fio = user.get("ФИО", "") if user else ""
    phone = user.get("Телефон", "") if user else context.user_data.get("phone", "")

    save_check(
        file_unique_id,
        tg_id,
        fio,
        phone,
        drive_file["webViewLink"],
    )

    await message.reply_text(
        f"✅ Данные сохранены.\nСпасибо!\n\n🔗 {drive_file['webViewLink']}"
    )

# ================== APP ==================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))

    if WEBHOOK_URL:
        logger.info("🚀 WEBHOOK MODE")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL,
        )
    else:
        logger.warning("⚠️ POLLING MODE — webhook удаляется")
        app.bot.delete_webhook(drop_pending_updates=True)
        app.run_polling()

if __name__ == "__main__":
    main()
