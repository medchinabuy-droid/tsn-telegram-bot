import os
import json
import logging
import io
import requests
from datetime import datetime, time
import pytz
import base64

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================= GPT =================
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

gpt = OpenAI(api_key=OPENAI_API_KEY)

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
        ["📊 Статистика"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# =====================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ
# =====================================================
def is_admin(uid):
    return uid in ADMIN_IDS

def log_event(event, uid="", username="", details="", error=""):
    try:
        sheet_logs.append_row([
            datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            event, uid, username, details, error
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
# 🧠 GPT VISION ANALYSIS
# =====================================================
def gpt_analyze_check(image_bytes: bytes) -> dict:
    """
    Возвращает:
    {
      "amount": float | None,
      "currency": "RUB",
      "comment": str,
      "status": "ok" | "suspicious"
    }
    """
    try:
        img_b64 = base64.b64encode(image_bytes).decode()

        resp = gpt.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты бухгалтер. Проанализируй чек.\n"
                        "Найди сумму оплаты. Если сомнительно — укажи.\n"
                        "Ответ строго в JSON."
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Проанализируй чек"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}"
                            }
                        }
                    ]
                }
            ],
            temperature=0
        )

        data = json.loads(resp.choices[0].message.content)
        return data

    except Exception as e:
        logger.error(f"GPT ERROR: {e}")
        return {
            "amount": None,
            "currency": "RUB",
            "comment": "Ошибка анализа",
            "status": "suspicious"
        }

# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = find_user(uid)

    fio = user.get("ФИО") if user else None
    greeting = f"Здравствуйте, *{fio}*!\n\n" if fio else "Здравствуйте!\n\n"

    text = (
        greeting +
        "Вы в *официальном боте ТСН «Искона-Парк»*.\n\n"
        "Используйте меню ниже 👇"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# =====================================================
# 📝 TEXT HANDLER
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text(
            "📎 Отправьте *фото или PDF чека*.",
            parse_mode="Markdown"
        )
        return

    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"*Реквизиты:*\n\n"
            f"{r[0]}\n{r[1]}\n{r[2]}\n{r[3]}\n{r[4]}",
            parse_mode="Markdown"
        )
        return

# =====================================================
# 📎 FILE HANDLER
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    if is_duplicate(file.file_unique_id):
        await msg.reply_text("⚠️ Этот чек уже был загружен.")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    drive_link = upload_to_drive(
        data,
        f"check_{update.effective_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    gpt_result = gpt_analyze_check(bytes(data))

    user = find_user(update.effective_user.id)

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username or "",
        user.get("ФИО") if user else "",
        user.get("Участок") if user else "",
        user.get("Телефон") if user else "",
        drive_link,
        gpt_result.get("amount"),
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        gpt_result.get("currency"),
        gpt_result.get("comment"),
        gpt_result.get("status"),
        "",
        file.file_unique_id,
        "gpt"
    ])

    context.user_data.clear()
    await msg.reply_text("✅ Чек принят и проверен.")

# =====================================================
# ⏰ УВЕДОМЛЕНИЯ
# =====================================================
async def monthly_notify(context: ContextTypes.DEFAULT_TYPE):
    for r in sheet_users.get_all_records():
        try:
            if not r.get("Telegram_ID"):
                continue
            await context.bot.send_message(
                int(r["Telegram_ID"]),
                "⏰ Напоминание об оплате взносов."
            )
        except:
            pass

# =====================================================
# 🚦 MAIN
# =====================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.job_queue.run_daily(
        monthly_notify,
        time=time(hour=18, minute=0, tzinfo=TZ)
    )

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
