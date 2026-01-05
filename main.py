import os
import json
import logging
from datetime import datetime
from io import BytesIO

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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not all([BOT_TOKEN, SPREADSHEET_ID, DRIVE_FOLDER_ID, GOOGLE_CREDENTIALS_JSON]):
    raise RuntimeError("❌ Не заданы переменные окружения")

logger.info("✅ ENV OK")

# -------------------- GOOGLE AUTH --------------------
creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)

# Sheets
gc = gspread.authorize(credentials)
spreadsheet = gc.open_by_key(SPREADSHEET_ID)
sheet2 = spreadsheet.worksheet("Лист 2")
logger.info("📄 Google Sheets подключены")

# Drive
drive_service = build("drive", "v3", credentials=credentials)
logger.info("📁 Google Drive подключен")

# -------------------- HELPERS --------------------
def is_duplicate(file_unique_id: str) -> bool:
    col = sheet2.col_values(11)  # File_Unique_ID
    return file_unique_id in col

def save_to_drive(file_bytes: bytes, filename: str, mime_type: str) -> str:
    media = MediaIoBaseUpload(BytesIO(file_bytes), mimetype=mime_type, resumable=False)
    file_metadata = {
        "name": filename,
        "parents": [DRIVE_FOLDER_ID],
    }
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return file["webViewLink"]

def append_row(data: dict):
    sheet2.append_row([
        data.get("telegram_id"),
        data.get("username"),
        data.get("fio"),
        data.get("house"),
        data.get("phone"),
        data.get("file_link"),
        "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
        data.get("is_duplicate"),
        data.get("file_unique_id"),
    ])

# -------------------- HANDLERS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Здравствуйте!\n\n"
        "📎 Пожалуйста, отправьте фото или PDF чека.\n"
        "Я проверю его и сохраню в базе."
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    file = None
    mime_type = None

    if message.photo:
        file = message.photo[-1]
        mime_type = "image/jpeg"
    elif message.document:
        file = message.document
        mime_type = file.mime_type
    else:
        await message.reply_text(
            "❌ Поддерживаются только фото или PDF.\n"
            "Пожалуйста, отправьте чек файлом."
        )
        return

    file_unique_id = file.file_unique_id

    # -------- DUPLICATE CHECK --------
    if is_duplicate(file_unique_id):
        await message.reply_text(
            "❌ Этот чек уже был загружен ранее.\n\n"
            "📎 Отправьте **другой чек** или новый файл.\n"
            "Если у вас новый чек — просто прикрепите новый файл."
        )
        return  # ❗ важно: но бот остаётся ждать новый файл

    await message.reply_text("📤 Чек принят. Загружаю файл на Google Drive...")

    tg_file = await context.bot.get_file(file.file_id)
    file_bytes = await tg_file.download_as_bytearray()

    filename = f"check_{update.effective_user.id}_{int(datetime.now().timestamp())}"

    link = save_to_drive(
        file_bytes=file_bytes,
        filename=filename,
        mime_type=mime_type,
    )

    append_row({
        "telegram_id": update.effective_user.id,
        "username": update.effective_user.username,
        "fio": "",
        "house": "",
        "phone": "",
        "file_link": link,
        "file_unique_id": file_unique_id,
        "is_duplicate": "нет",
    })

    await message.reply_text(
        "✅ Чек успешно сохранён!\n\n"
        f"🔗 Ссылка на файл:\n{link}\n\n"
        "Спасибо! Если нужно загрузить ещё один чек — просто отправьте его."
    )

# -------------------- APP --------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, handle_file))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=os.environ.get("RENDER_EXTERNAL_URL"),
    )

if __name__ == "__main__":
    main()
