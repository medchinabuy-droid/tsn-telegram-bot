import os
import json
import logging
from datetime import datetime, timedelta, time
from io import BytesIO

from dotenv import load_dotenv

from fastapi import FastAPI, Request
import uvicorn

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
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

import qrcode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# -------------------- CONFIG --------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(map(int, os.getenv("ADMIN_IDS", "").split(",")))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", 1000))

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# -------------------- GOOGLE --------------------

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
CREDS = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
GC = gspread.authorize(CREDS)
SPREAD = GC.open_by_key(SPREADSHEET_ID)

SHEET_USERS = SPREAD.worksheet("Лист 1")
SHEET_CHECKS = SPREAD.worksheet("Лист 2")
SHEET_LOGS = SPREAD.worksheet("Лист 3")
SHEET_REKV = SPREAD.worksheet("Реквизиты")

drive_service = build("drive", "v3", credentials=CREDS)

logger.info("✅ Google Sheets + Drive инициализированы")

# -------------------- FASTAPI + BOT --------------------

app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# -------------------- HELPERS --------------------

def log_event(event_type, uid=None, username=None, plot=None, details="", error=""):
    SHEET_LOGS.append_row([
        datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        event_type,
        uid,
        username,
        plot,
        details,
        error
    ])

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def get_all_users():
    return SHEET_USERS.get_all_records()

def find_user(uid=None, username=None):
    users = get_all_users()
    for idx, u in enumerate(users, start=2):
        if uid and str(u.get("Telegram_ID")) == str(uid):
            return idx, u
        if username and u.get("username") and u.get("username").lower() == username.lower():
            return idx, u
    return None, None

def generate_qr(text: str) -> BytesIO:
    qr = qrcode.make(text)
    bio = BytesIO()
    qr.save(bio, format="PNG")
    bio.seek(0)
    return bio

def get_requisites_text():
    rows = SHEET_REKV.get_all_records()
    if not rows:
        return "Реквизиты временно недоступны."
    r = rows[0]
    text = (
        f"Получатель: {r.get('Получатель')}\n"
        f"ИНН: {r.get('ИНН')}\n"
        f"Счёт получателя: {r.get('Счёт получателя')}\n"
        f"Банк: {r.get('Банк')}\n"
        f"БИК: {r.get('БИК')}\n"
        f"Назначение платежа: {r.get('Назначение платежа')}"
    )
    return text

def ensure_drive_folder(plot: str):
    query = f"name='Участок_{plot}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = drive_service.files().list(q=query).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    file_metadata = {
        "name": f"Участок_{plot}",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [DRIVE_FOLDER_ID],
    }
    folder = drive_service.files().create(body=file_metadata, fields="id").execute()
    return folder["id"]

def upload_to_drive(plot: str, filename: str, content: BytesIO):
    folder_id = ensure_drive_folder(plot)
    media = MediaIoBaseUpload(content, mimetype="image/jpeg")
    file_metadata = {"name": filename, "parents": [folder_id]}
    file = drive_service.files().create(body=file_metadata, media_body=media, fields="id, webViewLink").execute()
    return file.get("webViewLink")

# -------------------- UI --------------------

def main_keyboard(is_admin_user=False):
    kb = [
        ["💳 Реквизиты", "📊 Статус"],
        ["ℹ️ Информация", "🔄 Старт"],
    ]
    if is_admin_user:
        kb.append(["🛠 Админ панель", "📈 Статистика"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["📣 Напоминание по участку"],
        ["🏡 Информация по участку"],
        ["⬅️ Назад"],
    ], resize_keyboard=True)

# -------------------- HANDLERS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, u = find_user(uid=user.id, username=user.username)
    if not u:
        await update.message.reply_text(
            "👋 Приветствую!\n\n"
            "Вы ещё не привязаны к системе ТСН ИСКОНА ПАРК.\n"
            "Пожалуйста, обратитесь к администратору для регистрации."
        )
        return

    text = (
        f"🌿 Приветствую!\n\n"
        f"Вы зарегистрированы в системе ТСН ИСКОНА ПАРК.\n\n"
        f"👤 ФИО: {u.get('ФИО')}\n"
        f"🏡 Участок/Дом: {u.get('Участок')}"
    )

    await update.message.reply_text(text, reply_markup=main_keyboard(is_admin(user.id)))

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Информация о взносах:\n\n"
        "• Взнос ежемесячный 6000 руб.\n"
        "• Сумма может отличаться при наличии льгот\n"
        "• День оплаты указан индивидуально\n"
        "• Чеки проверяются автоматически\n"
        "• При просрочке формируется задолженность\n\n"
        "📧 Пропуска: propusk@tsn-iskona-park.ru\n"
        "📧 Связь с ТСН: info@iskonapark.ru"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row, u = find_user(uid=update.effective_user.id, username=update.effective_user.username)
    if not u:
        return

    await update.message.reply_text(
        f"📊 Ваш статус:\n\n"
        f"🏡 Участок: {u.get('Участок')}\n"
        f"💳 Сумма: {u.get('Сумма')} руб.\n"
        f"📅 День оплаты: {u.get('День_оплаты')}\n"
        f"📌 Статус: {u.get('Статус')}"
    )

async def rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_requisites_text()
    qr = generate_qr(text)
    await update.message.reply_photo(qr, caption=text)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🛠 Админ панель", reply_markup=admin_keyboard())

async def admin_remind_plot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["wait_plot_remind"] = True
    await update.message.reply_text("Введите номер участка:")

async def admin_plot_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["wait_plot_info"] = True
    await update.message.reply_text("Введите номер участка или фамилию:")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    total = len(users)
    debtors = sum(1 for u in users if str(u.get("Статус")).lower() == "долг")
    text = (
        "📈 Статистика ТСН:\n\n"
        f"👥 Всего собственников: {total}\n"
        f"⚠️ Должников: {debtors}\n"
        f"✅ Платят вовремя: {total - debtors}"
    )
    await update.message.reply_text(text)

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    users = get_all_users()

    if context.user_data.get("wait_plot_remind"):
        plot = text
        sent = 0
        for u in users:
            if str(u.get("Участок")) == plot:
                uid = u.get("Telegram_ID")
                username = u.get("username")
                msg = (
                    "🔔 Уведомление от правления ТСН «ИСКОНА ПАРК»\n\n"
                    "Напоминаем о необходимости оплаты поселкового взноса.\n"
                    "Благодарим за своевременную оплату 🙏"
                )
                try:
                    if uid:
                        await context.bot.send_message(int(uid), msg)
                        sent += 1
                    elif username:
                        await context.bot.send_message(f"@{username}", msg)
                        sent += 1
                except Exception as e:
                    log_event("error", error=str(e))
        context.user_data.clear()
        await update.message.reply_text(f"✅ Отправлено уведомлений: {sent}")
        return

    if context.user_data.get("wait_plot_info"):
        query = text.lower()
        found = []
        for u in users:
            if query in str(u.get("Участок")).lower() or query in str(u.get("ФИО")).lower():
                found.append(u)

        if not found:
            await update.message.reply_text("❌ Ничего не найдено.")
        else:
            for u in found:
                await update.message.reply_text(
                    f"🏡 Участок: {u.get('Участок')}\n"
                    f"👤 ФИО: {u.get('ФИО')}\n"
                    f"📞 Телефон: {u.get('Телефон')}\n"
                    f"📨 Telegram: {u.get('username')}\n"
                    f"💳 Сумма: {u.get('Сумма')}\n"
                    f"📅 День оплаты: {u.get('День_оплаты')}\n"
                    f"📌 Статус: {u.get('Статус')}"
                )
        context.user_data.clear()
        return

# -------------------- ROUTER --------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "🔄 Старт":
        await start(update, context)
    elif text == "ℹ️ Информация":
        await info(update, context)
    elif text == "📊 Статус":
        await status(update, context)
    elif text == "💳 Реквизиты":
        await rekv(update, context)
    elif text == "🛠 Админ панель":
        await admin_panel(update, context)
    elif text == "📣 Напоминание по участку":
        await admin_remind_plot(update, context)
    elif text == "🏡 Информация по участку":
        await admin_plot_info(update, context)
    elif text == "📈 Статистика":
        await admin_stats(update, context)
    elif text == "⬅️ Назад":
        await start(update, context)
    else:
        await handle_admin_text(update, context)

# -------------------- WEBHOOK --------------------

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

# -------------------- STARTUP --------------------

@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")
    scheduler.start()
    logger.info("🚀 Бот запущен")

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()

# -------------------- MAIN --------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
