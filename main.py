import os
import json
import logging
import hashlib
import asyncio
from datetime import datetime, timedelta
from io import BytesIO
from typing import Optional, Dict, List

from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

import qrcode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from tzlocal import get_localzone_name

import openai
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# -------------------- CONFIG --------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(map(int, filter(None, os.getenv("ADMIN_IDS", "").split(","))))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
PORT = int(os.getenv("PORT", "8000"))

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

MONTHLY_FEE_DEFAULT = int(os.getenv("MONTHLY_FEE", "6000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# -------------------- FASTAPI --------------------

app = FastAPI()
telegram_app: Optional[Application] = None
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# -------------------- GOOGLE INIT --------------------

GC = None
SHEET_USERS = None
SHEET_CHECKS = None
SHEET_LOGS = None
DRIVE = None

HEADERS_USERS = [
    "Участок", "ФИО", "Telegram_ID", "username", "Телефон",
    "День_оплаты", "Электро", "Сумма", "Дата", "Статус", "Роль",
    "Дата_напоминания", "Дата_рождения", "Дата_регистрации",
    "Последняя_оплата", "Комментарий_админа", "Активен"
]

def init_google():
    global GC, SHEET_USERS, SHEET_CHECKS, SHEET_LOGS, DRIVE
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        GC = gspread.authorize(creds)
        sh = GC.open_by_key(SPREADSHEET_ID)

        SHEET_USERS = sh.worksheet("Лист 1")
        SHEET_CHECKS = sh.worksheet("Лист 2")
        SHEET_LOGS = sh.worksheet("Лист 3")

        DRIVE = build("drive", "v3", credentials=creds)

        logger.info("✅ Google Sheets + Drive инициализированы")
    except Exception as e:
        logger.exception("❌ Ошибка инициализации Google API: %s", e)

# -------------------- HELPERS --------------------

def log_event(event_type, uid=None, username=None, plot=None, details="", error=""):
    try:
        if SHEET_LOGS:
            SHEET_LOGS.append_row([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                event_type, uid, username, plot, details, error
            ])
    except Exception as e:
        logger.error("Log error: %s", e)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def get_all_users():
    if not SHEET_USERS:
        return []
    return SHEET_USERS.get_all_records()

def find_user(uid: int, username: Optional[str]):
    if not SHEET_USERS:
        return None, None

    users = SHEET_USERS.get_all_records()
    for idx, u in enumerate(users, start=2):
        if str(u.get("Telegram_ID")) == str(uid):
            return idx, u

    if username:
        for idx, u in enumerate(users, start=2):
            if u.get("username") == username:
                SHEET_USERS.update_cell(idx, HEADERS_USERS.index("Telegram_ID") + 1, uid)
                return idx, u

    return None, None

def ensure_headers():
    current = SHEET_USERS.row_values(1)
    if current != HEADERS_USERS:
        SHEET_USERS.update("A1", [HEADERS_USERS])

def register_user_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_step"] = "fio"

async def ask_next_reg_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("reg_step")
    if step == "fio":
        await update.message.reply_text("Введите ФИО:")
    elif step == "plot":
        await update.message.reply_text("Введите номер участка/дома:")
    elif step == "phone":
        await update.message.reply_text("Введите телефон:")
    elif step == "birth":
        await update.message.reply_text("Введите дату рождения в формате ДД.ММ.ГГГГ:")

def parse_birthdate(text: str) -> Optional[str]:
    try:
        datetime.strptime(text, "%d.%m.%Y")
        return text
    except:
        return None

def generate_qr(text: str) -> BytesIO:
    qr = qrcode.make(text)
    bio = BytesIO()
    qr.save(bio, format="PNG")
    bio.seek(0)
    return bio

def hash_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def upload_to_drive(file_bytes: bytes, filename: str) -> str:
    media = MediaIoBaseUpload(BytesIO(file_bytes), mimetype="image/jpeg")
    file_metadata = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
    file = DRIVE.files().create(body=file_metadata, media_body=media, fields="id").execute()
    DRIVE.permissions().create(fileId=file["id"], body={"role": "reader", "type": "anyone"}).execute()
    return f"https://drive.google.com/file/d/{file['id']}/view"

# -------------------- OCR / GPT --------------------

async def ocr_stub(_bytes: bytes) -> Dict:
    return {"date": None, "amount": None, "raw_text": ""}

async def gpt_admin_summary(text: str) -> str:
    if not OPENAI_API_KEY:
        return "GPT не подключён."
    resp = await openai.ChatCompletion.acreate(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Ты аналитик ТСН."},
            {"role": "user", "content": text},
        ],
        temperature=0.3
    )
    return resp.choices[0].message.content

# -------------------- BOT UI --------------------

def main_keyboard(is_admin_user: bool):
    kb = [
        ["💳 Реквизиты", "📊 Статус"],
        ["📸 Загрузить чек", "ℹ️ Информация"],
    ]
    if is_admin_user:
        kb.append(["🛠 Админ панель", "📈 Статистика"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_headers()
    uid = update.effective_user.id
    username = update.effective_user.username

    row, user = find_user(uid, username)

    if not user:
        register_user_flow(update, context)
        await update.message.reply_text(
            "🌿 Приветствуем в ТСН «ИСКОНА ПАРК»!\n\n"
            "Вы пока не зарегистрированы. Давайте зарегистрируемся 👇"
        )
        await ask_next_reg_step(update, context)
        return

    await update.message.reply_text(
        f"🌿 Приветствуем в ТСН «ИСКОНА ПАРК»!\n\n"
        f"👤 ФИО: {user.get('ФИО')}\n"
        f"🏡 Участок/Дом: {user.get('Участок')}",
        reply_markup=main_keyboard(is_admin(uid))
    )

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Информация о взносах:\n\n"
        "• Взнос ежемесячный 6000 руб.\n"
        "• Сумма может отличаться при наличии льгот\n"
        "• День оплаты указан индивидуально\n"
        "• Чеки проверяются автоматически\n"
        "• При просрочке формируется задолженность\n\n"
        "📧 Контакты для пропусков: propusk@tsn-iskona-park.ru\n"
        "📧 Связь с ТСН: info@iskonapark.ru"
    )

async def rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Получатель: ТСН «ИСКОНА ПАРК»\n"
        "ИНН: 0000000000\n"
        "Счёт: 0000000000000000\n"
        "Банк: Банк\n"
        "БИК: 000000000"
    )
    await update.message.reply_text(text)
    qr = generate_qr(text)
    await update.message.reply_photo(InputFile(qr))

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row, user = find_user(update.effective_user.id, update.effective_user.username)
    if not user:
        return
    await update.message.reply_text(
        f"📊 Ваш статус:\n\n"
        f"Участок: {user.get('Участок')}\n"
        f"Сумма: {user.get('Сумма')}\n"
        f"День оплаты: {user.get('День_оплаты')}\n"
        f"Статус: {user.get('Статус')}"
    )
# -------------------- REG FLOW --------------------

async def reg_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("reg_step")
    text = update.message.text.strip()

    if step == "fio":
        context.user_data["fio"] = text
        context.user_data["reg_step"] = "plot"
        await ask_next_reg_step(update, context)
    elif step == "plot":
        context.user_data["plot"] = text
        context.user_data["reg_step"] = "phone"
        await ask_next_reg_step(update, context)
    elif step == "phone":
        context.user_data["phone"] = text
        context.user_data["reg_step"] = "birth"
        await ask_next_reg_step(update, context)
    elif step == "birth":
        bd = parse_birthdate(text)
        if not bd:
            await update.message.reply_text("❌ Неверный формат. Введите ДД.ММ.ГГГГ")
            return

        uid = update.effective_user.id
        username = update.effective_user.username or ""

        SHEET_USERS.append_row([
            context.user_data["plot"],
            context.user_data["fio"],
            uid,
            username,
            context.user_data["phone"],
            25,
            "",
            MONTHLY_FEE_DEFAULT,
            "",
            "активен",
            "user",
            "",
            bd,
            datetime.now().strftime("%d.%m.%Y"),
            "",
            "",
            "да"
        ])

        context.user_data.clear()
        await update.message.reply_text("✅ Регистрация завершена!", reply_markup=main_keyboard(False))

# -------------------- CHECK UPLOAD --------------------

async def ask_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Пришлите фото чека.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    file = await update.message.photo[-1].get_file()
    b = await file.download_as_bytearray()
    h = hash_bytes(bytes(b))
    link = upload_to_drive(bytes(b), f"check_{uid}_{int(datetime.now().timestamp())}.jpg")

    SHEET_CHECKS.append_row([
        uid,
        update.effective_user.username or "",
        update.effective_user.full_name,
        "",
        "",
        link,
        "",
        datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "",
        "",
        "",
        h,
        update.message.photo[-1].file_unique_id,
        "new"
    ])

    await update.message.reply_text("✅ Чек загружен и сохранён в облаке.")

# -------------------- ADMIN + STATS --------------------

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🛠 Админ панель")

def build_pdf_stats() -> BytesIO:
    users = get_all_users()
    total = len(users)
    debtors = len([u for u in users if u.get("Статус") == "долг"])

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    elems = [
        Paragraph("Отчёт по взносам ТСН «ИСКОНА ПАРК»", styles["Title"]),
        Spacer(1, 12),
        Paragraph(f"Всего собственников: {total}", styles["Normal"]),
        Paragraph(f"Должников: {debtors}", styles["Normal"]),
    ]
    doc.build(elems)
    buf.seek(0)
    return buf

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pdf = build_pdf_stats()
    await update.message.reply_document(InputFile(pdf, filename="stats.pdf"))

# -------------------- REMINDERS --------------------

async def payment_reminder_job():
    users = get_all_users()
    today = datetime.now().day

    for idx, u in enumerate(users, start=2):
        try:
            uid = u.get("Telegram_ID")
            if not uid or u.get("Активен") != "да":
                continue

            pay_day = int(u.get("День_оплаты", 0) or 0)
            delta = pay_day - today

            if delta in (5, 3, 1):
                text = f"🔔 Напоминание об оплате взноса. День оплаты: {pay_day}."
            elif delta < 0:
                text = "⚠️ У вас задолженность по взносам. Просьба оплатить."
            else:
                continue

            await telegram_app.bot.send_message(int(uid), text)
        except Exception as e:
            log_event("reminder_error", error=str(e))

async def birthday_job():
    today = datetime.now().strftime("%d.%m")
    for u in get_all_users():
        try:
            bd = u.get("Дата_рождения", "")
            if bd and bd[:5] == today and u.get("Telegram_ID"):
                await telegram_app.bot.send_message(
                    int(u["Telegram_ID"]),
                    "🎉 С днём рождения!\n\n"
                    "Правление ТСН «Искона Парк» поздравляет вас! 😊\n"
                    "Желаем здоровья, уюта в доме и хороших соседей 🌿"
                )
        except Exception as e:
            log_event("birthday_error", error=str(e))

# -------------------- ROUTER --------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("reg_step"):
        await reg_flow_handler(update, context)
        return

    text = update.message.text
    uid = update.effective_user.id

    if text == "ℹ️ Информация":
        await info(update, context)
    elif text == "📊 Статус":
        await status(update, context)
    elif text == "💳 Реквизиты":
        await rekv(update, context)
    elif text == "📸 Загрузить чек":
        await ask_check(update, context)
    elif text == "🛠 Админ панель" and is_admin(uid):
        await admin_panel(update, context)
    elif text == "📈 Статистика" and is_admin(uid):
        await stats(update, context)

# -------------------- WEBHOOK --------------------

@app.on_event("startup")
async def on_startup():
    global telegram_app
    init_google()

    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    await telegram_app.initialize()
    await telegram_app.start()

    await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")

    scheduler.add_job(payment_reminder_job, "cron", hour=18, minute=0)
    scheduler.add_job(birthday_job, "cron", hour=10, minute=0)
    scheduler.start()

    logger.info("🚀 Бот запущен")

@app.post("/webhook/{secret}")
async def webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403)
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return PlainTextResponse("OK")

@app.get("/")
async def health():
    return PlainTextResponse("OK")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
