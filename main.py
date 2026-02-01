import os
import json
import logging
import re
from datetime import datetime, timedelta
from io import BytesIO

from dotenv import load_dotenv
from fastapi import FastAPI, Request
import uvicorn

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import gspread
from google.oauth2.service_account import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from google.cloud import vision
import qrcode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ---------------- CONFIG ----------------

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

# ---------------- GOOGLE ----------------

creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

CREDS = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
GC = gspread.authorize(CREDS)
SPREAD = GC.open_by_key(SPREADSHEET_ID)

SHEET_USERS = SPREAD.worksheet("Лист 1")
SHEET_CHECKS = SPREAD.worksheet("Лист 2")
SHEET_LOGS = SPREAD.worksheet("Лист 3")
SHEET_REKV = SPREAD.worksheet("Реквизиты")

drive_service = build("drive", "v3", credentials=CREDS)

vision_creds = service_account.Credentials.from_service_account_info(creds_dict)
vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)

# ---------------- BOT + FASTAPI ----------------

app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ---------------- HELPERS ----------------

def is_admin(uid: int):
    return uid in ADMIN_IDS

def get_all_users():
    return SHEET_USERS.get_all_records()

def find_user(uid=None, username=None):
    for i, u in enumerate(get_all_users(), start=2):
        if uid and str(u.get("Telegram_ID")) == str(uid):
            return i, u
        if username and u.get("username") and u["username"].lower() == username.lower():
            return i, u
    return None, None

def ocr_receipt(image_bytes: bytes):
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    text = response.text_annotations[0].description if response.text_annotations else ""
    return text

def parse_sum(text: str):
    m = re.search(r"(\d+[.,]\d{2})", text.replace(",", "."))
    return m.group(1) if m else None

def parse_date(text: str):
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    return m.group(1) if m else None

# ---------------- UI ----------------

def main_keyboard(is_admin=False):
    kb = [["💳 Реквизиты", "📊 Статус"], ["ℹ️ Информация"]]
    if is_admin:
        kb.append(["🛠 Админ", "📈 Статистика"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    _, u = find_user(uid=user.id, username=user.username)
    if not u:
        await update.message.reply_text("Вы не зарегистрированы. Обратитесь к администратору.")
        return
    await update.message.reply_text("Добро пожаловать!", reply_markup=main_keyboard(is_admin(user.id)))

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, u = find_user(uid=update.effective_user.id, username=update.effective_user.username)
    if not u:
        return
    await update.message.reply_text(f"Ваш статус: {u.get('Статус')}")

async def rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = SHEET_REKV.get_all_records()[0]
    text = f"{r['Получатель']}\n{r['Счёт получателя']}\n{r['Банк']}\n{r['Назначение платежа']}"
    qr = qrcode.make(text)
    bio = BytesIO()
    qr.save(bio, format="PNG")
    bio.seek(0)
    await update.message.reply_photo(bio, caption=text)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    debtors = [u for u in users if str(u.get("Статус")).lower() == "долг"]
    await update.message.reply_text(
        f"Всего: {len(users)}\nДолжники: {len(debtors)}\nПлатят: {len(users)-len(debtors)}"
    )

# ---------------- ROUTER ----------------

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "📊 Статус":
        await status(update, context)
    elif t == "💳 Реквизиты":
        await rekv(update, context)
    elif t == "📈 Статистика":
        await admin_stats(update, context)
    else:
        await start(update, context)

# ---------------- WEBHOOK ----------------

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

# ---------------- STARTUP ----------------

@app.on_event("startup")
async def on_startup():
    await application.initialize()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))
    await application.start()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")
    scheduler.start()
    logger.info("🚀 Бот запущен")

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()

# ---------------- MAIN ----------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
