import os
import json
import logging
from datetime import datetime
import asyncio

import aiohttp
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

# ================== GOOGLE SHEETS ==================
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
def log_event(event_type, tg_id, username, text):
    sheet_logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event_type,
        tg_id,
        username or "",
        text
    ])

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ================== KEYBOARDS ==================
def start_keyboard(is_admin=False):
    kb = [
        [InlineKeyboardButton("🔍 Долг по участку", callback_data="debt")],
        [InlineKeyboardButton("📎 Загрузить чек", callback_data="upload")],
    ]
    if is_admin:
        kb.append([InlineKeyboardButton("🛠 Админ-меню", callback_data="admin")])
    return InlineKeyboardMarkup(kb)

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Боевое уведомление", callback_data="battle")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton("🔁 Ручная рассылка", callback_data="manual_send")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")]
    ])

# ================== HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log_event("START", user.id, user.username, "Bot started")
    await update.message.reply_text(
        "👋 Добро пожаловать в ТСН «Искона-Парк»",
        reply_markup=start_keyboard(is_admin(user.id))
    )

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if query.data == "back":
        await query.edit_message_text(
            "Главное меню",
            reply_markup=start_keyboard(is_admin(user.id))
        )

    elif query.data == "admin" and is_admin(user.id):
        await query.edit_message_text(
            "🛠 Админ-меню",
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
                    log_event("BLOCKED", r["tg_id"], r["username"], "Bot blocked")
        log_event("BATTLE", user.id, user.username, f"House {house}")
        await query.edit_message_text("✅ Уведомление отправлено")

    elif query.data == "debt":
        await query.edit_message_text("Введите номер участка:")
        context.user_data["wait_debt"] = True

    elif query.data == "stats" and is_admin(user.id):
        count_users = len(sheet_users.get_all_records())
        count_logs = len(sheet_logs.get_all_records())
        await query.edit_message_text(
            f"📊 Статистика\n\n👥 Пользователей: {count_users}\n📄 Логов: {count_logs}",
            reply_markup=admin_keyboard()
        )

async def messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    if context.user_data.get("wait_debt"):
        context.user_data["wait_debt"] = False
        rows = sheet_debts.get_all_records()
        for r in rows:
            if str(r.get("участок")) == text:
                await update.message.reply_text(
                    f"💰 Долг по участку {text}: {r['сумма']} ₽",
                    reply_markup=start_keyboard(is_admin(user.id))
                )
                return
        await update.message.reply_text("❌ Долг не найден")

# ================== AUTO REMINDERS ==================
async def auto_reminders(app: Application):
    rows = sheet_debts.get_all_records()
    users = sheet_users.get_all_records()
    for d in rows:
        for u in users:
            if u.get("участок") == d.get("участок"):
                try:
                    await app.bot.send_message(
                        chat_id=int(u["chat_id"]),
                        text=f"⏰ Напоминание: долг {d['сумма']} ₽"
                    )
                    log_event("AUTO_NOTIFY", u["tg_id"], u["username"], "Reminder sent")
                except:
                    log_event("BLOCKED", u["tg_id"], u["username"], "Bot blocked")

# ================== WEBHOOK ==================
async def webhook(request):
    data = await request.json()
    await application.update_queue.put(Update.de_json(data, application.bot))
    return web.Response(text="ok")

# ================== START APP ==================
async def main():
    global application
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callbacks))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, messages))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(auto_reminders, "interval", hours=24, args=[application])
    scheduler.start()

    await application.bot.set_webhook(f"{WEBHOOK_URL}/{WEBHOOK_SECRET}")

    app = web.Application()
    app.router.add_post(f"/{WEBHOOK_SECRET}", webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))
    await site.start()

    logger.info("BOT STARTED")
    await application.initialize()
    await application.start()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
