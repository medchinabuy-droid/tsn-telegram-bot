# ===============================
# TSN ISKONA PARK — TELEGRAM BOT
# MAIN.PY (PART 1 / 2)
# ===============================

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
import pytz

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import Forbidden

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from io import BytesIO

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ===============================
# ENV
# ===============================

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]

SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "ТСН")
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

GOOGLE_DRIVE_FOLDER_ID = os.environ["GOOGLE_DRIVE_FOLDER_ID"]

TIMEZONE = pytz.timezone("Europe/Moscow")

# ===============================
# LOGGING
# ===============================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===============================
# GOOGLE AUTH (IMPORTANT)
# ===============================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_logs = sh.worksheet("Лист 3")

drive_service = build("drive", "v3", credentials=creds)

# ===============================
# KEYBOARDS
# ===============================

def main_keyboard(is_admin=False):
    kb = [
        [KeyboardButton("💳 Реквизиты")],
        [KeyboardButton("📎 Загрузить чек")],
    ]
    if is_admin:
        kb.append([KeyboardButton("🛠 Админ-панель")])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔍 Долг по участку")],
            [KeyboardButton("📣 Боевое уведомление")],
            [KeyboardButton("📊 Статистика")],
        ],
        resize_keyboard=True,
    )

# ===============================
# HELPERS
# ===============================

def log_event(event_type, uid="", username="", plot="", event="", details="", error=""):
    sheet_logs.append_row([
        datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
        event_type,
        uid,
        username,
        plot,
        event,
        details,
        error,
    ])

def get_user_by_tg(tg_id):
    rows = sheet_users.get_all_records()
    for r in rows:
        if str(r.get("Telegram_ID")) == str(tg_id):
            return r
    return None

def get_user_by_plot(plot):
    rows = sheet_users.get_all_records()
    for r in rows:
        if str(r.get("Участок")) == str(plot):
            return r
    return None

# ===============================
# START / REGISTRATION
# ===============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_admin = user.id in ADMIN_IDS

    existing = get_user_by_tg(user.id)
    if existing:
        await update.message.reply_text(
            "👋 С возвращением!",
            reply_markup=main_keyboard(is_admin),
        )
        return

    context.user_data["reg_step"] = "fio"
    await update.message.reply_text("👋 Добро пожаловать!\nВведите ФИО:")

async def registration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("reg_step") != "fio":
        return

    fio = update.message.text.strip()
    user = update.effective_user

    sheet_users.append_row([
        "",                     # Участок
        fio,                    # ФИО
        user.id,                # Telegram_ID
        user.username or "",    # username
        "", "", "", "", "", "", "", "", ""
    ])

    log_event(
        "register",
        uid=user.id,
        username=user.username,
        event="Регистрация",
        details=fio,
    )

    context.user_data.clear()
    await update.message.reply_text(
        "✅ Регистрация завершена",
        reply_markup=main_keyboard(user.id in ADMIN_IDS),
    )

# ===============================
# REQUISITES + QR
# ===============================

async def requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💳 Реквизиты:\n\n"
        "Банк: БАНКА ВТБ (ПАО)\n"
        "БИК: 44525411\n"
        "Счёт: 40703810900810019988\n"
        "Получатель: ТСН \"ИСКОНА ПАРК\"\n"
        "ИНН: 5028040362"
    )
    await update.message.reply_text(text)

    try:
        with open("qr.png", "rb") as qr:
            await update.message.reply_photo(photo=qr)
    except Exception as e:
        logger.error(e)

# ===============================
# CHECK UPLOAD
# ===============================

async def ask_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Отправьте фото или PDF чека")

async def receive_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    file = None
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        filename = f"{user.id}_{int(datetime.now().timestamp())}.jpg"
    elif update.message.document:
        file = await update.message.document.get_file()
        filename = update.message.document.file_name
    else:
        return

    buffer = BytesIO()
    await file.download_to_memory(out=buffer)
    buffer.seek(0)

    media = MediaIoBaseUpload(buffer, mimetype="application/octet-stream")
    drive_file = drive_service.files().create(
        body={
            "name": filename,
            "parents": [GOOGLE_DRIVE_FOLDER_ID],
        },
        media_body=media,
        fields="id",
    ).execute()

    sheet_checks.append_row([
        user.id,
        user.username or "",
        "",
        "",
        "",
        drive_file["id"],
        "",
        datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "",
        "",
        "",
        file.file_id,
        "ожидает",
    ])

    log_event("check", uid=user.id, event="Чек загружен", details=filename)

    await update.message.reply_text("✅ Чек принят. Ожидайте подтверждения администратора.")
# ===============================
# ADMIN PANEL
# ===============================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(
        "🛠 Админ-панель",
        reply_markup=admin_keyboard(),
    )

# ===============================
# DEBT BY PLOT
# ===============================

async def ask_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите номер участка:")

async def show_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = update.message.text.strip()
    user_row = get_user_by_plot(plot)

    if not user_row:
        await update.message.reply_text("❌ Участок не найден")
        return

    debt = user_row.get("Долг", "0")
    await update.message.reply_text(
        f"🏠 Участок {plot}\n💰 Долг: {debt}"
    )

# ===============================
# NOTIFICATION (BY PLOT)
# ===============================

async def notify_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📣 Уведомление\n\n"
        "Введите номер участка, которому нужно отправить уведомление:"
    )

async def notify_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = update.message.text.strip()
    admin = update.effective_user

    user_row = get_user_by_plot(plot)
    if not user_row:
        await update.message.reply_text("❌ Участок не найден")
        return

    tg_id = user_row.get("Telegram_ID")
    if not tg_id:
        await update.message.reply_text("❌ У участка нет Telegram ID")
        return

    try:
        await context.bot.send_message(
            chat_id=int(tg_id),
            text=(
                f"📢 Уведомление по участку {plot}\n\n"
                "Пожалуйста, проверьте информацию в ТСН."
            ),
        )
        log_event(
            "notify",
            uid=tg_id,
            plot=plot,
            event="Уведомление отправлено",
            details=f"Админ {admin.id}",
        )
        await update.message.reply_text("✅ Уведомление отправлено")

    except Forbidden:
        log_event(
            "blocked",
            uid=tg_id,
            plot=plot,
            event="Пользователь заблокировал бота",
        )
        await update.message.reply_text("⛔ Пользователь заблокировал бота")

# ===============================
# STATISTICS
# ===============================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = sheet_users.get_all_records()
    logs = sheet_logs.get_all_records()

    total_users = len(users)
    blocked = len([l for l in logs if l["Тип"] == "blocked"])
    notifications = len([l for l in logs if l["Тип"] == "notify"])

    await update.message.reply_text(
        "📊 Статистика бота\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"⛔ Заблокировали: {blocked}\n"
        f"📨 Уведомлений отправлено: {notifications}"
    )

# ===============================
# AUTO NOTIFICATIONS (18:00 MSK)
# ===============================

async def auto_notifications():
    today = datetime.now(TIMEZONE).day
    rows = sheet_users.get_all_records()

    for r in rows:
        pay_day = r.get("День_оплаты")
        tg_id = r.get("Telegram_ID")
        plot = r.get("Участок")

        if not pay_day or not tg_id:
            continue

        try:
            pay_day = int(pay_day)
        except:
            continue

        if today >= pay_day - 5 and today <= pay_day:
            try:
                await application.bot.send_message(
                    chat_id=int(tg_id),
                    text=(
                        f"⏰ Напоминание по оплате\n\n"
                        f"Участок {plot}\n"
                        "Срок оплаты приближается."
                    ),
                )
                log_event(
                    "auto_notify",
                    uid=tg_id,
                    plot=plot,
                    event="Авто-уведомление",
                )
            except Forbidden:
                log_event(
                    "blocked",
                    uid=tg_id,
                    plot=plot,
                    event="Авто: пользователь заблокировал бота",
                )

# ===============================
# APP INIT
# ===============================

application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^🚀 Начать$"), start))

application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^💳 Реквизиты$"), requisites))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📎 Загрузить чек$"), ask_check))
application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, receive_check))

application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^🛠 Админ-панель$"), admin_panel))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^🔍 Долг по участку$"), ask_debt))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📣 Уведомление$"), notify_start))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📊 Статистика$"), stats))

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, show_debt))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, notify_send))

# ===============================
# SCHEDULER
# ===============================

scheduler = AsyncIOScheduler(timezone=TIMEZONE)
scheduler.add_job(auto_notifications, "cron", hour=18, minute=0)
scheduler.start()

# ===============================
# RUN
# ===============================

if __name__ == "__main__":
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        webhook_url=os.environ["WEBHOOK_URL"],
    )
