import os
import json
import io
import logging
import re
from datetime import datetime, time
import pytz
import requests
import base64

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
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
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", "10000"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MONTHLY_FEE = float(os.getenv("MONTHLY_FEE", "6000"))

# =====================================================
# 🤖 GPT
# =====================================================
gpt = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================
# 📊 GOOGLE
# =====================================================
creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDS_JSON),
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
    [
        ["📎 Загрузить чек", "💳 Реквизиты"],
        ["🛠 Админ-панель"]
    ],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["📊 Статистика", "🔍 Долги"],
        ["📣 Напоминание дому"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# =====================================================
# 🛠 УТИЛИТЫ
# =====================================================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def log_event(event, uid="", details="", error=""):
    try:
        sheet_logs.append_row([
            datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            event,
            uid,
            details,
            error
        ])
    except Exception as e:
        logger.warning(f"log error: {e}")

def find_user(uid):
    for r in sheet_users.get_all_records():
        if str(r.get("Telegram_ID")) == str(uid):
            return r
    return None

def upload_to_drive(data: bytes, name: str, mime: str) -> str:
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    file = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{file['id']}"

# =====================================================
# 🧠 GPT OCR
# =====================================================
def gpt_parse_check(image_bytes: bytes):
    b64 = base64.b64encode(image_bytes).decode()

    res = gpt.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Найди сумму и дату на чеке"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}}
            ]
        }],
        max_tokens=300
    )

    text = res.choices[0].message.content

    amount = re.search(r"(\d{3,6})", text)
    date = re.search(r"\d{2}[./]\d{2}[./]\d{4}", text)

    return {
        "amount": int(amount.group(1)) if amount else None,
        "date": date.group(0) if date else None,
        "raw": text
    }

# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = find_user(uid)

    text = "👋 Добро пожаловать в бот ТСН «Искона-Парк».\n\n"
    if user:
        text += f"Здравствуйте, *{user.get('ФИО')}*!\n"
    else:
        text += "Вы ещё не зарегистрированы.\n"

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# =====================================================
# 📝 TEXT
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    uid = update.effective_user.id

    if txt == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель:", reply_markup=ADMIN_PANEL)
        return

    if txt == "⬅️ Назад":
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return

    if txt == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"💳 *Реквизиты*\n\n"
            f"🏦 Банк: {r[0]}\n"
            f"🔢 БИК: {r[1]}\n"
            f"💼 Счёт: {r[2]}\n"
            f"👤 Получатель: {r[3]}",
            parse_mode="Markdown"
        )
        return

    if txt == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text("📎 Отправьте фото или PDF чека")
        return

# =====================================================
# 📎 FILE
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    ocr = gpt_parse_check(bytes(data))

    link = upload_to_drive(
        data,
        f"check_{update.effective_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    user = find_user(update.effective_user.id)

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username or "",
        user.get("ФИО") if user else "",
        user.get("Участок") if user else "",
        user.get("Телефон") if user else "",
        link,
        ocr["amount"],
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        ocr["date"],
        ocr["raw"],
        file.file_unique_id,
        "новый"
    ])

    context.user_data.clear()
    await msg.reply_text("✅ Чек принят и отправлен на проверку")

# =====================================================
# ⏰ УВЕДОМЛЕНИЯ
# =====================================================
async def monthly_notify(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).day

    for r in sheet_users.get_all_records():
        try:
            pay_day = int(r.get("День_оплаты") or 0)
            debt = float(str(r.get("Сумма") or "0").replace(",", "."))

            if not r.get("Telegram_ID"):
                continue

            days = pay_day - today
            fio = r.get("ФИО")

            if debt > 0:
                if days in (5, 3, 1):
                    await context.bot.send_message(
                        int(r["Telegram_ID"]),
                        f"⏰ *{fio}*, до оплаты {days} дн.",
                        parse_mode="Markdown"
                    )
                if days < 0:
                    await context.bot.send_message(
                        int(r["Telegram_ID"]),
                        f"❗ *{fio}*, у вас задолженность.",
                        parse_mode="Markdown"
                    )

        except Exception as e:
            log_event("notify_error", r.get("Telegram_ID"), error=str(e))

# =====================================================
# 🚦 MAIN
# =====================================================
def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.job_queue.run_daily(
        monthly_notify,
        time=time(hour=18, minute=0, tzinfo=TZ)
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )

if __name__ == "__main__":
    main()
