import os
import json
import logging
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# ---------------- GOOGLE ----------------
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
gc = gspread.authorize(creds)

sh = gc.open_by_key(SPREADSHEET_ID)
sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_reqs = sh.worksheet("Реквизиты")

# ---------------- KEYBOARD ----------------
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📎 Загрузить чек")],
        [KeyboardButton("💳 Реквизиты")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

# ---------------- HELPERS ----------------
def find_user_row(telegram_id: int):
    ids = sheet_users.col_values(3)
    for i, val in enumerate(ids, start=1):
        if val == str(telegram_id):
            return i
    return None


# ---------------- START ----------------
async def start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "👋 Добро пожаловать в ТСН «Искона-Парк»\n\n"
        "⬇️ Используйте кнопки меню внизу",
        reply_markup=MAIN_MENU,
    )

    row = find_user_row(update.effective_user.id)

    if not row:
        context.user_data["step"] = "fio"
        await update.message.reply_text("Введите ФИО:")
    else:
        await update.message.reply_text(
            "Вы уже зарегистрированы ✅",
            reply_markup=MAIN_MENU,
        )


# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user

    # 🚀 Начать
    if text == "🚀 Начать":
        await start_flow(update, context)
        return

    step = context.user_data.get("step")

    # --- FIO ---
    if step == "fio":
        sheet_users.append_row(
            ["", text, str(user.id), "", "", "", "", "", "", "", "", "", "", ""]
        )
        context.user_data["step"] = "phone"
        await update.message.reply_text(
            "📞 Укажите телефон\nпример: +79261234567",
            reply_markup=MAIN_MENU,
        )
        return

    # --- PHONE ---
    if step == "phone":
        row = find_user_row(user.id)
        sheet_users.update_cell(row, 4, text)
        context.user_data["step"] = "house"
        await update.message.reply_text(
            "🏠 Укажите номер дома (участка):",
            reply_markup=MAIN_MENU,
        )
        return

    # --- HOUSE ---
    if step == "house":
        row = find_user_row(user.id)
        sheet_users.update_cell(row, 1, text)
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Регистрация завершена",
            reply_markup=MAIN_MENU,
        )
        return

    # --- MENU ---
    if text == "💳 Реквизиты":
        r = sheet_reqs.get_all_records()[0]
        await update.message.reply_text(
            f"💳 Реквизиты:\n\n"
            f"Банк: {r.get('Банк')}\n"
            f"Получатель: {r.get('Получатель')}\n"
            f"Счёт: {r.get('Счёт получателя')}\n"
            f"ИНН: {r.get('ИНН')}",
            reply_markup=MAIN_MENU,
        )
        return

    # --- FALLBACK ---
    await update.message.reply_text(
        "ℹ️ Используйте кнопки меню внизу ⬇️",
        reply_markup=MAIN_MENU,
    )


# ---------------- PHOTO ----------------
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]

    sheet_checks.append_row(
        [
            user.id,
            user.username or "",
            "",
            "",
            "",
            "",
            "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "",
            photo.file_unique_id,
        ]
    )

    await update.message.reply_text(
        "📎 Чек получен ✅",
        reply_markup=MAIN_MENU,
    )


# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_flow))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=f"https://tsn-telegram-bot.onrender.com/{WEBHOOK_SECRET}",
        secret_token=WEBHOOK_SECRET,
    )


if __name__ == "__main__":
    main()
