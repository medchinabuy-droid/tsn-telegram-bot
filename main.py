# =========================================================
# TSN TELEGRAM BOT — PART 1 / 2
# =========================================================
# Блоки:
# 1. Imports
# 2. ENV
# 3. Logging
# 4. Google Sheets / Drive
# 5. Меню
# 6. Хелперы
# 7. Регистрация
# 8. /start
# 9. Реквизиты + QR картинкой
# =========================================================

# =========================
# 1. IMPORTS
# =========================
import os
import json
import logging
import re
import io
from datetime import datetime, timedelta

import pytz
import gspread

from apscheduler.schedulers.background import BackgroundScheduler

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

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# =========================
# 2. ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# =========================
# 3. LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TSN-BOT")

# =========================
# 4. GOOGLE
# =========================
creds_json = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)

SHEET_USERS = sh.worksheet("Лист 1")
SHEET_CHECKS = sh.worksheet("Лист 2")
SHEET_LOGS = sh.worksheet("Лист 3")
SHEET_REQS = sh.worksheet("Реквизиты")

# =========================
# 5. MENUS
# =========================
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["🔍 Долг по участку"],
        ["📣 Боевое уведомление"],
        ["📊 Статистика"],
        ["⬅️ Назад"],
    ],
    resize_keyboard=True
)

# =========================
# 6. HELPERS
# =========================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def log_event(event, uid="", username="", section="", details="", error=""):
    SHEET_LOGS.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event,
        uid,
        username,
        section,
        details,
        error
    ])


def find_user_row(username: str):
    if not username:
        return None
    col = SHEET_USERS.col_values(4)
    for i, val in enumerate(col, start=2):
        if val == username:
            return i
    return None


def get_or_create_user(update: Update):
    user = update.effective_user
    username = user.username or ""
    uid = user.id

    row = find_user_row(username)
    if row:
        SHEET_USERS.update_cell(row, 3, str(uid))
        return row

    SHEET_USERS.append_row([
        "", "", uid, username, "", "", "", "", "", "", "", ""
    ])
    return SHEET_USERS.row_count


def is_registered(row):
    fio = SHEET_USERS.cell(row, 2).value
    phone = SHEET_USERS.cell(row, 5).value
    house = SHEET_USERS.cell(row, 1).value
    return bool(fio and phone and house)


def valid_fio(text):
    return len(text.split()) >= 2


def valid_phone(text):
    return bool(re.fullmatch(r"\+7\d{10}", text))


# =========================
# 7. REGISTRATION FLOW
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = get_or_create_user(update)

    if is_registered(row):
        await update.message.reply_text(
            "Вы уже зарегистрированы ✅",
            reply_markup=ADMIN_MENU if is_admin(user.id) else USER_MENU
        )
        return

    context.user_data["reg_step"] = "fio"
    await update.message.reply_text("👋 Добро пожаловать!\nВведите ФИО:")


async def registration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_or_create_user(update)
    step = context.user_data.get("reg_step")
    text = update.message.text.strip()

    if step == "fio":
        if not valid_fio(text):
            await update.message.reply_text("Введите ФИО полностью")
            return
        SHEET_USERS.update_cell(row, 2, text)
        context.user_data["reg_step"] = "phone"
        await update.message.reply_text("Введите телефон в формате +79998887766")
        return

    if step == "phone":
        if not valid_phone(text):
            await update.message.reply_text("Неверный формат телефона")
            return
        SHEET_USERS.update_cell(row, 5, text)
        context.user_data["reg_step"] = "house"
        await update.message.reply_text("Введите номер участка")
        return

    if step == "house":
        SHEET_USERS.update_cell(row, 1, text)
        context.user_data.pop("reg_step", None)

        await update.message.reply_text(
            "✅ Регистрация завершена",
            reply_markup=ADMIN_MENU if is_admin(update.effective_user.id) else USER_MENU
        )
        log_event("registration", update.effective_user.id, update.effective_user.username)
        return


# =========================
# 8. REQUISITES + QR
# =========================
async def send_requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = SHEET_REQS.row_values(2)

    text = (
        f"💳 Реквизиты:\n\n"
        f"Банк: {req[0]}\n"
        f"БИК: {req[1]}\n"
        f"Счёт: {req[2]}\n"
        f"Получатель: {req[3]}\n"
        f"ИНН: {req[4]}"
    )

    await update.message.reply_text(text)

    qr_url = req[5]
    if qr_url:
        await update.message.reply_photo(qr_url)
# ============================================================
# PART 2 / 2
# ============================================================
# СОДЕРЖАНИЕ:
# 10. Загрузка чеков
# 11. Inline-подтверждение чеков админом
# 12. Авто-закрытие долга
# 13. Боевое уведомление (ALL / SELF / номер участка)
# 14. Авто-уведомления в 18:00 МСК
# 15. Статистика бота
# 16. Роутер текстовых сообщений
# 17. MAIN + APScheduler
# ============================================================


# ============================================================
# 10. ЗАГРУЗКА ЧЕКА
# ============================================================
async def handle_check_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = get_user_row(user.id)

    if not row:
        await update.message.reply_text("❗ Сначала завершите регистрацию")
        return

    file = update.message.photo[-1] if update.message.photo else update.message.document
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()
    file_uid = file.file_unique_id

    if file_uid in SHEET_CHECKS.col_values(13):
        await update.message.reply_text("❌ Этот чек уже был загружен")
        return

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=file.mime_type)
    uploaded = drive.files().create(
        body={"name": f"check_{file_uid}", "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()

    link = f"https://drive.google.com/file/d/{uploaded['id']}"

    SHEET_CHECKS.append_row([
        user.id,                              # telegram_id
        user.username or "",                  # username
        SHEET_USERS.cell(row, 2).value,       # ФИО
        SHEET_USERS.cell(row, 1).value,       # Участок
        SHEET_USERS.cell(row, 5).value,       # Телефон
        link,                                 # Ссылка на чек
        "",                                   # Сумма по чеку
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "", "", "", "",                       # OCR поля
        file_uid,
        "⏳ На проверке"
    ])

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve|{file_uid}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject|{file_uid}")
    ]])

    for admin in ADMIN_IDS:
        await context.bot.send_message(
            admin,
            f"📎 Новый чек\n"
            f"ФИО: {SHEET_USERS.cell(row, 2).value}\n"
            f"Участок: {SHEET_USERS.cell(row, 1).value}",
            reply_markup=keyboard
        )

    log_event("check_uploaded", user.id, user.username, SHEET_USERS.cell(row, 1).value)
    await update.message.reply_text("✅ Чек загружен и отправлен администратору")


# ============================================================
# 11. INLINE-ПОДТВЕРЖДЕНИЕ ЧЕКА
# ============================================================
async def inline_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, file_uid = query.data.split("|")
    col = SHEET_CHECKS.col_values(13)

    if file_uid not in col:
        await query.edit_message_text("❌ Чек не найден")
        return

    row = col.index(file_uid) + 1
    house = SHEET_CHECKS.cell(row, 4).value

    if action == "approve":
        SHEET_CHECKS.update_cell(row, 14, "✅ Подтверждён")
        close_debt(house)
        log_event("check_approved", "", "", house)
        await query.edit_message_text("✅ Чек подтверждён. Долг закрыт.")

    elif action == "reject":
        SHEET_CHECKS.update_cell(row, 14, "❌ Отклонён")
        log_event("check_rejected", "", "", house)
        await query.edit_message_text("❌ Чек отклонён.")


# ============================================================
# 12. АВТО-ЗАКРЫТИЕ ДОЛГА
# ============================================================
def close_debt(house):
    col = SHEET_USERS.col_values(1)
    if house in col:
        r = col.index(house) + 1
        SHEET_USERS.update_cell(r, 10, "Оплачено")
        SHEET_USERS.update_cell(r, 12, "")  # очистка даты напоминания


# ============================================================
# 13. БОЕВОЕ УВЕДОМЛЕНИЕ
# ============================================================
async def battle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["battle_mode"] = True
    await update.message.reply_text(
        "📣 Боевое уведомление\n\n"
        "Введите:\n"
        "ALL — всем\n"
        "SELF — себе\n"
        "или номер участка (например 81)"
    )


async def battle_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip()
    context.user_data.pop("battle_mode", None)

    sent = 0
    for u in SHEET_USERS.get_all_records():
        try:
            if target == "ALL":
                chat_id = int(u["Telegram_ID"])
            elif target == "SELF":
                chat_id = update.effective_user.id
            elif target.isdigit() and str(u["Участок"]) == target:
                chat_id = int(u["Telegram_ID"])
            else:
                continue

            await context.bot.send_message(chat_id, BATTLE_TEXT)
            sent += 1
        except:
            log_event("blocked", u.get("Telegram_ID"), u.get("username"))

    log_event("battle_sent", update.effective_user.id, update.effective_user.username, "", f"sent={sent}")
    await update.message.reply_text(f"📨 Отправлено уведомлений: {sent}")


# ============================================================
# 14. АВТО-РАССЫЛКА В 18:00 МСК
# ============================================================
async def auto_reminders():
    today = datetime.now(MOSCOW_TZ).day

    for u in SHEET_USERS.get_all_records():
        try:
            pay_day = int(u.get("День_оплаты", 0))
            if not pay_day:
                continue

            if today >= pay_day - 5 and u.get("Статус") != "Оплачено":
                await app.bot.send_message(
                    int(u["Telegram_ID"]),
                    "⏰ Напоминание: приближается дата оплаты взноса"
                )
                log_event("auto_notify", u["Telegram_ID"], u.get("username"), u.get("Участок"))
        except:
            log_event("blocked", u.get("Telegram_ID"), u.get("username"))


# ============================================================
# 15. СТАТИСТИКА
# ============================================================
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = SHEET_USERS.get_all_records()
    logs = SHEET_LOGS.get_all_records()

    blocked = [str(l["UID"]) for l in logs if l["Тип"] == "blocked"]
    sent = len([l for l in logs if "notify" in l["Тип"] or "battle" in l["Тип"]])

    await update.message.reply_text(
        "📊 Статистика бота\n\n"
        f"👥 Пользователей: {len(users)}\n"
        f"⛔ Заблокировали: {len(blocked)}\n"
        f"📨 Уведомлений отправлено: {sent}\n\n"
        f"Blocked: {', '.join(blocked)}"
    )


# ============================================================
# 16. РОУТЕР ТЕКСТА
# ============================================================
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if context.user_data.get("reg_step"):
        await registration_flow(update, context)
        return

    if context.user_data.get("battle_mode"):
        await battle_send(update, context)
        return

    if text == "🚀 Начать":
        await start(update, context)

    elif text == "📎 Загрузить чек":
        await update.message.reply_text("📎 Отправьте фото или PDF чека")

    elif text == "💳 Реквизиты":
        await send_requisites(update, context)

    elif text == "🛠 Админ-панель" and is_admin(update.effective_user.id):
        await update.message.reply_text("🛠 Админ-панель", reply_markup=ADMIN_PANEL)

    elif text == "📣 Боевое уведомление":
        await battle_start(update, context)

    elif text == "📊 Статистика":
        await show_stats(update, context)

    elif text == "⬅️ Назад":
        await update.message.reply_text("Главное меню", reply_markup=ADMIN_MENU)


# ============================================================
# 17. MAIN + SCHEDULER
# ============================================================
def main():
    global app
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(inline_check_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_check_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    scheduler = BackgroundScheduler(timezone=MOSCOW_TZ)
    scheduler.add_job(lambda: app.create_task(auto_reminders()), "cron", hour=18, minute=0)
    scheduler.start()

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=os.getenv("WEBHOOK_URL")
    )


if __name__ == "__main__":
    main()
