import os
import json
import logging
import hashlib
from datetime import datetime, timedelta

from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

ADMIN_IDS = {6810194645}

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "Лист 1")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

# ================== ЛОГИ ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== GOOGLE ==================
creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
sheet_main = sh.worksheet("Лист1")
sheet_new = sh.worksheet("Лист2")
sheet_logs = sh.worksheet("Лист3")

drive_service = build("drive", "v3", credentials=creds)

# ================== КНОПКИ ==================
USER_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📎 Загрузить чек"), KeyboardButton("💳 Реквизиты")],
    ],
    resize_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📣 Боевое уведомление"), KeyboardButton("📊 Статистика")],
    ],
    resize_keyboard=True,
)

# ================== ВСПОМОГАТЕЛЬНЫЕ ==================
def log_event(event, user_id, text=""):
    sheet_logs.append_row([
        datetime.now().isoformat(),
        event,
        user_id,
        text
    ])

def get_user_row(user_id):
    rows = sheet_main.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("tg_id")) == str(user_id):
            return i, r
    return None, None

def save_to_drive(file_bytes, filename):
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype="image/jpeg")
    file = drive_service.files().create(
        media_body=media,
        body={
            "name": filename,
            "parents": [DRIVE_FOLDER_ID],
        }
    ).execute()
    return file.get("id")

# ================== ХЕНДЛЕРЫ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in ADMIN_IDS:
        await update.message.reply_text("Админ-панель", reply_markup=ADMIN_MENU)
    else:
        await update.message.reply_text(
            "👋 Добро пожаловать в ТСН «Искона-Парк»",
            reply_markup=USER_MENU,
        )

async def requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 Реквизиты:\n\n"
        "Банк: БАНКА ВТБ (ПАО)\n"
        "Получатель: ТСН \"ИСКОНА ПАРК\"\n"
        "Счёт: 40703810900810019988\n"
        "ИНН: 5028040362"
    )

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Пришлите фото чека")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()

    h = hashlib.md5(data).hexdigest()
    rows = sheet_logs.get_all_records()
    if any(r.get("hash") == h for r in rows):
        await update.message.reply_text("❌ Этот чек уже загружен")
        return

    drive_id = save_to_drive(data, f"check_{uid}_{h}.jpg")
    log_event("CHECK_UPLOADED", uid, drive_id)
    await update.message.reply_text("✅ Чек принят. Долг закрыт на 30 дней")

async def admin_battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Участок 81", callback_data="battle_81")]
    ])
    await update.message.reply_text("Выберите участок:", reply_markup=kb)

async def battle_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    house = q.data.split("_")[1]
    rows = sheet_main.get_all_records()
    for r in rows:
        if str(r.get("house")) == house:
            try:
                await context.bot.send_message(
                    chat_id=r["tg_id"],
                    text="⚠️ Срочное уведомление по задолженности"
                )
                log_event("BATTLE_NOTIFY", r["tg_id"], house)
            except:
                log_event("BLOCKED", r["tg_id"], "bot blocked")

# ================== ПЛАНИРОВЩИК ==================
async def reminders():
    rows = sheet_main.get_all_records()
    for r in rows:
        if r.get("debt", 0) > 0:
            try:
                await app.bot.send_message(
                    r["tg_id"],
                    "🔔 Напоминание о задолженности"
                )
            except:
                log_event("BLOCKED", r["tg_id"])

# ================== WEBHOOK ==================
async def webhook_handler(request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="ok")

# ================== MAIN ==================
async def main():
    global app
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Text("💳 Реквизиты"), requisites))
    app.add_handler(MessageHandler(filters.Text("📎 Загрузить чек"), upload_check))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Text("📣 Боевое уведомление"), admin_battle))
    app.add_handler(CallbackQueryHandler(battle_send, pattern="^battle_"))

    await app.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminders, "interval", days=1)
    scheduler.start()

    web_app = web.Application()
    web_app.router.add_post("/", webhook_handler)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))
    await site.start()

    logger.info("Bot started")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
