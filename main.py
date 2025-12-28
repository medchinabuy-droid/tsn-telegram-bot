import logging
import datetime
import os

import gspread
from google.oauth2.service_account import Credentials

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

USERS_SHEET_NAME = "Лист 1"
CHECKS_SHEET_NAME = "Лист 2"

ASK_FIO, ASK_HOUSE, ASK_PHONE, ASK_CHECK = range(4)

logging.basicConfig(level=logging.INFO)

# ================== GOOGLE SHEETS ==================

def get_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    creds = Credentials.from_service_account_info(
        eval(GOOGLE_CREDENTIALS_JSON),
        scopes=scopes,
    )

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    return (
        sh.worksheet(USERS_SHEET_NAME),
        sh.worksheet(CHECKS_SHEET_NAME),
    )

# ================== УТИЛИТЫ ==================

def find_user(users_sheet, telegram_id):
    rows = users_sheet.get_all_records()
    for row in rows:
        if str(row.get("Telegram_ID")) == str(telegram_id):
            return row
    return None

def add_user(users_sheet, fio, house, phone, telegram_id):
    users_sheet.append_row([
        house,
        fio,
        telegram_id,
        phone,
    ])

def add_check(checks_sheet, data: dict):
    checks_sheet.append_row([
        data.get("telegram_id"),
        data.get("username"),
        data.get("fio"),
        data.get("house"),
        data.get("phone"),
        data.get("check_link"),
        "",
        data.get("date"),
        "",
        "",
        data.get("file_id"),
    ])

# ================== КОМАНДЫ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📎 Отправить чек")]],
        resize_keyboard=True
    )
    await update.message.reply_text(
        "Здравствуйте! Выберите действие:",
        reply_markup=keyboard
    )

async def send_check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users_sheet, _ = get_sheets()

    user = update.effective_user
    found = find_user(users_sheet, user.id)

    context.user_data["telegram_id"] = user.id
    context.user_data["username"] = user.username

    if found:
        context.user_data["fio"] = found.get("ФИО")
        context.user_data["house"] = found.get("Участок")
        context.user_data["phone"] = found.get("Телефон")

        await update.message.reply_text(
            "Пользователь найден. Пришлите фото или PDF чека."
        )
        return ASK_CHECK
    else:
        await update.message.reply_text("Введите ФИО:")
        return ASK_FIO

async def ask_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["fio"] = update.message.text.strip()
    await update.message.reply_text("Введите номер дома / участка:")
    return ASK_HOUSE

async def ask_house(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["house"] = update.message.text.strip()
    await update.message.reply_text("Введите телефон:")
    return ASK_PHONE

async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text("Теперь пришлите чек (фото или PDF):")
    return ASK_CHECK

async def receive_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users_sheet, checks_sheet = get_sheets()

    file = None
    if update.message.photo:
        file = update.message.photo[-1]
    elif update.message.document:
        file = update.message.document

    if not file:
        await update.message.reply_text("Пожалуйста, отправьте файл чека.")
        return ASK_CHECK

    context.user_data["file_id"] = file.file_unique_id
    context.user_data["check_link"] = file.file_id
    context.user_data["date"] = datetime.datetime.now().strftime("%Y-%m-%d")

    if not find_user(users_sheet, context.user_data["telegram_id"]):
        add_user(
            users_sheet,
            context.user_data["fio"],
            context.user_data["house"],
            context.user_data["phone"],
            context.user_data["telegram_id"]
        )

    add_check(checks_sheet, context.user_data)

    await update.message.reply_text("✅ Чек успешно сохранён. Спасибо!")
    return ConversationHandler.END

# ================== WEBHOOK CLEANUP ==================

async def post_init(application):
    # 🔥 УДАЛЯЕМ webhook, иначе polling не работает
    await application.bot.delete_webhook(drop_pending_updates=True)

# ================== MAIN ==================

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📎 Отправить чек$"), send_check_start)
        ],
        states={
            ASK_FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_fio)],
            ASK_HOUSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_house)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            ASK_CHECK: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, receive_check)
            ],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    # ❗ БЕЗ await
    app.run_polling()

if __name__ == "__main__":
    main()
