import os
import io
import json
import logging
from datetime import datetime

from telegram import Update
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

# ================== ЛОГИ ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "TSN")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not all([BOT_TOKEN, GOOGLE_JSON, SPREADSHEET_NAME, GOOGLE_DRIVE_FOLDER_ID, WEBHOOK_URL]):
    raise RuntimeError("❌ Не все ENV заданы")

logger.info("✅ ENV OK")

# ================== GOOGLE AUTH ==================
creds_dict = json.loads(GOOGLE_JSON)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)

gc = gspread.authorize(credentials)
spreadsheet = gc.open(SPREADSHEET_NAME)

# ВАЖНО: ИМЕНА С ПРОБЕЛАМИ
sheet_users = spreadsheet.worksheet("Лист 1")
sheet_checks = spreadsheet.worksheet("Лист 2")

drive_service = build("drive", "v3", credentials=credentials)

# ================== STATES ==================
ASK_FIO, ASK_HOUSE, ASK_PHONE, WAIT_CHECK = range(4)

# ================== HELPERS ==================
def find_user_by_telegram_id(tg_id: str):
    records = sheet_users.get_all_records()
    for r in records:
        if str(r.get("Telegram_ID")).strip() == tg_id:
            return r
    return None


def is_duplicate(file_unique_id: str) -> bool:
    ids = sheet_checks.col_values(11)  # File_Unique_ID
    return file_unique_id in ids


# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_id = str(user.id)

    record = find_user_by_telegram_id(tg_id)

    if record:
        context.user_data["fio"] = record.get("ФИО", "")
        context.user_data["house"] = record.get("Участок", "")
        context.user_data["phone"] = record.get("Телефон", "")

        await update.message.reply_text(
            f"👋 Привет, {context.user_data['fio']}!\n\n"
            f"Мы вас узнали.\n"
            f"🏠 Участок: {context.user_data['house']}\n"
            f"📞 Телефон: {context.user_data['phone']}\n\n"
            f"📸 Пожалуйста, отправьте фото или PDF чека."
        )
        return WAIT_CHECK

    await update.message.reply_text("Здравствуйте!\nВведите ваше ФИО:")
    return ASK_FIO


# ================== REGISTRATION ==================
async def ask_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["fio"] = update.message.text.strip()
    await update.message.reply_text("Введите номер участка:")
    return ASK_HOUSE


async def ask_house(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["house"] = update.message.text.strip()
    await update.message.reply_text(
        "Введите телефон в формате:\n"
        "+7926XXXXXXX\n\n"
        "❗ Обязательно с +7"
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()

    await update.message.reply_text(
        f"✅ Данные сохранены.\n"
        f"Спасибо, {context.user_data['fio']}!\n\n"
        f"📸 Теперь отправьте фото или PDF чека."
    )
    return WAIT_CHECK


# ================== CHECK HANDLER ==================
async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if message.photo:
        file = message.photo[-1]
    elif message.document:
        file = message.document
    else:
        await message.reply_text("❌ Отправьте фото или PDF файл.")
        return WAIT_CHECK

    file_id = file.file_id
    file_unique_id = file.file_unique_id

    duplicate = is_duplicate(file_unique_id)

    tg_file = await context.bot.get_file(file_id)
    file_bytes = await tg_file.download_as_bytearray()

    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype="application/octet-stream",
        resumable=False,
    )

    uploaded = drive_service.files().create(
        body={
            "name": f"check_{update.effective_user.id}_{file_unique_id}",
            "parents": [GOOGLE_DRIVE_FOLDER_ID],
        },
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    drive_link = uploaded["webViewLink"]

    sheet_checks.append_row([
        str(update.effective_user.id),
        update.effective_user.username,
        context.user_data.get("fio", ""),
        context.user_data.get("house", ""),
        context.user_data.get("phone", ""),
        drive_link,
        "",
        datetime.now().strftime("%Y-%m-%d"),
        "",
        "ДА" if duplicate else "НЕТ",
        file_unique_id,
    ])

    if duplicate:
        await message.reply_text(
            "⚠️ Этот чек уже был загружен ранее.\n"
            "Пожалуйста, отправьте другой чек."
        )
    else:
        await message.reply_text(
            "✅ Чек успешно принят и сохранён.\n"
            "Спасибо!"
        )

    return WAIT_CHECK


# ================== MAIN ==================
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_fio)],
            ASK_HOUSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_house)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            WAIT_CHECK: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_check)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    application.add_handler(conv)

    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )


if __name__ == "__main__":
    main()
