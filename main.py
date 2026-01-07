import os
import json
import logging
import asyncio
from aiohttp import web
from datetime import datetime

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://tsn-telegram-bot.onrender.com{WEBHOOK_PATH}"

FOLDER_NAME = "TSN_CHECKS"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ================== GOOGLE INIT ==================

if not GOOGLE_CREDENTIALS_JSON:
    raise RuntimeError("❌ GOOGLE_CREDENTIALS_JSON отсутствует")

creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
scopes = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets"
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)

gc = gspread.authorize(credentials)
sheet = gc.open_by_key(SPREADSHEET_ID).sheet1

drive_service = build("drive", "v3", credentials=credentials)

logger.info("✅ Google Sheets и Drive инициализированы")

# ================== DRIVE ==================

def get_or_create_folder(name: str) -> str:
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    res = drive_service.files().list(q=query, fields="files(id)").execute()
    if res["files"]:
        return res["files"][0]["id"]

    folder = drive_service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id"
    ).execute()
    return folder["id"]

FOLDER_ID = get_or_create_folder(FOLDER_NAME)

# ================== UI ==================

START_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("🚀 Начать")]],
    resize_keyboard=True
)

UPLOAD_HINT = (
    "📸 <b>Загрузите фото или PDF чека</b>\n\n"
    "👉 Нажмите <b>📎 (скрепку)</b>\n"
    "👉 Выберите <b>Камера</b> или <b>Файл</b>\n"
    "👉 Отправьте чек\n\n"
    "❗ Текст вводить не нужно"
)

# ================== HANDLERS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "👋 Добро пожаловать!\nНажмите кнопку ниже 👇",
        reply_markup=START_KEYBOARD
    )

async def start_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "WAIT_CHECK"

    await update.message.reply_text(
        UPLOAD_HINT,
        parse_mode="HTML"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state == "WAIT_CHECK":
        await update.message.reply_text(
            "📎 Я жду <b>фото или PDF</b> чека.\n"
            "Нажмите скрепку ⬇️",
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(
        "🤖 Нажмите «🚀 Начать»",
        reply_markup=START_KEYBOARD
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "WAIT_CHECK":
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

    else:
        return

    file_bytes = await tg_file.download_as_bytearray()
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        resumable=False
    )

    drive_file = drive_service.files().create(
        body={
            "name": filename,
            "parents": [FOLDER_ID]
        },
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    sheet.append_row([
        "",                     # Участок
        update.effective_user.full_name,
        update.effective_user.id,
        "",                     # Телефон
        "",                     # День оплаты
        "",                     # Электро
        "",                     # Сумма
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        "Чек получен",
        "", "", "",
        drive_file["webViewLink"]
    ])

    context.user_data["state"] = "DONE"

    await update.message.reply_text(
        "✅ Чек принят!\nСпасибо 🙌"
    )

# ================== WEB SERVER ==================

async def webhook_handler(request):
    data = await request.json()
    await application.process_update(Update.de_json(data, application.bot))
    return web.Response(text="OK")

async def healthcheck(request):
    return web.Response(text="OK")

async def start_webserver():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.router.add_get("/", healthcheck)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"🚀 HTTP сервер запущен на порту {PORT}")

# ================== MAIN ==================

async def main():
    global application
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex("^🚀 Начать$"), start_button))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    await application.bot.set_webhook(WEBHOOK_URL)
    await application.initialize()
    await application.start()

    await start_webserver()

    logger.info("🌍 Webhook установлен и бот запущен")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
