import os
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------- ЛОГИ --------------------
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO
)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# -------------------- GOOGLE --------------------
creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 2")
sheet_reqs = sh.worksheet("Реквизиты")

# -------------------- КНОПКИ --------------------
MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["📎 Загрузить чек"],
        ["💳 Реквизиты"]
    ],
    resize_keyboard=True
)

# -------------------- ВСПОМОГАТЕЛЬНО --------------------
def find_user_row(telegram_id: int):
    records = sheet_users.get_all_records()
    for idx, row in enumerate(records, start=2):
        if str(row.get("telegram_id")) == str(telegram_id):
            return idx, row
    return None, None


# -------------------- START --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    telegram_id = user.id

    row_idx, row = find_user_row(telegram_id)

    if not row:
        context.user_data.clear()
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать в ТСН «Искона-Парк»\n\nВведите ФИО:",
        )
        return

    # есть пользователь — проверяем, что заполнено
    if not row.get("Телефон"):
        context.user_data["step"] = "phone"
        await update.message.reply_text("📞 Укажите номер телефона:")
        return

    if not row.get("Дом"):
        context.user_data["step"] = "house"
        await update.message.reply_text("🏠 Укажите номер участка:")
        return

    await update.message.reply_text(
        "ℹ️ Используйте меню ⬇️\n\n"
        "💡 Если меню скрыто — нажмите кнопку справа от поля ввода 😊",
        reply_markup=MAIN_MENU
    )


# -------------------- ТЕКСТОВЫЙ ОБРАБОТЧИК --------------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    step = context.user_data.get("step")

    # КНОПКИ
    if text == "💳 Реквизиты":
        await send_requisites(update)
        return

    if text == "📎 Загрузить чек":
        await update.message.reply_text(
            "📎 Отправьте фото или PDF чека.\n"
            "⚠️ Один чек — один платёж."
        )
        return

    # РЕГИСТРАЦИЯ
    if step == "fio":
        context.user_data["fio"] = text
        context.user_data["step"] = "phone"
        await update.message.reply_text("📞 Введите телефон:")
        return

    if step == "phone":
        context.user_data["phone"] = text
        context.user_data["step"] = "house"
        await update.message.reply_text("🏠 Номер участка:")
        return

    if step == "house":
        user = update.effective_user
        sheet_users.append_row([
            user.id,
            user.username or "",
            context.user_data.get("fio"),
            text,
            context.user_data.get("phone"),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            ""
        ])
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Регистрация завершена",
            reply_markup=MAIN_MENU
        )
        return

    await update.message.reply_text(
        "ℹ️ Используйте меню ⬇️",
        reply_markup=MAIN_MENU
    )


# -------------------- РЕКВИЗИТЫ --------------------
async def send_requisites(update: Update):
    row = sheet_reqs.get_all_records()[0]
    text = (
        "💳 Реквизиты:\n\n"
        f"🏦 Банк: {row.get('Банк')}\n"
        f"📄 Получатель: {row.get('Получатель')}\n"
        f"💼 Счёт: {row.get('Счёт получателя')}\n"
        f"🧾 ИНН: {row.get('ИНН')}\n"
    )
    await update.message.reply_text(text, reply_markup=MAIN_MENU)


# -------------------- ЧЕКИ --------------------
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    file = message.document or message.photo[-1]
    file_unique_id = file.file_unique_id

    # проверка дубля
    records = sheet_users.get_all_records()
    for r in records:
        if r.get("File_Unique_ID") == file_unique_id:
            await message.reply_text(
                "⚠️ Этот чек уже был загружен ранее."
            )
            return

    row_idx, _ = find_user_row(message.from_user.id)

    if not row_idx:
        await message.reply_text("❌ Сначала выполните регистрацию через /start")
        return

    sheet_users.update_cell(row_idx, 11, "Загружен чек")
    sheet_users.update_cell(row_idx, 12, datetime.now().strftime("%Y-%m-%d %H:%M"))
    sheet_users.update_cell(row_idx, 13, file_unique_id)

    await message.reply_text(
        "✅ Чек принят.\n"
        "После проверки долг будет закрыт.",
        reply_markup=MAIN_MENU
    )


# -------------------- MAIN --------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ВАЖНО: порядок!
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=os.environ.get("RENDER_EXTERNAL_URL")
    )


if __name__ == "__main__":
    main()
