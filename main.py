import os
import json
import logging
from datetime import datetime, timedelta

from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputFile
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
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

# ---------------- CONFIG ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ---------------- LOGGING ----------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- GOOGLE ----------------

creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.get_worksheet(0)
sheet_debts = sh.get_worksheet(1)
sheet_logs = sh.get_worksheet(2)
sheet_reqs = sh.get_worksheet(3)

drive = build("drive", "v3", credentials=creds)

# ---------------- KEYBOARDS ----------------

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

# ---------------- HELPERS ----------------

def log_event(event, user_id="", details=""):
    sheet_logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event,
        user_id,
        details
    ])

def get_user_row(user_id):
    ids = sheet_users.col_values(1)
    if str(user_id) in ids:
        return ids.index(str(user_id)) + 1
    return None

def get_user_plot(user_id):
    row = get_user_row(user_id)
    if not row:
        return None
    return sheet_users.cell(row, 3).value

# ---------------- BOT HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    row = get_user_row(u.id)

    if not row:
        sheet_users.append_row([
            u.id,
            u.username or "",
            "",
            datetime.now().isoformat(),
            ""
        ])
        log_event("REGISTER", u.id)

    kb = ADMIN_KB if u.id in ADMIN_IDS else USER_KB

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "⬇️ Используйте меню внизу экрана",
        reply_markup=kb
    )

# ---------- ДОЛГ ----------

async def debt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = get_user_plot(update.effective_user.id)

    if not plot:
        await update.message.reply_text("❗ У вас не указан участок.")
        return

    rows = sheet_debts.get_all_values()[1:]
    for r in rows:
        if r[0] == plot:
            await update.message.reply_text(
                f"🔍 Долг по участку {plot}\n\n"
                f"💰 Сумма: {r[1]}\n"
                f"📅 Дедлайн: {r[2]}"
            )
            return

    await update.message.reply_text("✅ Задолженностей нет")

# ---------- ЧЕК ----------

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    file = await doc.get_file()
    content = await file.download_as_bytearray()

    fh = io.BytesIO(content)
    media = MediaIoBaseUpload(fh, mimetype=doc.mime_type)

    file_metadata = {
        "name": doc.file_name,
        "parents": [DRIVE_FOLDER_ID]
    }

    uploaded = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()

    log_event("CHECK_UPLOADED", update.effective_user.id, uploaded["id"])

    await update.message.reply_text("✅ Чек загружен и принят")

# ---------- ADMIN BROADCAST ----------

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["await_plot"] = True
    await update.message.reply_text("Введите номер участка:")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # ADMIN FLOW
    if uid in ADMIN_IDS:

        if text == "📣 Боевое уведомление":
            await admin_broadcast(update, context)
            return

        if context.user_data.get("await_plot"):
            context.user_data["plot"] = text
            context.user_data["await_plot"] = False

            sent = 0
            for row in sheet_users.get_all_values()[1:]:
                if row[2] == text:
                    try:
                        await context.bot.send_message(
                            chat_id=int(row[0]),
                            text="⚠️ Напоминание о задолженности"
                        )
                        sent += 1
                    except:
                        log_event("BLOCKED", row[0])

            log_event("BROADCAST", uid, f"plot={text}, sent={sent}")
            await update.message.reply_text(f"Отправлено: {sent}")
            return

    # USER FLOW
    if text == "🔍 Долг по участку":
        await debt_handler(update, context)
        return

    if text == "📎 Загрузить чек":
        await update.message.reply_text("📎 Пришлите PDF или фото чека")
        return

    if text == "💳 Реквизиты":
        rows = sheet_reqs.get_all_values()
        msg = "\n".join(r[0] for r in rows if r)
        await update.message.reply_text(msg)
        return

# ---------------- SCHEDULER ----------------

async def reminders():
    today = datetime.now().date()
    rows = sheet_debts.get_all_values()[1:]

    for r in rows:
        deadline = datetime.fromisoformat(r[2]).date()
        delta = (deadline - today).days

        if delta in [5, 3, 1]:
            for u in sheet_users.get_all_values()[1:]:
                if u[2] == r[0]:
                    try:
                        await application.bot.send_message(
                            chat_id=int(u[0]),
                            text=f"⏰ Напоминание: долг {r[1]} до {r[2]}"
                        )
                        log_event("AUTO_NOTIFY", u[0], f"{delta} days")
                    except:
                        log_event("BLOCKED", u[0])

# ---------------- WEBHOOK ----------------

async def webhook(request):
    data = await request.json()
    await application.update_queue.put(
        Update.de_json(data, application.bot)
    )
    return web.Response(text="ok")

# ---------------- INIT ----------------

application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.Document.ALL, upload_check))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

scheduler = AsyncIOScheduler()
scheduler.add_job(reminders, "cron", hour=9)
scheduler.start()

async def on_startup(app):
    await application.initialize()
    await application.bot.set_webhook(WEBHOOK_URL)
    await application.start()
    logger.info("BOT START")

async def on_shutdown(app):
    await application.stop()
    await application.shutdown()

app = web.Application()
app.router.add_post("/", webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=int(os.getenv("PORT", 8080)))
