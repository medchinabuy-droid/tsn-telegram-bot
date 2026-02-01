import os
import json
import logging
from datetime import datetime, timedelta, date
from io import BytesIO

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
import uvicorn

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# ---------------- CONFIG ----------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(map(int, os.getenv("ADMIN_IDS", "").split(",")))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", 1000))
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# ---------------- GOOGLE ----------------

creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDS = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
GC = gspread.authorize(CREDS)
SPREAD = GC.open_by_key(SPREADSHEET_ID)

SHEET_USERS = SPREAD.worksheet("Лист 1")

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

def parse_date_ddmmyyyy(s):
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except:
        return None

# ---------------- UI ----------------

def main_keyboard(is_admin=False):
    kb = [["📊 Статус", "ℹ️ Информация"]]
    if is_admin:
        kb.append(["📈 Статистика", "📄 PDF отчёт"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Добро пожаловать в ТСН ИСКОНА ПАРК 🌿", reply_markup=main_keyboard(is_admin(update.effective_user.id)))

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, u = find_user(uid=update.effective_user.id, username=update.effective_user.username)
    if not u:
        await update.message.reply_text("Вы не привязаны.")
        return
    await update.message.reply_text(f"Ваш статус: {u.get('Статус')}")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    debtors = [u for u in users if str(u.get("Статус")).lower() == "долг"]
    await update.message.reply_text(
        f"📈 Статистика:\n\n"
        f"Всего: {len(users)}\n"
        f"Должники: {len(debtors)}\n"
        f"Платят вовремя: {len(users) - len(debtors)}"
    )

async def admin_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    file_path = "/mnt/data/report.pdf"

    doc = SimpleDocTemplate(file_path)
    styles = getSampleStyleSheet()
    story = [Paragraph("Отчёт по задолженностям ТСН ИСКОНА ПАРК", styles["Title"]), Spacer(1, 12)]

    table_data = [["Участок", "ФИО", "Статус", "Сумма"]]
    for u in users:
        table_data.append([u.get("Участок"), u.get("ФИО"), u.get("Статус"), u.get("Сумма")])

    table = Table(table_data)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
    ]))

    story.append(table)
    doc.build(story)

    await update.message.reply_document(document=open(file_path, "rb"), filename="report.pdf")

# ---------------- NOTIFICATIONS ----------------

async def payment_notifications():
    today = date.today()
    users = get_all_users()

    for u in users:
        uid = u.get("Telegram_ID")
        pay_day = u.get("День_оплаты")
        if not uid or not pay_day:
            continue

        try:
            pay_day = int(pay_day)
            pay_date = date(today.year, today.month, pay_day)
        except:
            continue

        delta = (pay_date - today).days

        if delta in (5, 3, 1):
            msg = "🔔 Напоминание: скоро день оплаты взноса. Благодарим за своевременную оплату 🙏"
        elif delta < 0 and str(u.get("Статус")).lower() == "долг":
            msg = "⚠️ У вас задолженность по взносам. Просим срочно погасить долг."
        else:
            continue

        try:
            await application.bot.send_message(chat_id=int(uid), text=msg)
        except Exception as e:
            logger.error(e)

async def birthday_notifications():
    today = date.today()
    users = get_all_users()

    for u in users:
        uid = u.get("Telegram_ID")
        bday = parse_date_ddmmyyyy(u.get("Дата_рождения", ""))
        if not uid or not bday:
            continue

        if bday.day == today.day and bday.month == today.month:
            msg = "🎉 Поздравляем с Днём Рождения! 🎂\nЖелаем здоровья, благополучия и отличного настроения!\n\nС уважением, правление ТСН ИСКОНА ПАРК 🌿"
            try:
                await application.bot.send_message(chat_id=int(uid), text=msg)
            except Exception as e:
                logger.error(e)

# ---------------- DASHBOARD ----------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    users = get_all_users()
    total = len(users)
    debtors = len([u for u in users if str(u.get("Статус")).lower() == "долг"])

    html = f"""
    <html>
    <head><title>ТСН ИСКОНА ПАРК — Дашборд</title></head>
    <body>
        <h1>Дашборд ТСН ИСКОНА ПАРК</h1>
        <p>Всего собственников: {total}</p>
        <p>Должники: {debtors}</p>
        <p>Оплачивают вовремя: {total - debtors}</p>
    </body>
    </html>
    """
    return html

# ---------------- ROUTER ----------------

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "📊 Статус":
        await status(update, context)
    elif t == "📈 Статистика":
        await admin_stats(update, context)
    elif t == "📄 PDF отчёт":
        await admin_pdf(update, context)
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

    scheduler.add_job(payment_notifications, "cron", hour=10)
    scheduler.add_job(birthday_notifications, "cron", hour=9)
    scheduler.start()

    logger.info("🚀 Бот запущен")

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()

# ---------------- MAIN ----------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
