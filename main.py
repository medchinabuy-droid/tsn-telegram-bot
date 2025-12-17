import os
import logging
from flask import Flask, request
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, CallbackQueryHandler, MessageHandler, Filters

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dispatcher = Dispatcher(bot=bot, update_queue=None, workers=0)

# ================= КНОПКИ =================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 Отправить платёжку", callback_data="send_payment")],
        [InlineKeyboardButton("📄 Реквизиты ТСН", callback_data="requisites")]
    ])

# ================= ХЕНДЛЕРЫ =================
def start(update, context):
    update.message.reply_text(
        "Здравствуйте! Бот ТСН.\nВыберите действие:",
        reply_markup=main_menu()
    )

def buttons(update, context):
    query = update.callback_query
    query.answer()

    if query.data == "send_payment":
        query.message.reply_text("Пришлите PDF или фото платёжки")

    elif query.data == "requisites":
        query.message.reply_text(
            "📄 Реквизиты ТСН:\n"
            "ИНН: XXXXXXXX\n"
            "Р/с: XXXXXXXXX\n"
            "Банк: XXXXX"
        )

def handle_files(update, context):
    update.message.reply_text(
        "Платёжка получена ✅\n"
        "Данные будут проверены бухгалтером."
    )

# ================= РЕГИСТРАЦИЯ =================
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CallbackQueryHandler(buttons))
dispatcher.add_handler(MessageHandler(Filters.document | Filters.photo, handle_files))

# ================= FLASK =================
app = Flask(__name__)

@app.route("/")
def index():
    return "TSN BOT OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.json, bot)
    dispatcher.process_update(update)
    return "ok"

# ================= ЗАПУСК =================
if __name__ == "__main__":
    bot.set_webhook(f"{APP_URL}/webhook")
    app.run(host="0.0.0.0", port=10000)
