# main.py
import os, json, re, logging, hashlib
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, ContextTypes, MessageHandler, CommandHandler, filters

import gspread
from google.oauth2.service_account import Credentials
from google.cloud import vision

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from openai import OpenAI

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook/secure"
PORT = int(os.getenv("PORT", "1000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ADMINS = set(map(int, os.getenv("ADMINS", "").split(","))) if os.getenv("ADMINS") else set()
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
BASE_CHECKS_DIR = Path("Чеки ТСН")

GPT_MODEL = "gpt-4.1-mini"  # оптимально по цене/качеству

# ================== GOOGLE ==================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
gc = gspread.authorize(creds)
SPREAD = gc.open_by_key(SPREADSHEET_ID)
vision_client = vision.ImageAnnotatorClient(credentials=creds)

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# ================== FASTAPI + BOT ==================
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

# ================== SHEETS ==================
def get_or_create_sheet(title: str, headers: List[str]):
    try:
        ws = SPREAD.worksheet(title)
        if ws.row_values(1) != headers:
            ws.update("A1", [headers])
        return ws
    except:
        ws = SPREAD.add_worksheet(title=title, rows=3000, cols=40)
        ws.update("A1", [headers])
        return ws

SHEET_USERS = get_or_create_sheet("Лист 1", [
    "Участок","ФИО","Telegram_ID","username","Телефон","День_оплаты","Электро","Сумма",
    "Дата","Статус","Роль","Дата_напоминания","Дата_рождения","Дата_регистрации",
    "Последняя_оплата","Комментарий_админа","Активен"
])

SHEET_CHECKS = get_or_create_sheet("Лист 2", [
    "telegram_id","username","ФИО","Дом","Телефон","Ссылка_на_чек","Сумма_по_чеку",
    "Дата_загрузки","Дата_чека","OCR_Дата","OCR","Дубль_чека","File_Unique_ID","Статус"
])

SHEET_REKV = get_or_create_sheet("Реквизиты", [
    "Ключ","Значение","Счёт получателя","Получатель","ИНН","Назначение платежа","QR_оплата"
])

# ================== HELPERS ==================
def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

def is_duplicate(file_uid: str):
    rows = SHEET_CHECKS.get_all_records()
    for r in rows:
        if r.get("File_Unique_ID") == file_uid:
            return True
    return False

def gpt_parse_receipt(ocr_text: str) -> Dict:
    prompt = f"""
Ты помощник для разбора банковских чеков.
Из текста ниже вытащи JSON:
{{
  "amount": число или null,
  "date": "ДД.ММ.ГГГГ" или null,
  "months": ["2026-01","2026-02"] если можно понять месяцы оплаты
}}

Текст OCR:
{ocr_text}
"""
    resp = openai_client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    txt = resp.choices[0].message.content
    try:
        return json.loads(re.search(r"\{.*\}", txt, re.S).group())
    except:
        return {"amount": None, "date": None, "months": []}

# ================== DASHBOARD ==================
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(plot: str = None):
    users = SHEET_USERS.get_all_records()
    if plot:
        users = [u for u in users if str(u.get("Участок")) == plot]

    rows_html = "".join([
        f"<tr><td>{u.get('Участок')}</td><td>{u.get('ФИО')}</td><td>{u.get('Статус')}</td><td>{u.get('Сумма')}</td></tr>"
        for u in users
    ])

    return HTMLResponse(f"""
    <html>
    <head>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
      <h1>🛠 Админ-дашборд ТСН</h1>

      <form>
        Фильтр по участку:
        <input name="plot" placeholder="81" />
        <button>Фильтровать</button>
      </form>

      <table border="1">
        <tr><th>Участок</th><th>ФИО</th><th>Статус</th><th>Сумма</th></tr>
        {rows_html}
      </table>

      <canvas id="chart"></canvas>
      <script>
        new Chart(document.getElementById('chart'), {{
          type: 'pie',
          data: {{
            labels: ['Оплачено', 'Долг'],
            datasets: [{{ data: [
              {len([u for u in users if u.get("Статус")=="оплачено"])},
              {len([u for u in users if u.get("Статус")!="оплачено"])}
            ] }}]
          }}
        }});
      </script>
    </body>
    </html>
    """)

# ================== BOT: GPT CHAT FOR ADMIN ==================
async def gpt_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return
    question = update.message.text
    resp = openai_client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": question}],
        temperature=0.4
    )
    answer = resp.choices[0].message.content
    await update.message.reply_text("🤖 GPT:\n" + answer)

# ================== WEBHOOK ==================
@app.post(WEBHOOK_PATH)
async def webhook(req: Request):
    data = await req.json()
    await application.update_queue.put(Update.de_json(data, application.bot))
    return {"ok": True}

@app.on_event("startup")
async def startup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)
    scheduler.start()
    logger.info("🚀 Bot started")

# ================== HANDLERS ==================
application.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("🚀 Нажмите Старт")))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("GPT"), gpt_chat))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
