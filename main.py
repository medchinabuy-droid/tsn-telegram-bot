import os
import json
import logging
import io
from datetime import datetime, timedelta

import pytz
import gspread

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================= LOG =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

TZ = pytz.timezone("Europe/Moscow")

# ================= GOOGLE =================
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)
sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_stats = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# ================= TEXTS =================
REMINDER_TEXT = (
    "🔔 Уведомление ТСН\n\n"
    "У вас имеется задолженность.\n"
    "Просим произвести оплату.\n\n"
    "После оплаты загрузите чек в бота."
)

# ================= MENUS =================
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"],
     ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"],
     ["🛠 Админ-панель"],
     ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["📣 Уведомление"],
     ["🔍 Долг по участку"],
     ["📊 Статистика"],
     ["⬅️ Назад"]],
    resize_keyboard=True
)

# ================= HELPERS =================
def is_admin(uid):
    return uid in ADMIN_IDS

def log_stat(event, uid="", house="", comment=""):
    sheet_stats.append_row([
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        event, uid, house, comment
    ])

def load_users():
    return sheet_users.get_all_records()

def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(11)

# ================= AUTO REMINDERS =================
async def auto_reminders(app: Application):
    today = datetime.now(TZ).day
    rows = load_users()

    for r in rows:
        try:
            pay_day = str(r.get("День_оплаты", "")).strip()
            if not pay_day.isdigit():
                continue
            if int(pay_day) != today:
                continue

            debt = float(str(r.get("Сумма", "0")).replace(",", "."))
            if debt <= 0:
                continue

            if r.get("Статус", "").upper() == "ОПЛАЧЕНО":
                continue

            tg_id = r.get("TelegramID")
            if not tg_id:
                continue

            await app.bot.send_message(int(tg_id), REMINDER_TEXT)
            log_stat("авто_уведомление", tg_id, r.get("Участок"))

        except Exception as e:
            log_stat("ошибка_уведомления", r.get("TelegramID"), r.get("Участок"), str(e))

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data.clear()
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    log_stat("start", uid)
    await update.message.reply_text("👋 Добро пожаловать", reply_markup=menu)

# ================= TEXT HANDLER =================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text == "🚀 Начать":
        return await start(update, context)

    if text == "🛠 Админ-панель" and is_admin(uid):
        return await update.message.reply_text("Админ-меню", reply_markup=ADMIN_PANEL)

    if text == "⬅️ Назад":
        return await start(update, context)

    # ---- УВЕДОМЛЕНИЕ ----
    if text == "📣 Уведомление" and is_admin(uid):
        context.user_data["notify"] = True
        return await update.message.reply_text("Введите номер участка")

    if context.user_data.get("notify"):
        context.user_data.clear()
        sent = 0
        for r in load_users():
            if str(r.get("Участок")) == text and r.get("TelegramID"):
                try:
                    await context.bot.send_message(int(r["TelegramID"]), REMINDER_TEXT)
                    sent += 1
                except Exception as e:
                    log_stat("blocked", r["TelegramID"], text, str(e))
        log_stat("ручное_уведомление", uid, text, f"sent={sent}")
        return await update.message.reply_text(f"Отправлено: {sent}")

    # ---- ДОЛГ ----
    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["debt"] = True
        return await update.message.reply_text("Введите номер участка")

    if context.user_data.get("debt"):
        context.user_data.clear()
        for r in load_users():
            if str(r.get("Участок")) == text:
                msg = (
                    f"🏠 Участок: {r.get('Участок')}\n"
                    f"👤 ФИО: {r.get('ФИО')}\n"
                    f"📞 Телефон: {r.get('Телефон')}\n"
                    f"💰 Долг: {r.get('Сумма')}\n"
                    f"👤 Username: @{r.get('Username')}\n"
                    f"🤖 Бот: {'OK' if r.get('TelegramID') else 'Нет'}"
                )
                return await update.message.reply_text(msg)
        return await update.message.reply_text("❌ Участок не найден")

    # ---- РЕКВИЗИТЫ ----
