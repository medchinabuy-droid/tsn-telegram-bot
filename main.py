import os
import json
import logging

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
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

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))

# ---------------- GOOGLE SHEETS ----------------
creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDENTIALS_JSON),
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
sheet = sh.worksheet("Лист 1")

# ---------------- HELPERS ----------------
def find_user_row(telegram_id: int):
    ids = sheet.col_values(3)  # Telegram_ID
    for idx, value in enumerate(ids[1:], start=2):
        if str(value) == str(telegram_id):
            return idx
    return None


def menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📎 Загрузить чек")],
            [KeyboardButton("💳 Реквизиты")],
        ],
        resize_keyboard=True,
    )


async def show_menu(update: Update):
    await update.message.reply_text(
        "✅ Данные сохранены\n\n"
        "ℹ️ Внизу экрана есть меню 📎\n"
        "Через него можно:\n"
        "• 📎 Загрузить чек\n"
        "• 💳 Получить реквизиты",
        reply_markup=menu_keyboard(),
    )

# ---------------- STATES ----------------
ASK_FIO = "ASK_FIO"
ASK_PHONE = "ASK_PHONE"
ASK_HOUSE = "ASK_HOUSE"

# ---------------- /start ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = find_user_row(user.id)

    context.user_data.clear()

    if not row:
        context.user_data["new"] = True
        await update.message.reply_text("Введите ФИО:")
        context.user_data["state"] = ASK_FIO
        return

    data = sheet.row_values(row)
    context.user_data["row"] = row

    fio = data[1] if len(data) > 1 else ""
    phone = data[3] if len(data) > 3 else ""
    house = data[0] if len(data) > 0 else ""

    if not fio:
        await update.message.reply_text("Введите ФИО:")
        context.user_data["state"] = ASK_FIO
        return

    if not phone:
        await update.message.reply_text(
            "Введите телефон\n👉 пример: +79260000000"
        )
        context.user_data["state"] = ASK_PHONE
        return

    if not house:
        await update.message.reply_text("Введите номер дома:")
        context.user_data["state"] = ASK_HOUSE
        return

    await show_menu(update)

# ---------------- TEXT HANDLER ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = update.message.text

    if state == ASK_FIO:
        context.user_data["fio"] = text
        await update.message.reply_text(
            "Введите телефон\n👉 пример: +79260000000"
        )
        context.user_data["state"] = ASK_PHONE
        return

    if state == ASK_PHONE:
        context.user_data["phone"] = text
        await update.message.reply_text("Введите номер дома:")
        context.user_data["state"] = ASK_HOUSE
        return

    if state == ASK_HOUSE:
        fio = context.user_data.get("fio")
        phone = context.user_data.get("phone")
        house = text
        telegram_id = update.effective_user.id

        if context.user_data.get("new"):
            sheet.append_row([
                house,
                fio,
                telegram_id,
                phone,
            ])
        else:
            row = context.user_data["row"]
            sheet.update_cell(row, 1, house)
            sheet.update_cell(row, 2, fio)
            sheet.update_cell(row, 4, phone)

        context.user_data.clear()
        await show_menu(update)
        return

# ---------------- MENU ACTIONS ----------------
async def menu_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "📎 Загрузить чек":
        await update.message.reply_text(
            "📎 Нажмите на скрепку внизу экрана и загрузите фото или PDF чека"
        )

    elif update.message.text == "💳 Реквизиты":
        await update.message.reply_text("Реквизиты будут показаны здесь")

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^(📎 Загрузить чек|💳 Реквизиты)$"), menu_actions))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
    )

if __name__ == "__main__":
    main()
