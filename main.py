import os
import json
import logging
from datetime import datetime

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials


# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_NAME = "УЧАСТКИ"  # НАЗВАНИЕ ТАБЛИЦЫ В GOOGLE
SHEET_NAME = "Лист1"          # НАЗВАНИЕ ЛИСТА

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


# ================= GOOGLE SHEETS =================

google_creds_json = os.getenv("GOOGLE_CREDS_JSON")

if not google_creds_json:
    raise RuntimeError("GOOGLE_CREDS_JSON не задан в Render")

creds_dict = json.loads(google_creds_json)

credentials = Credentials.from_service_account_info(
    creds_dict,
    scopes=SCOPES
)

gc = gspread.authorize(credentials)
spreadsheet = gc.open(SPREADSHEET_NAME)
sheet = spreadsheet.worksheet(SHEET_NAME)


# ================= ВСПОМОГАТЕЛЬНОЕ =================

def find_row_by_telegram_id(telegram_id: int):
    records = sheet.get_all_records()
    for idx, row in enumerate(records, start=2):
        if str(row.get("Telegram_ID")) == str(telegram_id):
            return idx
    return None


def find_row_by_phone(phone: str):
    records = sheet.get_all_records()
    for idx, row in enumerate(records, start=2):
        if str(row.get("Телефон")) == phone:
            return idx
    return None


# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    button = KeyboardButton("Отправить номер телефона", request_contact=True)
    keyboard = ReplyKeyboardMarkup([[button]], resize_keyboard=True)

    await update.message.reply_text(
        "Здравствуйте! Нажмите кнопку ниже, чтобы отправить номер телефона.",
        reply_markup=keyboard
    )


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    telegram_id = update.message.from_user.id
    phone = contact.phone_number

    row = find_row_by_phone(phone)

    if not row:
        await update.message.reply_text(
            "❌ Ваш номер не найден в таблице.\n"
            "Обратитесь к администратору."
        )
        return

    sheet.update(f"C{row}", telegram_id)

    await update.message.reply_text(
        "✅ Номер подтверждён!\n"
        "Telegram ID сохранён."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.message.from_user.id
    username = update.message.from_user.username

    row = find_row_by_telegram_id(telegram_id)

    if row:
        await update.message.reply_text("✅ Вы уже зарегистрированы.")
        return

    if username:
        records = sheet.get_all_records()
        for idx, r in enumerate(records, start=2):
            if r.get("Telegram_ID") == f"@{username}":
                sheet.update(f"C{idx}", telegram_id)
                await update.message.reply_text("✅ Telegram ID обновлён.")
                return

    await update.message.reply_text(
        "❌ Вы не найдены в таблице.\n"
        "Нажмите /start и отправьте номер телефона."
    )


# ================= ЗАПУСК =================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("🤖 Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
