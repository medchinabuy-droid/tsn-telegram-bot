import os
import json
import logging
import asyncio
from datetime import datetime, timedelta

from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.error import Forbidden

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME_USERS = os.getenv("SHEET_NAME")  # Лист 2
LOG_SHEET_NAME = "Логи"  # Лист 3

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== GOOGLE ==================
def init_gs():
    creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID)

    users_ws = sheet.worksheet(SHEET_NAME_USERS)

    try:
        log_ws = sheet.worksheet(LOG_SHEET_NAME)
    except gspread.WorksheetNotFound:
        log_ws = sheet.add_worksheet(LOG_SHEET_NAME, rows=1000, cols=10)
        log_ws.append_row(
            ["timestamp", "type", "action", "user_id", "username", "details"]
        )

    return users_ws, log_ws

USERS_WS, LOG_WS = init_gs()

def log_event(event_type, action, user_id=None, username=None, details=""):
    LOG_WS.append_row([
        datetime.utcnow().isoformat(),
        event_type,
        action,
        str(user_id) if user_id else "",
        username or "",
        details,
    ])

# ================== BOT ==================
app = Application.builder().token(BOT_TOKEN).build()

# ================== UI ==================
def start_keyboard(is_admin=False):
    kb = [[InlineKeyboardButton("🚀 Начать", callback_data="start")]]
    if is_admin:
        kb.append([InlineKeyboardButton("📣 Боевое уведомление", callback_data="battle")])
    return InlineKeyboardMarkup(kb)

# ================== HANDLERS ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_admin = user.id in ADMIN_IDS

    await update.message.reply_text(
        "Добро пожаловать 👋",
        reply_markup=start_keyboard(is_admin),
    )

    log_event("INFO", "START", user.id, user.username)

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    data = query.data

    if data == "start":
        await query.edit_message_text("Главное меню", reply_markup=start_keyboard(user.id in ADMIN_IDS))

    elif data == "battle":
        if user.id not in ADMIN_IDS:
            return
        kb = []
        for i in range(1, 101):
            kb.append([InlineKeyboardButton(f"Участок {i}", callback_data=f"battle_{i}")])
        await query.edit_message_text(
            "Выберите участок:",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    elif data.startswith("battle_"):
        if user.id not in ADMIN_IDS:
            return

        house = data.split("_")[1]
        await send_battle_notification(house, user)

# ================== NOTIFY ==================
async def send_battle_notification(house, admin):
    records = USERS_WS.get_all_records()

    sent = 0
    blocked = 0

    for row in records:
        if str(row.get("Участок")) != str(house):
            continue

        chat_id = row.get("chat_id")
        if not chat_id:
            continue

        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text="📣 ВАЖНОЕ УВЕДОМЛЕНИЕ\nПросьба проверить задолженность по ТСН.",
            )
            sent += 1
        except Forbidden:
            blocked += 1
            log_event(
                "BUSINESS",
                "BOT_BLOCKED",
                chat_id,
                row.get("username"),
                f"house={house}",
            )
        except Exception as e:
            log_event(
                "ERROR",
                "SEND_FAIL",
                chat_id,
                row.get("username"),
                str(e),
            )

    log_event(
        "INFO",
        "BATTLE_NOTIFY",
        admin.id,
        admin.username,
        f"house={house}, sent={sent}, blocked={blocked}",
    )

# ================== SCHEDULER ==================
scheduler = AsyncIOScheduler()

async def reminders():
    log_event("INFO", "AUTO_REMINDER_RUN")

scheduler.add_job(reminders, "interval", hours=24)

# ================== WEBHOOK ==================
async def webhook_handler(request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return web.Response(status=403)

    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="ok")

# ================== MAIN ==================
async def main():
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_button))

    await app.initialize()
    await app.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
    )

    scheduler.start()

    web_app = web.Application()
    web_app.router.add_post("/webhook", webhook_handler)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()

    logger.info("Bot started with webhook")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
