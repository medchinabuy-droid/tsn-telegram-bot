import os
import json
import logging
from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

import gspread
from google.oauth2.service_account import Credentials

import pytesseract
from PIL import Image

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"

SHEET_USERS = "Лист 1"
SHEET_CHECKS = "Чеки"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

HOUSE_NAME = "Дом_1"

# ================= ЛОГИ =================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ================= GOOGLE =================

creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)

sheet_checks = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_CHECKS)

logging.info("📄 Подключение к Google Sheets успешно")

# ================= OCR =================

def recognize_text(image_path):
    image = Image.open(image_path)
    text = pytesseract.image_to_string(image, lang="rus+eng")
    return text

# ================= TELEGRAM =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["💳 Реквизиты"],
        ["📎 Загрузить чек"]
    ]

    await update.message.reply_text(
        "✅ Бот ТСН запущен\n\n"
        "Выберите действие:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True
        )
    )

async def show_requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "💳 Реквизиты":
        await update.message.reply_text(
            "💳 Реквизиты для оплаты:\n\n"
            "Получатель: ТСН «Пример»\n"
            "ИНН: 0000000000\n"
            "Счёт: 00000000000000000000\n"
            "Банк: Пример Банк\n\n"
            "❗ После оплаты загрузите чек"
        )

async def upload_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "📎 Загрузить чек":
        await update.message.reply_text(
            "📸 Отправьте фото чека одним сообщением"
        )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]
    file = await photo.get_file()

    date_folder = datetime.now().strftime("%Y-%m-%d")
    filename = f"check_{user.id}.jpg"
    temp_path = f"/tmp/{filename}"

    await file.download_to_drive(temp_path)

    text = recognize_text(temp_path)

    amount = "Не найдено"
    for line in text.splitlines():
        if "₽" in line or "RUB" in line:
            amount = line.strip()
            break

    sheet_checks.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        user.username or user.id,
        "",
        amount,
        "На проверке",
        "Файл загружен"
    ])

    await update.message.reply_text(
        "✅ Чек получен\n"
        "📄 Статус: На проверке"
    )

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT, show_requisites))
    app.add_handler(MessageHandler(filters.TEXT, upload_hint))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logging.info("🤖 Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
