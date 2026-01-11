import os
import json
import logging
from datetime import datetime

from aiohttp import web
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
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

# =======================
# ENV
# =======================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_IDS = set(map(int, os.getenv("ADMIN_IDS", "").split(",")))

# =======================
# LOGGING
# =======================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =======================
# GOOGLE SHEETS
# =======================
creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(credentials)
sh = gc.open_by_key(SPREADSHEET_ID)

def ws_by_index(i: int, name: str):
    ws = sh.get_worksheet(i)
    logger.info(f"WS OK index={i} title='{ws.title}' expected='{name}'")
    return ws

sheet_main = ws_by_index(0, "Лист 1")
sheet_new = ws_by_index(1, "Лист 2")
sheet_logs = ws_by_index(2, "Лист 3")
sheet_rekv = ws_by_index(3, "Реквизиты")

# =======================
# UTILS
# =======================
def log_event(event: str, user_id=None, extra=""):
    sheet_logs.append_row([
        datetime.now().isoformat(),
        event,
        str(user_id) if user_id else "",
        extra
    ])

def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["📎 Загрузить чек"],
            ["💳 Реквизиты"],
        ],
        resize_keyboard=True
    )

# =======================
# HANDLERS
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log_event("START", user.id, user.username or "")
    await update.message.reply_text(
        "👋 Добро пожаловать в ТСН «Искона-Парк»\n\n"
        "ℹ️ Используйте меню ⬇️",
        reply_markup=main_menu()
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.effective_user

    if text == "💳 Реквизиты":
        rows = sheet_rekv.get_all_records()
        if rows:
            r = rows[0]
            msg = (
                "💳 Реквизиты:\n\n"
                f"Банк: {r.get('Банк')}\n"
                f"БИК: {r.get('БИК')}\n"
                f"Счёт: {r.get('Счёт получателя')}\n"
                f"Получатель: {r.get('Получатель')}\n"
                f"ИНН: {r.get('ИНН')}"
            )
        else:
            msg = "❌ Реквизиты не найдены"
        await update.message.reply_text(msg)
        log_event("REKV", user.id)
        return

    if text == "📎 Загрузить чек":
        await update.message.reply_text(
            "📎 Отправьте фото или PDF чека",
            reply_markup=main_menu()
        )
        log_event("UPLOAD_PROMPT", user.id)
        return

    await update.message.reply_text(
        "ℹ️ Используйте кнопки меню ⬇️",
        reply_markup=main_menu()
    )

# =======================
# WEBHOOK APP
# =======================
async def handle_webhook(request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return web.Response(status=403)

    data = await request.json()
    await application.update_queue.put(Update.de_json(data, application.bot))
    return web.Response(text="OK")

async def on_startup(app):
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )
    logger.info("Webhook set")

# =======================
# MAIN
# =======================
application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

aio_app = web.Application()
aio_app.router.add_post("/", handle_webhook)
aio_app.on_startup.append(on_startup)

if __name__ == "__main__":
    logger.info("BOT START")
    web.run_app(aio_app, port=int(os.getenv("PORT", 8080)))
