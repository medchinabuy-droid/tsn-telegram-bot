import os
import json
import logging
from flask import Flask, request

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ================== ЛОГИ ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

logger.info("BOT_TOKEN set: %s", bool(BOT_TOKEN))
logger.info("BASE_URL set: %s", bool(BASE_URL))
logger.info("WEBHOOK_SECRET set: %s", bool(WEBHOOK_SECRET))

if not all([BOT_TOKEN, BASE_URL, WEBHOOK_SECRET]):
    raise RuntimeError("❌ Не заданы BOT_TOKEN / BASE_URL / WEBHOOK_SECRET")

# ================== СОСТОЯНИЯ ==================
ASK_NAME, ASK_FLAT, WAIT_PHOTO = range(3)

# ================== FLASK ==================
app = Flask(__name__)

telegram_app = Application.builder().token(BOT_TOKEN).build()

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📤 Отправить чек")]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Этот бот принимает чеки.\n"
        "Нажмите кнопку ниже, чтобы начать.",
        reply_markup=keyboard,
    )

# ================== НАЧАТЬ СЦЕНАРИЙ ==================
async def start_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите ваше имя:")
    return ASK_NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Введите номер квартиры:")
    return ASK_FLAT

async def ask_flat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["flat"] = update.message.text
    await update.message.reply_text("Теперь отправьте фото чека 📸")
    return WAIT_PHOTO

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file_id = photo.file_id

    name = context.user_data.get("name")
    flat = context.user_data.get("flat")

    # ⬇️ ЗДЕСЬ ПОТОМ БУДЕТ OCR + GOOGLE SHEETS
    logger.info(
        "Получен чек: имя=%s квартира=%s file_id=%s",
        name, flat, file_id
    )

    await update.message.reply_text(
        "✅ Чек получен!\n\n"
        f"Имя: {name}\n"
        f"Квартира: {flat}\n\n"
        "Спасибо! Данные будут обработаны."
    )

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Действие отменено")
    return ConversationHandler.END

# ================== HANDLERS ==================
conv_handler = ConversationHandler(
    entry_points=[
        MessageHandler(
            filters.TEXT & filters.Regex("^📤 Отправить чек$"),
            start_receipt,
        )
    ],
    states={
        ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
        ASK_FLAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_flat)],
        WAIT_PHOTO: [MessageHandler(filters.PHOTO, handle_photo)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(conv_handler)

# ================== WEBHOOK ROUTE ==================
@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    telegram_app.update_queue.put_nowait(update)
    return "ok"

@app.route("/")
def index():
    return "Bot is running"

# ================== ENTRY ==================
if __name__ == "__main__":
    async def main():
        await telegram_app.initialize()
        await telegram_app.bot.set_webhook(
            url=f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"
        )
        await telegram_app.start()
        logger.info("✅ Webhook установлен")

    import asyncio
    asyncio.run(main())

    app.run(host="0.0.0.0", port=10000)
