import os
import json
import logging
from io import BytesIO
from datetime import datetime, timedelta, date

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
)
from telegram.ext import (
    Application, ContextTypes,
    MessageHandler, CommandHandler, filters
)

import gspread
from google.oauth2.service_account import Credentials

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# ================== CONFIG ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook/secure"
PORT = int(os.getenv("PORT", "1000"))

ADMINS = set(map(int, os.getenv("ADMINS", "").split(","))) if os.getenv("ADMINS") else set()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# ================== LOGGING ==================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# ================== GOOGLE SHEETS ==================

creds_info = json.loads(GOOGLE_CREDS_JSON)
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
gc = gspread.authorize(creds)
SPREAD = gc.open_by_key(SPREADSHEET_ID)

def get_or_create_worksheet(title, rows=1000, cols=30):
    try:
        return SPREAD.worksheet(title)
    except Exception:
        ws = SPREAD.add_worksheet(title=title, rows=rows, cols=cols)
        return ws

SHEET_USERS = get_or_create_worksheet("Лист 1")
SHEET_CHECKS = get_or_create_worksheet("Лист 2")
SHEET_REKV = get_or_create_worksheet("Реквизиты")

# ================== FASTAPI ==================

app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

# ================== KEYBOARDS ==================

def user_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("ℹ️ Информация"), KeyboardButton("💳 Реквизиты")],
        [KeyboardButton("📊 Статус"), KeyboardButton("📈 Статистика")],
        [KeyboardButton("📎 Загрузить чек")],
    ], resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🛠 Админ"), KeyboardButton("📈 Статистика")],
    ], resize_keyboard=True)

# ================== HELPERS ==================

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def find_user(tg_id, username):
    records = SHEET_USERS.get_all_records()
    for i, r in enumerate(records, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    for i, r in enumerate(records, start=2):
        if r.get("username") == username:
            return i, r
    return None, None

# ================== HANDLERS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, rec = find_user(user.id, user.username)
    if not rec:
        SHEET_USERS.append_row([ "", "", "", "", "", "", "", "", user.id, user.username ])
        await update.message.reply_text(
            "👋 Вы зарегистрированы в системе. Теперь можете загружать чеки.",
            reply_markup=user_kb()
        )
    else:
        await update.message.reply_text("Добро пожаловать!", reply_markup=user_kb())

async def info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ Информация о взносах:\n\n"
        "• Взнос ежемесячный 6000 руб.\n"
        "• Сумма может отличаться при наличии льгот\n"
        "• День оплаты указан индивидуально\n"
        "• Чеки проверяются автоматически\n"
        "• При просрочке формируется задолженность\n\n"
        "📧 Контакты для пропусков: propusk@tsn-iskona-park.ru\n"
        "📧 Связь с ТСН: info@iskonapark.ru"
    )
    await update.message.reply_text(text, reply_markup=user_kb())

async def rekv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = SHEET_REKV.get_all_values()
    text = "💳 Реквизиты для оплаты:\n\n"
    for row in rows[1:]:
        text += f"{row[0]}: {row[1]}\n"
    await update.message.reply_text(text, reply_markup=user_kb())

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    _, rec = find_user(user.id, user.username)
    if not rec:
        await update.message.reply_text("Вы не зарегистрированы.")
        return
    text = (
        f"📊 Ваш статус:\n\n"
        f"Участок: {rec.get('Участок')}\n"
        f"Статус: {rec.get('Статус')}\n"
        f"Задолженность: {rec.get('Сумма') or '—'}"
    )
    await update.message.reply_text(text, reply_markup=user_kb())

async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📈 Статистика доступна в веб-кабинете:\n"
        f"{WEBHOOK_URL}",
        reply_markup=user_kb()
    )

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    await update.message.reply_text("🛠 Админ-панель", reply_markup=admin_kb())

# ================== PDF ==================

def generate_pdf():
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    users = SHEET_USERS.get_all_records()
    for u in users:
        c.drawString(40, y, f"{u.get('ФИО')} | Участок {u.get('Участок')} | {u.get('Статус')}")
        y -= 15
        if y < 50:
            c.showPage()
            y = 800
    c.save()
    buf.seek(0)
    return buf

# ================== DASHBOARD ==================

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
    <html>
    <head>
      <title>ТСН Дашборд</title>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
      <h2>📈 Статистика ТСН</h2>
      <canvas id="chart"></canvas>
      <script>
        const ctx = document.getElementById('chart');
        new Chart(ctx, {
          type: 'bar',
          data: {
            labels: ['Оплачено', 'Долг'],
            datasets: [{
              label: 'Статусы',
              data: [10, 3]
            }]
          }
        });
      </script>
    </body>
    </html>
    """

@app.post(WEBHOOK_PATH)
async def telegram_webhook(req: Request):
    data = await req.json()
    await application.update_queue.put(Update.de_json(data, application.bot))
    return {"ok": True}

# ================== STARTUP ==================

@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)
    scheduler.start()
    logger.info("🚀 Bot started")

# ================== HANDLERS BIND ==================

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^ℹ️ Информация$"), info_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^💳 Реквизиты$"), rekv_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📊 Статус$"), status_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📈 Статистика$"), stats_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^🛠 Админ$"), admin_handler))

# ================== RUN ==================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
