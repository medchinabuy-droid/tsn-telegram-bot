import os
import json
import logging
import io
from datetime import datetime, timedelta, time
import pytz

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from telegram.error import Forbidden, BadRequest

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Moscow")

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# ---------------- GOOGLE ----------------
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(
    creds_info,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_logs = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# ---------------- MENUS ----------------
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["🔍 Долг по участку"], ["📊 Статистика"], ["⬅️ Назад"]],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def is_admin(uid):
    return uid in ADMIN_IDS

def log_event(event, uid="", username="", house="", details="", error=""):
    try:
        sheet_logs.append_row([
            datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            event,
            uid,
            username,
            house,
            event,
            details,
            error
        ])
    except Exception:
        pass

def find_user(uid):
    for r in sheet_users.get_all_records():
        if str(r.get("Telegram_ID")) == str(uid):
            return r
    return None

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
    file = drive.files().create(
        body={
            "name": name,
            "parents": [DRIVE_FOLDER_ID]
        },
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{file['id']}/view"
# ---------------- УВЕДОМЛЕНИЯ ----------------
def notify_text(fio, level):
    base = (
        f"Уважаемый(ая) {fio}!\n\n"
        "Просим Вас оплатить поселковые сборы в ТСН «Искона-Парк».\n"
        "У Вас имеется задолженность.\n\n"
        "С уважением,\nПравление ТСН"
    )
    if level == "soft":
        return "⏳ Напоминание\n\n" + base
    if level == "medium":
        return "⚠️ Важно\n\n" + base
    return "❗ Срочно\n\n" + base

async def monthly_notify(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).day
    blocked = 0

    for r in sheet_users.get_all_records():
        try:
            uid = int(r["Telegram_ID"])
            pay_day = int(r.get("День_оплаты") or 0)
            debt = float(str(r.get("Сумма") or "0").replace(",", "."))
            fio = r.get("ФИО", "")

            if debt <= 0 or pay_day == 0:
                continue

            delta = pay_day - today
            if delta == 3:
                text = notify_text(fio, "soft")
            elif delta == 1:
                text = notify_text(fio, "medium")
            elif delta == 0:
                text = notify_text(fio, "hard")
            else:
                continue

            await context.bot.send_message(uid, text)

        except Forbidden:
            blocked += 1
            log_event("blocked", r["Telegram_ID"], r.get("username"))
        except Exception as e:
            log_event("notify_error", r["Telegram_ID"], error=str(e))

    context.bot_data["blocked_count"] = blocked

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = find_user(uid)

    if user:
        text = f"Здравствуйте, {user.get('ФИО')}.\n\nБот ТСН «Искона-Парк» готов к работе."
    else:
        text = "Здравствуйте.\n\nБот ТСН «Искона-Парк»."

    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    await update.message.reply_text(text, reply_markup=menu)

# ---------------- РЕКВИЗИТЫ ----------------
async def send_reqs(update: Update):
    r = sheet_reqs.row_values(2)
    await update.message.reply_text(
        f"💳 Реквизиты\n\n"
        f"Банк: {r[0]}\nБИК: {r[1]}\nСчёт: {r[2]}\nПолучатель: {r[3]}\nИНН: {r[4]}"
    )
    if len(r) > 5 and r[5]:
        try:
            await update.message.reply_photo(r[5])
        except BadRequest:
            await update.message.reply_document(r[5])

# ---------------- ЧЕК ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.photo[-1] if update.message.photo else update.message.document
    uid = file.file_unique_id

    if is_duplicate(uid):
        await update.message.reply_text("❌ Такой чек уже загружался ранее.")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()
    link = upload_to_drive(data, f"check_{uid}", file.mime_type)

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        "",
        "",
        "",
        link,
        "",
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "",
        "",
        "НЕТ",
        uid,
        "новый"
    ])

    await update.message.reply_text(
        "✅ Чек получен и сохранён.\nБлагодарим за оплату!"
    )

# ---------------- ТЕКСТ ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    uid = update.effective_user.id

    if t == "🚀 Начать":
        await start(update, context)
    elif t == "💳 Реквизиты":
        await send_reqs(update)
    elif t == "📎 Загрузить чек":
        await update.message.reply_text("📎 Отправьте фото или PDF чека")
    elif t == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель", reply_markup=ADMIN_PANEL)
    elif t == "📊 Статистика" and is_admin(uid):
        blocked = context.bot_data.get("blocked_count", 0)
        await update.message.reply_text(
            f"📊 Статистика\n\n"
            f"🚫 Заблокировали бота: {blocked}"
        )

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.job_queue.run_daily(monthly_notify, time=time(18, 0, tzinfo=TZ))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
