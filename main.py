# === imports ===
import os, json, re, logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, List

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

# === CONFIG ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook/secure"
PORT = int(os.getenv("PORT", "1000"))
ADMINS = set(map(int, os.getenv("ADMINS", "").split(","))) if os.getenv("ADMINS") else set()
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

BASE_CHECKS_DIR = Path("Чеки ТСН")
BASE_CHECKS_DIR.mkdir(exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_info(json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")), scopes=SCOPES)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# === GOOGLE ===
gc = gspread.authorize(creds)
SPREAD = gc.open_by_key(SPREADSHEET_ID)

def get_or_create_sheet(title, headers):
    try:
        ws = SPREAD.worksheet(title)
        return ws
    except:
        ws = SPREAD.add_worksheet(title=title, rows=2000, cols=20)
        ws.update("A1", [headers])
        return ws

SHEET_USERS = get_or_create_sheet("Пользователи", ["ФИО","Участок","Сумма","День_оплаты","Статус","ДР","Telegram_ID","username","Телефон"])
SHEET_CHECKS = get_or_create_sheet("Чеки", ["Дата_загрузки","Telegram_ID","ФИО","Участок","Сумма_по_чеку","Дата_по_чеку","Путь_к_файлу","Статус"])
SHEET_REKV = get_or_create_sheet("Реквизиты", ["Ключ","Значение"])

vision_client = vision.ImageAnnotatorClient(credentials=creds)

# === FASTAPI + BOT ===
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

# === KEYBOARDS ===
def start_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("🚀 Поехали")]], resize_keyboard=True)

def user_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("ℹ️ Информация"), KeyboardButton("💳 Реквизиты")],
        [KeyboardButton("📊 Ваш статус"), KeyboardButton("📎 Загрузить чек")]
    ], resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🛠 Админ-панель"), KeyboardButton("📈 Админ-статистика")],
        [KeyboardButton("📣 Уведомить участок"), KeyboardButton("🔎 Поиск собственника")]
    ], resize_keyboard=True)

# === HELPERS ===
def is_admin(uid): return uid in ADMINS
def get_users(): return SHEET_USERS.get_all_records()

def find_user(tg_id):
    for i, r in enumerate(get_users(), start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    return None, None

def parse_sum_and_date(text):
    sm = re.search(r"(\d{2,6})\s?руб", text.lower())
    dm = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    return (int(sm.group(1)) if sm else None, dm.group(1) if dm else None)

# === REGISTRATION FLOW ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Нажмите «Поехали», чтобы начать регистрацию.", reply_markup=start_kb())

async def go_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    username = update.effective_user.username
    row_i, row = find_user(uid)

    if not row:
        SHEET_USERS.append_row(["", "", "", "", "не оплачено", "", uid, username, ""])
        await update.message.reply_text("Введите ФИО (нужно для актуализации данных):")
        context.user_data["step"] = "fio"
    else:
        await update.message.reply_text(f"С возвращением, {row.get('ФИО','')} 👋", reply_markup=user_kb())

async def text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step")
    row_i, row = find_user(update.effective_user.id)
    if not row_i: return

    if step == "fio":
        SHEET_USERS.update_cell(row_i, 1, update.message.text)
        context.user_data["step"] = "phone"
        await update.message.reply_text("Введите телефон (для связи):")
    elif step == "phone":
        SHEET_USERS.update_cell(row_i, 9, update.message.text)
        context.user_data["step"] = "dob"
        await update.message.reply_text("Введите дату рождения (дд.мм.гггг):")
    elif step == "dob":
        SHEET_USERS.update_cell(row_i, 6, update.message.text)
        context.user_data.clear()
        await update.message.reply_text("✅ Регистрация завершена!", reply_markup=user_kb())

# === USER HANDLERS ===
async def status_handler(update, context):
    _, u = find_user(update.effective_user.id)
    text = f"📊 Ваш статус:\n\nФИО: {u['ФИО']}\nУчасток: {u['Участок']}\nСумма: {u['Сумма']}\nДень оплаты: {u['День_оплаты']}\nСтатус: {u['Статус']}"
    await update.message.reply_text(text, reply_markup=user_kb())

async def rekv_handler(update, context):
    rows = SHEET_REKV.get_all_records()
    text, qr = [], None
    for r in rows:
        if "qr" in r["Ключ"].lower():
            qr = r["Значение"]
        else:
            text.append(f"{r['Ключ']}: {r['Значение']}")
    await update.message.reply_text("💳 Реквизиты:\n\n" + "\n".join(text))
    if qr:
        await update.message.reply_photo(qr, caption="QR для оплаты")

async def upload_check(update, context):
    _, u = find_user(update.effective_user.id)
    photo = update.message.photo[-1]
    f = await photo.get_file()

    plot = u["Участок"] or "Неизвестно"
    folder = BASE_CHECKS_DIR / f"Участок_{plot}"
    folder.mkdir(parents=True, exist_ok=True)

    path = folder / f"чек_{date.today()}_{update.effective_user.id}.jpg"
    await f.download_to_drive(str(path))

    with open(path, "rb") as img:
        resp = vision_client.text_detection(image=vision.Image(content=img.read()))
    text = resp.full_text_annotation.text if resp.text_annotations else ""
    s, d = parse_sum_and_date(text)

    SHEET_CHECKS.append_row([str(datetime.now()), update.effective_user.id, u["ФИО"], plot, s, d, str(path), "оплачено"])
    SHEET_USERS.update_cell(find_user(update.effective_user.id)[0], 5, "оплачено")
    await update.message.reply_text("✅ Чек принят, статус обновлён.")

# === SCHEDULER ===
async def notify_jobs():
    today = date.today().day
    for r in get_users():
        try:
            tg = int(r["Telegram_ID"])
            pay_day = int(r["День_оплаты"])
            delta = pay_day - today
            fio = r["ФИО"]
            if delta in (5,3,1):
                await application.bot.send_message(tg, f"{fio}, напоминаем об оплате через {delta} дн.")
            elif delta < 0 and r["Статус"] != "оплачено":
                await application.bot.send_message(tg, f"{fio}, у вас задолженность. Просьба оплатить.")
        except: pass

def schedule_jobs():
    scheduler.add_job(lambda: notify_jobs(), CronTrigger(hour=9, minute=0))
    scheduler.start()

# === WEB ===
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    users = get_users()
    return f"<h2>Админ-дашборд</h2><p>Всего пользователей: {len(users)}</p>"

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
    logger.info("🚀 Bot started")

# === HANDLERS ===
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^🚀 Поехали$"), go_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📊 Ваш статус$"), status_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^💳 Реквизиты$"), rekv_handler))
application.add_handler(MessageHandler(filters.PHOTO, upload_check))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_flow))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
