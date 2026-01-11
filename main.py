import os
import json
import logging
import asyncio
from datetime import datetime

from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", 10000))
BASE_URL = "https://tsn-telegram-bot.onrender.com"

# ---------------- MENUS ----------------
USER_MENU = ReplyKeyboardMarkup(
    [
        ["🚀 Начать"],
        ["📎 Загрузить чек", "💳 Реквизиты"]
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["🚀 Начать"],
        ["🛠 Админ-панель"],
        ["📎 Загрузить чек", "💳 Реквизиты"]
    ],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["📣 Боевое уведомление"],
        ["🔍 Долг по участку"],
        ["📊 Статистика"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    await update.message.reply_text(
        "👋 Бот ТСН «Искона-Парк» работает\n\n⬇️ Используйте меню ниже",
        reply_markup=menu
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    # START
    if text == "🚀 Начать":
        await start(update, context)
        return

    # ADMIN PANEL
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text(
            "🛠 Админ-панель\nВыберите действие:",
            reply_markup=ADMIN_PANEL
        )
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "⬇️ Возврат в меню",
            reply_markup=ADMIN_MENU
        )
        return

    # TEST BATTLE NOTIFY
    if text == "📣 Боевое уведомление" and is_admin(uid):
        context.user_data["battle_wait"] = True
        await update.message.reply_text(
            "🏠 Введите номер участка для боевого уведомления:"
        )
        return

    if context.user_data.get("battle_wait") and is_admin(uid):
        context.user_data.pop("battle_wait")

        house = text.strip()
        # ⚠️ пока тест: отправляем админу
        await update.message.reply_text(
            f"📣 Боевое уведомление отправлено\n"
            f"Участок: {house}\n\n"
            f"(пока тест — реальная рассылка будет дальше)"
        )
        logger.info(f"BATTLE_NOTIFY house={house} by admin={uid}")
        return

    await update.message.reply_text("ℹ️ Используйте кнопки меню ⬇️")

# ---------------- APP INIT ----------------
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# ---------------- AIOHTTP WEBHOOK ----------------
async def handle_webhook(request: web.Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(text="ok")
    except Exception as e:
        logger.exception("Webhook error")
        return web.Response(status=500, text="error")

async def on_startup(app):
    await application.initialize()
    await application.bot.set_webhook(f"{BASE_URL}{WEBHOOK_PATH}")
    await application.start()
    logger.info("Webhook set & bot started")

async def on_shutdown(app):
    await application.stop()
    await application.shutdown()

# ---------------- MAIN ----------------
def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, port=PORT)

if __name__ == "__main__":
    main()
