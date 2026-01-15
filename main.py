# =========================================================
# 1. IMPORTS
# =========================================================
import os
import json
import logging
import re
import io
from datetime import datetime, timedelta

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# =========================================================
# 2. LOGGING
# =========================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================================================
# 3. ENV
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# =========================================================
# 4. GOOGLE
# =========================================================
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_info(creds_info, scopes=scopes)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)
sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_stats = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# =========================================================
# 5. CONSTANTS
# =========================================================
BATTLE_TEXT = (
    "⚠️ Уважаемый собственник!\n\n"
    "У вас имеется задолженность по взносам ТСН.\n"
    "Просим срочно погасить долг.\n\n"
    "Если оплата произведена — загрузите чек в бота."
)

# =========================================================
# 6. MENUS
# =========================================================
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True,
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["🔍 Долг по участку"],
        ["📣 Боевое уведомление"],
        ["📊 Статистика"],
        ["⬅️ Назад"],
    ],
    resize_keyboard=True,
)

# =========================================================
# 7. HELPERS
# =========================================================
def is_admin(uid):
    return uid in ADMIN_IDS


def log_event(event_type, uid="", username="", house="", details="", error=""):
    sheet_stats.append_row(
        [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            event_type,
            uid,
            username,
            house,
            details,
            error,
        ]
    )


def find_user_row(uid):
    col = sheet_users.col_values(3)
    for i, v in enumerate(col, start=2):
        if v == str(uid):
            return i
    return None


def is_registered(uid):
    row = find_user_row(uid)
    if not row:
        return False
    return bool(sheet_users.cell(row, 1).value and sheet_users.cell(row, 5).value)


def valid_fio(t): return len(t.split()) >= 2
def valid_phone(t): return bool(re.fullmatch(r"\+7\d{10}", t))


def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)


def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id",
    ).execute()
    return f["id"]

# =========================================================
# 8. START
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    username = update.effective_user.username or ""
    context.user_data.clear()

    log_event("start", uid, username)

    row = find_user_row(uid)
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    if row:
        fio = sheet_users.cell(row, 2).value
        await update.message.reply_text(
            f"👋 С возвращением, {fio}",
            reply_markup=menu,
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\nВведите ФИО:",
            reply_markup=ReplyKeyboardMarkup([["🚀 Начать"]], resize_keyboard=True),
        )

# =========================================================
# 9. TEXT HANDLER
# =========================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    username = update.effective_user.username or ""
    step = context.user_data.get("step")

    if text == "🚀 Начать":
        await start(update, context)
        return

    if not is_registered(uid) and not step:
        await update.message.reply_text("❗ Сначала завершите регистрацию")
        return

    # -------- REGISTRATION --------
    if step == "fio":
        if not valid_fio(text):
            await update.message.reply_text("Введите ФИО полностью")
            return
        sheet_users.append_row(["", text, uid, username])
        context.user_data["step"] = "phone"
        await update.message.reply_text("Введите телефон +7XXXXXXXXXX")
        return

    if step == "phone":
        if not valid_phone(text):
            await update.message.reply_text("Формат +7XXXXXXXXXX")
            return
        row = find_user_row(uid)
        sheet_users.update_cell(row, 5, text)
        context.user_data["step"] = "house"
        await update.message.reply_text("Введите номер участка")
        return

    if step == "house":
        row = find_user_row(uid)
        sheet_users.update_cell(row, 1, text)
        context.user_data.clear()
        log_event("registration", uid, username, text)
        await update.message.reply_text(
            "✅ Регистрация завершена",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU,
        )
        return

    # -------- ADMIN PANEL --------
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "Главное меню",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU,
        )
        return

    # -------- BATTLE --------
    if text == "📣 Боевое уведомление" and is_admin(uid):
        context.user_data["battle"] = True
        await update.message.reply_text("Введите участок / ALL / SELF")
        return

    if context.user_data.get("battle"):
        context.user_data.pop("battle")
        sent = 0
        blocked = []

        for r in sheet_users.get_all_records():
            if text in ("ALL", str(r["Участок"])) or text == "SELF":
                chat_id = uid if text == "SELF" else int(r["Telegram_ID"])
                try:
                    await context.bot.send_message(chat_id, BATTLE_TEXT)
                    sent += 1
                except Exception as e:
                    blocked.append(str(chat_id))
                    log_event("blocked", chat_id, "", "", "", str(e))

        log_event("battle_send", uid, username, text, f"sent={sent}")
        await update.message.reply_text(
            f"📣 Отправлено: {sent}\n⛔ Заблокировали: {', '.join(blocked)}",
            reply_markup=ADMIN_PANEL,
        )
        return

    # -------- STATS --------
    if text == "📊 Статистика" and is_admin(uid):
        users = len(sheet_users.get_all_records())
        checks = len(sheet_checks.get_all_records())
        battles = len([r for r in sheet_stats.get_all_records() if r["Тип"] == "battle_send"])
        blocked = [r["UID"] for r in sheet_stats.get_all_records() if r["Тип"] == "blocked"]

        await update.message.reply_text(
            "📊 Статистика бота\n\n"
            f"👥 Пользователей: {users}\n"
            f"📎 Чеков загружено: {checks}\n"
            f"📣 Уведомлений отправлено: {battles}\n"
            f"⛔ Заблокировали: {len(blocked)}\n"
            f"{', '.join(blocked)}",
            reply_markup=ADMIN_PANEL,
        )
        return

    # -------- REQUISITES --------
    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"💳 Реквизиты\n\n"
            f"Банк: {r[0]}\nБИК: {r[1]}\n"
            f"Счёт: {r[2]}\nПолучатель: {r[3]}\nИНН: {r[4]}"
        )
        if r[5]:
            await context.bot.send_photo(uid, r[5], caption="📲 QR для оплаты")
        return

    # -------- CHECK --------
    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text("Отправьте фото или PDF чека")
        return

# =========================================================
# 10. FILE HANDLER + INLINE CONFIRM
# =========================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    file = update.message.photo[-1] if update.message.photo else update.message.document

    if is_duplicate(file.file_unique_id):
        await update.message.reply_text("❌ Этот чек уже загружен")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()
    drive_id = upload_to_drive(data, "check", file.mime_type)

    uid = update.effective_user.id
    row = find_user_row(uid)

    sheet_checks.append_row([
        uid,
        update.effective_user.username,
        sheet_users.cell(row, 2).value,
        sheet_users.cell(row, 1).value,
        sheet_users.cell(row, 5).value,
        f"https://drive.google.com/file/d/{drive_id}",
        "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "",
        "",
        "",
        file.file_unique_id,
        "new",
    ])

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve:{uid}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{uid}")
        ]
    ])

    for admin in ADMIN_IDS:
        await context.bot.send_message(
            admin,
            f"📎 Новый чек от {sheet_users.cell(row,2).value}\n"
            f"https://drive.google.com/file/d/{drive_id}",
            reply_markup=keyboard,
        )

    context.user_data.pop("wait_check")
    await update.message.reply_text("✅ Чек отправлен на проверку")

# =========================================================
# 11. CALLBACK (APPROVE / REJECT)
# =========================================================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, uid = query.data.split(":")
    uid = int(uid)

    if action == "approve":
        row = find_user_row(uid)
        sheet_users.update_cell(row, 10, "Оплачен")
        sheet_users.update_cell(row, 8, 0)
        log_event("check_approved", uid)
        await query.edit_message_text("✅ Чек подтверждён, долг закрыт")

    if action == "reject":
        log_event("check_rejected", uid)
        await query.edit_message_text("❌ Чек отклонён")

# =========================================================
# 12. AUTO REMINDERS 18:00 MSK
# =========================================================
async def auto_reminders(app: Application):
    now = datetime.now(MOSCOW_TZ)
    for r in sheet_users.get_all_records():
        if r["Статус"] != "Оплачен":
            try:
                await app.bot.send_message(int(r["Telegram_ID"]), BATTLE_TEXT)
            except:
                pass

# =========================================================
# 13. MAIN
# =========================================================
def main():
    scheduler = BackgroundScheduler(timezone=MOSCOW_TZ)
    scheduler.add_job(
        lambda: auto_reminders(app),
        "cron",
        hour=18,
        minute=0,
    )
    scheduler.start()

    global app
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com",
    )

if __name__ == "__main__":
    main()
