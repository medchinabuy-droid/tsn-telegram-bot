# ===========================
# TELEGRAM TSN BOT (PART 1)
# ===========================

import os
import io
import re
import json
import time
import base64
import logging
from datetime import datetime, timedelta
import pytz

from telegram import (
    Update, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, CallbackQueryHandler, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from openai import OpenAI
import qrcode

# ===========================
# CONFIG
# ===========================
TZ = pytz.timezone("Europe/Moscow")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MONTHLY_FEE = float(os.getenv("MONTHLY_FEE", "6000"))

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))

# ===========================
# GOOGLE
# ===========================
creds = Credentials.from_service_account_info(
    GOOGLE_CREDS,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ws_users = sh.worksheet("Лист 1")
ws_checks = sh.worksheet("Лист 2")
ws_logs = sh.worksheet("Лист 3")
ws_req = sh.worksheet("Реквизиты")
ws_houses = sh.worksheet("Дома")

# ===========================
# GPT
# ===========================
gpt = OpenAI(api_key=OPENAI_API_KEY)

# ===========================
# MENUS
# ===========================
USER_MENU = ReplyKeyboardMarkup(
    [["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["📎 Загрузить чек", "💳 Реквизиты"],
     ["🛠 Админ-панель"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["📊 Статистика", "🔍 Долги"],
     ["📣 Напоминание дому"],
     ["⬅️ Назад"]],
    resize_keyboard=True
)

# ===========================
# HELPERS
# ===========================
def is_admin(uid): return uid in ADMIN_IDS

def now(): return datetime.now(TZ)

def log_event(event, uid="", info="", err=""):
    ws_logs.append_row([
        now().strftime("%Y-%m-%d %H:%M:%S"),
        event, uid, info, err
    ])

def find_user(uid):
    for r in ws_users.get_all_records():
        if str(r.get("Telegram_ID")) == str(uid):
            return r
    return None

def user_debt(user):
    try:
        return float(str(user.get("Сумма") or "0").replace(",", "."))
    except:
        return 0.0

# ===========================
# START
# ===========================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = find_user(uid)

    if not user:
        houses = [
            [KeyboardButton(h["Дом"])]
            for h in ws_houses.get_all_records() if h["Активен"]
        ]
        await update.message.reply_text(
            "Выберите дом:",
            reply_markup=ReplyKeyboardMarkup(houses, resize_keyboard=True)
        )
        ctx.user_data["select_house"] = True
        return

    await update.message.reply_text(
        "Добро пожаловать 👋",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# ===========================
# TEXT HANDLER
# ===========================
async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    uid = update.effective_user.id

    # --- выбор дома ---
    if ctx.user_data.get("select_house"):
        ws_users.append_row([
            uid, update.effective_user.username,
            txt, "", MONTHLY_FEE, "", ""
        ])
        ctx.user_data.clear()
        await update.message.reply_text(
            "Дом сохранён ✅",
            reply_markup=USER_MENU
        )
        return

    # --- загрузка чека ---
    if txt == "📎 Загрузить чек":
        ctx.user_data["wait_check"] = True
        await update.message.reply_text(
            "Пришлите фото или PDF чека 📎"
        )
        return

    # --- реквизиты + QR ---
    if txt == "💳 Реквизиты":
        r = ws_req.row_values(2)
        qr = qrcode.make(r[2])
        buf = io.BytesIO()
        qr.save(buf)
        buf.seek(0)

        await update.message.reply_photo(
            buf,
            caption=(
                f"🏦 {r[0]}\n"
                f"БИК: {r[1]}\n"
                f"Счёт: {r[2]}\n"
                f"Получатель: {r[3]}"
            )
        )
        return

    # --- админ ---
    if txt == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text(
            "Админ-панель:",
            reply_markup=ADMIN_PANEL
        )
        return

    if txt == "📊 Статистика" and is_admin(uid):
        users = ws_users.get_all_records()
        total_debt = sum(user_debt(u) for u in users)
        await update.message.reply_text(
            f"👥 Пользователей: {len(users)}\n"
            f"💸 Общий долг: {total_debt:.2f} ₽"
        )
        return

    if txt == "🔍 Долги" and is_admin(uid):
        rows = []
        for u in ws_users.get_all_records():
            if user_debt(u) > 0:
                rows.append(f"{u['ФИО']} — {u['Сумма']} ₽")
        await update.message.reply_text(
            "Должники:\n" + "\n".join(rows) if rows else "Долгов нет 🎉"
        )
        return

    if txt == "📣 Напоминание дому" and is_admin(uid):
        for u in ws_users.get_all_records():
            if user_debt(u) > 0 and u.get("Telegram_ID"):
                await ctx.bot.send_message(
                    int(u["Telegram_ID"]),
                    f"❗ Напоминание об оплате. Долг: {u['Сумма']} ₽"
                )
        await update.message.reply_text("Рассылка отправлена ✅")
        return
# ===========================
# TELEGRAM TSN BOT (PART 1)
# ===========================

import os
import io
import re
import json
import time
import base64
import logging
from datetime import datetime, timedelta
import pytz

from telegram import (
    Update, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, CallbackQueryHandler, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from openai import OpenAI
import qrcode

# ===========================
# CONFIG
# ===========================
TZ = pytz.timezone("Europe/Moscow")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MONTHLY_FEE = float(os.getenv("MONTHLY_FEE", "6000"))

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))

# ===========================
# GOOGLE
# ===========================
creds = Credentials.from_service_account_info(
    GOOGLE_CREDS,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ws_users = sh.worksheet("Лист 1")
ws_checks = sh.worksheet("Лист 2")
ws_logs = sh.worksheet("Лист 3")
ws_req = sh.worksheet("Реквизиты")
ws_houses = sh.worksheet("Дома")

# ===========================
# GPT
# ===========================
gpt = OpenAI(api_key=OPENAI_API_KEY)

# ===========================
# MENUS
# ===========================
USER_MENU = ReplyKeyboardMarkup(
    [["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["📎 Загрузить чек", "💳 Реквизиты"],
     ["🛠 Админ-панель"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["📊 Статистика", "🔍 Долги"],
     ["📣 Напоминание дому"],
     ["⬅️ Назад"]],
    resize_keyboard=True
)

# ===========================
# HELPERS
# ===========================
def is_admin(uid): return uid in ADMIN_IDS

def now(): return datetime.now(TZ)

def log_event(event, uid="", info="", err=""):
    ws_logs.append_row([
        now().strftime("%Y-%m-%d %H:%M:%S"),
        event, uid, info, err
    ])

def find_user(uid):
    for r in ws_users.get_all_records():
        if str(r.get("Telegram_ID")) == str(uid):
            return r
    return None

def user_debt(user):
    try:
        return float(str(user.get("Сумма") or "0").replace(",", "."))
    except:
        return 0.0

# ===========================
# START
# ===========================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = find_user(uid)

    if not user:
        houses = [
            [KeyboardButton(h["Дом"])]
            for h in ws_houses.get_all_records() if h["Активен"]
        ]
        await update.message.reply_text(
            "Выберите дом:",
            reply_markup=ReplyKeyboardMarkup(houses, resize_keyboard=True)
        )
        ctx.user_data["select_house"] = True
        return

    await update.message.reply_text(
        "Добро пожаловать 👋",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# ===========================
# TEXT HANDLER
# ===========================
async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    uid = update.effective_user.id

    # --- выбор дома ---
    if ctx.user_data.get("select_house"):
        ws_users.append_row([
            uid, update.effective_user.username,
            txt, "", MONTHLY_FEE, "", ""
        ])
        ctx.user_data.clear()
        await update.message.reply_text(
            "Дом сохранён ✅",
            reply_markup=USER_MENU
        )
        return

    # --- загрузка чека ---
    if txt == "📎 Загрузить чек":
        ctx.user_data["wait_check"] = True
        await update.message.reply_text(
            "Пришлите фото или PDF чека 📎"
        )
        return

    # --- реквизиты + QR ---
    if txt == "💳 Реквизиты":
        r = ws_req.row_values(2)
        qr = qrcode.make(r[2])
        buf = io.BytesIO()
        qr.save(buf)
        buf.seek(0)

        await update.message.reply_photo(
            buf,
            caption=(
                f"🏦 {r[0]}\n"
                f"БИК: {r[1]}\n"
                f"Счёт: {r[2]}\n"
                f"Получатель: {r[3]}"
            )
        )
        return

    # --- админ ---
    if txt == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text(
            "Админ-панель:",
            reply_markup=ADMIN_PANEL
        )
        return

    if txt == "📊 Статистика" and is_admin(uid):
        users = ws_users.get_all_records()
        total_debt = sum(user_debt(u) for u in users)
        await update.message.reply_text(
            f"👥 Пользователей: {len(users)}\n"
            f"💸 Общий долг: {total_debt:.2f} ₽"
        )
        return

    if txt == "🔍 Долги" and is_admin(uid):
        rows = []
        for u in ws_users.get_all_records():
            if user_debt(u) > 0:
                rows.append(f"{u['ФИО']} — {u['Сумма']} ₽")
        await update.message.reply_text(
            "Должники:\n" + "\n".join(rows) if rows else "Долгов нет 🎉"
        )
        return

    if txt == "📣 Напоминание дому" and is_admin(uid):
        for u in ws_users.get_all_records():
            if user_debt(u) > 0 and u.get("Telegram_ID"):
                await ctx.bot.send_message(
                    int(u["Telegram_ID"]),
                    f"❗ Напоминание об оплате. Долг: {u['Сумма']} ₽"
                )
        await update.message.reply_text("Рассылка отправлена ✅")
        return
