import os
import json
import logging
from datetime import datetime
from io import BytesIO

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------- НАСТРОЙКИ --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

ADMIN_USERNAMES = [
    u.strip().lower()
    for u in os.getenv("ADMIN_IDS", "").split(",")
    if u.strip()
]

# -------------------- ЛОГИ --------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- GOOGLE --------------------
creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")

drive = build("drive", "v3", credentials=creds)

# -------------------- DRIVE --------------------
def get_folder_id(name):
    res = drive.files().list(
        q=f"name='{name}' and mimeType='application/vnd.google-apps.folder'",
        fields="files(id)"
    ).execute().get("files", [])
    if res:
        return res[0]["id"]

    folder = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"}
    ).execute()
    return folder["id"]

FOLDER_ID = get_folder_id("TSN_CHECKS")

# -------------------- ПОИСК ПОЛЬЗОВАТЕЛЯ --------------------
def find_user(tg_id, phone=None):
    rows = sheet_users.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return r, i
        if phone and phone == str(r.get("Телефон")):
            sheet_users.update_cell(i, 3, tg_id)  # Telegram_ID
            return r, i
    return None, None

# -------------------- START --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("🚀 Начать")]],
        resize_keyboard=True
    )
    await update.message.reply_text(
        "👋 Добро пожаловать!\nНажмите «🚀 Начать»",
        reply_markup=kb
    )

# -------------------- BEGIN --------------------
async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📱 Введите номер телефона\n\n"
        "👉 Пример: +7 926 123-45-67"
    )
    context.user_data["wait_phone"] = True

# -------------------- PHONE --------------------
async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_phone"):
        return

    phone = update.message.text.strip()
    user = update.effective_user

    data, _ = find_user(user.id, phone)
    context.user_data["wait_phone"] = False

    if data:
        await update.message.reply_text(
            f"👋 Здравствуйте, {data.get('ФИО')}!\n"
            f"🏠 Участок: {data.get('Участок')}\n\n"
            "📎 Прикрепите фото или PDF чека\n"
            "ℹ️ Нажмите на скрепку 📎"
        )
    else:
        await update.message.reply_text(
            "❌ Телефон не найден в базе.\n"
            "Обратитесь к администратору."
        )

# -------------------- FILE --------------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user

    file = msg.photo[-1] if msg.photo else msg.document
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    media = MediaIoBaseUpload(
        BytesIO(data),
        mimetype="application/octet-stream"
    )

    uploaded = drive.files().create(
        media_body=media,
        body={
            "name": f"{user.id}_{datetime.now().isoformat()}",
            "parents": [FOLDER_ID]
        }
    ).execute()

    link = f"https://drive.google.com/file/d/{uploaded['id']}"

    sheet_checks.append_row([
        user.id,
        user.username,
        "",
        "",
        "",
        link,
        "",
        datetime.now().strftime("%Y-%m-%d"),
        "",
        "",
        file.file_unique_id
    ])

    await update.message.reply_text("✅ Чек принят. Спасибо!")

# -------------------- MAIN --------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Text("🚀 Начать"), begin))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=10000,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
