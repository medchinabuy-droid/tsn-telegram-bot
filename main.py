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
    except Exception as e:
        logger.warning(f"LOG SKIPPED: {e}")

def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{f['id']}"

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)

# ---------------- AUTO MONTHLY NOTIFY ----------------
async def monthly_notify(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()

    for r in sheet_users.get_all_records():
        try:
            pay_day = int(r.get("День_оплаты") or 0)
            if pay_day == 0:
                continue

            debt = float(str(r.get("Сумма") or "0").replace(",", "."))
            if debt <= 0 or str(r.get("Статус")).upper() == "ОПЛАЧЕНО":
                continue

            start_day = pay_day - 5
            if not (start_day <= today.day <= pay_day):
                continue

            await context.bot.send_message(
                int(r["Telegram_ID"]),
                "⏰ Напоминание ТСН\n\n"
                "У вас есть задолженность.\n"
                "Просим произвести оплату.\n\n"
                "После оплаты загрузите чек в бота."
            )

            log_event("auto_notify", r["Telegram_ID"], r.get("username"), r.get("Участок"))

        except Exception as e:
            log_event(
                "blocked",
                r.get("Telegram_ID"),
                r.get("username"),
                r.get("Участок"),
                error=str(e)
            )

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    await update.message.reply_text("👋 Добро пожаловать", reply_markup=menu)

# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text == "🚀 Начать":
        await start(update, context)
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text("Меню", reply_markup=ADMIN_MENU)
        return

    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["wait_house"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("wait_house"):
        context.user_data.clear()
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                await update.message.reply_text(
                    f"🏠 Участок: {text}\n"
                    f"ФИО: {r.get('ФИО')}\n"
                    f"Телефон: {r.get('Телефон')}\n"
                    f"Сумма: {r.get('Сумма')}\n"
                    f"Статус: {r.get('Статус')}\n"
                    f"Username: @{r.get('username')}",
                    reply_markup=ADMIN_PANEL
                )
                return
        await update.message.reply_text("❌ Не найдено", reply_markup=ADMIN_PANEL)

    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"💳 Реквизиты\n\n"
            f"Банк: {r[0]}\nБИК: {r[1]}\n"
            f"Счёт: {r[2]}\nПолучатель: {r[3]}\nИНН: {r[4]}"
        )
        if r[5]:
            await update.message.reply_photo(r[5])

    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text("Отправьте фото или PDF чека")

# ---------------- FILE ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    file = update.message.photo[-1] if update.message.photo else update.message.document

    if is_duplicate(file.file_unique_id):
        await update.message.reply_text("❌ Чек уже был")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()
    link = upload_to_drive(data, "check", file.mime_type)

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
        "",
        file.file_unique_id,
        "новый"
    ])

    context.user_data.clear()
    await update.message.reply_text("✅ Чек принят")

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.job_queue.run_daily(
        monthly_notify,
        time=time(hour=18, minute=0, tzinfo=TZ)
    )

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
