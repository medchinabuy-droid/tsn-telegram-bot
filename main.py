import os
import json
import logging
import io
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

# ================== CONFIG ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== GOOGLE ==================

creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ws_users = sh.get_worksheet(0)
ws_debts = sh.get_worksheet(1)
ws_logs = sh.get_worksheet(2)
ws_reqs = sh.get_worksheet(3)

drive = build("drive", "v3", credentials=creds)

# ================== KEYBOARDS ==================

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

# ================== HELPERS ==================

def log_event(event, uid="", details=""):
    ws_logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event,
        str(uid),
        details
    ])

def get_user_row(uid):
    ids = ws_users.col_values(1)
    if str(uid) in ids:
        return ids.index(str(uid)) + 1
    return None

def get_plot(uid):
    r = get_user_row(uid)
    return ws_users.cell(r, 3).value if r else None

# ================== HANDLERS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not get_user_row(u.id):
        ws_users.append_row([
            u.id, u.username or "", "", datetime.now().isoformat(), ""
        ])
        log_event("REGISTER", u.id)

    kb = ADMIN_KB if u.id in ADMIN_IDS else USER_KB
    await update.message.reply_text("Выберите действие ⬇️", reply_markup=kb)

# ---------- ДОЛГ ----------

async def debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = get_plot(update.effective_user.id)
    if not plot:
        await update.message.reply_text("❗ Участок не указан")
        return

    for r in ws_debts.get_all_values()[1:]:
        if r[0] == plot:
            await update.message.reply_text(
                f"🔍 Участок {plot}\n💰 Долг: {r[1]}\n📅 До: {r[2]}"
            )
            return
    await update.message.reply_text("✅ Долгов нет")

# ---------- ЧЕК ----------

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    uid = update.effective_user.id

    existing = ws_logs.findall(doc.file_unique_id)
    if existing:
        await update.message.reply_text("⚠️ Этот чек уже загружен")
        return

    file = await doc.get_file()
    data = await file.download_as_bytearray()

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=doc.mime_type)
    meta = {"name": doc.file_name, "parents": [DRIVE_FOLDER_ID]}

    uploaded = drive.files().create(
        body=meta, media_body=media, fields="id"
    ).execute()

    log_event("CHECK", uid, f"{uploaded['id']} | {doc.file_unique_id}")
    await update.message.reply_text("✅ Чек загружен")

# ---------- ADMIN ----------

async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    if text == "📣 Боевое уведомление":
        context.user_data["await_plot"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("await_plot"):
        plot = text
        context.user_data.clear()
        sent = 0

        for u in ws_users.get_all_values()[1:]:
            if u[2] == plot:
                try:
                    await context.bot.send_message(
                        chat_id=int(u[0]),
                        text="⚠️ ВАЖНО! У вас задолженность. Срочно оплатите."
                    )
                    sent += 1
                except:
                    log_event("BLOCKED", u[0])

        log_event("MANUAL_NOTIFY", uid, f"plot={plot}, sent={sent}")
        await update.message.reply_text(f"Отправлено: {sent}")
        return

    if text == "📊 Статистика":
        users = len(ws_users.get_all_values()) - 1
        debts = len(ws_debts.get_all_values()) - 1
        checks = len([r for r in ws_logs.get_all_values() if r[1] == "CHECK"])
        await update.message.reply_text(
            f"📊 Статистика\n\n"
            f"👤 Пользователей: {users}\n"
            f"💰 Долгов: {debts}\n"
            f"📎 Чеков: {checks}"
        )

# ---------- ROUTER ----------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id in ADMIN_IDS:
        await admin_text(update, context)
        return

    if update.message.text == "🔍 Долг по участку":
        await debt(update, context)

    elif update.message.text == "📎 Загрузить чек":
        await update.message.reply_text("Пришлите PDF или фото")

    elif update.message.text == "💳 Реквизиты":
        msg = "\n".join(r[0] for r in ws_reqs.get_all_values())
        await update.message.reply_text(msg)

# ================== AUTO ==================

async def auto_notify():
    today = datetime.now().date()

    for d in ws_debts.get_all_values()[1:]:
        deadline = datetime.fromisoformat(d[2]).date()
        days = (deadline - today).days
        if days in (5, 3, 1):
            for u in ws_users.get_all_values()[1:]:
                if u[2] == d[0]:
                    try:
                        await app_bot.send_message(
                            chat_id=int(u[0]),
                            text=f"⏰ Напоминание: долг {d[1]} до {d[2]}"
                        )
                        log_event("AUTO_NOTIFY", u[0], f"{days} days")
                    except:
                        log_event("BLOCKED", u[0])

# ================== WEBHOOK ==================

async def webhook(request):
    data = await request.json()
    await application.update_queue.put(
        Update.de_json(data, application.bot)
    )
    return web.Response(text="ok")

# ================== INIT ==================

application = Application.builder().token(BOT_TOKEN).build()
app_bot = application.bot

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.Document.ALL, upload_check))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

scheduler = AsyncIOScheduler()
scheduler.add_job(auto_notify, "cron", hour=9)
scheduler.start()

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
