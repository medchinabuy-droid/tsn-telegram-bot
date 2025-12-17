import os
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_URL = os.getenv("APP_URL")  # URL Render
MONTHLY_FEE = 6000

logging.basicConfig(level=logging.INFO)

# ================== TELEGRAM APP ==================
application = Application.builder().token(BOT_TOKEN).build()

# ================== КНОПКИ ==================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 Отправить платёжку", callback_data="send_payment")],
        [InlineKeyboardButton("📄 Реквизиты ТСН", callback_data="requisites")],
        [InlineKeyboardButton("📅 Моя дата оплаты", callback_data="my_date")],
    ])

# ================== ХЕНДЛЕРЫ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здравствуйте! Бот ТСН для оплаты взносов.",
        reply_markup=main_menu()
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "send_payment":
        context.user_data["awaiting_payment"] = True
        await query.message.reply_text("Пришлите PDF или фото платёжки")

    elif query.data == "requisites":
        await query.message.reply_text(
            "📄 Реквизиты ТСН:\n"
            "ИНН: XXXXXXXX\n"
            "Р/с: XXXXXXXXXXXXX\n"
            "Банк: XXXXX"
        )

    elif query.data == "my_date":
        await query.message.reply_text("Дата оплаты указана в реестре ТСН")

async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_payment"):
        return

    await update.message.reply_text(
        "Платёжка получена ✅\n"
        "Сумма распознаётся и отправлена бухгалтеру на сверку."
    )
    context.user_data["awaiting_payment"] = False

# ================== РЕГИСТРАЦИЯ ==================
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(buttons))
application.add_handler(
    MessageHandler(filters.Document.ALL | filters.PHOTO, handle_payment)
)

# ================== FLASK ==================
app = Flask(__name__)

@app.route("/")
def index():
    return "TSN BOT OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.json, application.bot)
    application.update_queue.put_nowait(update)
    return "ok"

# ================== ЗАПУСК ==================
if __name__ == "__main__":
    application.bot.set_webhook(f"{APP_URL}/webhook")
    app.run(host="0.0.0.0", port=10000)
