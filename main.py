import os
import logging
import asyncio
from flask import Flask, request

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ================= ЛОГИ =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not BOT_TOKEN or not BASE_URL or not WEBHOOK_SECRET:
    raise RuntimeError("ENV variables are missing")

logger.info("ENV OK")

# ================= СОСТОЯНИЯ =================
ASK_NAME, ASK_HOUSE, WAIT_PHOTO = range(3)

# ================= TELEGRAM =================
telegram_app = Application.builder().token(BOT_TOKEN).build()

# ================= FLASK =================
app = Flask(__name__)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("➡️ /start received")
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True
    )
    await update.message.reply_text(
        "Здравствуйте 👋\n\n"
        "Этот бот принимает чеки.\n"
        "Нажмите кнопку ниже, чтобы начать.",
        reply_markup=keyboard
    )

async def start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ваше имя:")
    return ASK_NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Введите номер дома:")
    return ASK_HOUSE

async def ask_house(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["house"] = update.message.text
    await update.message.reply_text("Отправьте фото чека 📸")
    return WAIT_PHOTO

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    name = context.user_data.get("name")
    house = context.user_data.get("house")

    logger.info("Чек получен: %s, дом %s, file_id=%s", name, house, photo.file_id)

    await update.message.reply_text(
        "✅ Чек принят!\n\n"
        f"Имя: {name}\n"
        f"Дом: {house}\n\n"
        "Спасибо."
    )
    return ConversationHandler.END

# ================= CONVERSATION =================
conv_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.TEXT & filters.Regex("^📤 Отправить чек$"), start_flow)
    ],
    states={
        ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
        ASK_HOUSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_house)],
        WAIT_PHOTO: [MessageHandler(filters.PHOTO, handle_photo)],
    },
    fallbacks=[],
)

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(conv_handler)

# ================= WEBHOOK =================
@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    asyncio.get_event_loop().create_task(
        telegram_app.process_update(update)
    )
    return "ok"

@app.route("/")
def index():
    return "OK"

# ================= START =================
async def main():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(
        url=f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"
    )
    await telegram_app.start()
    logger.info("✅ Webhook установлен")

if __name__ == "__main__":
    asyncio.run(main())
    app.run(host="0.0.0.0", port=10000)
