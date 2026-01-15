import os
import json
import re
import io
import logging
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CallbackQueryHandler

)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# ---------------- LOG ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------- ENV (Render) ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")  # папка для чеков
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

GOOGLE_CREDS = os.getenv("GOOGLE_CREDENTIALS_JSON")
if not GOOGLE_CREDS:
    raise RuntimeError("GOOGLE_CREDENTIALS_JSON not set")

# ---------------- GOOGLE AUTH ----------------
creds_info = json.loads(GOOGLE_CREDS)
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

# ---------------- SHEETS (СТРОГО КАК У ТЕБЯ) ----------------
sh = gc.open_by_key(SPREADSHEET_ID)

SHEET_USERS = sh.worksheet("Лист 1")
SHEET_CHECKS = sh.worksheet("Лист 2")
SHEET_STATS = sh.worksheet("Лист 3")
SHEET_REQS = sh.worksheet("Реквизиты")

logger.info("Google Sheets connected")

# ---------------- CONSTANTS ----------------
MOSCOW_TZ_OFFSET = 3  # UTC+3

BATTLE_TEXT = (
    "⚠️ Уважаемый собственник!\n\n"
    "По вашему участку зафиксирована задолженность.\n"
    "Просим срочно погасить долг.\n\n"
    "Если оплата произведена — загрузите чек в бота."
)

# ---------------- HELPERS ----------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def now_str():
    return datetime.utcnow() + timedelta(hours=MOSCOW_TZ_OFFSET)


def log_stat(event_type, uid="", username="", house="", details="", error=""):
    """
    Лист 3:
    A Дата | B Тип | C UID | D Username | E Участок | F Событие | G Детали | H Ошибка
    """
    try:
        SHEET_STATS.append_row([
            now_str().strftime("%Y-%m-%d %H:%M:%S"),
            event_type,
            uid,
            username,
            house,
            event_type,
            details,
            error
        ])
    except Exception as e:
        logger.error(f"STAT LOG ERROR: {e}")


def find_user_row(uid: int):
    """Ищем пользователя по Telegram_ID (колонка C)"""
    col = SHEET_USERS.col_values(3)
    for i, v in enumerate(col, start=1):
        if v == str(uid):
            return i
    return None


def is_registered(uid: int) -> bool:
    return find_user_row(uid) is not None


def valid_fio(text): 
    return len(text.split()) >= 2


def valid_phone(text): 
    return bool(re.fullmatch(r"\+7\d{10}", text))


def valid_house(text): 
    return text.isdigit()


def is_duplicate_check(file_uid: str) -> bool:
    return file_uid in SHEET_CHECKS.col_values(13)


def upload_check_to_drive(data: bytes, name: str, mime: str) -> str:
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={
            "name": name,
            "parents": [DRIVE_FOLDER_ID]
        },
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{f['id']}"


def download_drive_file(file_url: str) -> bytes:
    """
    Скачивает файл из Google Drive по ссылке
    """
    file_id = file_url.split("/d/")[1].split("/")[0]
    request = drive.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read()
# ---------------- MENUS ----------------
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

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    username = user.username or ""

    context.user_data.clear()

    row = find_user_row(uid)
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    if row:
        fio = SHEET_USERS.cell(row, 2).value
        await update.message.reply_text(
            f"👋 С возвращением, {fio}",
            reply_markup=menu
        )
        log_stat("start_ok", uid, username)
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\n"
            "Для работы с ботом необходимо зарегистрироваться.\n"
            "Введите ФИО:",
            reply_markup=ReplyKeyboardMarkup([["🚀 Начать"]], resize_keyboard=True)
        )
        log_stat("start_new", uid, username)


# ---------------- ACCESS GUARD ----------------
async def require_registration(update: Update):
    await update.message.reply_text(
        "⛔ Для использования бота необходимо пройти регистрацию.\n"
        "Нажмите 🚀 Начать",
        reply_markup=ReplyKeyboardMarkup([["🚀 Начать"]], resize_keyboard=True)
    )


# ---------------- TEXT HANDLER ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    uid = user.id
    username = user.username or ""

    logger.info(f"{uid} | {text}")

    # -------- START --------
    if text == "🚀 Начать":
        await start(update, context)
        return

    # -------- BLOCK UNREGISTERED --------
    if not is_registered(uid):
        step = context.user_data.get("step")

        if step == "fio":
            if not valid_fio(text):
                await update.message.reply_text("Введите ФИО полностью")
                return
            SHEET_USERS.append_row([
                "",            # Участок
                text,          # ФИО
                uid,           # Telegram_ID
                username,      # username
                "",            # Телефон
                "", "", "", "", "новый", "user", "", ""
            ])
            context.user_data["step"] = "phone"
            await update.message.reply_text("Введите телефон в формате +7XXXXXXXXXX")
            return

        if step == "phone":
            if not valid_phone(text):
                await update.message.reply_text("Формат телефона: +7XXXXXXXXXX")
                return
            row = find_user_row(uid)
            SHEET_USERS.update_cell(row, 5, text)
            context.user_data["step"] = "house"
            await update.message.reply_text("Введите номер участка")
            return

        if step == "house":
            if not valid_house(text):
                await update.message.reply_text("Номер участка — только цифры")
                return
            row = find_user_row(uid)
            SHEET_USERS.update_cell(row, 1, text)
            SHEET_USERS.update_cell(row, 10, "активен")
            SHEET_USERS.update_cell(row, 11, "admin" if is_admin(uid) else "user")
            context.user_data.clear()

            log_stat("registration", uid, username, text)

            await update.message.reply_text(
                "✅ Регистрация завершена",
                reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
            )
            return

        await require_registration(update)
        return

    # -------- ADMIN PANEL --------
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("🛠 Админ-панель", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "Главное меню",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return
# --------- REQUISITES ---------
if text == "💳 Реквизиты":
    row = SHEET_REQS.row_values(2)

    bank, bik, account, receiver, inn, qr_link = row

    await update.message.reply_text(
        "💳 *Реквизиты для оплаты:*\n\n"
        f"🏦 Банк: {bank}\n"
        f"🔢 БИК: {bik}\n"
        f"💼 Счёт: {account}\n"
        f"👤 Получатель: {receiver}\n"
        f"🧾 ИНН: {inn}\n\n"
        "⬇️ Ниже QR-код для быстрой оплаты",
        parse_mode="Markdown"
    )

    # 🔹 ВАЖНО: отправляем QR КАК ФОТО
    await context.bot.send_photo(
        chat_id=uid,
        photo=qr_link,
        caption="📸 Отсканируйте QR в банковском приложении"
    )

    await update.message.reply_text(
        "✅ После оплаты нажмите «📎 Загрузить чек»",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

    log_stat("requisites_view", uid, username)
    return
# --------- REQUISITES ---------
if text == "💳 Реквизиты":
    row = SHEET_REQS.row_values(2)

    bank, bik, account, receiver, inn, qr_link = row

    await update.message.reply_text(
        "💳 *Реквизиты для оплаты:*\n\n"
        f"🏦 Банк: {bank}\n"
        f"🔢 БИК: {bik}\n"
        f"💼 Счёт: {account}\n"
        f"👤 Получатель: {receiver}\n"
        f"🧾 ИНН: {inn}\n\n"
        "⬇️ Ниже QR-код для быстрой оплаты",
        parse_mode="Markdown"
    )

    # 🔹 ВАЖНО: отправляем QR КАК ФОТО
    await context.bot.send_photo(
        chat_id=uid,
        photo=qr_link,
        caption="📸 Отсканируйте QR в банковском приложении"
    )

    await update.message.reply_text(
        "✅ После оплаты нажмите «📎 Загрузить чек»",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

    log_stat("requisites_view", uid, username)
    return
# --------- UPLOAD CHECK BUTTON ---------
if text == "📎 Загрузить чек":
    context.user_data["wait_check"] = True

    await update.message.reply_text(
        "📎 *Загрузка чека*\n\n"
        "1️⃣ Нажмите на 📎 (скрепку)\n"
        "2️⃣ Выберите *Фото* или *PDF*\n"
        "3️⃣ Отправьте файл в чат\n\n"
        "⚠️ Один чек = один платёж",
        parse_mode="Markdown"
    )
    return
# ---------------- FILE HANDLER ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    # 🔒 Защита от дублей
    if is_duplicate(file.file_unique_id):
        await msg.reply_text("❌ Этот чек уже был загружен ранее")
        log_stat("check_duplicate", msg.from_user.id)
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    # 📂 Загрузка в Google Drive
    link = upload_to_drive(
        data,
        f"check_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    uid = msg.from_user.id
    username = msg.from_user.username or ""
    row = find_user_row(uid)

    house = SHEET_USERS.cell(row, 1).value
    fio = SHEET_USERS.cell(row, 2).value
    phone = SHEET_USERS.cell(row, 5).value

    # 📝 Запись в Лист 2
    SHEET_CHECKS.append_row([
        uid, username, fio, house, phone,
        link, "", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "", "", "", "", file.file_unique_id, "ожидает"
    ])

    # ✅ АВТО-ЗАКРЫТИЕ ДОЛГА
    SHEET_USERS.update_cell(row, 10, "оплачено")
    SHEET_USERS.update_cell(row, 12, "")  # очистка даты напоминания

    context.user_data.pop("wait_check", None)

    await msg.reply_text(
        "✅ *Чек успешно загружен!*\n\n"
        "🔒 Долг автоматически закрыт\n"
        "🧾 Чек отправлен на проверку администратору",
        parse_mode="Markdown",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

    log_stat("check_uploaded", uid, username, house)
# ===== БЛОК 4.1.2 — Уведомление админа о чеке =====
async def notify_admin_about_check(context, check_row, house, fio):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"check_ok:{check_row}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"check_no:{check_row}")
        ]
    ])

    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"🔐 Новый чек\n🏠 Участок: {house}\n👤 {fio}",
            reply_markup=keyboard
        )
# ===== БЛОК 4.1.3 — Обработка inline-кнопок =====
async def admin_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, row = query.data.split(":")
    row = int(row)

    admin_id = query.from_user.id
    username = query.from_user.username or ""

    if action == "check_ok":
        sheet_checks.update_cell(row, 14, "подтверждён")
        log_stat("check_confirmed", admin_id, username)
        await query.edit_message_text("✅ Чек подтверждён")

    elif action == "check_no":
        sheet_checks.update_cell(row, 14, "отклонён")
        log_stat("check_rejected", admin_id, username)
        await query.edit_message_text("❌ Чек отклонён")
import asyncio
import pytz
from datetime import timedelta
# ===== БЛОК 4.2.2 — Автоматические уведомления =====
async def auto_notify(bot):
    tz = pytz.timezone("Europe/Moscow")
    today = datetime.now(tz).strftime("%Y-%m-%d")

    sent = 0

    for r in sheet_users.get_all_records():
        if r["Статус"] == "оплачено":
            continue

        if r["Дата_напоминания"] and r["Дата_напоминания"] > today:
            continue

        try:
            await bot.send_message(
                chat_id=int(r["Telegram_ID"]),
                text=BATTLE_TEXT
            )
            sent += 1
        except:
            log_stat("blocked", r["Telegram_ID"], r["username"])

    log_stat("auto_notify", "", "", "", f"отправлено: {sent}")
# ===== БЛОК 4.2.3 — Планировщик 18:00 МСК =====
async def daily_scheduler(app):
    while True:
        tz = pytz.timezone("Europe/Moscow")
        now = datetime.now(tz)

        target = now.replace(hour=18, minute=0, second=0)
        if now > target:
            target += timedelta(days=1)

        await asyncio.sleep((target - now).total_seconds())
        await auto_notify(app.bot)
# ===== БЛОК 4.3 — Статистика =====
if text == "📊 Статистика" and is_admin(uid):
    users = len(sheet_users.get_all_records())
    blocked = len([r for r in sheet_stats.get_all_records() if r["Тип"] == "blocked"])
    checks = len(sheet_checks.get_all_records())

    await update.message.reply_text(
        "📊 *Статистика бота*\n\n"
        f"👥 Пользователей: {users}\n"
        f"⛔ Заблокировали бота: {blocked}\n"
        f"📎 Чеков загружено: {checks}",
        parse_mode="Markdown",
        reply_markup=ADMIN_PANEL
    )
    return
# ===== БЛОК 4.4 — Анти-спам =====
last_action = context.user_data.get("last_action")
now = datetime.now().timestamp()

if last_action and now - last_action < 2:
    await update.message.reply_text("⏳ Подождите пару секунд")
    return

context.user_data["last_action"] = now
# ===== БЛОК 4.5 — Регистрация =====
app.add_handler(CallbackQueryHandler(admin_check_callback))
app.create_task(daily_scheduler(app))
