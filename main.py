import os
import json
import re
import io
import logging
import asyncio
from datetime import datetime, timedelta

import pytz
import gspread
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


# ================= LOG =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
PORT = int(os.getenv("PORT", 10000))


# ================= GOOGLE =================
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(creds_info, scopes=scopes)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)
sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_stats = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")


# ================= CONSTANTS =================
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

BATTLE_TEXT = (
    "⚠️ Уважаемый собственник!\n\n"
    "Зафиксирована задолженность по взносам ТСН.\n"
    "Просим срочно погасить долг.\n\n"
    "Если оплата произведена — загрузите чек в бота."
)


# ================= MENUS =================
USER_MENU = ReplyKeyboardMarkup(
    [
        ["🚀 Начать"],
        ["📎 Загрузить чек", "💳 Реквизиты"]
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["🚀 Начать"],
        ["🛠 Админ-панель"],
        ["📎 Загрузить чек", "💳 Реквизиты"]
    ],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["🔍 Долг по участку"],
        ["📣 Боевое уведомление"],
        ["📊 Статистика"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)


# ================= HELPERS =================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def log_event(event_type, uid="", username="", house="", details="", error=""):
    sheet_stats.append_row([
        datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        event_type,
        uid,
        username,
        house,
        details,
        error
    ])


def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None


def is_registered(uid):
    return find_user_row(uid) is not None


def valid_fio(t): return len(t.split()) >= 2
def valid_phone(t): return bool(re.fullmatch(r"\+7\d{10}", t))
def valid_house(t): return t.isdigit()


def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f["id"]


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    username = update.effective_user.username or ""
    context.user_data.clear()

    log_event("start", uid, username)

    if is_registered(uid):
        row = find_user_row(uid)
        fio = sheet_users.cell(row, 2).value
        await update.message.reply_text(
            f"👋 С возвращением, {fio}",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\nВведите ФИО:",
            reply_markup=ReplyKeyboardMarkup([["🚀 Начать"]], resize_keyboard=True)
        )


# ================= TEXT =================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    username = update.effective_user.username or ""

    logger.info(f"{uid}: {text}")

    if text == "🚀 Начать":
        await start(update, context)
        return

    if not is_registered(uid):
        await update.message.reply_text("❗ Сначала завершите регистрацию")
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("🛠 Админ-панель", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text("Главное меню", reply_markup=ADMIN_MENU)
        return

    # ----- РЕКВИЗИТЫ + QR -----
    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        qr_url = r[5]

        await update.message.reply_text(
            f"💳 Реквизиты:\n\n"
            f"Банк: {r[0]}\n"
            f"БИК: {r[1]}\n"
            f"Счёт: {r[2]}\n"
            f"Получатель: {r[3]}\n"
            f"ИНН: {r[4]}"
        )

        await context.bot.send_photo(
            chat_id=uid,
            photo=qr_url,
            caption="📱 QR для оплаты"
        )
        return

    # ----- ЗАГРУЗКА ЧЕКА -----
    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text(
            "📎 Отправьте фото или PDF чека.\n"
            "После загрузки чек будет отправлен админу на подтверждение."
        )
        return

    # ----- АДМИН: БОЕВОЕ -----
    if text == "📣 Боевое уведомление" and is_admin(uid):
        context.user_data["wait_battle"] = True
        await update.message.reply_text(
            "Введите:\n"
            "• номер участка\n"
            "• ALL — всем\n"
            "• TEST — себе"
        )
        return

    if context.user_data.get("wait_battle") and is_admin(uid):
        context.user_data.pop("wait_battle")
        sent = 0

        if text == "TEST":
            await context.bot.send_message(uid, BATTLE_TEXT)
            sent = 1
        else:
            for r in sheet_users.get_all_records():
                if text == "ALL" or str(r["Участок"]) == text:
                    try:
                        await context.bot.send_message(int(r["Telegram_ID"]), BATTLE_TEXT)
                        sent += 1
                    except:
                        log_event("send_error", r["Telegram_ID"], "", r["Участок"], "боевое")

        log_event("battle", uid, username, text, f"sent={sent}")
        await update.message.reply_text(f"✅ Отправлено: {sent}", reply_markup=ADMIN_PANEL)
        return

    # ----- АДМИН: СТАТИСТИКА -----
    if text == "📊 Статистика" and is_admin(uid):
        users = len(sheet_users.get_all_records())
        blocked = len([r for r in sheet_stats.get_all_records() if r["Тип"] == "blocked"])
        sent = len([r for r in sheet_stats.get_all_records() if r["Тип"] == "battle"])

        await update.message.reply_text(
            f"📊 Статистика бота\n\n"
            f"👥 Пользователей: {users}\n"
            f"⛔ Заблокировали: {blocked}\n"
            f"📨 Уведомлений: {sent}",
            reply_markup=ADMIN_PANEL
        )
        return

    # ----- РЕГИСТРАЦИЯ -----
    step = context.user_data.get("step")

    if step == "fio":
        if not valid_fio(text):
            await update.message.reply_text("Введите ФИО (2+ слова)")
            return
        sheet_users.append_row(["", text, uid, username])
        context.user_data["step"] = "phone"
        await update.message.reply_text("📞 Телефон +7XXXXXXXXXX")
        return

    if step == "phone":
        if not valid_phone(text):
            await update.message.reply_text("Формат +7XXXXXXXXXX")
            return
        row = find_user_row(uid)
        sheet_users.update_cell(row, 5, text)
        context.user_data["step"] = "house"
        await update.message.reply_text("🏠 Номер участка")
        return

    if step == "house":
        if not valid_house(text):
            await update.message.reply_text("Только цифры")
            return
        row = find_user_row(uid)
        sheet_users.update_cell(row, 1, text)
        context.user_data.clear()
        log_event("register", uid, username, text)
        await update.message.reply_text(
            "✅ Регистрация завершена",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )


# ================= FILE =================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    file_id = upload_to_drive(data, "check", file.mime_type)

    uid = update.effective_user.id
    row = find_user_row(uid)

    fio = sheet_users.cell(row, 2).value
    house = sheet_users.cell(row, 1).value
    phone = sheet_users.cell(row, 5).value

    sheet_checks.append_row([
        uid,
        msg.from_user.username,
        fio,
        house,
        phone,
        f"https://drive.google.com/file/d/{file_id}",
        "",
        datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "",
        "",
        "",
        file.file_unique_id,
        "ожидает"
    ])

    log_event("check_uploaded", uid, msg.from_user.username, house)

    context.user_data.pop("wait_check")
    await msg.reply_text("✅ Чек загружен и отправлен на проверку")


# ================= AUTO REMINDERS =================
async def auto_reminders(app: Application):
    for r in sheet_users.get_all_records():
        try:
            if r["Статус"] == "долг":
                await app.bot.send_message(int(r["Telegram_ID"]), BATTLE_TEXT)
                log_event("auto_reminder", r["Telegram_ID"], r["username"], r["Участок"])
        except:
            log_event("blocked", r["Telegram_ID"], r["username"], r["Участок"])


# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
    scheduler.add_job(
        auto_reminders,
        trigger="cron",
        hour=18,
        minute=0,
        args=[app]
    )
    scheduler.start()

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )


if __name__ == "__main__":
    main()
