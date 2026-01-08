import os
import json
import re
import logging
from datetime import datetime, timedelta
import io

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

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
sheet_reqs = sh.worksheet("Реквизиты")

# ---------------- MENUS ----------------
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["🔍 Долг по участку"], ["📊 Статистика"], ["⬅️ Назад"]],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def is_admin(uid): return uid in ADMIN_IDS
def valid_fio(t): return len(t.split()) >= 2
def valid_phone(t): return bool(re.fullmatch(r"\+7\d{10}", t))
def valid_house(t): return t.isdigit()

def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{f['id']}"

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    row = find_user_row(uid)
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    if row:
        fio = sheet_users.cell(row, 2).value
        await update.message.reply_text(
            f"👋 С возвращением, {fio}\n⬇️ Используйте меню ниже",
            reply_markup=menu
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать в ТСН «Искона-Парк»\n\nВведите ФИО:",
            reply_markup=menu
        )

# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text == "🚀 Начать":
        await start(update, context)
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("🛠 Админ-панель:", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text("⬇️ Главное меню", reply_markup=ADMIN_MENU)
        return

    # -------- ДОЛГ ПО УЧАСТКУ --------
    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["wait_house"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("wait_house") and is_admin(uid):
        context.user_data.pop("wait_house")
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                await update.message.reply_text(
                    f"🏠 Участок: {text}\n"
                    f"👤 Telegram: @{r.get('username') or 'не указан'}\n"
                    f"ФИО: {r.get('ФИО')}\n"
                    f"📞 Телефон: {r.get('Телефон')}\n"
                    f"💰 Задолженность: {r.get('Сумма')} ₽\n"
                    f"📅 Напоминание: {r.get('Дата_напоминания')}",
                    reply_markup=ADMIN_PANEL
                )
                return
        await update.message.reply_text("❌ Участок не найден", reply_markup=ADMIN_PANEL)
        return

    # -------- СТАТИСТИКА --------
    if text == "📊 Статистика" and is_admin(uid):
        users = sheet_users.get_all_records()
        checks = sheet_checks.get_all_records()

        total_users = len(users)
        debtors = [u for u in users if str(u.get("Сумма")).isdigit() and int(u["Сумма"]) > 0]
        total_debt = sum(int(u["Сумма"]) for u in debtors)

        await update.message.reply_text(
            f"📊 Статистика ТСН\n\n"
            f"👥 Пользователей: {total_users}\n"
            f"💸 Должников: {len(debtors)}\n"
            f"💰 Общий долг: {total_debt} ₽\n"
            f"📎 Чеков загружено: {len(checks)}",
            reply_markup=ADMIN_PANEL
        )
        return

# ---------------- REMINDERS ----------------
async def send_reminders(app: Application):
    today = datetime.now().date()
    rows = sheet_users.get_all_records()

    for idx, r in enumerate(rows, start=2):
        try:
            debt = int(r.get("Сумма") or 0)
            if debt <= 0:
                continue

            last = r.get("Дата_напоминания")
            if last:
                last_date = datetime.strptime(last, "%Y-%m-%d").date()
                if today - last_date < timedelta(days=30):
                    continue

            uid = int(r.get("Telegram_ID"))
            await app.bot.send_message(
                uid,
                f"⏰ Напоминание об оплате\n\n"
                f"🏠 Участок: {r.get('Участок')}\n"
                f"💰 Задолженность: {debt} ₽\n\n"
                f"Пожалуйста, произведите оплату.\n"
                f"Реквизиты доступны в меню 💳"
            )

            sheet_users.update_cell(idx, 13, today.strftime("%Y-%m-%d"))
            sheet_users.update_cell(idx, 14, "отправлено")

        except Exception as e:
            logger.error(e)

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminders, "interval", days=1, args=[app])
    scheduler.start()

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
