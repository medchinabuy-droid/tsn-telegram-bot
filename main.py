# ================== IMPORTS ==================
import os
import json
import re
import logging
import io
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from telegram.error import Forbidden

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================== LOG ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

ADMIN_IDS = [6810194645]  # супер-админ

# ================== GOOGLE ==================
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(creds_info, scopes=scopes)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)
sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_reqs = sh.worksheet("Реквизиты")
sheet_logs = sh.worksheet("LOGS")

# ================== MENUS ==================
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["🔍 Долг по участку"],
        ["📊 Статистика"],
        ["📢 Отправить уведомление"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# ================== HELPERS ==================
def is_admin(uid): 
    return uid in ADMIN_IDS

def log_event(kind, uid="", username="", house="", event="", details="", error=""):
    try:
        sheet_logs.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            kind, uid, username, house, event, details, error
        ])
    except Exception as e:
        logger.error(f"LOG ERROR: {e}")

def find_user_by_house(house):
    for r in sheet_users.get_all_records():
        if str(r.get("Участок")) == str(house):
            return r
    return None

def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(11)

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{f['id']}"

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    await update.message.reply_text("⬇️ Используйте меню", reply_markup=menu)

# ================== TEXT HANDLER ==================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "🚀 Начать":
        await start(update, context)
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель:", reply_markup=ADMIN_PANEL)
        return

    if text == "📢 Отправить уведомление" and is_admin(uid):
        context.user_data["notify_wait_house"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("notify_wait_house") and is_admin(uid):
        context.user_data.pop("notify_wait_house")
        house = text
        user = find_user_by_house(house)

        if not user:
            await update.message.reply_text("❌ Участок не найден")
            return

        try:
            await context.bot.send_message(
                chat_id=user.get("Telegram_ID"),
                text=f"📢 Уведомление ТСН\n\nПо вашему участку №{house} есть сообщение от администрации."
            )

            log_event(
                "BUSINESS",
                user.get("Telegram_ID"),
                user.get("Telegram_username"),
                house,
                "MANUAL_NOTIFY",
                "Уведомление отправлено админом"
            )

            await update.message.reply_text("✅ Уведомление отправлено")

        except Forbidden:
            log_event(
                "TECH",
                user.get("Telegram_ID"),
                user.get("Telegram_username"),
                house,
                "BOT_BLOCKED",
                "Пользователь заблокировал бота"
            )
            await update.message.reply_text("⚠️ Бот заблокирован этим пользователем")

        return

# ================== FILE HANDLER ==================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    if is_duplicate(file.file_unique_id):
        await msg.reply_text("❌ Этот чек уже загружен")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    link = upload_to_drive(
        data,
        f"check_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    uid = update.effective_user.id
    sheet_checks.append_row([
        uid,
        update.effective_user.username or "",
        link,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        file.file_unique_id
    ])

    log_event(
        "BUSINESS",
        uid,
        update.effective_user.username,
        "",
        "CHECK_UPLOADED",
        link
    )

    await msg.reply_text("✅ Чек сохранён")

# ================== MAIN ==================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
