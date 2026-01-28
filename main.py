import os
import json
import io
import logging
from datetime import datetime, timedelta, time

import pytz
import requests
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
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
# 📊 GOOGLE
# =====================================================
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(
    creds_info,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
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
    resize_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["📎 Загрузить чек", "💳 Реквизиты"], ["🧠 GPT панель"]],
    resize_keyboard=True,
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["📊 GPT прогноз", "🧾 GPT анализ чека"], ["⬅️ Назад"]],
    resize_keyboard=True,
)

# =====================================================
# 🛠 УТИЛИТЫ
# =====================================================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def log_event(event, uid="", username="", house="", details="", error=""):
    try:
        sheet_logs.append_row(
            [
                datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                event,
                uid,
                username,
                house,
                details,
                error,
            ]
        )
    except Exception as e:
        logger.warning(f"LOG ERROR: {e}")


def find_user(uid):
    rows = sheet_users.get_all_records()
    for r in rows:
        if str(r.get("Telegram_ID")) == str(uid):
            return r
    return None


def upload_to_drive(data: bytes, name: str, mime: str) -> str:
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = (
        drive.files()
        .create(
            body={"name": name, "parents": [DRIVE_FOLDER_ID]},
            media_body=media,
            fields="id",
        )
        .execute()
    )
    return f"https://drive.google.com/uc?id={f['id']}"

# =====================================================
# 🧠 GPT
# =====================================================
async def ask_gpt(system: str, user: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content


async def gpt_vision(image_bytes: bytes) -> str:
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Распознай сумму и дату платежа"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_bytes.hex()}"},
                    },
                ],
            }
        ],
    )
    return resp.choices[0].message.content

# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = find_user(uid)

    fio = user["ФИО"] if user else "Уважаемый пользователь"

    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    await update.message.reply_text(
        f"👋 Добро пожаловать, {fio}!\n\n"
        "Вы находитесь в официальном боте ТСН «Искона-Парк».\n\n"
        "ℹ️ Здесь вы можете:\n"
        "• получать уведомления о поселковых взносах\n"
        "• загрузить чек об оплате\n"
        "• посмотреть реквизиты\n\n"
        "Используйте кнопки ниже 👇",
        reply_markup=menu,
    )

# =====================================================
# 💳 РЕКВИЗИТЫ
# =====================================================
async def show_reqs(update: Update):
    r = sheet_reqs.row_values(2)

    await update.message.reply_text(
        f"💳 Реквизиты для оплаты\n\n"
        f"🏦 Банк: {r[0]}\n"
        f"🔢 БИК: {r[1]}\n"
        f"💼 Счёт: {r[2]}\n"
        f"👤 Получатель: {r[3]}\n"
        f"🧾 ИНН: {r[4]}",
    )

    if r[5]:
        await update.message.reply_photo(r[5])

# =====================================================
# 📎 ЧЕК
# =====================================================
async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.photo[-1]
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    link = upload_to_drive(
        data,
        f"check_{update.effective_user.id}_{datetime.now().strftime('%Y%m%d_%H%M')}",
        "image/jpeg",
    )

    sheet_checks.append_row(
        [
            update.effective_user.id,
            update.effective_user.username,
            "",
            "",
            "",
            link,
            "",
            datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "",
            "",
            "",
            file.file_unique_id,
            "новый",
        ]
    )

    await update.message.reply_text(
        "✅ Чек принят.\n"
        "🧠 Сейчас он будет проверен автоматически.",
    )

# =====================================================
# 🧠 GPT АДМИН
# =====================================================
async def gpt_panel(update: Update):
    await update.message.reply_text(
        "🧠 GPT панель администратора",
        reply_markup=ADMIN_PANEL,
    )


async def gpt_forecast(update: Update):
    debts = [
        f"{r['Участок']} — {r['Сумма']}₽"
        for r in sheet_users.get_all_records()
        if float(str(r.get("Сумма") or 0).replace(",", ".")) > 0
    ]

    text = "\n".join(debts) or "Нет задолженностей"

    report = await ask_gpt(
        "Ты финансовый аналитик ТСН. Спрогнозируй задолженности.",
        text,
    )

    await update.message.reply_text(report)

# =====================================================
# ⏰ АВТО-УВЕДОМЛЕНИЯ
# =====================================================
async def auto_notify(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).day

    for r in sheet_users.get_all_records():
        try:
            pay_day = int(r.get("День_оплаты") or 0)
            if not (pay_day - 5 <= today <= pay_day):
                continue

            uid = int(r["Telegram_ID"])
            fio = r.get("ФИО")

            await context.bot.send_message(
                uid,
                f"⏰ Напоминание об оплате\n\n"
                f"{fio}, напоминаем о необходимости оплатить поселковый взнос.\n"
                "Пожалуйста, произведите оплату и загрузите чек.",
            )
        except Exception as e:
            log_event("notify_error", error=str(e))

# =====================================================
# 📩 HANDLER
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "💳 Реквизиты":
        await show_reqs(update)
    elif text == "🧠 GPT панель" and is_admin(update.effective_user.id):
        await gpt_panel(update)
    elif text == "📊 GPT прогноз" and is_admin(update.effective_user.id):
        await gpt_forecast(update)
    elif text == "⬅️ Назад":
        await start(update, context)

# =====================================================
# 🚦 MAIN
# =====================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, upload_check))

    # ✅ авто-уведомления каждый день в 18:00
    app.job_queue.run_daily(
        auto_notify,
        time=time(hour=18, minute=0, tzinfo=TZ),
    )

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        webhook_url=os.getenv("WEBHOOK_URL"),
    )


if __name__ == "__main__":
    main()
