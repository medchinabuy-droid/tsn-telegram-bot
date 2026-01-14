import os
import json
import re
import logging
import io
from datetime import datetime, timedelta, time

import pytz

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

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# ---------------- GOOGLE ----------------
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_stats = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# ---------------- CONSTANTS ----------------
BATTLE_TEXT = (
    "⚠️ Уважаемый собственник!\n\n"
    "Зафиксирована задолженность по взносам ТСН.\n"
    "Просим срочно погасить долг.\n\n"
    "Если оплата произведена — загрузите чек в бота."
)

CHECK_LIMIT_HOURS = 24
NOTIFY_PAUSE_DAYS = 30

# ---------------- MENUS ----------------
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
        ["📊 Финансовый отчёт"],
        ["⬅️ Назад"],
    ],
    resize_keyboard=True,
)

# ---------------- HELPERS ----------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def log_stat(event, uid="", username="", house="", comment=""):
    sheet_stats.append_row(
        [
            datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            event,
            uid,
            username,
            house,
            comment,
        ]
    )


def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None


def valid_fio(t):
    return len(t.split()) >= 2


def valid_phone(t):
    return bool(re.fullmatch(r"\+7\d{10}", t))


def valid_house(t):
    return t.isdigit()


def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(8)


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
    return f"https://drive.google.com/file/d/{f['id']}"


def can_upload_check(uid):
    for r in sheet_checks.get_all_records():
        if str(r.get("UID")) == str(uid):
            dt = datetime.strptime(r.get("Дата"), "%Y-%m-%d %H:%M:%S")
            if datetime.now() - dt < timedelta(hours=CHECK_LIMIT_HOURS):
                return False
    return True


# ---------------- START ----------------
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
            f"👋 С возвращением, {fio}\n\nИспользуйте меню ⬇️",
            reply_markup=menu,
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать в ТСН!\n\nВведите ФИО:",
            reply_markup=menu,
        )
import os
import json
import re
import logging
import io
from datetime import datetime, timedelta, time

import pytz

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

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# ---------------- GOOGLE ----------------
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_stats = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# ---------------- CONSTANTS ----------------
BATTLE_TEXT = (
    "⚠️ Уважаемый собственник!\n\n"
    "Зафиксирована задолженность по взносам ТСН.\n"
    "Просим срочно погасить долг.\n\n"
    "Если оплата произведена — загрузите чек в бота."
)

CHECK_LIMIT_HOURS = 24
NOTIFY_PAUSE_DAYS = 30

# ---------------- MENUS ----------------
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
        ["📊 Финансовый отчёт"],
        ["⬅️ Назад"],
    ],
    resize_keyboard=True,
)

# ---------------- HELPERS ----------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def log_stat(event, uid="", username="", house="", comment=""):
    sheet_stats.append_row(
        [
            datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            event,
            uid,
            username,
            house,
            comment,
        ]
    )


def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None


def valid_fio(t):
    return len(t.split()) >= 2


def valid_phone(t):
    return bool(re.fullmatch(r"\+7\d{10}", t))


def valid_house(t):
    return t.isdigit()


def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(8)


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
    return f"https://drive.google.com/file/d/{f['id']}"


def can_upload_check(uid):
    for r in sheet_checks.get_all_records():
        if str(r.get("UID")) == str(uid):
            dt = datetime.strptime(r.get("Дата"), "%Y-%m-%d %H:%M:%S")
            if datetime.now() - dt < timedelta(hours=CHECK_LIMIT_HOURS):
                return False
    return True


# ---------------- START ----------------
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
            f"👋 С возвращением, {fio}\n\nИспользуйте меню ⬇️",
            reply_markup=menu,
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать в ТСН!\n\nВведите ФИО:",
            reply_markup=menu,
        )
# ---------------- TEXT HANDLER ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    username = update.effective_user.username or ""

    logger.info(f"{uid}: {text}")

    if text == "🚀 Начать":
        await start(update, context)
        return

    # -------- ADMIN PANEL --------
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("🛠 Админ-панель", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "Главное меню",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU,
        )
        return

    # -------- DEBT BY HOUSE --------
    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["wait_house"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("wait_house") and is_admin(uid):
        context.user_data.pop("wait_house")
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                await update.message.reply_text(
                    f"🏠 Участок {text}\n"
                    f"ФИО: {r.get('ФИО')}\n"
                    f"Телефон: {r.get('Телефон')}\n"
                    f"Долг: {r.get('Долг')}\n"
                    f"Статус: {r.get('Статус')}",
                    reply_markup=ADMIN_PANEL,
                )
                log_stat("долг_просмотр", uid, username, text)
                return
        await update.message.reply_text("❌ Участок не найден", reply_markup=ADMIN_PANEL)
        return

    # -------- BATTLE NOTIFY --------
    if text == "📣 Боевое уведомление" and is_admin(uid):
        context.user_data["wait_battle"] = True
        await update.message.reply_text(
            "Введите номер участка, ALL или ME (отправить себе):"
        )
        return

    if context.user_data.get("wait_battle") and is_admin(uid):
        context.user_data.pop("wait_battle")
        sent = 0

        if text == "ME":
            await context.bot.send_message(chat_id=uid, text=BATTLE_TEXT)
            sent = 1
        else:
            for r in sheet_users.get_all_records():
                if text == "ALL" or str(r.get("Участок")) == text:
                    try:
                        await context.bot.send_message(
                            chat_id=int(r.get("TelegramID")),
                            text=BATTLE_TEXT,
                        )
                        sent += 1
                    except:
                        pass

        log_stat("боевое_уведомление", uid, username, text, f"отправлено {sent}")
        await update.message.reply_text(
            f"✅ Отправлено уведомлений: {sent}",
            reply_markup=ADMIN_PANEL,
        )
        return

    # -------- STATS --------
    if text == "📊 Статистика" and is_admin(uid):
        rows = sheet_stats.get_all_records()
        await update.message.reply_text(
            f"📊 Всего событий: {len(rows)}",
            reply_markup=ADMIN_PANEL,
        )
        return

    # -------- FIN REPORT --------
    if text == "📊 Финансовый отчёт" and is_admin(uid):
        total = 0
        for r in sheet_checks.get_all_records():
            if r.get("Статус") == "ПОДТВЕРЖДЁН":
                try:
                    total += float(r.get("Сумма", 0))
                except:
                    pass
        await update.message.reply_text(
            f"💰 Подтверждено оплат за период: {total}",
            reply_markup=ADMIN_PANEL,
        )
        return

    # -------- REQUISITES --------
    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"🏦 Банк: {r[0]}\n"
            f"БИК: {r[1]}\n"
            f"Счёт: {r[2]}\n"
            f"Получатель: {r[3]}\n"
            f"ИНН: {r[4]}\n\n"
            f"QR:\n{r[5]}",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU,
        )
        return

    # -------- UPLOAD CHECK --------
    if text == "📎 Загрузить чек":
        if not can_upload_check(uid):
            await update.message.reply_text("⏳ Чек уже загружен недавно. Подождите.")
            return
        context.user_data["wait_check"] = True
        await update.message.reply_text("📎 Отправьте фото или PDF чека")
        return

    # -------- REGISTRATION --------
    step = context.user_data.get("step")

    if step == "fio":
        if not valid_fio(text):
            await update.message.reply_text("Введите ФИО (минимум 2 слова)")
            return
        sheet_users.append_row(["", text, uid])
        context.user_data["step"] = "phone"
        await update.message.reply_text("Телефон +7XXXXXXXXXX")
        return

    if step == "phone":
        if not valid_phone(text):
            await update.message.reply_text("Формат +7XXXXXXXXXX")
            return
        row = find_user_row(uid)
        sheet_users.update_cell(row, 4, text)
        context.user_data["step"] = "house"
        await update.message.reply_text("Номер участка")
        return

    if step == "house":
        row = find_user_row(uid)
        sheet_users.update_cell(row, 1, text)
        context.user_data.clear()
        log_stat("регистрация", uid, username, text)
        await update.message.reply_text(
            "✅ Регистрация завершена",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU,
        )
        return

    await update.message.reply_text(
        "ℹ️ Используйте кнопки меню ⬇️",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU,
    )
# ---------------- FILE HANDLER ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    file = update.message.photo[-1] if update.message.photo else update.message.document

    if is_duplicate(file.file_unique_id):
        await update.message.reply_text("❌ Этот чек уже был загружен")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    link = upload_to_drive(
        data,
        f"check_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type,
    )

    uid = update.effective_user.id
    row = find_user_row(uid)

    fio = sheet_users.cell(row, 2).value
    house = sheet_users.cell(row, 1).value
    phone = sheet_users.cell(row, 4).value

    sheet_checks.append_row(
        [
            uid,
            fio,
            house,
            phone,
            link,
            "ОЖИДАЕТ",
            "",
            file.file_unique_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ]
    )

    log_stat("чек_загружен", uid, update.effective_user.username, house)

    context.user_data.pop("wait_check")
    await update.message.reply_text(
        "✅ Чек загружен и отправлен на проверку администратору",
        reply_markup=USER_MENU,
    )


# ---------------- AUTO NOTIFY 18:00 ----------------
async def auto_notify(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(MOSCOW_TZ)
    if now.time().hour != 18:
        return

    for r in sheet_users.get_all_records():
        try:
            await context.bot.send_message(
                chat_id=int(r.get("TelegramID")), text=BATTLE_TEXT
            )
        except:
            pass


# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.job_queue.run_repeating(auto_notify, interval=3600, first=10)

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=os.getenv("WEBHOOK_URL"),
    )


if __name__ == "__main__":
    main()
