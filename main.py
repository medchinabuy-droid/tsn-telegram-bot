import os
import json
import logging
import io
import re
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

# =====================================================
# ⚙️ НАСТРОЙКИ
# =====================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Moscow")

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# =====================================================
# 🔐 GOOGLE
# =====================================================
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

# =====================================================
# 📋 МЕНЮ
# =====================================================
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"],
     ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"],
     ["🛠 Админ-панель"],
     ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["🔍 Долг по участку"],
     ["📊 Статистика"],
     ["📣 Уведомление"],
     ["⬅️ Назад"]],
    resize_keyboard=True
)

# =====================================================
# 🧰 ВСПОМОГАТЕЛЬНЫЕ
# =====================================================
def is_admin(uid):
    return uid in ADMIN_IDS

def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f["id"]

def download_from_drive(file_id):
    request = drive.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh

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

# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    await update.message.reply_text(
        "👋 Добро пожаловать в бот ТСН «Искона-Парк»\n\n"
        "Выберите действие в меню ниже.",
        reply_markup=menu
    )
import os
import json
import logging
import io
import re
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

# =====================================================
# ⚙️ НАСТРОЙКИ
# =====================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Moscow")

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# =====================================================
# 🔐 GOOGLE
# =====================================================
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

# =====================================================
# 📋 МЕНЮ
# =====================================================
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"],
     ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"],
     ["🛠 Админ-панель"],
     ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["🔍 Долг по участку"],
     ["📊 Статистика"],
     ["📣 Уведомление"],
     ["⬅️ Назад"]],
    resize_keyboard=True
)

# =====================================================
# 🧰 ВСПОМОГАТЕЛЬНЫЕ
# =====================================================
def is_admin(uid):
    return uid in ADMIN_IDS

def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f["id"]

def download_from_drive(file_id):
    request = drive.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh

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

# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    await update.message.reply_text(
        "👋 Добро пожаловать в бот ТСН «Искона-Парк»\n\n"
        "Выберите действие в меню ниже.",
        reply_markup=menu
    )
# =====================================================
# 📣 АВТО-УВЕДОМЛЕНИЯ (РАЗ В МЕСЯЦ, ЗА 5 ДНЕЙ)
# =====================================================
async def monthly_notify(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()

    for r in sheet_users.get_all_records():
        try:
            pay_day = int(r.get("День_оплаты") or 0)
            if pay_day <= 0:
                continue

            debt = float(str(r.get("Сумма") or "0").replace(",", "."))
            if debt <= 0:
                continue

            start_day = max(1, pay_day - 5)
            if not (start_day <= today.day <= pay_day):
                continue

            uid = int(r["Telegram_ID"])
            fio = r.get("ФИО")

            await context.bot.send_message(
                uid,
                f"Уважаемый(ая) {fio}!\n\n"
                f"Просим Вас оплатить паевые сборы ТСН «Искона-Парк».\n"
                f"У Вас имеется задолженность.\n\n"
                f"После оплаты, пожалуйста, загрузите чек в бота.\n\n"
                f"С уважением,\nПравление ТСН"
            )

            log_event("auto_notify", uid, r.get("username"), r.get("Участок"))

        except Exception as e:
            log_event(
                "blocked",
                r.get("Telegram_ID"),
                r.get("username"),
                r.get("Участок"),
                error=str(e)
            )

# =====================================================
# 📝 ТЕКСТОВЫЕ КОМАНДЫ
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    # START
    if text == "🚀 Начать":
        await start(update, context)
        return

    # ---------------- ADMIN PANEL ----------------
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text(
            "🛠 Админ-панель\n\nВыберите действие:",
            reply_markup=ADMIN_PANEL
        )
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "⬇️ Возврат в меню",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return

    # ---------------- ДОЛГ ПО УЧАСТКУ ----------------
    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["wait_house"] = True
        await update.message.reply_text(
            "🏠 Введите номер участка.\n"
            "После этого бот покажет данные по задолженности."
        )
        return

    if context.user_data.get("wait_house") and is_admin(uid):
        context.user_data.clear()
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                await update.message.reply_text(
                    f"🏠 Участок: {text}\n"
                    f"ФИО: {r.get('ФИО')}\n"
                    f"Телефон: {r.get('Телефон')}\n"
                    f"Сумма долга: {r.get('Сумма')}\n"
                    f"Статус: {r.get('Статус')}\n"
                    f"Username: @{r.get('username')}",
                    reply_markup=ADMIN_PANEL
                )
                return

        await update.message.reply_text(
            "❌ Участок не найден.",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---------------- СТАТИСТИКА ----------------
    if text == "📊 Статистика" and is_admin(uid):
        users = sheet_users.get_all_records()
        checks = sheet_checks.get_all_records()

        total_users = len(users)
        total_checks = len(checks)
        total_debt = sum(
            float(str(u.get("Сумма") or "0").replace(",", "."))
            for u in users
        )

        blocked = [
            u.get("username")
            for u in users
            if str(u.get("Статус")).lower() == "blocked"
        ]

        await update.message.reply_text(
            "📊 Статистика бота\n\n"
            f"👥 Пользователей: {total_users}\n"
            f"📎 Загружено чеков: {total_checks}\n"
            f"💰 Общая задолженность: {total_debt}\n"
            f"⛔ Заблокировали бота: {len(blocked)}\n"
            f"📵 Список: {', '.join(blocked) if blocked else '—'}",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---------------- ПРИНУДИТЕЛЬНОЕ УВЕДОМЛЕНИЕ ----------------
    if text == "📣 Уведомление" and is_admin(uid):
        context.user_data["notify_house"] = True
        await update.message.reply_text(
            "📣 Введите номер участка для принудительного уведомления."
        )
        return

    if context.user_data.get("notify_house") and is_admin(uid):
        context.user_data.clear()
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                uid_target = int(r["Telegram_ID"])
                fio = r.get("ФИО")

                await context.bot.send_message(
                    uid_target,
                    f"Уважаемый(ая) {fio}!\n\n"
                    f"Просим Вас оплатить поселковые сборы ТСН «Искона-Парк».\n"
                    f"У Вас имеется задолженность.\n\n"
                    f"С уважением,\nПравление ТСН"
                )

                log_event("manual_notify", uid_target, r.get("username"), text)

                await update.message.reply_text(
                    "✅ Уведомление отправлено.",
                    reply_markup=ADMIN_PANEL
                )
                return

        await update.message.reply_text(
            "❌ Участок не найден.",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---------------- РЕКВИЗИТЫ + QR ----------------
    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)

        await update.message.reply_text(
            f"💳 Реквизиты для оплаты\n\n"
            f"Банк: {r[0]}\n"
            f"БИК: {r[1]}\n"
            f"Счёт: {r[2]}\n"
            f"Получатель: {r[3]}\n"
            f"ИНН: {r[4]}"
        )

        if r[5]:
            try:
                qr_bytes = download_from_drive(r[5])
                await update.message.reply_photo(
                    photo=qr_bytes,
                    caption="📷 QR-код для оплаты"
                )
            except Exception as e:
                logger.error(e)

        return

    # ---------------- ЗАГРУЗКА ЧЕКА ----------------
    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text(
            "📎 Пожалуйста, отправьте фото или PDF чека.\n"
            "После загрузки бот сообщит результат."
        )
        return

# =====================================================
# 📎 ФАЙЛЫ (ЧЕКИ)
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    if is_duplicate(file.file_unique_id):
        await msg.reply_text("⚠️ Такой чек уже был загружен ранее.")
        return

    await msg.reply_text("⏳ Идёт загрузка чека...")

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    file_id = upload_to_drive(
        data,
        f"check_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        "",
        "",
        "",
        file_id,
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
    await msg.reply_text("✅ Чек успешно сохранён. Спасибо!")

# =====================================================
# ▶️ MAIN
# =====================================================
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
