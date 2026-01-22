import os
import json
import logging
import io
import requests
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
# 🔧 НАСТРОЙКИ И ЛОГИ
# =====================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
TZ = pytz.timezone("Europe/Moscow")

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# =====================================================
# 📊 GOOGLE SHEETS / DRIVE
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
# 🧭 МЕНЮ
# =====================================================
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
        ["📣 Уведомление"],
        ["📊 Статистика"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# =====================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================
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
            details,
            error
        ])
    except Exception as e:
        logger.warning(f"LOG SKIPPED: {e}")

def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=2):
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
    return f"https://drive.google.com/uc?id={f['id']}"

def download_qr_as_bytes(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.content

# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "ℹ️ Используйте кнопки ниже для работы с ботом.",
        reply_markup=menu
    )

# =====================================================
# 📝 TEXT HANDLER
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    # ---------- START ----------
    if text == "🚀 Начать":
        await start(update, context)
        return

    # ---------- ADMIN PANEL ----------
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text(
            "🛠 Админ-панель\n\n"
            "Выберите действие:",
            reply_markup=ADMIN_PANEL
        )
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "⬇️ Возврат в главное меню",
            reply_markup=ADMIN_MENU
        )
        return

    # ---------- ADMIN: DEBT ----------
    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["wait_house"] = True
        await update.message.reply_text(
            "Введите номер участка.\n"
            "ℹ️ Я покажу долг, телефон, username и статус бота."
        )
        return

    if context.user_data.get("wait_house") and is_admin(uid):
        context.user_data.pop("wait_house")
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                status_bot = "❌ Заблокировал" if not r.get("Telegram_ID") else "✅ Активен"
                await update.message.reply_text(
                    f"🏠 Участок: {text}\n"
                    f"👤 ФИО: {r.get('ФИО')}\n"
                    f"📱 Телефон: {r.get('Телефон')}\n"
                    f"💰 Сумма: {r.get('Сумма')}\n"
                    f"📎 Username: @{r.get('username')}\n"
                    f"🤖 Бот: {status_bot}",
                    reply_markup=ADMIN_PANEL
                )
                return

        await update.message.reply_text(
            "❌ Участок не найден.",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---------- ADMIN: NOTIFY ----------
    if text == "📣 Уведомление" and is_admin(uid):
        context.user_data["notify_house"] = True
        await update.message.reply_text(
            "Введите номер участка.\n"
            "ℹ️ Уведомление уйдёт ТОЛЬКО этому участку."
        )
        return

    if context.user_data.get("notify_house") and is_admin(uid):
        context.user_data.pop("notify_house")
        sent = False
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                try:
                    await context.bot.send_message(
                        int(r["Telegram_ID"]),
                        "📣 Уведомление ТСН\n\n"
                        "Это принудительное уведомление от администратора."
                    )
                    log_event("manual_notify", r["Telegram_ID"], r.get("username"), text)
                    sent = True
                except Exception as e:
                    log_event("blocked", r.get("Telegram_ID"), r.get("username"), text, error=str(e))

        await update.message.reply_text(
            "✅ Уведомление отправлено." if sent else "❌ Не удалось отправить.",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---------- REQUISITES ----------
    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"💳 Реквизиты\n\n"
            f"🏦 Банк: {r[0]}\n"
            f"🔢 БИК: {r[1]}\n"
            f"💼 Счёт: {r[2]}\n"
            f"👤 Получатель: {r[3]}\n"
            f"🧾 ИНН: {r[4]}\n\n"
            f"⬇️ QR-код для оплаты ниже"
        )
        if r[5]:
            qr_bytes = download_qr_as_bytes(r[5])
            await update.message.reply_photo(qr_bytes)
        return

    # ---------- UPLOAD CHECK ----------
    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text(
            "📎 Отправьте фото или PDF чека.\n"
            "ℹ️ После загрузки я подтвержу сохранение."
        )
        return
# =====================================================
# 📎 FILE HANDLER — ЗАГРУЗКА ЧЕКОВ
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    # --- Проверка на дубль ---
    if is_duplicate(file.file_unique_id):
        await msg.reply_text(
            "⚠️ Этот чек уже был загружен ранее.\n"
            "Если это ошибка — обратитесь к администратору."
        )
        log_event(
            "duplicate_check",
            update.effective_user.id,
            update.effective_user.username,
            details="Попытка загрузки дубля чека"
        )
        context.user_data.clear()
        return

    # --- Скачиваем файл ---
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    # --- Загружаем в Google Drive ---
    link = upload_to_drive(
        data,
        f"check_{update.effective_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    uid = update.effective_user.id
    row = find_user_row(uid)

    fio = house = phone = ""
    if row:
        fio = sheet_users.cell(row, 2).value
        house = sheet_users.cell(row, 1).value
        phone = sheet_users.cell(row, 5).value

    # --- Запись в Лист 2 ---
    sheet_checks.append_row([
        uid,                                    # telegram_id
        update.effective_user.username or "",   # username
        fio,                                    # ФИО
        house,                                  # Дом
        phone,                                  # Телефон
        link,                                   # Ссылка_на_чек
        "",                                     # Сумма_по_чеку
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),  # Дата_загрузки
        "", "", "",                              # OCR поля
        "",                                     # Дубль_чека
        file.file_unique_id,                    # File_Unique_ID
        "новый"                                 # Статус
    ])

    log_event(
        "check_uploaded",
        uid,
        update.effective_user.username,
        house,
        details="Чек успешно загружен"
    )

    context.user_data.clear()
    await msg.reply_text(
        "✅ Чек успешно сохранён.\n"
        "ℹ️ После проверки администратором долг будет закрыт.",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# =====================================================
# ⏰ АВТО-УВЕДОМЛЕНИЯ (РАЗ В МЕСЯЦ, ЗА 5 ДНЕЙ)
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

            # окно уведомлений: за 5 дней
            start_day = max(1, pay_day - 5)
            if not (start_day <= today.day <= pay_day):
                continue

            uid = int(r["Telegram_ID"])

            await context.bot.send_message(
                uid,
                "⏰ Напоминание об оплате\n\n"
                "У вас есть задолженность по участку.\n"
                "Просим произвести оплату и загрузить чек в бота.\n\n"
                "💳 Реквизиты доступны по кнопке «Реквизиты»."
            )

            log_event(
                "auto_notify",
                uid,
                r.get("username"),
                r.get("Участок"),
                details="Авто-уведомление отправлено"
            )

        except Exception as e:
            log_event(
                "blocked",
                r.get("Telegram_ID"),
                r.get("username"),
                r.get("Участок"),
                error=str(e)
            )

# =====================================================
# 📊 СТАТИСТИКА
# =====================================================
async def send_stats(update: Update):
    users = sheet_users.get_all_records()
    total = len(users)

    blocked = []
    for r in users:
        try:
            # пробный ping невозможен — ориентируемся по логам
            pass
        except:
            blocked.append(str(r.get("username")))

    await update.message.reply_text(
        "📊 Статистика бота\n\n"
        f"👥 Пользователей: {total}\n"
        f"⛔️ Заблокировали бота: {len(blocked)}\n"
        f"📨 Уведомлений отправлено: см. Лист 3\n\n"
        f"⛔️ Список: {', '.join(blocked) if blocked else '—'}"
    )

# =====================================================
# 🚦 MAIN
# =====================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # --- авто-уведомление каждый день в 18:00 МСК ---
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
