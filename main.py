import os
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------------------------------------
# НАСТРОЙКИ
# -------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

ADMIN_USERNAMES = [
    u.strip().lower()
    for u in os.getenv("ADMIN_IDS", "").split(",")
    if u.strip()
]

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
DRIVE_FOLDER_NAME = "TSN_CHECKS"

# -------------------------------------------------
# ЛОГИ
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------
# GOOGLE API
# -------------------------------------------------
creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_req = sh.worksheet("Реквизиты")

drive_service = build("drive", "v3", credentials=creds)

# -------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ
# -------------------------------------------------
def get_drive_folder_id(name: str) -> str:
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    res = drive_service.files().list(q=q).execute().get("files", [])
    if res:
        return res[0]["id"]

    folder = drive_service.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
    ).execute()
    return folder["id"]


FOLDER_ID = get_drive_folder_id(DRIVE_FOLDER_NAME)


def is_admin(update: Update) -> bool:
    username = (update.effective_user.username or "").lower()
    return username in ADMIN_USERNAMES


def find_user_by_telegram_id(tg_id: int):
    rows = sheet_users.get_all_records()
    for r in rows:
        if str(r.get("Telegram_ID")) == str(tg_id):
            return r
    return None


def is_duplicate(file_unique_id: str) -> bool:
    ids = sheet_checks.col_values(11)  # File_Unique_ID
    return file_unique_id in ids


# -------------------------------------------------
# ХЕНДЛЕРЫ
# -------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("🚀 Начать")]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "👋 Добро пожаловать!\nНажмите «🚀 Начать», чтобы продолжить.",
        reply_markup=kb,
    )


async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    known = find_user_by_telegram_id(user.id)

    if known:
        await update.message.reply_text(
            f"👋 Здравствуйте, {known.get('ФИО')}!\n"
            "📎 Пожалуйста, прикрепите фото или PDF чека.\n\n"
            "ℹ️ Нажмите на 📎 (скрепку) и выберите файл."
        )
    else:
        await update.message.reply_text(
            "👋 Вас нет в базе.\n"
            "Пожалуйста, отправьте телефон.\n\n"
            "👉 Пример: +7 926 123-45-67"
        )
        context.user_data["wait_phone"] = True


async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_phone"):
        return

    phone = update.message.text.strip()
    context.user_data["phone"] = phone
    context.user_data["wait_phone"] = False

    await update.message.reply_text(
        "✅ Данные сохранены.\n"
        "📎 Теперь загрузите фото или PDF чека.\n\n"
        "ℹ️ Используйте значок 📎"
    )


async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user

    file = None
    file_unique_id = None

    if msg.photo:
        file = msg.photo[-1]
        file_unique_id = file.file_unique_id
    elif msg.document:
        file = msg.document
        file_unique_id = file.file_unique_id
    else:
        return

    duplicate = is_duplicate(file_unique_id)

    tg_file = await file.get_file()
    file_bytes = await tg_file.download_as_bytearray()

    media = MediaIoBaseUpload(
        fd=bytes(file_bytes),
        mimetype="application/octet-stream",
        resumable=False,
    )

    uploaded = drive_service.files().create(
        media_body=media,
        body={
            "name": f"{user.id}_{datetime.now().isoformat()}",
            "parents": [FOLDER_ID],
        },
    ).execute()

    link = f"https://drive.google.com/file/d/{uploaded['id']}"

    sheet_checks.append_row([
        user.id,
        user.username,
        "",
        "",
        context.user_data.get("phone", ""),
        link,
        "",
        datetime.now().strftime("%Y-%m-%d"),
        "",
        "ДА" if duplicate else "НЕТ",
        file_unique_id,
    ])

    await update.message.reply_text(
        "✅ Чек сохранён.\nСпасибо!"
    )


# -------------------------------------------------
# ЗАПУСК
# -------------------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Text("🚀 Начать"), begin))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=10000,
        webhook_url=WEBHOOK_URL,
    )


if __name__ == "__main__":
    main()
