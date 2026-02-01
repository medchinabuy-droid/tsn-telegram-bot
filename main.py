import os
import json
import logging
from io import BytesIO
from datetime import datetime, date, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

import gspread
from google.oauth2.service_account import Credentials

from google.cloud import vision_v1 as vision  # ✅ фикс импорта

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

# ---------------- CONFIG ----------------

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- GOOGLE CREDS ----------------

creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)

gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID)
SHEET_USERS = sheet.worksheet("Лист 1")
SHEET_CHECKS = sheet.worksheet("Лист 2")

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/tmp/google_creds.json"
with open("/tmp/google_creds.json", "w") as f:
    json.dump(creds_dict, f)

vision_client = vision.ImageAnnotatorClient()

# ---------------- FASTAPI + TG ----------------

app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

# ---------------- UI ----------------

def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("ℹ️ Информация о взносах")],
        [KeyboardButton("📤 Загрузить чек")]
    ], resize_keyboard=True)

INFO_TEXT = """ℹ️ Информация о взносах:

• Взнос ежемесячный 6000 руб.
• Сумма может отличаться при наличии льгот
• День оплаты указан индивидуально
• Чеки проверяются автоматически
• При просрочке формируется задолженность

📧 Контакты для пропусков: propusk@tsn-iskona-park.ru
📧 Связь с ТСН: info@iskonapark.ru
"""

# ---------------- HELPERS ----------------

def parse_date_ddmmyyyy(s):
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except:
        return None

def get_all_users():
    rows = SHEET_USERS.get_all_records()
    return rows

def find_user(uid=None, username=None):
    users = get_all_users()
    for i, u in enumerate(users, start=2):
        if uid and str(u.get("Telegram_ID")) == str(uid):
            return i, u
    for i, u in enumerate(users, start=2):
        if username and str(u.get("username")).lower() == str(username).lower():
            return i, u
    return None, None

def ocr_check(image_bytes: bytes) -> str:
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    texts = response.text_annotations
    return texts[0].description if texts else ""

# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Добро пожаловать в бот ТСН «Искона Парк» 👋\nВыберите действие:",
        reply_markup=main_keyboard()
    )

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(INFO_TEXT)

async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, u = find_user(uid=user.id, username=user.username)
    if not u:
        await update.message.reply_text("Вы не найдены в реестре. Обратитесь в правление.")
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    bio = BytesIO()
    await file.download_to_memory(out=bio)
    bio.seek(0)

    text = ocr_check(bio.getvalue())

    amount = None
    check_date = None

    for t in text.replace(",", ".").split():
        if t.replace(".", "").isdigit() and len(t) > 3:
            amount = t
        d = parse_date_ddmmyyyy(t)
        if d:
            check_date = d.strftime("%d.%m.%Y")

    SHEET_CHECKS.append_row([
        user.id, user.username, u.get("ФИО"), u.get("Дом"), u.get("Телефон"),
        "", amount, datetime.now().strftime("%d.%m.%Y %H:%M"),
        check_date, check_date, text[:500], "", photo.file_id, "оплачено"
    ])

    await update.message.reply_text(
        f"✅ Чек принят автоматически.\n"
        f"💳 Сумма: {amount}\n"
        f"📅 Дата: {check_date}"
    )

# ---------------- JOBS ----------------

async def reminders_job():
    today = date.today()
    for u in get_all_users():
        uid = u.get("Telegram_ID")
        pay_day = parse_date_ddmmyyyy(u.get("День_оплаты", ""))
        status = str(u.get("Статус", "")).lower()

        if not pay_day:
            continue

        delta = (pay_day - today).days

        if delta in [5, 3, 1]:
            msg = f"🔔 Напоминание: через {delta} дн. день оплаты взноса."
        elif delta < 0 and status != "оплачено":
            msg = "⚠️ У вас задолженность по взносам. Просим срочно оплатить."
        else:
            continue

        try:
            if uid:
                await application.bot.send_message(int(uid), msg)
        except Exception as e:
            logger.error(e)

async def birthday_job():
    today = date.today().strftime("%d.%m")
    for u in get_all_users():
        dr = u.get("Дата_рождения")
        if dr and dr.startswith(today):
            uid = u.get("Telegram_ID")
            try:
                await application.bot.send_message(
                    int(uid),
                    "🎉 Поздравляем с Днём Рождения! Желаем здоровья и благополучия! С уважением, правление ТСН 🌿"
                )
            except:
                pass

# ---------------- WEB ----------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    users = get_all_users()
    total = len(users)
    debtors = sum(1 for u in users if str(u.get("Статус")).lower() == "долг")

    return f"""
    <html>
    <head><title>ТСН Дашборд</title></head>
    <body style="font-family:Arial;">
        <h1>ТСН «Искона Парк»</h1>
        <p>Всего участков: {total}</p>
        <p>Должников: {debtors}</p>
        <p>Оплатили: {total - debtors}</p>
    </body>
    </html>
    """

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

# ---------------- STARTUP ----------------

@app.on_event("startup")
async def startup():
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^ℹ️"), info))
    application.add_handler(MessageHandler(filters.PHOTO, handle_check))

    await application.initialize()
    await application.start()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")

    scheduler.add_job(reminders_job, "cron", hour=10)
    scheduler.add_job(birthday_job, "cron", hour=9)
    scheduler.start()

    logger.info("🚀 TSN BOT ЗАПУЩЕН")

@app.on_event("shutdown")
async def shutdown():
    await application.stop()
    await application.shutdown()

# ---------------- MAIN ----------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
