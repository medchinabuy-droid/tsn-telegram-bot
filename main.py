import os
import json
import logging
import asyncio
from datetime import datetime

from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

PORT = int(os.getenv("PORT", 10000))

# ================== GOOGLE ==================
creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_debts = sh.worksheet("Лист 2")
sheet_logs = sh.worksheet("Лист 3")

logger.info("Sheets connected")

# ================== HELPERS ==================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def log_event(event_type, user, text):
    sheet_logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event_type,
        user.id,
        user.username or "",
        text
    ])

# ================== KEYBOARDS ==================
def main_keyboard(is_admin=False):
    kb = [
        [InlineKeyboardButton("🔍 Долг по участку", callback_data="debt")],
    ]
    if is_admin:
        kb.append([InlineKeyboardButton("🛠 Админ-меню", callback_data="admin")])
    return InlineKeyboardMarkup(kb)

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Боевое уведомление", callback_data="battle")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")]
    ])

# ================== HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log_event("START", user, "start")
    await update.message.reply_text(
        "👋 Бот ТСН активен",
        reply_markup=main_keyboard(is_admin(user.id))
    )

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if query.data == "back":
        await query.edit_message_text(
            "Главное меню",
            reply_markup=main_keyboard(is_admin(user.id))
        )

    elif query.data == "admin" and is_admin(user.id):
        await query.edit_message_text(
            "🛠 Админ-меню",
            reply_markup=admin_keyboard()
        )

    elif query.data == "stats" and is_admin(user.id):
        users = len(sheet_users.get_all_records())
        logs = len(sheet_logs.get_all_records())
        await query.edit_message_text(
            f"📊 Статистика\n\n👥 Пользователей: {users}\n🧾 Логов: {logs}",
            reply_markup=admin_keyboard()
        )

    elif query.data == "battle" and is_admin(user.id):
        rows = sheet_users.get_all_records()
        buttons = [
            [InlineKeyboardButton(f"Участок {r['участок']}", callback_data=f"battle_{r['участок']}")]
            for r in rows if r.get("участок")
        ]
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin")])
        await query.edit_message_text(
            "Выберите участок:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data.startswith("battle_") and is_admin(user.id):
        house = query.data.split("_")[1]
        rows = sheet_users.get_all_records()
        for r in rows:
            if str(r.get("участок")) == house:
                try:
                    await context.bot.send_message(
                        chat_id=int(r["chat_id"]),
                        text=f"📣 ВАЖНОЕ УВЕДОМЛЕНИЕ\nУчасток {house}"
                    )
                except:
                    sheet_logs.append_row([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "BLOCKED",
                        r["tg_id"],
                        r["username"],
                        "bot blocked"
                    ])
        await query.edit_message_text("✅ Отправлено")

# ================== AUTO ==================
async def auto_notify(app: Application):
    users = sheet_users.get_all_records()
    debts = sheet_debts.get_all_records()
    for d in debts:
        for u in users:
            if u.get("участок") == d.get("участок"):
                try:
                    await app.bot.send_message(
                        chat_id=int(u["chat_id"]),
                        text=f"⏰ Напоминание: долг {d['сумма']} ₽"
                    )
                except:
                    pass

# ================== WEBHOOK ==================
async def telegram_webhook(request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return web.Response(text="ok")

async def healthcheck(request):
    return web.Response(text="ok")

# ================== MAIN ==================
async def main():
    global application

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callbacks))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(auto_notify, "interval", hours=24, args=[application])
    scheduler.start()

    await application.initialize()
    await application.start()

    await application.bot.set_webhook(f"{WEBHOOK_URL}/{WEBHOOK_SECRET}")

    app = web.Application()
    app.router.add_get("/", healthcheck)
    app.router.add_post(f"/{WEBHOOK_SECRET}", telegram_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info("BOT READY")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
