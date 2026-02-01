import os
import json
import logging
import re
from datetime import datetime
from io import BytesIO

from dotenv import load_dotenv

from fastapi import FastAPI, Request
import uvicorn

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.cloud import vision

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
    "https://www.googleapis.com/auth/cloud-vision"
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
vision_client = vision.ImageAnnotatorClient(credentials=CREDS)

logger.info("✅ Google Sheets + Drive + Vision инициализированы")

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
    r = rows[0]
    return (
        f"Получатель: {r.get('Получатель')}\n"
        f"ИНН: {r.get('ИНН')}\n"
        f"Счёт: {r.get('Счёт получателя')}\n"
        f"Банк: {r.get('Банк')}\n"
        f"БИК: {r.get('БИК')}\n"
        f"Назначение: {r.get('Назначение платежа')}"
    )

def ensure_drive_folder(plot: str):
    query = f"name='Участок_{plot}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    files = drive_service.files().list(q=query).execute().get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": f"Участок_{plot}", "mimeType": "application/vnd.google-apps.folder", "parents": [DRIVE_FOLDER_ID]}
    folder = drive_service.files().create(body=meta, fields="id").execute()
    return folder["id"]

def upload_to_drive(plot: str, filename: str, content: BytesIO):
    folder_id = ensure_drive_folder(plot)
    media = MediaIoBaseUpload(content, mimetype="image/jpeg")
    meta = {"name": filename, "parents": [folder_id]}
    file = drive_service.files().create(body=meta, media_body=media, fields="id, webViewLink").execute()
    return file["webViewLink"]

def ocr_receipt(image_bytes: bytes):
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    text = response.text_annotations[0].description if response.text_annotations else ""
    return text

def parse_amount_and_date(text: str):
    amount_match = re.search(r'(\d+[.,]\d{2})\s*₽', text)
    date_match = re.search(r'(\d{2}\.\d{2}\.\d{4})', text)
    amount = amount_match.group(1).replace(",", ".") if amount_match else None
    date = date_match.group(1) if date_match else None
    return amount, date

def allocate_payment(sum_paid: float, monthly_fee: float):
    months = int(sum_paid // monthly_fee)
    remainder = round(sum_paid - months * monthly_fee, 2)
    return months, remainder

# -------------------- UI --------------------

def main_keyboard(is_admin_user=False):
    kb = [
        ["💳 Реквизиты", "📊 Статус"],
        ["ℹ️ Информация", "🔄 Старт"],
    ]
    if is_admin_user:
        kb.append(["🛠 Админ панель", "📈 Статистика"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# -------------------- HANDLERS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, u = find_user(uid=user.id, username=user.username)
    if not u:
        await update.message.reply_text("👋 Вы не зарегистрированы в системе ТСН.")
        return

    text = (
        f"🌿 Добро пожаловать в ТСН ИСКОНА ПАРК!\n\n"
        f"👤 {u.get('ФИО')}\n"
        f"🏡 Участок: {u.get('Участок')}\n\n"
        f"Мы рады, что вы с нами 😊"
    )
    await update.message.reply_text(text, reply_markup=main_keyboard(is_admin(user.id)))

async def rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_requisites_text()
    qr = generate_qr(text)
    await update.message.reply_photo(qr, caption=text)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, u = find_user(uid=update.effective_user.id, username=update.effective_user.username)
    amount = u.get("Сумма") or "—"
    await update.message.reply_text(
        f"📊 Ваш статус:\n\n"
        f"🏡 Участок: {u.get('Участок')}\n"
        f"💳 Сумма: {amount} руб.\n"
        f"📅 День оплаты: {u.get('День_оплаты')}\n"
        f"📌 Статус: {u.get('Статус')}"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, u = find_user(uid=user.id, username=user.username)
    if not u:
        await update.message.reply_text("Вы не зарегистрированы.")
        return

    photo = await update.message.photo[-1].get_file()
    bio = BytesIO()
    await photo.download_to_memory(out=bio)
    bio.seek(0)

    ocr_text = ocr_receipt(bio.getvalue())
    amount, date = parse_amount_and_date(ocr_text)

    plot = u.get("Участок")
    link = upload_to_drive(plot, f"check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg", bio)

    SHEET_CHECKS.append_row([
        user.id,
        user.username,
        u.get("ФИО"),
        plot,
        u.get("Телефон"),
        link,
        amount,
        datetime.now().strftime("%d.%m.%Y"),
        date,
        date,
        ocr_text[:500],
        "нет",
        photo.file_id,
        "принят"
    ])

    SHEET_USERS.update_cell(row, list(SHEET_USERS.row_values(1)).index("Статус") + 1, "оплачено")

    await update.message.reply_text(
        "✅ Чек принят автоматически!\n\n"
        f"💰 Сумма по чеку: {amount or 'не распознана'}\n"
        f"📅 Дата по чеку: {date or 'не распознана'}\n\n"
        "Если есть ошибка — сообщите администратору."
    )

# -------------------- ROUTER --------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🔄 Старт":
        await start(update, context)
    elif text == "💳 Реквизиты":
        await rekv(update, context)
    elif text == "📊 Статус":
        await status(update, context)
    else:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню.")

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
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

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
