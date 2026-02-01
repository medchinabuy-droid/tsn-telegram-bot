import os, json, logging, re
from datetime import datetime, timedelta
from io import BytesIO

from fastapi import FastAPI, Request
import uvicorn

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, ContextTypes

from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials
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
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
CREDS = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
GC = gspread.authorize(CREDS)
SPREAD = GC.open_by_key(SPREADSHEET_ID)

SHEET_USERS = SPREAD.worksheet("Лист 1")
SHEET_CHECKS = SPREAD.worksheet("Лист 2")
SHEET_LOGS = SPREAD.worksheet("Лист 3")
SHEET_REKV = SPREAD.worksheet("Реквизиты")
SHEET_REQUESTS = SPREAD.worksheet("Заявки")

drive_service = build("drive", "v3", credentials=CREDS)
vision_client = vision.ImageAnnotatorClient(credentials=CREDS)

# ---------------- APP ----------------
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ---------------- UI ----------------
def main_keyboard(is_admin=False):
    kb = [
        ["🔄 Старт"],
        ["📤 Загрузить чек"],
        ["💳 Реквизиты"],
        ["📊 Статус"],
        ["ℹ️ Информация"],
    ]
    if is_admin:
        kb.append(["🛠 Админ панель"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["📣 Уведомление по участку"],
        ["📈 Статистика"],
        ["⬅️ Назад"],
    ], resize_keyboard=True)

# ---------------- HELPERS ----------------
def find_user(uid=None, username=None):
    users = SHEET_USERS.get_all_records()
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
    r = SHEET_REKV.get_all_records()[0]
    return (
        f"Получатель: {r.get('Получатель')}\n"
        f"ИНН: {r.get('ИНН')}\n"
        f"Счёт: {r.get('Счёт получателя')}\n"
        f"Банк: {r.get('Банк')}\n"
        f"БИК: {r.get('БИК')}\n"
        f"Назначение платежа: {r.get('Назначение платежа')}"
    )

def parse_receipt(text: str):
    amount = re.search(r"(\d+[.,]\d{2})", text)
    date = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    return (amount.group(1) if amount else None), (date.group(1) if date else None)

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, u = find_user(uid=user.id, username=user.username)

    if not u:
        await update.message.reply_text(
            "👋 Вы не зарегистрированы.\n\n"
            "Отправьте номер участка и ФИО для регистрации.",
            reply_markup=main_keyboard(False)
        )
        context.user_data["wait_register"] = True
        return

    await update.message.reply_text(
        f"🌿 Добро пожаловать, {u.get('ФИО')}!",
        reply_markup=main_keyboard(user.id in ADMIN_IDS)
    )

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Информация о взносах:\n\n"
        "• Взнос ежемесячный 6000 руб.\n"
        "• Сумма может отличаться при наличии льгот\n"
        "• День оплаты указан индивидуально\n"
        "• Чеки проверяются автоматически\n"
        "• При просрочке формируется задолженность\n\n"
        "📧 Контакты для пропусков: propusk@tsn-iskona-park.ru\n"
        "📧 Связь с ТСН: info@iskonapark.ru"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, u = find_user(uid=update.effective_user.id)
    if not u: return
    await update.message.reply_text(
        f"🏡 Участок: {u.get('Участок')}\n"
        f"💳 Сумма: {u.get('Сумма')}\n"
        f"📅 День оплаты: {u.get('День_оплаты')}\n"
        f"📌 Статус: {u.get('Статус')}"
    )

async def rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_requisites_text()
    qr = generate_qr(text)
    await update.message.reply_photo(qr, caption=text)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    await update.message.reply_text("🛠 Админ панель", reply_markup=admin_keyboard())

# ---------------- ROUTER ----------------
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

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
    else:
        await update.message.reply_text("Выберите действие кнопками ниже 👇")

# ---------------- WEBHOOK ----------------
@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

# ---------------- STARTUP ----------------
@app.on_event("startup")
async def startup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")
    application.add_handler(telegram.ext.MessageHandler(telegram.ext.filters.TEXT, router))
    scheduler.start()
    logger.info("🚀 Бот запущен")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
