import os, json, re, hashlib, logging, asyncio
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

ADMINS = set(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else set()
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
BASE_CHECKS_DIR = Path("Чеки ТСН")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
gpt = OpenAI(api_key=OPENAI_API_KEY)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-platform"
]

GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
if not GOOGLE_CREDS_JSON:
    raise RuntimeError("GOOGLE_CREDENTIALS_JSON не задан")

creds_info = json.loads(GOOGLE_CREDS_JSON)
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
        ws = SPREAD.add_worksheet(title=title, rows=2000, cols=30)
        ws.update("A1", [headers])
        return ws

SHEET_USERS = get_or_create_sheet("Лист1", [
    "Участок","ФИО","Telegram_ID","username","Телефон","День_оплаты","Электро","Сумма",
    "Дата","Статус","Роль","Дата_напоминания","Дата_рождения","Дата_регистрации","Последняя_оплата",
    "Комментарий_админа","Активен"
])

SHEET_CHECKS = get_or_create_sheet("Лист2", [
    "telegram_id","username","ФИО","Дом","Телефон","Ссылка_на_чек",
    "Сумма_по_чеку","Дата_загрузки","Дата_чека","OCR_Дата","OCR","Дубль_чека","File_Unique_ID","Статус"
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
        [KeyboardButton("📊 Ваш статус"), KeyboardButton("💳 Оплатить")],
        [KeyboardButton("📎 Загрузить чек"), KeyboardButton("ℹ️ Информация")]
    ], resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🛠 Админ"), KeyboardButton("📈 Статистика")],
        [KeyboardButton("📣 Уведомить участок"), KeyboardButton("🤖 GPT помощник")]
    ], resize_keyboard=True)

def start_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("🚀 Поехали")]], resize_keyboard=True)

# ================== HELPERS ==================
def is_admin(uid: int) -> bool:
    return uid in ADMINS

def get_users() -> List[Dict]:
    return SHEET_USERS.get_all_records()

def find_user_by_tg(tg_id: int) -> Optional[Dict]:
    for row in get_users():
        if str(row.get("Telegram_ID")) == str(tg_id):
            return row
    return None

def update_user_row(tg_id: int, updates: Dict):
    rows = SHEET_USERS.get_all_records()
    for i, row in enumerate(rows, start=2):
        if str(row.get("Telegram_ID")) == str(tg_id):
            for col, val in updates.items():
                col_idx = SHEET_USERS.find(col).col
                SHEET_USERS.update_cell(i, col_idx, val)
            return

def parse_sum_and_date(text: str):
    sum_match = re.search(r"(\d{3,7})\s?руб", text.lower())
    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    amount = int(sum_match.group(1)) if sum_match else None
    dt = date_match.group(1) if date_match else None
    return amount, dt

def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

async def gpt_parse_months(text: str) -> List[str]:
    prompt = f"Пользователь написал месяцы оплаты: '{text}'. Верни список месяцев в формате: январь, февраль."
    resp = gpt.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.choices[0].message.content
    return re.findall(r"[а-яА-Я]+", raw)

# ================== BOT HANDLERS ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Нажмите «Поехали», чтобы начать.", reply_markup=start_kb())

async def start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = find_user_by_tg(user.id)
    kb = admin_kb() if is_admin(user.id) else user_kb()

    if existing:
        fio = existing.get("ФИО") or user.full_name
        await update.message.reply_text(f"С возвращением, {fio}! 👋", reply_markup=kb)
    else:
        SHEET_USERS.append_row(["","","",user.username,"","","","","","не оплачено","","","",str(date.today()),"","","TRUE"])
        update_user_row(user.id, {"Telegram_ID": user.id, "ФИО": user.full_name})
        await update.message.reply_text("Вы зарегистрированы!", reply_markup=kb)

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = find_user_by_tg(update.effective_user.id)
    await update.message.reply_text(
        f"📊 Ваш статус:\n\n"
        f"Участок: {u.get('Участок')}\n"
        f"Сумма: {u.get('Сумма')}\n"
        f"День оплаты: {u.get('День_оплаты')}\n"
        f"Статус: {u.get('Статус')}",
        reply_markup=user_kb()
    )

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = find_user_by_tg(update.effective_user.id)
    photo = update.message.photo[-1]
    file = await photo.get_file()

    plot = user.get("Участок") or "Неизвестно"
    folder = BASE_CHECKS_DIR / f"Участок_{plot}"
    folder.mkdir(parents=True, exist_ok=True)

    fname = folder / f"чек_{date.today()}_{update.effective_user.id}.jpg"
    await file.download_to_drive(str(fname))

    file_hash = hash_file(fname)

    img = vision.Image(content=fname.read_bytes())
    res = vision_client.text_detection(image=img)
    text = res.full_text_annotation.text if res.text_annotations else ""

    amount, dt = parse_sum_and_date(text)

    SHEET_CHECKS.append_row([
        update.effective_user.id, update.effective_user.username, user.get("ФИО"),
        plot, user.get("Телефон"), str(fname), amount, str(datetime.now()),
        dt, dt, text, "нет", file_hash, "оплачено"
    ])

    update_user_row(update.effective_user.id, {"Статус": "оплачено", "Последняя_оплата": str(date.today())})
    await update.message.reply_text("✅ Чек принят. Оплата засчитана!")

# ================== APSCHEDULER ==================
async def notify_payments():
    today = date.today().day
    for u in get_users():
        try:
            pay_day = int(u.get("День_оплаты") or 0)
            tg_id = int(u.get("Telegram_ID"))
            fio = u.get("ФИО")
            delta = pay_day - today
            if delta in (5,3,1):
                await application.bot.send_message(tg_id, f"Здравствуйте, {fio}! Напоминаем об оплате через {delta} дн.")
            elif delta < 0 and u.get("Статус") != "оплачено":
                await application.bot.send_message(tg_id, f"{fio}, просим погасить задолженность по взносу 🙏")
        except:
            pass

async def notify_birthdays():
    today = date.today().strftime("%d.%m")
    for u in get_users():
        if u.get("Дата_рождения") == today:
            await application.bot.send_message(int(u["Telegram_ID"]),
                f"🎉 С Днём Рождения, {u.get('ФИО')}! Уюта, радости и благополучия!")

def schedule_jobs():
    scheduler.add_job(lambda: asyncio.create_task(notify_payments()), CronTrigger(hour=9, minute=0))
    scheduler.add_job(lambda: asyncio.create_task(notify_birthdays()), CronTrigger(hour=10, minute=0))

# ================== WEB DASHBOARD ==================
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    users = get_users()
    paid = len([u for u in users if u.get("Статус") == "оплачено"])
    debt = len(users) - paid
    return HTMLResponse(f"""
    <h1>ТСН ИСКОНА-ПАРК</h1>
    <canvas id="c"></canvas>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
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
application.add_handler(MessageHandler(filters.Regex("^🚀 Поехали$"), start_flow))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📊 Ваш статус$"), status_handler))
application.add_handler(MessageHandler(filters.PHOTO, upload_check))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
