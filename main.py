import os
import json
import logging
from datetime import datetime
import io

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from telegram.error import Forbidden

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# ---------------- GOOGLE ----------------
creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)
users = sh.worksheet("Лист 1")
checks = sh.worksheet("Лист 2")
notify_log = sh.worksheet("Лист 3")

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
def is_admin(uid): return uid in ADMIN_IDS

def log_notification(row, user, amount, notif_type, status):
    notify_log.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user["id"],
        user.get("username", ""),
        row["Участок"],
        amount,
        notif_type,
        status
    ])

def mark_blocked(row_idx):
    users.update_cell(row_idx, users.find("Заблокирован").col, "TRUE")

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🛠 Вы администратор",
            reply_markup=ADMIN_MENU
        )
    else:
        await update.message.reply_text("ℹ️ Используйте меню")

# ---------------- ADMIN ACTIONS ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "🚀 Начать":
        await start(update, context)
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text(
            "Админ-панель:",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---- БОЕВОЕ УВЕДОМЛЕНИЕ ----
    if text == "📣 Боевое уведомление" and is_admin(uid):
        context.user_data["broadcast"] = True
        await update.message.reply_text("Введите текст уведомления:")
        return

    if context.user_data.get("broadcast") and is_admin(uid):
        msg = text
        context.user_data.pop("broadcast")

        sent = blocked = 0
        for i, r in enumerate(users.get_all_records(), start=2):
            try:
                await update.get_bot().send_message(
                    chat_id=int(r["Telegram_ID"]),
                    text=msg
                )
                sent += 1
                log_notification(r, update.effective_user, "", "ручное", "доставлено")
            except Forbidden:
                blocked += 1
                mark_blocked(i)
                log_notification(r, update.effective_user, "", "ручное", "заблокирован")

        await update.message.reply_text(
            f"✅ Рассылка завершена\n"
            f"📨 Отправлено: {sent}\n"
            f"⛔ Заблокировали: {blocked}",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---- АВТО РАССЫЛКА ДОЛГОВ ----
    if text == "🚀 Запустить рассылку" and is_admin(uid):
        sent = blocked = 0
        for i, r in enumerate(users.get_all_records(), start=2):
            if r.get("Сумма", 0) and int(r["Сумма"]) > 0:
                try:
                    await update.get_bot().send_message(
                        chat_id=int(r["Telegram_ID"]),
                        text=f"⚠️ У вас задолженность {r['Сумма']} ₽"
                    )
                    sent += 1
                    log_notification(r, update.effective_user, r["Сумма"], "долг", "доставлено")
                except Forbidden:
                    blocked += 1
                    mark_blocked(i)
                    log_notification(r, update.effective_user, r["Сумма"], "долг", "заблокирован")

        await update.message.reply_text(
            f"🚀 Рассылка долгов завершена\n"
            f"📨 Отправлено: {sent}\n"
            f"⛔ Заблокировали: {blocked}",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---- СТАТИСТИКА ----
    if text == "📊 Статистика" and is_admin(uid):
        total = len(users.get_all_records())
        blocked = sum(1 for r in users.get_all_records() if r.get("Заблокирован") == "TRUE")
        notifs = len(notify_log.get_all_records())

        await update.message.reply_text(
            f"📊 Статистика бота\n\n"
            f"👥 Пользователей: {total}\n"
            f"⛔ Заблокировали: {blocked}\n"
            f"📨 Уведомлений отправлено: {notifs}",
            reply_markup=ADMIN_PANEL
        )
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "⬇️ Главное меню",
            reply_markup=ADMIN_MENU
        )
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
