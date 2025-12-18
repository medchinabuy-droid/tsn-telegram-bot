import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import gspread
from google.oauth2.service_account import Credentials

# Настройка логов
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Подключение к Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
creds = Credentials.from_service_account_file('telegram-bot-481523-05b49bfbb763.json', scopes=SCOPES)
gc = gspread.authorize(creds)
SHEET = gc.open("Название вашей таблицы").sheet1  # Первый лист

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("Отправить контакт", request_contact=True)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Привет, {user.first_name}! Отправь мне свой контакт.",
        reply_markup=reply_markup
    )

# Обработка контактов
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    username = update.effective_user.username or ""
    phone = contact.phone_number or ""
    name = contact.first_name or ""

    # Добавление в Google Sheets
    SHEET.append_row([username, name, phone])
    await update.message.reply_text(f"Данные сохранены: {name} ({username}) {phone}")

# Настройка приложения
app = ApplicationBuilder().token("ВАШ_ТОКЕН_БОТА").build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters=Filters.CONTACT, callback=handle_contact))

# Запуск
app.run_polling()
