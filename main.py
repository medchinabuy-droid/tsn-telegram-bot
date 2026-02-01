import os
import json
import logging
import hashlib
import asyncio
from datetime import datetime, timedelta, time
from io import BytesIO
from typing import Optional, Dict

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
import qrcode

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import openai
from PIL import Image

# -------------------- CONFIG --------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(map(int, filter(None, os.getenv("ADMIN_IDS", "").split(","))))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://tsn-telegram-bot.onrender.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
PORT = int(os.getenv("PORT", "8000"))

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

MONTHLY_FEE_DEFAULT = int(os.getenv("MONTHLY_FEE", "6000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# -------------------- FASTAPI --------------------

app = FastAPI()
telegram_app: Optional[Application] = None
scheduler = AsyncIOScheduler()

# -------------------- GOOGLE SHEETS INIT --------------------

GC = None
SHEET_USERS = None
SHEET_CHECKS = None
SHEET_LOGS = None

def init_google():
    global GC, SHEET_USERS, SHEET_CHECKS, SHEET_LOGS
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        GC = gspread.authorize(creds)
        sh = GC.open_by_key(SPREADSHEET_ID)

        SHEET_USERS = sh.worksheet("Лист 1")
        SHEET_CHECKS = sh.worksheet("Лист 2")
        SHEET_LOGS = sh.worksheet("Лист 3")

        logger.info("✅ Google Sheets инициализированы")
    except Exception as e:
        logger.exception("❌ Ошибка инициализации Google Sheets: %s", e)
        GC = None

# -------------------- HELPERS --------------------

def log_event(event_type, uid=None, username=None, plot=None, details="", error=""):
    if not SHEET_LOGS:
        return
    try:
        SHEET_LOGS.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            event_type,
            uid,
            username,
            plot,
            details,
            error
        ])
    except Exception as e:
        logger.error("Log error: %s", e)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def get_all_users():
    if not SHEET_USERS:
        return []
    return SHEET_USERS.get_all_records()

def find_user_by_tg(uid: int):
    if not SHEET_USERS:
        return None, None
    users = SHEET_USERS.get_all_records()
    for idx, u in enumerate(users, start=2):
        if str(u.get("Telegram_ID")) == str(uid):
            return idx, u
    return None, None

def register_user_if_not_exists(update: Update):
    uid = update.effective_user.id
    username = update.effective_user.username or ""
    row, user = find_user_by_tg(uid)
    if user:
        return row, user

    # минимальная регистрация
    SHEET_USERS.append_row([
        "",  # Участок
        f"{update.effective_user.first_name or ''} {update.effective_user.last_name or ''}".strip(),
        uid,
        username,
        "",  # Телефон
        25,  # День_оплаты (дефолт)
        "",  # Электро
        MONTHLY_FEE_DEFAULT,  # Сумма
        "",  # Дата
        "новый",  # Статус
        "user",  # Роль
        "",  # Дата_напоминания
    ])
    log_event("register", uid=uid, username=username)
    return find_user_by_tg(uid)

def generate_qr(text: str) -> BytesIO:
    qr = qrcode.make(text)
    bio = BytesIO()
    qr.save(bio, format="PNG")
    bio.seek(0)
    return bio

def hash_image_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

# -------------------- OCR / GPT (заглушки с логикой) --------------------

async def ocr_extract_stub(image_bytes: bytes) -> Dict:
    # TODO: заменить на Vision API
    return {
        "date": None,
        "amount": None,
        "raw_text": ""
    }

async def gpt_validate_stub(text: str) -> bool:
    # TODO: заменить на реальный GPT-анализ
    return True

# -------------------- BOT HANDLERS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row, user = register_user_if_not_exists(update)

    kb = [
        ["💳 Реквизиты", "📊 Статус"],
        ["📸 Загрузить чек", "ℹ️ Информация"],
    ]
    if is_admin(uid):
        kb.append(["🛠 Админ панель"])

    await update.message.reply_text(
        f"👋 Здравствуйте!\n"
        f"Вы зарегистрированы в системе поселковых взносов.\n\n"
        f"ФИО: {user.get('ФИО')}\n"
        f"Участок: {user.get('Участок') or 'не указан'}",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Информация о взносах:\n\n"
        "• Взнос ежемесячный\n"
        "• Сумма может отличаться при наличии льгот\n"
        "• День оплаты указан индивидуально\n"
        "• Чеки проверяются автоматически\n"
        "• При просрочке формируется задолженность"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row, user = find_user_by_tg(update.effective_user.id)
    if not user:
        return
    await update.message.reply_text(
        f"📊 Ваш статус:\n\n"
        f"Участок: {user.get('Участок')}\n"
        f"Сумма: {user.get('Сумма')}\n"
        f"День оплаты: {user.get('День_оплаты')}\n"
        f"Статус: {user.get('Статус')}"
    )

async def rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Получатель: ТСН\n"
        "ИНН: 0000000000\n"
        "Счёт: 0000000000000000\n"
        "Банк: Банк\n"
        "БИК: 000000000"
    )
    qr = generate_qr(text)
    await update.message.reply_photo(InputFile(qr), caption=text)

async def ask_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Пришлите фото чека для загрузки и проверки.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    file = await update.message.photo[-1].get_file()
    b = await file.download_as_bytearray()

    file_unique_id = update.message.photo[-1].file_unique_id
    img_hash = hash_image_bytes(bytes(b))

    # Проверка дубля
    if SHEET_CHECKS:
        rows = SHEET_CHECKS.get_all_records()
        for r in rows:
            if r.get("File_Unique_ID") == file_unique_id or r.get("Дубль_чека") == img_hash:
                await update.message.reply_text("⚠️ Этот чек уже был загружен ранее.")
                log_event("duplicate_check", uid=uid)
                return

    ocr = await ocr_extract_stub(bytes(b))
    ok = await gpt_validate_stub(ocr.get("raw_text", ""))

    SHEET_CHECKS.append_row([
        uid,
        update.effective_user.username or "",
        update.effective_user.full_name,
        "",  # Дом
        "",  # Телефон
        file.file_path,
        "",  # Сумма_по_чеку
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",  # Дата_чека
        ocr.get("date"),
        ocr.get("raw_text"),
        img_hash,
        file_unique_id,
        "auto_ok" if ok else "need_review"
    ])

    await update.message.reply_text("✅ Чек принят. Он будет проверен автоматически.")

# -------------------- ADMIN --------------------

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    kb = [
        ["📣 Напоминание по участку"],
        ["⬅️ Назад"],
    ]
    await update.message.reply_text(
        "🛠 Админ панель",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def admin_remind_plot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["wait_plot"] = True
    await update.message.reply_text("Введите номер участка:")

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("wait_plot"):
        plot = update.message.text.strip()
        sent = 0
        for u in get_all_users():
            if str(u.get("Участок")) == plot and u.get("Telegram_ID"):
                try:
                    await context.bot.send_message(
                        int(u["Telegram_ID"]),
                        f"🔔 Напоминание об оплате.\nУчасток: {plot}"
                    )
                    sent += 1
                except Exception as e:
                    logger.error("Send error: %s", e)
        context.user_data.clear()
        await update.message.reply_text(f"✅ Отправлено: {sent}")
# -------------------- REMINDERS --------------------

async def payment_reminder_job():
    users = get_all_users()
    today = datetime.now().day

    for idx, u in enumerate(users, start=2):
        try:
            uid = u.get("Telegram_ID")
            if not uid:
                continue

            pay_day = int(u.get("День_оплаты", 0) or 0)
            delta = pay_day - today

            if delta in (5, 3, 1):
                text = (
                    f"👋 {u.get('ФИО')},\n"
                    f"Напоминаем об оплате поселковых взносов. "
                    f"День оплаты: {pay_day}."
                )
            elif delta < 0:
                text = (
                    f"⚠️ {u.get('ФИО')},\n"
                    f"У вас образовалась задолженность. "
                    f"Просьба погасить задолженность."
                )
            else:
                continue

            await telegram_app.bot.send_message(int(uid), text)
            if SHEET_USERS:
                SHEET_USERS.update_cell(idx, 12, datetime.now().strftime("%Y-%m-%d"))

        except Exception as e:
            log_event("reminder_error", error=str(e))

# -------------------- ROUTER --------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "ℹ️ Информация":
        await info(update, context)
    elif text == "📊 Статус":
        await status(update, context)
    elif text == "💳 Реквизиты":
        await rekv(update, context)
    elif text == "📸 Загрузить чек":
        await ask_check(update, context)
    elif text == "🛠 Админ панель":
        await admin_panel(update, context)
    elif text == "📣 Напоминание по участку":
        await admin_remind_plot(update, context)
    elif text == "⬅️ Назад":
        await start(update, context)
    else:
        await handle_admin_text(update, context)

# -------------------- FASTAPI WEBHOOK --------------------

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

    webhook_full_url = f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}"
    await telegram_app.bot.set_webhook(webhook_full_url)

    scheduler.add_job(payment_reminder_job, "cron", hour=9, minute=0)
    scheduler.start()

    logger.info("🚀 Бот запущен, webhook установлен: %s", webhook_full_url)

@app.on_event("shutdown")
async def on_shutdown():
    if telegram_app:
        await telegram_app.stop()
        await telegram_app.shutdown()
    scheduler.shutdown()

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return PlainTextResponse("OK")

@app.get("/")
async def health():
    return PlainTextResponse("OK")

# -------------------- RUN --------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
