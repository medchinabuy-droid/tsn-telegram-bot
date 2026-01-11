import os
import json
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

# -------------------- CONFIG --------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# -------------------- LOGGING --------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- GOOGLE SHEETS --------------------

creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
gc = gspread.authorize(creds)

sh = gc.open_by_key(os.getenv("SPREADSHEET_ID"))

sheet_users = sh.get_worksheet(0)   # Лист 1
sheet_debts = sh.get_worksheet(1)   # Лист 2
sheet_logs = sh.get_worksheet(2)    # Лист 3
sheet_reqs = sh.get_worksheet(3)    # Реквизиты

# -------------------- KEYBOARDS --------------------

USER_KB = ReplyKeyboardMarkup(
    [
        ["🔍 Долг по участку"],
        ["📎 Загрузить чек"],
        ["💳 Реквизиты"]
    ],
    resize_keyboard=True
)

ADMIN_KB = ReplyKeyboardMarkup(
    [
        ["📣 Боевое уведомление"],
        ["📊 Статистика"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# -------------------- HELPERS --------------------

def log_event(event, user_id="", details=""):
    sheet_logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event,
        user_id,
        details
    ])

def get_all_users():
    return sheet_users.col_values(1)[1:]

# -------------------- HANDLERS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    users = get_all_users()

    if str(user.id) not in users:
        sheet_users.append_row([user.id, user.username or "", datetime.now().isoformat()])
        log_event("REGISTER", user.id)

    kb = ADMIN_KB if user.id in ADMIN_IDS else USER_KB
    await update.message.reply_text("Главное меню", reply_markup=kb)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    # -------- ADMIN --------
    if user_id in ADMIN_IDS:

        if text == "📣 Боевое уведомление":
            sent = 0
            blocked = 0
            message = (
                "⚠️ ВАЖНОЕ УВЕДОМЛЕНИЕ\n\n"
                "Просим срочно проверить задолженность и оплатить."
            )

            for uid in get_all_users():
                try:
                    await context.bot.send_message(chat_id=int(uid), text=message)
                    sent += 1
                except:
                    blocked += 1
                    log_event("BLOCKED", uid)

            log_event("BROADCAST", user_id, f"sent={sent}, blocked={blocked}")
            await update.message.reply_text(
                f"Рассылка завершена\n\nОтправлено: {sent}\nЗаблокировали: {blocked}"
            )
            return

        if text == "📊 Статистика":
            users = len(get_all_users())
            logs = sheet_logs.get_all_values()[1:]
            broadcasts = len([l for l in logs if l[1] == "BROADCAST"])
            blocked = len([l for l in logs if l[1] == "BLOCKED"])

            await update.message.reply_text(
                f"📊 Статистика\n\n"
                f"Пользователей: {users}\n"
                f"Рассылок: {broadcasts}\n"
                f"Блокировок: {blocked}"
            )
            return

        if text == "⬅️ Назад":
            await update.message.reply_text("Главное меню", reply_markup=USER_KB)
            return

    # -------- USER --------
    await update.message.reply_text("Используйте меню 👇")

# -------------------- WEBHOOK --------------------

async def handle_webhook(request):
    data = await request.json()
    await application.update_queue.put(Update.de_json(data, application.bot))
    return web.Response(text="ok")

# -------------------- APP INIT --------------------

application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

async def on_startup(app):
    await application.initialize()
    await application.bot.set_webhook(WEBHOOK_URL)
    await application.start()
    logger.info("BOT STARTED")

async def on_shutdown(app):
    await application.stop()
    await application.shutdown()

app = web.Application()
app.router.add_post("/", handle_webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=int(os.getenv("PORT", 8080)))
