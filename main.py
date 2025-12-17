import logging
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, MessageHandler, Filters, CallbackQueryHandler, Updater
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pytesseract
from pdf2image import convert_from_path
from io import BytesIO
from PIL import Image
import requests
import os

# --- Настройки ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GOOGLE_SHEET_URL = os.environ.get("GOOGLE_SHEET_URL")

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Google Sheets ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_url(GOOGLE_SHEET_URL).sheet1

# --- Бот ---
def start(update, context):
    update.message.reply_text("Привет! Отправь платежку через /paid")

def paid(update, context):
    keyboard = [
        [InlineKeyboardButton("Отправить PDF/картинку", callback_data='send_payment')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text('Нажми кнопку для отправки платежки', reply_markup=reply_markup)

def button(update, context):
    query = update.callback_query
    query.answer()
    if query.data == "send_payment":
        query.edit_message_text(text="Отправьте PDF или картинку с платежкой")

def handle_document(update, context):
    file = update.message.document.get_file()
    file_bytes = BytesIO()
    file.download(out=file_bytes)
    file_bytes.seek(0)

    # Если PDF
    if update.message.document.file_name.endswith(".pdf"):
        images = convert_from_path(file_bytes)
        text = ""
        for img in images:
            text += pytesseract.image_to_string(img)
    else:
        # Картинка
        img = Image.open(file_bytes)
        text = pytesseract.image_to_string(img)

    update.message.reply_text(f"Распознан текст:\n{text[:500]}...")  # первые 500 символов

    # Сюда можно добавить логику: сумма, месяцы, запись в Google Sheet

def main():
    # Используем Updater и polling (Render Free)
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("paid", paid))
    dp.add_handler(CallbackQueryHandler(button))
    dp.add_handler(MessageHandler(Filters.document | Filters.photo, handle_document))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
