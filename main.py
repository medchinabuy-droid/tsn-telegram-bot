import os
import json
import io
import logging
from datetime import datetime, timedelta

from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from telegram import Update, ReplyKeyboardMarkup
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

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

if not all([BOT_TOKEN, WEBHOOK_URL, SPREADSHEET_ID, DRIVE_FOLDER_ID]):
    raise RuntimeError("❌ Не заданы обязательные ENV-переменные")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= GOOGLE =================

creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ws_users = sh.worksheet("Лист 1")
ws_debts = sh.worksheet("Лист 2")
ws_logs  = sh.worksheet("Лист 3")
ws_reqs  = sh.worksheet("Реквизиты")

drive = build("drive", "v3", credentials=creds)

# ================= KEYBOARDS =================

START_KB = ReplyKeyboardMarkup([["🚀 Старт"]], resize_keyboard=True)

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

# ================= HELPERS =================

def log_event(event, uid="", details=""):
    ws_logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event,
        str(uid),
        details
    ])

def get_user_row(uid):
    ids = ws_users.col_values(1)
    return ids.index(str(uid)) + 1 if str(uid) in ids else None

def get_plot(uid):
    r = get_user_row(uid)
    return ws_users.cell(r, 3).value if r else None

def mark_debt_paid(plot):
    for i, r in enumerate(ws_debts.get_all_values()[1:], start=2):
        if r[0] == plot:
            ws_debts.update_cell(i, 4, "ОПЛАЧЕНО")
            return

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user

    if not get_user_row(u.id):
        ws_users.append_row([
            u.id,
            u.username or "",
            "",
            datetime.now().isoformat(),
            ""
        ])
        log_event("START", u.id)

    kb = ADMIN_KB if u.id in ADMIN_IDS else USER_KB
    await update.message.reply_text("Бот активен ✅", reply_markup=kb)

async def debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = get_plot(update.effective_user.id)
    if not plot:
        await update.message.reply_text("❗ Участок не указан")
        return

    for r in ws_debts.get_all_values()[1:]:
        if r[0] == plot:
            if r[3] == "ОПЛАЧЕНО":
                await update.message.reply_text("✅ Долг оплачен")
            else:
                await update.message.reply_text(
                    f"💰 Долг: {r[1]}\n📅 До: {r[2]}"
                )
            return

    await update.message.reply_text("✅ Долгов нет")

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    uid = update.effective_user.id

    if ws_logs.findall(doc.file_unique_id):
        await update.message.reply_text("⚠️ Этот чек уже загружен")
        return

    file = await doc.get_file()
    data = await file.download_as_bytearray()

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=doc.mime_type)
    meta = {"name": doc.file_name, "parents": [DRIVE_FOLDER_ID]}

    uploaded = drive.files().create(
        body=meta, media_body=media, fields="id"
    ).execute()

    plot = get_plot(uid)
    if plot:
        mark_debt_paid(plot)

    log_event("CHECK", uid, uploaded["id"])
    await update.message.reply_text("✅ Чек принят. Долг закрыт.")

# ================= ROUTER =================

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    uid = update.effective_user.id

    if txt == "🚀 Старт":
        await start(update, context)
    elif txt == "🔍 Долг по участку":
        await debt(update, context)
    elif txt == "📎 Загрузить чек":
        await update.message.reply_text("Пришлите PDF или фото")
    elif txt == "💳 Реквизиты":
        await update.message.reply_text(
            "\n".join(r[0] for r in ws_reqs.get_all_values())
        )

# ================= WEBHOOK =================

async def webhook(request):
    data = await request.json()
    await application.update_queue.put(
        Update.de_json(data, application.bot)
    )
    return web.Response(text="ok")

# ================= INIT =================

application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.Document.ALL, upload_check))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

async def on_startup(app):
    await application.initialize()
    await application.bot.set_webhook(WEBHOOK_URL)
    await application.start()
    logger.info("BOT STARTED")

async def on_shutdown(app):
    await application.stop()
    await application.shutdown()

app = web.Application()
app.router.add_post("/", webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=int(os.getenv("PORT", 8080)))
