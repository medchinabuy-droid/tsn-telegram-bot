# =========================================================
# ЧАСТЬ 1. БАЗОВАЯ ИНИЦИАЛИЗАЦИЯ И КОНФИГ
# =========================================================

import os
import logging
import asyncio
from datetime import datetime, timedelta, date

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler


# =========================================================
# НАСТРОЙКИ (ОБЯЗАТЕЛЬНО ПРОВЕРЬ)
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")  # токен Telegram

# Google Service Account JSON (путь)
GOOGLE_CREDS_FILE = "credentials.json"

# ID Google таблицы
SPREADSHEET_ID = "PUT_YOUR_SPREADSHEET_ID_HERE"

# Папка с QR-кодом (QR должен лежать тут как ФАЙЛ)
QR_IMAGE_PATH = "static/qr.png"

# Админы (telegram_id)
ADMINS = {6810194645}  # <-- добавь нужные ID


# =========================================================
# ЛОГИРОВАНИЕ
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# =========================================================
# GOOGLE SHEETS ПОДКЛЮЧЕНИЕ
# =========================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file(
    GOOGLE_CREDS_FILE, scopes=SCOPES
)
gc = gspread.authorize(creds)

spreadsheet = gc.open_by_key(SPREADSHEET_ID)

# --- Листы ---
sheet_users = spreadsheet.sheet1  # Лист 1
sheet_checks = spreadsheet.get_worksheet(1)  # Лист 2
sheet_logs = spreadsheet.get_worksheet(2)  # Лист 3


# =========================================================
# СОСТОЯНИЯ ДЛЯ ConversationHandler
# =========================================================

(
    REG_FIO,
    REG_PHONE,
    REG_PLOT,
    REG_PAYDAY,
    WAIT_CHECK,
    ADMIN_BROADCAST,
) = range(6)


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ РАБОТЫ С ТАБЛИЦЕЙ
# =========================================================

def find_user_row(telegram_id: int):
    """
    Ищем пользователя ТОЛЬКО по telegram_id.
    Возвращает номер строки или None.
    """
    try:
        ids = sheet_users.col_values(4)  # Telegram_ID колонка
        if str(telegram_id) in ids:
            return ids.index(str(telegram_id)) + 1
    except Exception as e:
        logger.error(f"Ошибка поиска пользователя: {e}")
    return None


def is_registered(telegram_id: int) -> bool:
    """Проверка регистрации пользователя"""
    return find_user_row(telegram_id) is not None


def add_user_one_row(data: dict):
    """
    ❗ КРИТИЧНО
    Добавляем пользователя СТРОГО В ОДНУ СТРОКУ
    (ты жаловался, что добавляется в две — здесь исправлено)
    """
    row = [
        data.get("plot"),
        data.get("fio"),
        data.get("telegram_id"),
        data.get("username"),
        data.get("phone"),
        data.get("payday"),
        "",  # Электро
        "",  # Сумма
        "",  # Дата
        "АКТИВЕН",
        "USER",
        "",  # Дата_напоминания
    ]
    sheet_users.append_row(row, value_input_option="USER_ENTERED")


def log_event(event_type: str, uid: int, username: str, plot: str, details: str = "", error: str = ""):
    """
    Логирование всех событий (Лист 3)
    """
    sheet_logs.append_row(
        [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            event_type,
            uid,
            username,
            plot,
            details,
            error,
        ],
        value_input_option="USER_ENTERED",
    )


# =========================================================
# КЛАВИАТУРЫ
# =========================================================

def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["🚀 Начать"],
            ["💳 Реквизиты", "📎 Загрузить чек"],
            ["🛠 Админ-панель"],
        ],
        resize_keyboard=True,
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        [
            ["📣 Боевое уведомление"],
            ["📊 Статистика"],
            ["⬅ Назад"],
        ],
        resize_keyboard=True,
    )


# =========================================================
# /start
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    log_event(
        "START",
        user.id,
        user.username or "",
        "",
        "Нажал /start",
    )

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Этот бот помогает:\n"
        "• отправлять чеки\n"
        "• получать реквизиты\n"
        "• получать уведомления\n\n"
        "Нажмите 🚀 Начать",
        reply_markup=main_menu(),
    )


# =========================================================
# НАЧАЛО РЕГИСТРАЦИИ
# =========================================================

async def begin_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if is_registered(user.id):
        await update.message.reply_text(
            "✅ Вы уже зарегистрированы",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END

    context.user_data.clear()

    await update.message.reply_text(
        "👤 Введите ФИО:",
    )
    return REG_FIO


async def reg_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["fio"] = update.message.text.strip()
    await update.message.reply_text("📞 Введите телефон:")
    return REG_PHONE


async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text("🏠 Введите номер участка:")
    return REG_PLOT


async def reg_plot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["plot"] = update.message.text.strip()
    await update.message.reply_text("📅 Введите день оплаты (1–30):")
    return REG_PAYDAY


async def reg_payday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        payday = int(update.message.text.strip())
        if payday < 1 or payday > 30:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❗ Введите число от 1 до 30")
        return REG_PAYDAY

    user = update.effective_user

    data = {
        "fio": context.user_data["fio"],
        "phone": context.user_data["phone"],
        "plot": context.user_data["plot"],
        "payday": payday,
        "telegram_id": user.id,
        "username": user.username or "",
    }

    add_user_one_row(data)

    log_event(
        "REGISTER",
        user.id,
        user.username or "",
        data["plot"],
        "Пользователь зарегистрирован",
    )

    await update.message.reply_text(
        "✅ Регистрация завершена!",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# =========================================================
# ПРОВЕРКА ДОСТУПА (ИСПОЛЬЗУЕТСЯ В ЧАСТИ 2)
# =========================================================

async def require_registration(update: Update):
    await update.message.reply_text(
        "❗ Сначала завершите регистрацию\n\n"
        "Нажмите 🚀 Начать и заполните данные"
    )


# =========================================================
# ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ
# =========================================================

def build_app():
    application = Application.builder().token(BOT_TOKEN).build()

    registration_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🚀 Начать$"), begin_registration)],
        states={
            REG_FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_fio)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone)],
            REG_PLOT: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_plot)],
            REG_PAYDAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_payday)],
        },
        fallbacks=[],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(registration_conv)

    return application


# =========================================================
# ТОЧКА ВХОДА
# =========================================================

if __name__ == "__main__":
    app = build_app()

    scheduler = AsyncIOScheduler()
    scheduler.start()

    logger.info("Бот запущен")
    app.run_polling()
# =========================================================
# ЧАСТЬ 2. ОСНОВНАЯ ЛОГИКА БОТА
# =========================================================

import pytz
from telegram.error import Forbidden


MOSCOW_TZ = pytz.timezone("Europe/Moscow")


# =========================================================
# 💳 РЕКВИЗИТЫ + QR
# =========================================================

PAY_TEXT = (
    "💳 Реквизиты:\n\n"
    "Банк: БАНК ВТБ (ПАО)\n"
    "БИК: 44525411\n"
    "Счёт: 40703810900810019988\n"
    "Получатель: ТСН «ИСКОНА ПАРК»\n"
    "ИНН: 5028040362"
)


async def show_requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PAY_TEXT)

    if os.path.exists(QR_IMAGE_PATH):
        await update.message.reply_photo(open(QR_IMAGE_PATH, "rb"))
    else:
        await update.message.reply_text("❗ QR-код временно недоступен")


# =========================================================
# 📎 ЗАГРУЗКА ЧЕКА
# =========================================================

async def upload_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_registered(update.effective_user.id):
        await require_registration(update)
        return ConversationHandler.END

    await update.message.reply_text("📎 Отправьте фото или PDF чека")
    return WAIT_CHECK


async def save_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = find_user_row(user.id)

    if not row:
        return ConversationHandler.END

    file = None
    ext = ""

    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        ext = "jpg"
    elif update.message.document:
        file = await update.message.document.get_file()
        ext = "pdf"
    else:
        await update.message.reply_text("❗ Пришлите фото или PDF")
        return WAIT_CHECK

    filename = f"checks/{user.id}_{int(datetime.now().timestamp())}.{ext}"
    os.makedirs("checks", exist_ok=True)
    await file.download_to_drive(filename)

    plot = sheet_users.cell(row, 1).value

    sheet_checks.append_row(
        [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            user.id,
            plot,
            filename,
            "ОЖИДАЕТ",
        ],
        value_input_option="USER_ENTERED",
    )

    # inline-кнопки
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"check_ok:{user.id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"check_no:{user.id}"),
            ]
        ]
    )

    for admin in ADMINS:
        await context.bot.send_message(
            admin,
            f"📎 Новый чек\nУчасток: {plot}",
            reply_markup=kb,
        )

    await update.message.reply_text("✅ Чек отправлен на проверку")
    return ConversationHandler.END


# =========================================================
