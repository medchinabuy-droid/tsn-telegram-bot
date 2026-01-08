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

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

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
)

# ---------------- HELPERS ----------------
def find_user_row(tg_id: int):
    ids = sheet_users.col_values(3)  # Telegram_ID
    for i, v in enumerate(ids, start=1):
        if v == str(tg_id):
            return i
    return None

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.effective_user.id

    await update.message.reply_text(
        "👋 Добро пожаловать в ТСН «Искона-Парк»",
        reply_markup=MAIN_MENU,
    )

    row = find_user_row(user_id)
    if not row:
        context.user_data["step"] = "fio"
        await update.message.reply_text("Введите ФИО:")
    else:
        await update.message.reply_text("Вы уже зарегистрированы ✅")

# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    step = context.user_data.get("step")

    # --- КНОПКИ ---
    if text == "📎 Загрузить чек":
        await update.message.reply_text(
            "📎 Пожалуйста, нажмите на скрепку 📎\n"
            "и отправьте фото или PDF чека",
            reply_markup=MAIN_MENU,
        )
        context.user_data["wait_check"] = True
        return

    if text == "💳 Реквизиты":
        rec = sheet_reqs.get_all_records()[0]
        await update.message.reply_text(
            f"💳 Реквизиты:\n\n"
            f"Банк: {rec.get('Банк','')}\n"
            f"БИК: {rec.get('БИК','')}\n"
            f"Получатель: {rec.get('Получатель','')}\n"
            f"Счёт: {rec.get('Счёт получателя','')}\n"
            f"ИНН: {rec.get('ИНН','')}",
            reply_markup=MAIN_MENU,
        )
        return

    # --- РЕГИСТРАЦИЯ ---
    if step == "fio":
        sheet_users.append_row(
            ["", text, str(user.id), "", "", "", "", "", "", "", "", "", "", ""]
        )
        context.user_data["step"] = "phone"
        await update.message.reply_text(
            "📞 Укажите телефон\nпример: +79261234567"
        )
        return

    if step == "phone":
        row = find_user_row(user.id)
        sheet_users.update_cell(row, 4, text)
        context.user_data["step"] = "house"
        await update.message.reply_text("🏠 Номер участка:")
        return

    if step == "house":
        row = find_user_row(user.id)
        sheet_users.update_cell(row, 1, text)
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Регистрация завершена",
            reply_markup=MAIN_MENU,
        )
        return

    await update.message.reply_text(
        "ℹ️ Используйте меню ⬇️",
        reply_markup=MAIN_MENU,
    )

# ---------------- PHOTO / PDF ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    user = update.effective_user
    row = find_user_row(user.id)

    fio = ""
    house = ""
    phone = ""

    if row:
        fio = sheet_users.cell(row, 2).value
        house = sheet_users.cell(row, 1).value
        phone = sheet_users.cell(row, 4).value

    file_unique_id = (
        update.message.photo[-1].file_unique_id
        if update.message.photo
        else update.message.document.file_unique_id
    )

    sheet_checks.append_row(
        [
            user.id,
            user.username or "",
            fio,
            house,
            phone,
            "",
            "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "",
            file_unique_id,
        ]
    )

    context.user_data.pop("wait_check", None)

    await update.message.reply_text(
        "✅ Чек получен и сохранён",
        reply_markup=MAIN_MENU,
    )

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com",
    )

if __name__ == "__main__":
    main()
