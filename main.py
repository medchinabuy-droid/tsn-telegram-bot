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
        ws = SPREAD.add_worksheet(title=title, rows=2000, cols=30)
        ws.update("A1", [headers])
        return ws

SHEET_USERS = get_or_create_sheet("Пользователи", [
    "ФИО", "Участок", "Сумма", "День_оплаты", "Статус", "ДР", "Телефон",
    "Telegram_ID", "username"
])
SHEET_CHECKS = get_or_create_sheet("Чеки", [
    "Дата_загрузки", "Telegram_ID", "ФИО", "Участок",
    "Сумма_по_чеку", "Дата_по_чеку", "Путь_к_файлу", "Статус"
])
SHEET_REKV = get_or_create_sheet("Реквизиты", ["Ключ", "Значение"])

vision_client = vision.ImageAnnotatorClient(credentials=creds)

# ================== FASTAPI + BOT ==================
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

# ================== KEYBOARDS ==================
def user_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("ℹ️ Информация"), KeyboardButton("💳 Реквизиты")],
        [KeyboardButton("📊 Ваш статус"), KeyboardButton("📎 Загрузить чек")]
    ], resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🛠 Админ-панель"), KeyboardButton("📈 Админ-статистика")],
        [KeyboardButton("📣 Уведомить участок")]
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
    text_low = text.lower()
    sum_patterns = [
        r"итого[:\s]+([\d\s]+)",
        r"к оплате[:\s]+([\d\s]+)",
        r"сумма[:\s]+([\d\s]+)",
        r"([\d]{3,6})\s?руб"
    ]
    date_patterns = [r"(\d{2}\.\d{2}\.\d{4})", r"(\d{2}/\d{2}/\d{4})"]

    amount, dt = None, None
    for p in sum_patterns:
        m = re.search(p, text_low)
        if m:
            amount = int(m.group(1).replace(" ", ""))
            break
    for p in date_patterns:
        m = re.search(p, text_low)
        if m:
            dt = m.group(1).replace("/", ".")
            break
    return amount, dt

# ================== BOT HANDLERS ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Нажмите «Поехали», чтобы начать.", reply_markup=start_kb())

async def start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = find_user_by_tg(user.id)

    if existing:
        fio = existing.get("ФИО") or user.full_name
        kb = admin_kb() if is_admin(user.id) else user_kb()
        await update.message.reply_text(f"С возвращением, {fio}! 👋", reply_markup=kb)
    else:
        SHEET_USERS.append_row(["", "", "", "", "не оплачено", "", "", user.id, user.username])
        context.user_data["reg_step"] = "fio"
        await update.message.reply_text("Введите ФИО для подтверждения личности:")

async def registration_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("reg_step")
    if not step:
        return
    uid = update.effective_user.id
    txt = update.message.text.strip()

    if step == "fio":
        update_user_row(uid, {"ФИО": txt})
        context.user_data["reg_step"] = "phone"
        await update.message.reply_text("📞 Укажите телефон:")
    elif step == "phone":
        update_user_row(uid, {"Телефон": txt})
        context.user_data["reg_step"] = "birth"
        await update.message.reply_text("🎂 Дата рождения (ДД.ММ.ГГГГ):")
    elif step == "birth":
        update_user_row(uid, {"ДР": txt})
        context.user_data["reg_step"] = None
        await update.message.reply_text("✅ Регистрация завершена!", reply_markup=user_kb())

async def info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Информация о взносах:\n\n"
        "• Взнос ежемесячный 6000 руб.\n"
        "• Сумма может отличаться при наличии льгот\n"
        "• День оплаты указан индивидуально\n"
        "• Чеки проверяются автоматически\n"
        "• При просрочке формируется задолженность\n\n"
        "📧 propusk@tsn-iskona-park.ru\n"
        "📧 info@iskonapark.ru",
        reply_markup=user_kb()
    )

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = find_user_by_tg(update.effective_user.id)
    await update.message.reply_text(
        f"📊 Ваш статус:\n\n"
        f"ФИО: {u.get('ФИО')}\n"
        f"Участок: {u.get('Участок')}\n"
        f"Сумма: {u.get('Сумма')}\n"
        f"День оплаты: {u.get('День_оплаты')}\n"
        f"Статус: {u.get('Статус')}",
        reply_markup=user_kb()
    )

async def rekv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = SHEET_REKV.get_all_records()
    text, qr = [], None
    for r in rows:
        if "qr" in (r.get("Ключ") or "").lower():
            qr = r.get("Значение")
        else:
            text.append(f"{r.get('Ключ')}: {r.get('Значение')}")
    await update.message.reply_text("💳 Реквизиты:\n\n" + "\n".join(text))
    if qr:
        await update.message.reply_photo(qr)

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = find_user_by_tg(update.effective_user.id)
    photo = update.message.photo[-1]
    file = await photo.get_file()

    plot = user.get("Участок") or "Неизвестно"
    folder = BASE_CHECKS_DIR / f"Участок_{plot}"
    folder.mkdir(parents=True, exist_ok=True)
    fname = folder / f"чек_{date.today()}_{update.effective_user.id}.jpg"
    await file.download_to_drive(str(fname))

    with open(fname, "rb") as f:
        img = vision.Image(content=f.read())
    res = vision_client.text_detection(image=img)
    text = res.full_text_annotation.text if res.text_annotations else ""

    amount, dt = parse_sum_and_date(text)
    SHEET_CHECKS.append_row([str(datetime.now()), update.effective_user.id, user.get("ФИО"),
                             plot, amount, dt, str(fname), "оплачено"])
    update_user_row(update.effective_user.id, {"Статус": "оплачено"})
    await update.message.reply_text("✅ Чек принят, оплата зачтена!")

# ================== APSCHEDULER ==================
async def notify_payments():
    today = date.today().day
    for u in get_users():
        try:
            pay_day = int(u.get("День_оплаты"))
            tg_id = int(u.get("Telegram_ID"))
            fio = u.get("ФИО")
            delta = pay_day - today
            if delta in (5, 3, 1):
                await application.bot.send_message(tg_id, f"Здравствуйте, {fio}! Напоминаем об оплате взноса через {delta} дн.")
            elif delta < 0 and u.get("Статус") != "оплачено":
                await application.bot.send_message(tg_id, f"{fio}, у вас задолженность по взносам. Просим срочно оплатить.")
        except:
            pass

async def notify_birthdays():
    today = date.today().strftime("%d.%m")
    for u in get_users():
        if u.get("ДР") == today:
            await application.bot.send_message(int(u["Telegram_ID"]),
                f"🎉 С Днём Рождения, {u.get('ФИО')}! Желаем уюта, благополучия и отличного настроения!")

def schedule_jobs():
    scheduler.add_job(lambda: notify_payments(), CronTrigger(hour=9, minute=0))
    scheduler.add_job(lambda: notify_birthdays(), CronTrigger(hour=10, minute=0))

# ================== WEB ==================
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
        type: 'doughnut',
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
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^ℹ️ Информация$"), info_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📊 Ваш статус$"), status_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^💳 Реквизиты$"), rekv_handler))
application.add_handler(MessageHandler(filters.PHOTO, upload_check))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration_flow))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
