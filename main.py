import os
import json
import logging
from datetime import datetime, timedelta, date
from io import BytesIO

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

from telegram import Update, ReplyKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

from google.cloud import vision

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

creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

CREDS = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
GC = gspread.authorize(CREDS)
SPREAD = GC.open_by_key(SPREADSHEET_ID)

SHEET_USERS = SPREAD.worksheet("Лист 1")
SHEET_CHECKS = SPREAD.worksheet("Лист 2")
SHEET_LOGS = SPREAD.worksheet("Лист 3")
SHEET_REKV = SPREAD.worksheet("Реквизиты")

drive_service = build("drive", "v3", credentials=CREDS)

vision_client = vision.ImageAnnotatorClient.from_service_account_info(creds_dict)

# ---------------- FASTAPI + BOT ----------------

app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# ---------------- HELPERS ----------------

def is_admin(uid: int):
    return uid in ADMIN_IDS

def parse_date_ddmmyyyy(s):
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except:
        return None

def get_all_users():
    return SHEET_USERS.get_all_records()

def find_user(uid=None, username=None):
    users = get_all_users()
    for idx, u in enumerate(users, start=2):
        if uid and str(u.get("Telegram_ID")) == str(uid):
            return idx, u
        if username and u.get("username") and u["username"].lower() == username.lower():
            return idx, u
    return None, None

def ensure_drive_folder(plot: str):
    query = f"name='Участок_{plot}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = drive_service.files().list(q=query).execute().get("files", [])
    if res:
        return res[0]["id"]

    folder = drive_service.files().create(body={
        "name": f"Участок_{plot}",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [DRIVE_FOLDER_ID]
    }, fields="id").execute()
    return folder["id"]

def upload_to_drive(plot, filename, content: BytesIO):
    folder_id = ensure_drive_folder(plot)
    media = MediaIoBaseUpload(content, mimetype="image/jpeg")
    file = drive_service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink"
    ).execute()
    return file.get("webViewLink")

def ocr_check(image_bytes: bytes):
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    text = response.full_text_annotation.text if response.full_text_annotation else ""
    return text

# ---------------- UI ----------------

def main_keyboard(is_admin=False):
    kb = [["💳 Реквизиты", "📊 Статус"], ["ℹ️ Информация", "🔄 Старт"]]
    if is_admin:
        kb.append(["🛠 Админ панель", "📈 Статистика"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["📣 Напоминание по участку"],
        ["🏡 Информация по участку"],
        ["📄 PDF отчёт"],
        ["⬅️ Назад"]
    ], resize_keyboard=True)

# ---------------- HANDLERS (часть 1) ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Добро пожаловать в ТСН ИСКОНА ПАРК 🌿",
        reply_markup=main_keyboard(is_admin(update.effective_user.id))
    )

async def rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = SHEET_REKV.get_all_records()
    r = rows[0]
    text = (
        f"Получатель: {r.get('Получатель')}\n"
        f"ИНН: {r.get('ИНН')}\n"
        f"Счёт: {r.get('Счёт получателя')}\n"
        f"Банк: {r.get('Банк')}\n"
        f"БИК: {r.get('БИК')}\n"
        f"Назначение: {r.get('Назначение платежа')}"
    )
    await update.message.reply_text(text)
# ---------------- OCR + ЧЕКИ ----------------

async def handle_check_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, u = find_user(uid=user.id, username=user.username)
    if not u:
        await update.message.reply_text("Вы не привязаны к участку. Обратитесь к администратору.")
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    bio = BytesIO()
    await file.download_to_memory(out=bio)
    bio.seek(0)

    ocr_text = ocr_check(bio.getvalue())

    amount = None
    check_date = None

    for token in ocr_text.replace(",", ".").split():
        if token.replace(".", "").isdigit() and len(token) > 2:
            amount = token
        d = parse_date_ddmmyyyy(token)
        if d:
            check_date = d.strftime("%d.%m.%Y")

    plot = u.get("Участок")
    link = upload_to_drive(plot, f"check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg", bio)

    SHEET_CHECKS.append_row([
        user.id,
        user.username,
        u.get("ФИО"),
        u.get("Дом"),
        u.get("Телефон"),
        link,
        amount,
        datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        check_date,
        check_date,
        ocr_text[:500],
        "",
        photo.file_id,
        "оплачено"
    ])

    await update.message.reply_text(
        f"✅ Чек принят и подтверждён автоматически.\n"
        f"💳 Сумма: {amount}\n"
        f"📅 Дата чека: {check_date}"
    )

# ---------------- УВЕДОМЛЕНИЯ ----------------

async def payment_reminder_job():
    today = date.today()
    users = get_all_users()

    for u in users:
        uid = u.get("Telegram_ID")
        username = u.get("username")
        day_pay = u.get("День_оплаты")
        status = str(u.get("Статус")).lower()

        if not day_pay:
            continue

        pay_date = parse_date_ddmmyyyy(day_pay)
        if not pay_date:
            continue

        delta = (pay_date - today).days

        if delta in [5, 3, 1]:
            msg = f"🔔 Напоминание: через {delta} дн. день оплаты взноса. Благодарим за своевременную оплату 🙏"
        elif delta < 0 and status != "оплачено":
            msg = "⚠️ У вас задолженность по поселковому взносу. Просим срочно оплатить."
        else:
            continue

        try:
            if uid:
                await application.bot.send_message(int(uid), msg)
            elif username:
                await application.bot.send_message(f"@{username}", msg)
        except Exception as e:
            logger.error(e)

# ---------------- ДР ----------------

async def birthday_job():
    today = date.today().strftime("%d.%m")
    users = get_all_users()

    for u in users:
        dr = u.get("Дата_рождения")
        if dr and dr.startswith(today):
            uid = u.get("Telegram_ID")
            msg = "🎉 Поздравляем с Днём Рождения! Желаем здоровья, уюта и благополучия! С уважением, правление ТСН 🌿"
            try:
                if uid:
                    await application.bot.send_message(int(uid), msg)
            except:
                pass

# ---------------- PDF ОТЧЁТ ----------------

def generate_pdf_report():
    buf = BytesIO()
    doc = SimpleDocTemplate(buf)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Отчёт по задолженностям ТСН", styles["Title"]), Spacer(1, 12)]

    users = get_all_users()
    data = [["Участок", "ФИО", "Статус", "Сумма"]]

    for u in users:
        data.append([
            u.get("Участок"),
            u.get("ФИО"),
            u.get("Статус"),
            u.get("Сумма")
        ])

    table = Table(data)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 1, colors.black),
    ]))

    elements.append(table)
    doc.build(elements)
    buf.seek(0)
    return buf

async def admin_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pdf = generate_pdf_report()
    await update.message.reply_document(InputFile(pdf, filename="report.pdf"))

# ---------------- DASHBOARD ----------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    users = get_all_users()
    total = len(users)
    debtors = sum(1 for u in users if str(u.get("Статус")).lower() == "долг")

    return f"""
    <html>
    <head><title>ТСН Дашборд</title></head>
    <body style="font-family:Arial;">
        <h1>ТСН ИСКОНА ПАРК — Дашборд</h1>
        <p>Всего участков: {total}</p>
        <p>Должников: {debtors}</p>
        <p>Оплачено: {total - debtors}</p>
    </body>
    </html>
    """

# ---------------- ROUTER ----------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🔄 Старт":
        await start(update, context)
    elif text == "💳 Реквизиты":
        await rekv(update, context)
    elif text == "🛠 Админ панель":
        await update.message.reply_text("Админ панель", reply_markup=admin_keyboard())
    elif text == "📄 PDF отчёт":
        await admin_pdf(update, context)

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
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_check_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    await application.initialize()
    await application.start()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")

    scheduler.add_job(payment_reminder_job, "cron", hour=10)
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
