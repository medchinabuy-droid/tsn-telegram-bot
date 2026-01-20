import os
import json
import logging
from datetime import datetime, time

import pytz
import gspread

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from google.oauth2.service_account import Credentials

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

TZ = pytz.timezone("Europe/Moscow")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= GOOGLE =================
creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_reqs = sh.worksheet("Реквизиты")

# ================= КЛАВИАТУРЫ =================
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"],
     ["💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"],
     ["📣 Уведомление"]],
    resize_keyboard=True
)

REMINDER_TEXT = (
    "🔔 Напоминание об оплате\n\n"
    "Просим произвести оплату.\n"
    "После оплаты загрузите чек."
)

# ================= ВСПОМОГАТЕЛЬНОЕ =================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def load_users():
    return sheet_users.get_all_records()

def load_reqs_text():
    rows = sheet_reqs.get_all_values()
    if len(rows) < 2:
        return "❌ Реквизиты не заполнены"
    r = rows[1]  # строго ВТОРАЯ строка
    return (
        f"🏦 Банк: {r[0]}\n"
        f"🔢 БИК: {r[1]}\n"
        f"💳 Счёт: {r[2]}\n"
        f"👤 Получатель: {r[3]}\n"
        f"🧾 ИНН: {r[4]}\n"
        f"📱 QR: {r[5]}"
    )

# ================= АВТО УВЕДОМЛЕНИЯ =================
async def auto_reminders(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).day
    users = load_users()

    for r in users:
        try:
            day_raw = str(r.get("День_оплаты", "")).strip()
            if not day_raw.isdigit():
                continue
            if int(day_raw) != today:
                continue

            tg_id = r.get("TelegramID")
            if not tg_id:
                continue

            await context.bot.send_message(int(tg_id), REMINDER_TEXT)

        except Exception as e:
            logger.error(f"Ошибка автоуведомления: {e}")

# ================= ХЕНДЛЕРЫ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    await update.message.reply_text(
        "👋 Добро пожаловать",
        reply_markup=menu
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "🚀 Начать":
        return await start(update, context)

    if text == "💳 Реквизиты":
        return await update.message.reply_text(load_reqs_text())

    if text == "📣 Уведомление" and is_admin(uid):
        context.user_data["notify"] = True
        return await update.message.reply_text("Введите номер участка")

    if context.user_data.get("notify"):
        context.user_data.clear()
        sent = 0
        for r in load_users():
            if str(r.get("Участок")) == text and r.get("TelegramID"):
                await context.bot.send_message(
                    int(r["TelegramID"]),
                    REMINDER_TEXT
                )
                sent += 1
        return await update.message.reply_text(f"Отправлено: {sent}")
