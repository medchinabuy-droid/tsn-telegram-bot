# === imports ===
import os
import json
import logging
from datetime import datetime, timedelta
from io import BytesIO

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, MessageHandler, ContextTypes, filters

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.cloud import vision

import qrcode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# === env ===
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

# === google ===
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-vision",
]

creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
CREDS = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
GC = gspread.authorize(CREDS)
SPREAD = GC.open_by_key(SPREADSHEET_ID)

def get_or_create_worksheet(title, rows=1000, cols=20):
    try:
        return SPREAD.worksheet(title)
    except Exception:
        ws = SPREAD.add_worksheet(title=title, rows=rows, cols=cols)
        return ws

SHEET_USERS = get_or_create_worksheet("Лист 1")
SHEET_CHECKS = get_or_create_worksheet("Лист 2")
SHEET_LOGS = get_or_create_worksheet("Лист 3")
SHEET_REKV = get_or_create_worksheet("Реквизиты")
SHEET_REQUESTS = get_or_create_worksheet("Заявки")

drive_service = build("drive", "v3", credentials=CREDS)
vision_client = vision.ImageAnnotatorClient(credentials=CREDS)

# === app ===
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

# === helpers ===

def is_admin(uid): 
    return uid in ADMIN_IDS

def log_event(event, user_id=None, text=""):
    SHEET_LOGS.append_row([
        datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        event, user_id, text
    ])

def find_user(uid, username):
    users = SHEET_USERS.get_all_records()
    for i, u in enumerate(users, start=2):
        if str(u.get("Telegram_ID")) == str(uid):
            return i, u
        if username and str(u.get("username")).lower() == username.lower():
            return i, u
    return None, None

def gen_qr(text):
    img = qrcode.make(text)
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio

def get_rekv_text():
    r = SHEET_REKV.get_all_records()[0]
    return f"""Получатель: {r.get('Получатель')}
ИНН: {r.get('ИНН')}
Счёт: {r.get('Счёт получателя')}
Банк: {r.get('Банк')}
БИК: {r.get('БИК')}
Назначение: {r.get('Назначение платежа')}"""

# === OCR ===

def ocr_check(image_bytes):
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    text = response.text_annotations[0].description if response.text_annotations else ""
    return text

# === keyboards ===

def main_kb(is_admin_user=False):
    kb = [
        ["🔄 Старт"],
        ["💳 Реквизиты", "📤 Загрузить чек"],
        ["ℹ️ Информация", "📊 Статистика по дому"]
    ]
    if is_admin_user:
        kb.append(["🛠 Админ панель"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        ["📣 Уведомление по участку"],
        ["📈 Общая статистика"],
        ["📄 PDF отчёт"],
        ["⬅️ Назад"]
    ], resize_keyboard=True)

# === handlers ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, u = find_user(user.id, user.username)
    if not u:
        SHEET_USERS.append_row([
            "", "", "", "", user.id, user.username, "не привязан", "", "", "", "Активен"
        ])
        await update.message.reply_text("Вы зарегистрированы. Администратор подтвердит данные.")
    await update.message.reply_text("Добро пожаловать в ТСН ИСКОНА ПАРК 🌿", reply_markup=main_kb(is_admin(user.id)))

# === webhook ===

@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

# === dashboard ===

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
    <html>
    <head>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
      <h2>📊 Дашборд ТСН ИСКОНА ПАРК</h2>
      <canvas id="chart"></canvas>
      <script>
        new Chart(document.getElementById('chart'), {
          type: 'bar',
          data: { labels: ['Оплачено','Долг'], datasets:[{data:[10,5]}] }
        });
      </script>
    </body>
    </html>
    """

# === startup ===

@app.on_event("startup")
async def startup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")
    scheduler.start()
    logger.info("🚀 Bot started")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
# === авто-уведомления 5-3-1 + после просрочки ===

async def payment_reminder_job():
    users = SHEET_USERS.get_all_records()
    today = datetime.now().date()

    for u in users:
        try:
            day_pay = int(u.get("День_оплаты"))
        except:
            continue

        plot = u.get("Участок")
        fio = u.get("ФИО")
        status = str(u.get("Статус")).lower()
        uid = u.get("Telegram_ID")

        if not uid:
            continue

        pay_date = today.replace(day=day_pay)

        delta = (pay_date - today).days

        if status == "оплачено":
            continue

        if delta in [5, 3, 1]:
            text = f"Здравствуйте, {fio}!\n\nНапоминаем о необходимости оплаты поселкового взноса за участок {plot}.\nСпасибо за понимание 🙏"
        elif delta < 0:
            text = f"{fio}, у вас образовалась задолженность по взносам за участок {plot}.\nПросим срочно произвести оплату."
        else:
            continue

        try:
            await application.bot.send_message(chat_id=int(uid), text=text)
        except Exception as e:
            log_event("notify_error", uid, str(e))


scheduler.add_job(payment_reminder_job, "cron", hour=10, minute=0)

# === поздравления с ДР ===

async def birthday_job():
    users = SHEET_USERS.get_all_records()
    today = datetime.now().strftime("%d.%m")

    for u in users:
        uid = u.get("Telegram_ID")
        fio = u.get("ФИО")
        dr = u.get("Дата_рождения")

        if not uid or not dr:
            continue

        if dr.startswith(today):
            text = f"🎉 Уважаемый(ая) {fio}!\n\nПоздравляем Вас с Днём Рождения! 🥳\nЖелаем здоровья, благополучия и уюта в доме!\n\nС уважением,\nПравление ТСН «ИСКОНА ПАРК» 🌿"
            try:
                await application.bot.send_message(int(uid), text)
            except:
                pass

scheduler.add_job(birthday_job, "cron", hour=9, minute=0)

# === загрузка чеков + OCR ===

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, u = find_user(user.id, user.username)

    if not u:
        await update.message.reply_text("Сначала нажмите «Старт» для регистрации.")
        return

    file = await update.message.photo[-1].get_file()
    content = await file.download_as_bytearray()

    text = ocr_check(content)

    amount = ""
    date_check = ""

    for line in text.splitlines():
        if "руб" in line.lower():
            amount = line
        if "." in line and len(line.strip()) == 10:
            date_check = line

    SHEET_CHECKS.append_row([
        user.id, user.username, u.get("ФИО"), u.get("Дом"),
        u.get("Телефон"), "", amount, datetime.now().strftime("%d.%m.%Y"),
        date_check, "", text, "", file.file_unique_id, "оплачено"
    ])

    SHEET_USERS.update_cell(row, list(u.keys()).index("Статус")+1, "оплачено")

    await update.message.reply_text("✅ Чек принят и обработан автоматически.")

application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# === PDF отчёт для правления ===

def generate_pdf_report():
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    users = SHEET_USERS.get_all_records()
    y = height - 50

    c.setFont("Helvetica", 10)
    c.drawString(50, y, "Отчёт по задолженностям ТСН ИСКОНА ПАРК")
    y -= 30

    for u in users:
        fio = u.get("ФИО")
        plot = u.get("Участок")
        status = u.get("Статус")

        c.drawString(50, y, f"{fio} | Участок: {plot} | Статус: {status}")
        y -= 15
        if y < 50:
            c.showPage()
            y = height - 50

    c.save()
    buffer.seek(0)
    return buffer

# === админ-кнопки ===

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🛠 Админ панель", reply_markup=admin_kb())

async def admin_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pdf = generate_pdf_report()
    await update.message.reply_document(pdf, filename="report.pdf")

application.add_handler(MessageHandler(filters.TEXT & filters.Regex("🛠 Админ панель"), admin_panel))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("📄 PDF отчёт"), admin_pdf))
