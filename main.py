import os
import json
import logging
import io
import base64
import requests
from datetime import datetime, time
import pytz

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from openai import OpenAI

# =====================================================
# 🔧 НАСТРОЙКИ
# =====================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Moscow")

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================
# 📊 GOOGLE API
# =====================================================
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(
    creds_info,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_logs = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# =====================================================
# 🧭 МЕНЮ
# =====================================================
USER_MENU = ReplyKeyboardMarkup(
    [["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["🔍 GPT анализ чека"],
        ["📊 GPT прогноз"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# =====================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ
# =====================================================
def is_admin(uid):
    return uid in ADMIN_IDS

def log_event(event, uid="", details="", error=""):
    try:
        sheet_logs.append_row([
            datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            event, uid, details, error
        ])
    except Exception as e:
        logger.warning(f"LOG ERROR: {e}")

def find_user(uid):
    for r in sheet_users.get_all_records():
        if str(r.get("Telegram_ID")) == str(uid):
            return r
    return None

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/uc?id={f['id']}"

# =====================================================
# 🤖 GPT OCR + ANALYSIS
# =====================================================
def gpt_ocr_and_sum(image_bytes: bytes) -> dict:
    b64 = base64.b64encode(image_bytes).decode()

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Это чек. Вытащи сумму оплаты и дату. Ответ строго JSON: amount, date"},
                {"type": "input_image", "image_base64": b64}
            ]
        }]
    )

    text = response.output_text
    try:
        return json.loads(text)
    except Exception:
        return {"amount": None, "date": None}

# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = find_user(uid)

    fio = user.get("ФИО") if user else ""
    text = (
        f"Здравствуйте, {fio}!\n\n"
        "Бот ТСН «Искона-Парк».\n"
        "Загрузите чек или посмотрите реквизиты."
    )

    await update.message.reply_text(
        text,
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# =====================================================
# 📝 TEXT HANDLER
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель", reply_markup=ADMIN_PANEL)

    elif text == "📊 GPT прогноз" and is_admin(uid):
        debts = [r for r in sheet_users.get_all_records() if float(r.get("Сумма", 0) or 0) > 0]
        summary = f"Должников: {len(debts)}"
        await update.message.reply_text(f"📈 GPT прогноз:\n{summary}")

    elif text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text("Отправьте фото или PDF чека")

# =====================================================
# 📎 FILE HANDLER
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    if is_duplicate(file.file_unique_id):
        await msg.reply_text("⚠️ Чек уже был загружен")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    gpt_data = gpt_ocr_and_sum(data)

    link = upload_to_drive(
        data,
        f"check_{update.effective_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        gpt_data.get("amount"),
        gpt_data.get("date"),
        link,
        file.file_unique_id,
        "новый"
    ])

    context.user_data.clear()
    await msg.reply_text(f"✅ Чек загружен\n💸 Сумма: {gpt_data.get('amount')}")

# =====================================================
# 🚦 MAIN
# =====================================================
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
import os
import json
import logging
import io
import base64
import requests
from datetime import datetime, time
import pytz

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from openai import OpenAI

# =====================================================
# 🔧 НАСТРОЙКИ
# =====================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Moscow")

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================
# 📊 GOOGLE API
# =====================================================
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(
    creds_info,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_logs = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# =====================================================
# 🧭 МЕНЮ
# =====================================================
USER_MENU = ReplyKeyboardMarkup(
    [["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["🔍 GPT анализ чека"],
        ["📊 GPT прогноз"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# =====================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ
# =====================================================
def is_admin(uid):
    return uid in ADMIN_IDS

def log_event(event, uid="", details="", error=""):
    try:
        sheet_logs.append_row([
            datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            event, uid, details, error
        ])
    except Exception as e:
        logger.warning(f"LOG ERROR: {e}")

def find_user(uid):
    for r in sheet_users.get_all_records():
        if str(r.get("Telegram_ID")) == str(uid):
            return r
    return None

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/uc?id={f['id']}"

# =====================================================
# 🤖 GPT OCR + ANALYSIS
# =====================================================
def gpt_ocr_and_sum(image_bytes: bytes) -> dict:
    b64 = base64.b64encode(image_bytes).decode()

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Это чек. Вытащи сумму оплаты и дату. Ответ строго JSON: amount, date"},
                {"type": "input_image", "image_base64": b64}
            ]
        }]
    )

    text = response.output_text
    try:
        return json.loads(text)
    except Exception:
        return {"amount": None, "date": None}

# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = find_user(uid)

    fio = user.get("ФИО") if user else ""
    text = (
        f"Здравствуйте, {fio}!\n\n"
        "Бот ТСН «Искона-Парк».\n"
        "Загрузите чек или посмотрите реквизиты."
    )

    await update.message.reply_text(
        text,
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# =====================================================
# 📝 TEXT HANDLER
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель", reply_markup=ADMIN_PANEL)

    elif text == "📊 GPT прогноз" and is_admin(uid):
        debts = [r for r in sheet_users.get_all_records() if float(r.get("Сумма", 0) or 0) > 0]
        summary = f"Должников: {len(debts)}"
        await update.message.reply_text(f"📈 GPT прогноз:\n{summary}")

    elif text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text("Отправьте фото или PDF чека")

# =====================================================
# 📎 FILE HANDLER
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    if is_duplicate(file.file_unique_id):
        await msg.reply_text("⚠️ Чек уже был загружен")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    gpt_data = gpt_ocr_and_sum(data)

    link = upload_to_drive(
        data,
        f"check_{update.effective_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        gpt_data.get("amount"),
        gpt_data.get("date"),
        link,
        file.file_unique_id,
        "новый"
    ])

    context.user_data.clear()
    await msg.reply_text(f"✅ Чек загружен\n💸 Сумма: {gpt_data.get('amount')}")

# =====================================================
# 🚦 MAIN
# =====================================================
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
