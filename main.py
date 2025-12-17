import os
import logging
from io import BytesIO
from PIL import Image
import pytesseract
from pdf2image import convert_from_path
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, Filters, CallbackContext
import qrcode

# --- Настройки ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GOOGLE_SHEET_URL = os.environ.get("GOOGLE_SHEET_URL")
REKVIZITS = "Реквизиты: ...\nQR:"

# --- Логирование ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Google Sheets ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_url(GOOGLE_SHEET_URL).sheet1

# --- Состояние пользователя ---
user_data_dict = {}

# --- Команды ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Привет! Отправь /paid чтобы оплатить платежку.")

def paid(update: Update, context: CallbackContext):
    keyboard = [[InlineKeyboardButton("Отправить PDF/картинку", callback_data='send_payment')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Нажми кнопку для отправки платежки", reply_markup=reply_markup)

def button(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id

    if query.data == "send_payment":
        query.edit_message_text("Отправьте PDF или картинку с платежкой")
        user_data_dict[user_id] = {"stage": "waiting_file"}

def handle_document(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id not in user_data_dict or user_data_dict[user_id].get("stage") != "waiting_file":
        update.message.reply_text("Сначала нажмите кнопку /paid")
        return

    file = update.message.document.get_file() if update.message.document else update.message.photo[-1].get_file()
    file_bytes = BytesIO()
    file.download(out=file_bytes)
    file_bytes.seek(0)

    # --- OCR ---
    if update.message.document and update.message.document.file_name.endswith(".pdf"):
        images = convert_from_path(file_bytes)
        text = ""
        for img in images:
            text += pytesseract.image_to_string(img)
    else:
        img = Image.open(file_bytes)
        text = pytesseract.image_to_string(img)

    # --- Авто поиск суммы ---
    import re
    match = re.search(r"(\d+[.,]?\d*)", text.replace(" ", ""))
    amount = match.group(1) if match else "не найдено"

    user_data_dict[user_id]["amount"] = amount
    user_data_dict[user_id]["stage"] = "waiting_months"

    # --- Предложить выбрать месяцы ---
    keyboard = [
        [InlineKeyboardButton("Январь", callback_data="month_1"), InlineKeyboardButton("Февраль", callback_data="month_2")],
        [InlineKeyboardButton("Март", callback_data="month_3"), InlineKeyboardButton("Апрель", callback_data="month_4")],
        [InlineKeyboardButton("Подтвердить", callback_data="confirm")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(f"Сумма распознана: {amount}\nВыберите месяцы:", reply_markup=reply_markup)

def month_button(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id
    query.answer()

    if user_id not in user_data_dict:
        query.edit_message_text("Сначала нажмите /paid")
        return

    data = user_data_dict[user_id]
    if "months" not in data:
        data["months"] = []

    if query.data.startswith("month_"):
        month_num = query.data.split("_")[1]
        if month_num not in data["months"]:
            data["months"].append(month_num)
        else:
            data["months"].remove(month_num)
        query.edit_message_text(f"Вы выбрали месяцы: {', '.join(data['months'])}")
    elif query.data == "confirm":
        # --- Запись в Google Sheet ---
        sheet.append_row([user_id, data["amount"], ",".join(data.get("months", []))])
        query.edit_message_text(f"Оплата сохранена! {REKVIZITS}")
        # --- Отправка QR ---
        qr = qrcode.make(REKVIZITS)
        bio = BytesIO()
        qr.save(bio, format="PNG")
        bio.seek(0)
        context.bot.send_photo(chat_id=user_id, photo=bio)
        del user_data_dict[user_id]

# --- Основной цикл ---
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("paid", paid))
    dp.add_handler(CallbackQueryHandler(button, pattern="^send_payment$"))
    dp.add_handler(CallbackQueryHandler(month_button, pattern="^(month_|confirm)"))
    dp.add_handler(MessageHandler(Filters.document | Filters.photo, handle_document))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
