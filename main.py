import os
import json
import logging
import asyncio
import time
from typing import Set

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "TSN")
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN or not RENDER_URL:
    raise RuntimeError("❌ BOT_TOKEN или RENDER_EXTERNAL_URL не заданы")

WEBHOOK_URL = f"{RENDER_URL}/webhook"

# ================== GOOGLE ==================
gsheet = None
worksheet_checks = None

def init_google():
    global gsheet, worksheet_checks

    if not GOOGLE_JSON:
        logger.error("❌ GOOGLE_SERVICE_ACCOUNT_JSON отсутствует")
        return

    try:
        import gspread

        creds = json.loads(GOOGLE_JSON)
        gc = gspread.service_account_from_dict(creds)

        gsheet = gc.open(SPREADSHEET_NAME)

        titles = [ws.title for ws in gsheet.worksheets()]
        if "TSN_CHECKS" not in titles:
            worksheet_checks = gsheet.add_worksheet(
                title="TSN_CHECKS", rows=1000, cols=6
            )
            worksheet_checks.append_row([
                "timestamp",
                "telegram_id",
                "username",
                "file_id",
                "file_unique_id",
                "status",
            ])
        else:
            worksheet_checks = gsheet.worksheet("TSN_CHECKS")

        logger.info("✅ Google Sheets подключён")

    except Exception as e:
        logger.exception(f"❌ Ошибка Google Sheets: {e}")

# ================== MEMORY ==================
used_files: Set[str] = set()
last_upload: dict[int, float] = {}
ANTI_FLOOD_SECONDS = 5

def is_flood(user_id: int) -> bool:
    now = time.time()
    last = last_upload.get(user_id, 0)
    last_upload[user_id] = now
    return now - last < ANTI_FLOOD_SECONDS

# ================== HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет!\n\n"
        "📸 Отправь фото или PDF чека.\n"
        "♻️ Дубликаты автоматически отсеиваются."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]

    if is_flood(user.id):
        await update.message.reply_text("⏳ Подожди пару секунд")
        return

    if photo.file_unique_id in used_files:
        await update.message.reply_text("♻️ Этот чек уже был загружен ранее")
        return

    used_files.add(photo.file_unique_id)

    if worksheet_checks:
        worksheet_checks.append_row([
            int(time.time()),
            user.id,
            user.username or "",
            photo.file_id,
            photo.file_unique_id,
            "OK",
        ])

    await update.message.reply_text("✅ Чек принят и сохранён")

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("OK")

# ================== CLEANUP ==================
async def cleanup_task():
    while True:
        await asyncio.sleep(3600)
        used_files.clear()
        logger.info("🧹 Очистка file_unique_id")

# ================== MAIN ==================
async def main():
    init_google()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("health", health))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    await application.initialize()
    await application.bot.set_webhook(WEBHOOK_URL)

    logger.info(f"🌍 Webhook установлен: {WEBHOOK_URL}")

    application.create_task(cleanup_task())

    await application.start()
    await application.bot.initialize()

    await asyncio.Event().wait()  # держим процесс живым

if __name__ == "__main__":
    asyncio.run(main())
