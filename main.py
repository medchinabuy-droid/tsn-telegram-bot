# main.py
import os, json, re, logging
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

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook/secure"
PORT = int(os.getenv("PORT", "1000"))

ADMINS = set(map(int, os.getenv("ADMINS", "").split(","))) if os.getenv("ADMINS") else set()
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
BASE_CHECKS_DIR = Path("Чеки ТСН")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# ================== GOOGLE ==================
gc = gspread.authorize(creds)
SPREAD = gc.open_by_key(SPREADSHEET_ID)

def get_or_create_sheet(title: str, headers: List[str]):
    try:
        ws = SPREAD.worksheet(title)
        if ws.row_values(1) != headers:
            ws.update("A1", [headers])
        return ws
    except:
        ws = SPREAD.add_worksheet(title=title, rows=2000, cols=40)
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

vision_client = vision.ImageAnnotatorClient(credentials=creds)

# ================== FASTAPI + BOT ==================
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

# ================== KEYBOARDS ==================
def user_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Ваш статус"), KeyboardButton("💳 Реквизиты")],
        [KeyboardButton("📎 Загрузить чек"), KeyboardButton("🚀 Старт")]
    ], resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Ваш статус"), KeyboardButton("💳 Реквизиты")],
        [KeyboardButton("📎 Загрузить чек"), KeyboardButton("🚀 Старт")],
        [KeyboardButton("🛠 Админ-панель"), KeyboardButton("📣 Массовые уведомления")]
    ], resize_keyboard=True)

def start_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("🚀 Старт")]], resize_keyboard=True)

# ================== HELPERS ==================
MONTHS = {
    "янв": 1, "январ": 1, "january": 1,
    "фев": 2, "феврал": 2, "feb": 2,
    "мар": 3, "март": 3,
    "апр": 4, "апрел": 4,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9, "сент": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}

def parse_months(text: str, year: int):
    text = text.lower()
    found = set()
    for k, v in MONTHS.items():
        if k in text:
            found.add(f"{year}-{v:02d}")
    nums = re.findall(r"\b(0?[1-9]|1[0-2])\b", text)
    for n in nums:
        found.add(f"{year}-{int(n):02d}")
    return sorted(found)

def detect_bank(text: str):
    t = text.lower()
    if "сбер" in t: return "Сбер"
    if "тинькофф" in t or "tinkoff" in t: return "Тинькофф"
    if "втб" in t: return "ВТБ"
    if "альфа" in t: return "Альфа"
    return "Неизвестно"

def get_users():
    return SHEET_USERS.get_all_records()

def find_user(uid):
    for i, r in enumerate(get_users(), start=2):
        if str(r.get("Telegram_ID")) == str(uid):
            return i, r
    return None, None

def update_user(row_i, updates: Dict):
    for col, val in updates.items():
        col_idx = SHEET_USERS.find(col).col
        SHEET_USERS.update_cell(row_i, col_idx, val)

def get_or_create_month_sheet(ym: str):
    headers = ["Дата","Telegram_ID","ФИО","Участок","Сумма","Источник"]
    return get_or_create_sheet(ym, headers)

def parse_sum_and_date(text: str):
    amount = None
    dt = None
    m = re.search(r"(\d{3,6})\s?руб", text.lower())
    if m:
        amount = int(m.group(1))
    d = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    if d:
        dt = d.group(1)
    return amount, dt

# ================== BOT ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row_i, user = find_user(uid)
    kb = admin_kb() if uid in ADMINS else user_kb()
    if user:
        fio = user.get("ФИО") or update.effective_user.full_name
        await update.message.reply_text(f"С возвращением, {fio} 👋", reply_markup=kb)
    else:
        SHEET_USERS.append_row(["","","",update.effective_user.username,"","","","","не оплачено","","", "", "", str(date.today()), "", "", "TRUE"])
        await update.message.reply_text("Привет! Мы вас зарегистрировали 👋", reply_markup=kb)

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row_i, u = find_user(update.effective_user.id)
    if not u:
        return
    await update.message.reply_text(
        f"📊 Ваш статус:\n\n"
        f"Участок: {u.get('Участок')}\n"
        f"Сумма: {u.get('Сумма')}\n"
        f"День оплаты: {u.get('День_оплаты')}\n"
        f"Статус: {u.get('Статус')}",
        reply_markup=admin_kb() if update.effective_user.id in ADMINS else user_kb()
    )

async def rekv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = SHEET_REKV.get_all_records()
    txt, qr = [], None
    for r in rows:
        if r.get("QR_оплата"):
            qr = r.get("QR_оплата")
        else:
            txt.append(f"{r.get('Ключ')}: {r.get('Значение')}")
    await update.message.reply_text("💳 Реквизиты:\n" + "\n".join(txt))
    if qr:
        await update.message.reply_photo(qr)

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row_i, user = find_user(uid)

    photo = update.message.photo[-1]
    file = await photo.get_file()

    folder = BASE_CHECKS_DIR / f"user_{uid}"
    folder.mkdir(parents=True, exist_ok=True)
    fname = folder / f"check_{datetime.now().timestamp()}.jpg"
    await file.download_to_drive(str(fname))

    with open(fname, "rb") as f:
        img = vision.Image(content=f.read())
    res = vision_client.text_detection(image=img)
    text = res.full_text_annotation.text if res.text_annotations else ""

    amount, dt = parse_sum_and_date(text)
    bank = detect_bank(text)

    context.user_data["last_check"] = {
        "amount": amount,
        "dt": dt,
        "bank": bank,
        "file": str(fname)
    }

    expected = int(user.get("Сумма") or 0)
    if amount and expected and amount != expected:
        await update.message.reply_text(
            f"⚠️ В чеке сумма {amount}₽, а по участку {expected}₽.\n"
            f"За какие месяцы платёж? Напишите: например «янв февраль»"
        )
    else:
        ym = datetime.now().strftime("%Y-%m")
        ws = get_or_create_month_sheet(ym)
        ws.append_row([str(datetime.now()), uid, user.get("ФИО"), user.get("Участок"), amount, bank])
        await update.message.reply_text("✅ Чек принят и учтён за текущий месяц")

async def months_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get("last_check")
    if not data:
        return
    months = parse_months(update.message.text, datetime.now().year)
    row_i, user = find_user(update.effective_user.id)

    for ym in months:
        ws = get_or_create_month_sheet(ym)
        ws.append_row([str(datetime.now()), update.effective_user.id, user.get("ФИО"), user.get("Участок"), data["amount"], data["bank"]])

    await update.message.reply_text(f"✅ Учёл оплату за месяцы: {', '.join(months)}")
    context.user_data["last_check"] = None

# ================== APSCHEDULER ==================
async def notify_payments():
    today = date.today().day
    for u in get_users():
        try:
            pay_day = int(u.get("День_оплаты") or 0)
            tg_id = int(u.get("Telegram_ID"))
            fio = u.get("ФИО")
            delta = pay_day - today
            if delta in (5, 3, 1):
                await application.bot.send_message(tg_id, f"{fio}, напоминание об оплате через {delta} дн.")
            elif delta < 0 and u.get("Статус") != "оплачено":
                await application.bot.send_message(tg_id, f"{fio}, у вас задолженность по взносам.")
        except:
            pass

async def notify_birthdays():
    today = date.today().strftime("%d.%m")
    for u in get_users():
        if u.get("Дата_рождения") == today:
            await application.bot.send_message(int(u["Telegram_ID"]), f"🎉 С Днём Рождения, {u.get('ФИО')}!")

def schedule_jobs():
    scheduler.add_job(lambda: notify_payments(), CronTrigger(hour=9, minute=0))
    scheduler.add_job(lambda: notify_birthdays(), CronTrigger(hour=10, minute=0))

# ================== DASHBOARD ==================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    users = get_users()
    paid = len([u for u in users if u.get("Статус") == "оплачено"])
    debt = len(users) - paid
    return HTMLResponse(f"""
    <h1>📊 Дашборд ТСН</h1>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <canvas id="c"></canvas>
    <script>
    new Chart(document.getElementById('c'), {{
        type: 'bar',
        data: {{
            labels: ['Оплачено', 'Долги'],
            datasets: [{{ data: [{paid}, {debt}] }}]
        }}
    }});
    </script>
    """)

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
    schedule_jobs()
    scheduler.start()
    logger.info("🚀 Bot started")

# ================== HANDLERS ==================
application.add_handler(CommandHandler("start", cmd_start))
application.add_handler(MessageHandler(filters.Regex("^🚀 Старт$"), cmd_start))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📊 Ваш статус$"), status_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^💳 Реквизиты$"), rekv_handler))
application.add_handler(MessageHandler(filters.PHOTO, upload_check))
application.add_handler(MessageHandler(filters.TEXT, months_reply))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
