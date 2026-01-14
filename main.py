import os
import json
import re
import logging
from datetime import datetime, timedelta
import io
import pytz

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# ---------------- TIMEZONE ----------------
TZ = pytz.timezone("Europe/Moscow")

# ---------------- GOOGLE ----------------
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
sheet_stats = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# ---------------- TEXTS ----------------
REMINDER_TEXT = (
    "⏰ Напоминание ТСН\n\n"
    "У вас есть задолженность.\n"
    "Просим произвести оплату.\n\n"
    "После оплаты загрузите чек в бота."
)

BATTLE_TEXT = (
    "⚠️ Боевое уведомление ТСН\n\n"
    "Задолженность не погашена.\n"
    "Необходимо срочно оплатить долг."
)

# ---------------- MENUS ----------------
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["🔍 Долг по участку"],
        ["📣 Боевое уведомление"],
        ["📊 Статистика"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def is_admin(uid):
    return uid in ADMIN_IDS

def log_stat(event, uid="", username="", house="", comment=""):
    sheet_stats.append_row([
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        event, uid, username, house, comment
    ])

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

# ---------------- AUTO REMINDERS ----------------
async def auto_reminders(app: Application):
    rows = sheet_users.get_all_records()
    today = datetime.now(TZ).date()

    for r in rows:
        try:
            debt = float(str(r.get("Сумма", "0")).replace(",", "."))
            if debt <= 0:
                continue

            if r.get("Статус", "").upper() == "ОПЛАЧЕНО":
                continue

            pause_date = r.get("Дата_напоминания")
            if pause_date:
                if today < datetime.strptime(pause_date, "%Y-%m-%d").date():
                    continue

            chat_id = int(r["TelegramID"])
            await app.bot.send_message(chat_id, REMINDER_TEXT)

            log_stat("авто_напоминание", chat_id, "", r.get("Участок"))

        except Exception as e:
            log_stat("blocked", r.get("TelegramID"), "", r.get("Участок"), str(e))
            for admin in ADMIN_IDS:
                await app.bot.send_message(
                    admin,
                    f"🚫 Блокировка\nУчасток: {r.get('Участок')}\nID: {r.get('TelegramID')}\n{e}"
                )

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data.clear()
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    await update.message.reply_text("👋 Добро пожаловать", reply_markup=menu)

# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text == "🚀 Начать":
        await start(update, context)
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-меню", reply_markup=ADMIN_PANEL)
        return

    if text == "📣 Боевое уведомление" and is_admin(uid):
        context.user_data["battle"] = True
        await update.message.reply_text("Участок / ALL / SELF")
        return

    if context.user_data.get("battle"):
        context.user_data.clear()
        if text == "SELF":
            await context.bot.send_message(uid, BATTLE_TEXT)
            return await update.message.reply_text("Отправлено себе")

        sent = 0
        for r in sheet_users.get_all_records():
            if text == "ALL" or str(r.get("Участок")) == text:
                try:
                    await context.bot.send_message(int(r["TelegramID"]), BATTLE_TEXT)
                    sent += 1
                except:
                    pass
        await update.message.reply_text(f"Отправлено: {sent}")

    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text("Отправьте чек")
        return

# ---------------- FILE ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    file = update.message.photo[-1] if update.message.photo else update.message.document
    if is_duplicate(file.file_unique_id):
        await update.message.reply_text("❌ Чек уже был")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()
    upload_to_drive(data, "check", file.mime_type)

    uid = update.effective_user.id
    row = find_user_row(uid)

    if row:
        sheet_users.update_cell(row, 5, "0")
        sheet_users.update_cell(row, 6, "ОПЛАЧЕНО")
        pause = (datetime.now(TZ) + timedelta(days=30)).strftime("%Y-%m-%d")
        sheet_users.update_cell(row, 7, pause)

    log_stat("авто_закрытие_долга", uid)
    context.user_data.clear()
    await update.message.reply_text("✅ Чек принят, долг закрыт", reply_markup=USER_MENU)

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(auto_reminders, "cron", hour=18, minute=0, args=[app])
    scheduler.start()

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
