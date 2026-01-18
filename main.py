import logging
import os
from datetime import datetime, time

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

# =========================
# НАСТРОЙКИ
# =========================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [123456789]  # ← замени на свой Telegram ID
SPREADSHEET_NAME = "TSN"

QR_PATH = "static/qr.png"
TIMEZONE = pytz.timezone("Europe/Moscow")

# =========================
# ЛОГИ
# =========================

logging.basicConfig(level=logging.INFO)

# =========================
# GOOGLE SHEETS
# =========================

scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(
    "service_account.json",
    scopes=scopes
)
gc = gspread.authorize(creds)

sheet = gc.open(SPREADSHEET_NAME)
users_ws = sheet.sheet1
checks_ws = sheet.get_worksheet(1)
logs_ws = sheet.get_worksheet(2)

# =========================
# ВСПОМОГАТЕЛЬНЫЕ
# =========================

def is_admin(update: Update) -> bool:
    return update.effective_user.id in ADMIN_IDS

def main_menu(admin=False):
    buttons = [
        ["🔍 Долг по участку"],
        ["💳 Реквизиты", "📎 Загрузить чек"],
    ]
    if admin:
        buttons.append(["📣 Уведомление"])
        buttons.append(["📊 Статистика"])
        buttons.append(["🛠 Админ-панель"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def back_menu(admin=False):
    return ReplyKeyboardMarkup([["⬅️ Назад"]], resize_keyboard=True)

def log_event(uid, username, event, details=""):
    logs_ws.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        uid,
        username,
        event,
        details
    ])

# =========================
# /start
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_ids = users_ws.col_values(4)

    if str(user.id) not in tg_ids:
        context.user_data["state"] = "register_plot"
        await update.message.reply_text(
            "👋 Добро пожаловать!\nВведите номер участка:",
            reply_markup=back_menu()
        )
        return

    await update.message.reply_text(
        "Главное меню:",
        reply_markup=main_menu(is_admin(update))
    )

# =========================
# РЕГИСТРАЦИЯ
# =========================

async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = update.message.text.strip()

    users_ws.append_row([
        plot, "", update.effective_user.id,
        update.effective_user.username or "",
        "", "", "", "", "", "Активен", "Пользователь"
    ])

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Регистрация завершена",
        reply_markup=main_menu(is_admin(update))
    )

# =========================
# ДОЛГ
# =========================

async def debt_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "wait_plot_debt"
    await update.message.reply_text(
        "Введите номер участка:",
        reply_markup=back_menu()
    )

async def handle_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = update.message.text.strip()
    rows = users_ws.get_all_records()

    for r in rows:
        if str(r["Участок"]) == plot:
            await update.message.reply_text(
                f"🔍 Участок {plot}\nЗадолженность: {r.get('Сумма', 0)} ₽",
                reply_markup=main_menu(is_admin(update))
            )
            context.user_data.clear()
            return

    await update.message.reply_text("❌ Участок не найден")

# =========================
# РЕКВИЗИТЫ + QR
# =========================

async def requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 Реквизиты:\nБанк: ВТБ\nИНН: xxxx\nСчёт: xxxx"
    )

    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=open(QR_PATH, "rb"),
        caption="📱 Оплата по QR"
    )

    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=main_menu(is_admin(update))
    )

# =========================
# ЗАГРУЗКА ЧЕКА
# =========================

async def upload_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "wait_check"
    await update.message.reply_text(
        "📎 Отправьте фото чека",
        reply_markup=back_menu()
    )

async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    checks_ws.append_row([
        update.effective_user.id,
        update.effective_user.username,
        photo.file_id,
        datetime.now().strftime("%Y-%m-%d"),
        "Ожидает"
    ])

    await update.message.reply_text(
        "✅ Чек отправлен на проверку",
        reply_markup=main_menu(is_admin(update))
    )

    context.user_data.clear()
import logging
import os
from datetime import datetime, time

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

# =========================
# НАСТРОЙКИ
# =========================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [123456789]  # ← замени на свой Telegram ID
SPREADSHEET_NAME = "TSN"

QR_PATH = "static/qr.png"
TIMEZONE = pytz.timezone("Europe/Moscow")

# =========================
# ЛОГИ
# =========================

logging.basicConfig(level=logging.INFO)

# =========================
# GOOGLE SHEETS
# =========================

scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(
    "service_account.json",
    scopes=scopes
)
gc = gspread.authorize(creds)

sheet = gc.open(SPREADSHEET_NAME)
users_ws = sheet.sheet1
checks_ws = sheet.get_worksheet(1)
logs_ws = sheet.get_worksheet(2)

# =========================
# ВСПОМОГАТЕЛЬНЫЕ
# =========================

def is_admin(update: Update) -> bool:
    return update.effective_user.id in ADMIN_IDS

def main_menu(admin=False):
    buttons = [
        ["🔍 Долг по участку"],
        ["💳 Реквизиты", "📎 Загрузить чек"],
    ]
    if admin:
        buttons.append(["📣 Уведомление"])
        buttons.append(["📊 Статистика"])
        buttons.append(["🛠 Админ-панель"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def back_menu(admin=False):
    return ReplyKeyboardMarkup([["⬅️ Назад"]], resize_keyboard=True)

def log_event(uid, username, event, details=""):
    logs_ws.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        uid,
        username,
        event,
        details
    ])

# =========================
# /start
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_ids = users_ws.col_values(4)

    if str(user.id) not in tg_ids:
        context.user_data["state"] = "register_plot"
        await update.message.reply_text(
            "👋 Добро пожаловать!\nВведите номер участка:",
            reply_markup=back_menu()
        )
        return

    await update.message.reply_text(
        "Главное меню:",
        reply_markup=main_menu(is_admin(update))
    )

# =========================
# РЕГИСТРАЦИЯ
# =========================

async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = update.message.text.strip()

    users_ws.append_row([
        plot, "", update.effective_user.id,
        update.effective_user.username or "",
        "", "", "", "", "", "Активен", "Пользователь"
    ])

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Регистрация завершена",
        reply_markup=main_menu(is_admin(update))
    )

# =========================
# ДОЛГ
# =========================

async def debt_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "wait_plot_debt"
    await update.message.reply_text(
        "Введите номер участка:",
        reply_markup=back_menu()
    )

async def handle_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = update.message.text.strip()
    rows = users_ws.get_all_records()

    for r in rows:
        if str(r["Участок"]) == plot:
            await update.message.reply_text(
                f"🔍 Участок {plot}\nЗадолженность: {r.get('Сумма', 0)} ₽",
                reply_markup=main_menu(is_admin(update))
            )
            context.user_data.clear()
            return

    await update.message.reply_text("❌ Участок не найден")

# =========================
# РЕКВИЗИТЫ + QR
# =========================

async def requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 Реквизиты:\nБанк: ВТБ\nИНН: xxxx\nСчёт: xxxx"
    )

    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=open(QR_PATH, "rb"),
        caption="📱 Оплата по QR"
    )

    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=main_menu(is_admin(update))
    )

# =========================
# ЗАГРУЗКА ЧЕКА
# =========================

async def upload_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "wait_check"
    await update.message.reply_text(
        "📎 Отправьте фото чека",
        reply_markup=back_menu()
    )

async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    checks_ws.append_row([
        update.effective_user.id,
        update.effective_user.username,
        photo.file_id,
        datetime.now().strftime("%Y-%m-%d"),
        "Ожидает"
    ])

    await update.message.reply_text(
        "✅ Чек отправлен на проверку",
        reply_markup=main_menu(is_admin(update))
    )

    context.user_data.clear()
# =========================
# УВЕДОМЛЕНИЯ (АДМИН)
# =========================

async def notify_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    context.user_data["state"] = "notify_target"
    await update.message.reply_text(
        "📣 Уведомление\n\nВведите:\nALL — всем\nSELF — себе\nили номер участка",
        reply_markup=back_menu(True)
    )

async def notify_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["target"] = update.message.text.strip()
    context.user_data["state"] = "notify_text"

    await update.message.reply_text(
        "Введите текст уведомления:",
        reply_markup=back_menu(True)
    )

async def notify_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    target = context.user_data["target"]
    rows = users_ws.get_all_records()

    sent = 0

    for r in rows:
        chat_id = r.get("Telegram ID")
        if not chat_id:
            continue

        if target == "ALL":
            await context.bot.send_message(chat_id, text)
            sent += 1
        elif target == "SELF" and chat_id == update.effective_user.id:
            await context.bot.send_message(chat_id, text)
            sent += 1
        elif str(r.get("Участок")) == target:
            await context.bot.send_message(chat_id, text)
            sent += 1

    await update.message.reply_text(
        f"✅ Уведомление отправлено ({sent})",
        reply_markup=main_menu(True)
    )

    context.user_data.clear()

# =========================
# INLINE-ПОДТВЕРЖДЕНИЕ ЧЕКОВ
# =========================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    rows = checks_ws.get_all_records()

    if not rows:
        await update.message.reply_text("📎 Чеков нет")
        return

    r = rows[-1]

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="check_ok"),
            InlineKeyboardButton("❌ Отклонить", callback_data="check_no")
        ]
    ])

    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=r["file_id"],
        caption=f"Чек от @{r['username']}\nСтатус: {r['status']}",
        reply_markup=keyboard
    )

async def check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    status = "Подтверждён" if query.data == "check_ok" else "Отклонён"
    checks_ws.update_cell(checks_ws.row_count, 5, status)

    await query.edit_message_caption(
        caption=f"Чек {status}"
    )

# =========================
# СТАТИСТИКА
# =========================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    users = len(users_ws.get_all_records())
    checks = len(checks_ws.get_all_records())
    blocked = sum(1 for r in users_ws.get_all_records() if r.get("Статус") == "Блок")

    await update.message.reply_text(
        f"📊 Статистика\n\n"
        f"👥 Пользователей: {users}\n"
        f"📎 Чеков: {checks}\n"
        f"⛔ Заблокировано: {blocked}",
        reply_markup=main_menu(True)
    )

# =========================
# АВТО-РАССЫЛКА 18:00 МСК
# =========================

async def daily_notify(context: ContextTypes.DEFAULT_TYPE):
    rows = users_ws.get_all_records()

    for r in rows:
        if r.get("Сумма", 0) > 0 and r.get("Telegram ID"):
            await context.bot.send_message(
                r["Telegram ID"],
                f"⏰ Напоминание\nУчасток {r['Участок']}\nДолг: {r['Сумма']} ₽"
            )

# =========================
# ПЛАНИРОВЩИК
# =========================

scheduler = AsyncIOScheduler(timezone=TIMEZONE)
scheduler.add_job(daily_notify, "cron", hour=18, minute=0)
scheduler.start()

# =========================
# ОБРАБОТЧИК СООБЩЕНИЙ
# =========================

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = update.message.text

    if text == "⬅️ Назад":
        context.user_data.clear()
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=main_menu(is_admin(update))
        )
        return

    if state == "register_plot":
        await handle_registration(update, context)
    elif state == "wait_plot_debt":
        await handle_debt(update, context)
    elif state == "wait_check":
        await handle_check(update, context)
    elif state == "notify_target":
        await notify_target(update, context)
    elif state == "notify_text":
        await notify_text(update, context)
    else:
        await update.message.reply_text(
            "❓ Используйте кнопки меню",
            reply_markup=main_menu(is_admin(update))
        )

# =========================
# РЕГИСТРАЦИЯ ХЭНДЛЕРОВ
# =========================

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(MessageHandler(filters.Regex("🔍 Долг по участку"), debt_start))
    app.add_handler(MessageHandler(filters.Regex("💳 Реквизиты"), requisites))
    app.add_handler(MessageHandler(filters.Regex("📎 Загрузить чек"), upload_check_start))
    app.add_handler(MessageHandler(filters.Regex("📣 Уведомление"), notify_start))
    app.add_handler(MessageHandler(filters.Regex("📊 Статистика"), stats))
    app.add_handler(MessageHandler(filters.Regex("🛠 Админ-панель"), admin_panel))

    app.add_handler(CallbackQueryHandler(check_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.run_polling()

# =========================
# ЗАПУСК
# =========================

if __name__ == "__main__":
    main()
