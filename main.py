import os
import json
import asyncio
import logging
import time
from typing import Set

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "TSN")
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN or not RENDER_URL:
    raise RuntimeError("❌ BOT_TOKEN или RENDER_EXTERNAL_URL не заданы")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_URL}{WEBHOOK_PATH}"

# ================= GOOGLE =================
worksheet_checks = None

def init_google():
    global worksheet_checks

    if not GOOGLE_JSON:
        logger.error("❌ GOOGLE_SERVICE_ACCOUNT_JSON отсутствует")
        return

    try:
        import gspread
        creds = json.loads(GOOGLE_JSON)
        gc = gspread.service_account_from_dict(creds)

        sh = gc.open(SPREADSHEET_NAME)

        if "TSN_CHECKS" not in [ws.title for ws in sh.worksheets()]:
            worksheet_checks = sh.add_worksheet(
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
            worksheet_checks = sh.worksheet("TSN_CHECKS")

        logger.info("✅ Google Sheets подключён")

    except Exception as e:
        logger.exception(f"❌ Google error: {e}")

# ================= MEMORY =================
used_files: Set[str] = set()
last_upload: dict[int, float] = {}
ANTI_FLOOD = 5

def is_flood(user_id: int) -> bool:
    now = time.time()
    last = last_upload.get(user_id, 0)
    last_upload[user_id] = now
    return now - last < ANTI_FLOOD

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет!\n\n"
        "📸 Отправь фото или PDF чека.\n"
        "♻️ Дубликаты отсеиваются автоматически."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]

    if is_flood(user.id):
        await update.message.reply_text("⏳ Подожди пару секунд")
        return

    if photo.file_unique_id in used_files:
        await update.message.reply_text("♻️ Этот чек уже был")
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

    await update.message.reply_text("✅ Чек принят")

# ================= WEBHOOK =================
async def telegram_webhook(request: web.Request):
    data = await request.json()
    update = Update.de_json(data, request.app["telegram_app"].bot)
    await request.app["telegram_app"].process_update(update)
    return web.Response(text="ok")

# ================= CLEANUP =================
async def cleanup():
    while True:
        await asyncio.sleep(3600)
        used_files.clear()
        logger.info("🧹 Очистка дублей")

# ================= MAIN =================
async def main():
    init_google()

    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(WEBHOOK_URL)
    await telegram_app.start()

    logger.info(f"🌍 Webhook установлен: {WEBHOOK_URL}")

    # aiohttp server
    app = web.Application()
    app["telegram_app"] = telegram_app
    app.router.add_post(WEBHOOK_PATH, telegram_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"🚀 Сервер слушает порт {PORT}")

    telegram_app.create_task(cleanup())

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
