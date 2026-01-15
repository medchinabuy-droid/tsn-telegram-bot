# ================== IMPORTS ==================
import os
import json
import logging
import re
import io
from datetime import datetime

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
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from apscheduler.schedulers.background import BackgroundScheduler

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# ================== GOOGLE ==================
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

# ================== CONSTANTS ==================
BATTLE_TEXT = (
    "⚠️ Уважаемый собственник!\n\n"
    "У вас зафиксирована задолженность по взносам.\n"
    "Просим срочно погасить долг.\n\n"
    "Если оплата уже произведена — загрузите чек в бота."
)

# ================== MENUS ==================
USER_MENU = ReplyKeyboardMarkup(
    [
        ["🚀 Начать"],
        ["📎 Загрузить чек", "💳 Реквизиты"],
    ],
    resize_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["🚀 Начать"],
        ["🛠 Админ-панель"],
        ["📎 Загрузить чек", "💳 Реквизиты"],
    ],
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

# ================== HELPERS ==================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def log_stat(event, uid="", username="", house="", details="", error=""):
    sheet_stats.append_row(
        [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            event,
            uid,
            username,
            house,
            details,
            error,
        ]
    )


def find_user_row(uid):
    ids = sheet_users.col_values(3)  # Telegram_ID
    for i, v in enumerate(ids, start=2):
        if v == str(uid):
            return i
    return None


def is_registered(uid):
    row = find_user_row(uid)
    if not row:
        return False
    phone = sheet_users.cell(row, 5).value
    house = sheet_users.cell(row, 1).value
    return bool(phone and house)


def valid_fio(text):
    return len(text.split()) >= 2


def valid_phone(text):
    return bool(re.fullmatch(r"\+7\d{10}", text))


def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(12)


def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = (
        drive.files()
        .create(
            body={"name": name, "parents": [DRIVE_FOLDER_ID]},
            media_body=media,
            fields="id",
        )
        .execute()
    )
    return f["id"]


# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    username = update.effective_user.username or ""

    context.user_data.clear()
    log_stat("start", uid, username)

    row = find_user_row(uid)
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    if row:
        fio = sheet_users.cell(row, 2).value
        await update.message.reply_text(
            f"👋 С возвращением, {fio}", reply_markup=menu
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\nВведите ФИО:",
            reply_markup=ReplyKeyboardMarkup([["🚀 Начать"]], resize_keyboard=True),
        )


# ================== TEXT HANDLER ==================
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

    # ---------- ADMIN ----------
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "Главное меню", reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return

    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["wait_debt"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("wait_debt"):
        context.user_data.pop("wait_debt")
        for r in sheet_users.get_all_records():
            if str(r["Участок"]) == text:
                await update.message.reply_text(
                    f"🏠 Участок {text}\n"
                    f"ФИО: {r['ФИО']}\n"
                    f"Сумма: {r['Сумма']}\n"
                    f"Статус: {r['Статус']}",
                    reply_markup=ADMIN_PANEL,
                )
                return
        await update.message.reply_text("❌ Не найдено", reply_markup=ADMIN_PANEL)
        return

    if text == "📣 Боевое уведомление" and is_admin(uid):
        context.user_data["wait_battle"] = True
        await update.message.reply_text("Введите участок, ALL или SELF:")
        return

    if context.user_data.get("wait_battle"):
        context.user_data.pop("wait_battle")
        count = 0
        for r in sheet_users.get_all_records():
            if text == "ALL" or str(r["Участок"]) == text or text == "SELF":
                chat_id = uid if text == "SELF" else int(r["Telegram_ID"])
                try:
                    await context.bot.send_message(chat_id, BATTLE_TEXT)
                    count += 1
                except:
                    pass
        log_stat("battle", uid, username, text, f"sent {count}")
        await update.message.reply_text(
            f"✅ Отправлено: {count}", reply_markup=ADMIN_PANEL
        )
        return

    if text == "📊 Статистика" and is_admin(uid):
        users = len(sheet_users.get_all_records())
        events = len(sheet_stats.get_all_records())
        await update.message.reply_text(
            f"📊 Статистика бота\n\n"
            f"👥 Пользователей: {users}\n"
            f"📨 Событий: {events}",
            reply_markup=ADMIN_PANEL,
        )
        return

    # ---------- REQS ----------
    if text == "💳 Реквизиты":
        row = sheet_reqs.row_values(2)
        qr_file_id = row[5]

        await update.message.reply_text(
            f"💳 Реквизиты\n\n"
            f"Банк: {row[0]}\n"
            f"БИК: {row[1]}\n"
            f"Счёт: {row[2]}\n"
            f"Получатель: {row[3]}\n"
            f"ИНН: {row[4]}"
        )

        if qr_file_id:
            await context.bot.send_photo(
                chat_id=uid,
                photo=qr_file_id,
                caption="📲 QR для оплаты",
            )
        return

    # ---------- CHECK ----------
    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text("Отправьте фото или PDF чека")
        return

    # ---------- REG ----------
    if step == "fio":
        if not valid_fio(text):
            await update.message.reply_text("Введите ФИО полностью")
            return
        sheet_users.append_row(["", text, str(uid), username])
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
        log_stat("registration", uid, username, text)
        await update.message.reply_text(
            "✅ Регистрация завершена",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU,
        )
        return


# ================== FILE HANDLER ==================
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

    sheet_checks.append_row(
        [
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
            file.file_unique_id,
            "new",
        ]
    )

    log_stat("check_uploaded", uid)
    context.user_data.pop("wait_check")

    await update.message.reply_text("✅ Чек принят и отправлен на проверку")


# ================== MAIN ==================
def main():
    scheduler = BackgroundScheduler()
    scheduler.start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com",
    )


if __name__ == "__main__":
    main()
