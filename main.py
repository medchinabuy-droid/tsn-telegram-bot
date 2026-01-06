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

# ================= ЛОГИ =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= ENV (БЕЗ ПАДЕНИЯ) =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "TSN")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))

missing_env = []
if not BOT_TOKEN: missing_env.append("BOT_TOKEN")
if not GOOGLE_JSON: missing_env.append("GOOGLE_SERVICE_ACCOUNT_JSON")
if not GOOGLE_DRIVE_FOLDER_ID: missing_env.append("GOOGLE_DRIVE_FOLDER_ID")
if not WEBHOOK_URL: missing_env.append("WEBHOOK_URL")

if missing_env:
    logger.error(f"❌ Отсутствуют ENV: {', '.join(missing_env)}")
else:
    logger.info("✅ Все ENV присутствуют")

# ================= GOOGLE INIT =================
sheet_users = None
sheet_checks = None
drive_service = None

if GOOGLE_JSON:
    try:
        creds_dict = json.loads(GOOGLE_JSON)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)

        gc = gspread.authorize(credentials)
        spreadsheet = gc.open(SPREADSHEET_NAME)

        sheet_users = spreadsheet.worksheet("Лист 1")
        sheet_checks = spreadsheet.worksheet("Лист 2")

        drive_service = build("drive", "v3", credentials=credentials)

        logger.info("✅ Google Sheets и Drive подключены")

    except Exception as e:
        logger.exception("❌ Ошибка Google API")

# ================= STATES =================
ASK_FIO, ASK_HOUSE, ASK_PHONE, WAIT_CHECK = range(4)

# ================= HELPERS =================
def find_user(tg_id: str):
    if not sheet_users:
        return None
    for row in sheet_users.get_all_records():
        if str(row.get("Telegram_ID", "")).strip() == tg_id:
            return row
    return None


def is_duplicate(unique_id: str) -> bool:
    if not sheet_checks:
        return False
    ids = sheet_checks.col_values(11)
    return unique_id in ids


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheet_users:
        await update.message.reply_text("⚠️ Сервис временно недоступен.")
        return ConversationHandler.END

    tg_id = str(update.effective_user.id)
    user = find_user(tg_id)

    if user:
        context.user_data.update({
            "fio": user.get("ФИО", ""),
            "house": user.get("Участок", ""),
            "phone": user.get("Телефон", ""),
        })

        await update.message.reply_text(
            f"👋 Привет, {context.user_data['fio']}!\n\n"
            f"📸 Отправьте фото или PDF чека."
        )
        return WAIT_CHECK

    await update.message.reply_text("Введите ФИО:")
    return ASK_FIO


async def ask_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["fio"] = update.message.text.strip()
    await update.message.reply_text("Введите номер участка:")
    return ASK_HOUSE


async def ask_house(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["house"] = update.message.text.strip()
    await update.message.reply_text(
        "Введите телефон в формате:\n"
        "+7926XXXXXXX"
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text("📸 Теперь отправьте чек.")
    return WAIT_CHECK


# ================= CHECK =================
async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not drive_service or not sheet_checks:
        await update.message.reply_text("⚠️ Сервис временно недоступен.")
        return WAIT_CHECK

    msg = update.message

    if msg.photo:
        file = msg.photo[-1]
    elif msg.document:
        file = msg.document
    else:
        await msg.reply_text("Отправьте фото или PDF.")
        return WAIT_CHECK

    if is_duplicate(file.file_unique_id):
        await msg.reply_text("⚠️ Этот чек уже был загружен.")
        return WAIT_CHECK

    tg_file = await context.bot.get_file(file.file_id)
    data = await tg_file.download_as_bytearray()

    media = MediaIoBaseUpload(io.BytesIO(data), resumable=False)

    uploaded = drive_service.files().create(
        body={
            "name": f"check_{update.effective_user.id}_{file.file_unique_id}",
            "parents": [GOOGLE_DRIVE_FOLDER_ID],
        },
        media_body=media,
        fields="webViewLink",
    ).execute()

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        context.user_data.get("fio"),
        context.user_data.get("house"),
        context.user_data.get("phone"),
        uploaded["webViewLink"],
        "",
        datetime.now().strftime("%Y-%m-%d"),
        "",
        "НЕТ",
        file.file_unique_id,
    ])

    await msg.reply_text("✅ Чек принят. Спасибо!")
    return WAIT_CHECK


# ================= MAIN =================
def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN отсутствует — бот не запущен")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_fio)],
            ASK_HOUSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_house)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            WAIT_CHECK: [MessageHandler(filters.PHOTO | filters.Document.ALL, handle_check)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(conv)

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
    )


if __name__ == "__main__":
    main()
