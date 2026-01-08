import os
import json
import logging
from datetime import datetime

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

# -------------------- LOGGING --------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

# -------------------- GOOGLE --------------------
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
gc = gspread.authorize(creds)

sh = gc.open_by_key(SPREADSHEET_ID)
sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_reqs = sh.worksheet("Реквизиты")

# -------------------- KEYBOARD --------------------
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📎 Загрузить чек")],
        [KeyboardButton("💳 Реквизиты")],
    ],
    resize_keyboard=True,
)

# -------------------- HELPERS --------------------
def find_user_row(telegram_id: int):
    col = sheet_users.col_values(3)  # Telegram_ID
    for i, val in enumerate(col, start=1):
        if val == str(telegram_id):
            return i
    return None


def get_user_data(row: int):
    headers = sheet_users.row_values(1)
    values = sheet_users.row_values(row)
    return dict(zip(headers, values))


def ask_missing_fields(data: dict):
    if not data.get("ФИО"):
        return "Введите ФИО:"
    if not data.get("Телефон"):
        return "📞 Укажите телефон\nпример: +79261234567"
    if not data.get("Участок"):
        return "🏠 Укажите номер дома (участка):"
    return None


# -------------------- HANDLERS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    telegram_id = user.id

    await update.message.reply_text(
        "👋 Добро пожаловать в ТСН «Искона-Парк»\n\n"
        "ℹ️ Внизу есть меню с кнопками\n"
        "📎 Чтобы загрузить чек — нажмите на скрепку\n\n"
        "⬇️ Выберите действие",
        reply_markup=MAIN_MENU,
    )

    row = find_user_row(telegram_id)

    if row:
        data = get_user_data(row)
        missing = ask_missing_fields(data)
        if missing:
            context.user_data["edit_row"] = row
            await update.message.reply_text(missing)
        else:
            await update.message.reply_text(
                f"Здравствуйте, {data.get('ФИО')} 👋\n"
                "Выберите действие в меню ⬇️",
                reply_markup=MAIN_MENU,
            )
    else:
        context.user_data["new_user"] = True
        await update.message.reply_text("Введите ФИО:")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user

    # --- New user ---
    if context.user_data.get("new_user"):
        sheet_users.append_row(
            [
                "",                 # Участок
                text,               # ФИО
                str(user.id),       # Telegram_ID
                "", "", "", "", "", "", "", "", "", "", ""
            ]
        )
        context.user_data.clear()
        await update.message.reply_text(
            "📞 Укажите телефон\nпример: +79261234567"
        )
        context.user_data["wait_phone"] = True
        return

    # --- Phone ---
    if context.user_data.get("wait_phone"):
        row = find_user_row(user.id)
        sheet_users.update_cell(row, 4, text)
        context.user_data.clear()
        await update.message.reply_text(
            "🏠 Укажите номер дома (участка):"
        )
        context.user_data["wait_house"] = True
        return

    # --- House ---
    if context.user_data.get("wait_house"):
        row = find_user_row(user.id)
        sheet_users.update_cell(row, 1, text)
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Данные сохранены.\nСпасибо!",
            reply_markup=MAIN_MENU,
        )
        return

    # --- Menu ---
    if text == "💳 Реквизиты":
        reqs = sheet_reqs.get_all_records()[0]
        await update.message.reply_text(
            f"💳 Реквизиты:\n\n"
            f"Банк: {reqs.get('Банк')}\n"
            f"Получатель: {reqs.get('Получатель')}\n"
            f"Счёт: {reqs.get('Счёт получателя')}\n"
            f"ИНН: {reqs.get('ИНН')}",
        )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]
    file_unique_id = photo.file_unique_id

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
            file_unique_id,
        ]
    )

    await update.message.reply_text(
        "📎 Чек получен и сохранён.\nСпасибо!",
        reply_markup=MAIN_MENU,
    )


# -------------------- APP --------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
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
