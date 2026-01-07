import os
import json
import io
import asyncio
import logging
from datetime import datetime
from aiohttp import web

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================== ENV ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
PORT = int(os.getenv("PORT", 10000))

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://tsn-telegram-bot.onrender.com{WEBHOOK_PATH}"

# ================== LOG ==================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger(__name__)

# ================== GOOGLE ==================

if not GOOGLE_CREDENTIALS_JSON:
    raise RuntimeError("GOOGLE_CREDENTIALS_JSON не задан")

creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDENTIALS_JSON),
    scopes=[
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets"
    ]
)

gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
drive = build("drive", "v3", credentials=creds)

log.info("✅ Google Sheets и Drive готовы")

# ================== DRIVE ==================

def get_folder_id(name: str) -> str:
    res = drive.files().list(
        q=f"name='{name}' and mimeType='application/vnd.google-apps.folder'",
        fields="files(id)"
    ).execute()
    if res["files"]:
        return res["files"][0]["id"]

    folder = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id"
    ).execute()
    return folder["id"]

FOLDER_ID = get_folder_id("TSN_CHECKS")

# ================== UI ==================

KB_START = ReplyKeyboardMarkup(
    [[KeyboardButton("🚀 Начать")]],
    resize_keyboard=True
)

UPLOAD_TEXT = (
    "📸 <b>Загрузите чек</b>\n\n"
    "1️⃣ Нажмите <b>📎 скрепку</b>\n"
    "2️⃣ Выберите <b>Фото</b> или <b>Файл (PDF)</b>\n"
    "3️⃣ Отправьте чек\n\n"
    "❗ Текст вводить не нужно"
)

# ================== HANDLERS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "START"
    await update.message.reply_text(
        "👋 Добро пожаловать!\nНажмите кнопку ниже ⬇️",
        reply_markup=KB_START
    )

async def press_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "WAIT_FILE"
    await update.message.reply_text(
        UPLOAD_TEXT,
        parse_mode="HTML"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state == "WAIT_FILE":
        await update.message.reply_text(
            "📎 Я жду <b>фото или PDF чека</b>\n"
            "Нажмите скрепку ⬇️",
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(
        "Нажмите «🚀 Начать»",
        reply_markup=KB_START
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "WAIT_FILE":
        await update.message.reply_text("Нажмите «🚀 Начать»")
        return

    tg_file = None
    filename = ""

    if update.message.photo:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        filename = f"check_{photo.file_unique_id}.jpg"

    elif update.message.document:
        doc = update.message.document
        tg_file = await doc.get_file()
        filename = f"check_{doc.file_unique_id}_{doc.file_name}"

    file_bytes = await tg_file.download_as_bytearray()

    media = MediaIoBaseUpload(io.BytesIO(file_bytes), resumable=False)

    uploaded = drive.files().create(
        body={"name": filename, "parents": [FOLDER_ID]},
        media_body=media,
        fields="webViewLink"
    ).execute()

    sheet.append_row([
        update.effective_user.full_name,
        update.effective_user.id,
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        uploaded["webViewLink"]
    ])

    context.user_data["state"] = "SAVED"

    await update.message.reply_text(
        "✅ Чек принят!\nСпасибо 🙌",
        reply_markup=KB_START
    )

# ================== WEB ==================

async def webhook_handler(request):
    data = await request.json()
    await app.process_update(Update.de_json(data, app.bot))
    return web.Response(text="OK")

async def health(request):
    return web.Response(text="OK")

async def run_web():
    web_app = web.Application()
    web_app.router.add_post(WEBHOOK_PATH, webhook_handler)
    web_app.router.add_get("/", health)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info(f"🚀 HTTP сервер запущен на порту {PORT}")

# ================== MAIN ==================

async def main():
    global app
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Начать$"), press_start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    await app.bot.set_webhook(WEBHOOK_URL)
    await app.initialize()
    await app.start()

    await run_web()

    log.info("🌍 Бот полностью запущен")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
