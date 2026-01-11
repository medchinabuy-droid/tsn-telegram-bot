import os
import json
import logging
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from telegram.error import Forbidden

import gspread
from google.oauth2.service_account import Credentials

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# ---------------- GOOGLE ----------------
creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
gc = gspread.authorize(creds)

sh = gc.open_by_key(SPREADSHEET_ID)
users = sh.worksheet("Лист 1")
notify_log = sh.worksheet("Лист 3")

# ---------------- CONSTANT TEXT ----------------
BATTLE_NOTIFICATION_TEXT = (
    "⚠️ ВАЖНОЕ УВЕДОМЛЕНИЕ ТСН «ИСКОНА-ПАРК»\n\n"
    "Пожалуйста, проверьте состояние оплат.\n"
    "При необходимости загрузите чек через меню бота.\n\n"
    "Спасибо за оперативную реакцию."
)

# ---------------- MENUS ----------------
ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["🛠 Админ-панель"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["📣 Боевое уведомление"],
        ["🚀 Запустить рассылку"],
        ["📊 Статистика"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def is_admin(uid): 
    return uid in ADMIN_IDS

def log_notification(user_row, notif_type, status):
    notify_log.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_row.get("Telegram_ID"),
        user_row.get("username", ""),
        user_row.get("Участок"),
        user_row.get("Сумма", ""),
        notif_type,
        status
    ])

def mark_blocked(row_idx):
    col = users.find("Заблокирован").col
    users.update_cell(row_idx, col, "TRUE")

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🛠 Вы администратор",
            reply_markup=ADMIN_MENU
        )
    else:
        await update.message.reply_text("ℹ️ Используйте меню")

# ---------------- HANDLER ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    # --- START ---
    if text == "🚀 Начать":
        await start(update, context)
        return

    # --- ADMIN PANEL ---
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель:", reply_markup=ADMIN_PANEL)
        return

    # --- BATTLE NOTIFICATION ---
    if text == "📣 Боевое уведомление" and is_admin(uid):
        context.user_data["await_plot"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("await_plot") and is_admin(uid):
        context.user_data.pop("await_plot")
        plot = text

        records = users.get_all_records()
        for idx, row in enumerate(records, start=2):
            if str(row.get("Участок")) == plot:
                try:
                    await update.get_bot().send_message(
                        chat_id=int(row["Telegram_ID"]),
                        text=BATTLE_NOTIFICATION_TEXT
                    )
                    log_notification(row, "боевое", "доставлено")
                    await update.message.reply_text(
                        f"✅ Уведомление отправлено участку {plot}",
                        reply_markup=ADMIN_PANEL
                    )
                except Forbidden:
                    mark_blocked(idx)
                    log_notification(row, "боевое", "заблокирован")
                    await update.message.reply_text(
                        f"⛔ Пользователь участка {plot} заблокировал бота",
                        reply_markup=ADMIN_PANEL
                    )
                return

        await update.message.reply_text(
            "❌ Участок не найден",
            reply_markup=ADMIN_PANEL
        )
        return

    # --- BACK ---
    if text == "⬅️ Назад":
        await update.message.reply_text("⬇️ Главное меню", reply_markup=ADMIN_MENU)
        return

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
