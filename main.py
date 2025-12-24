import os
import json
import logging
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

import gspread
from google.oauth2.service_account import Credentials

# ================= НАСТРОЙКИ =================

TOKEN = os.environ.get("TELEGRAM_TOKEN")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"

SHEET_USERS = "Жильцы"
SHEET_CHECKS = "Чеки"

ADMIN_IDS = [123456789]  # <-- ВПИШИ СВОЙ TELEGRAM ID

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ================= ЛОГИ =================

logging.basicConfig(level=logging.INFO)

# ================= GOOGLE =================

creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDS_JSON),
    scopes=SCOPES
)
gc = gspread.authorize(creds)

users_sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_USERS)
checks_sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_CHECKS)

# ================= ВСПОМОГАТЕЛЬНОЕ =================

def get_user_row(tg_id):
    users = users_sheet.get_all_records()
    for i, u in enumerate(users, start=2):
        if str(u["telegram_id"]) == str(tg_id):
            return i, u
    return None, None

# ================= TELEGRAM =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    row, data = get_user_row(user.id)

    if not data:
        users_sheet.append_row([
            user.id,
            user.username,
            "",
            "",
            ""
        ])
        await update.message.reply_text("Введите ФИО:")
        context.user_data["step"] = "fio"
        return

    for field in ["ФИО", "Дом", "Телефон"]:
        if not data[field]:
            await update.message.reply_text(f"Введите {field}:")
            context.user_data["step"] = field
            return

    keyboard = [["💳 Реквизиты", "📎 Загрузить чек"]]
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    step = context.user_data.get("step")

    if step:
        row, _ = get_user_row(user.id)
        col_map = {"ФИО": 3, "Дом": 4, "Телефон": 5}
        users_sheet.update_cell(row, col_map[step], update.message.text)
        context.user_data["step"] = None
        await start(update, context)
        return

    if update.message.text == "💳 Реквизиты":
        await update.message.reply_text(
            "💳 Реквизиты:\n\n"
            "ТСН «_____»\n"
            "Счёт: _____\n"
            "Банк: _____"
        )

    if update.message.text == "📎 Загрузить чек":
        await update.message.reply_text("Отправьте фото чека")

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row, data = get_user_row(user.id)

    check_id = int(datetime.now().timestamp())

    checks_sheet.append_row([
        check_id,
        user.id,
        data["ФИО"],
        data["Дом"],
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        "На проверке"
    ])

    await update.message.reply_text(
        f"✅ Чек принят\nID: {check_id}\nСтатус: На проверке"
    )

async def accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    check_id = context.args[0]
    rows = checks_sheet.get_all_records()

    for i, r in enumerate(rows, start=2):
        if str(r["id"]) == check_id:
            checks_sheet.update_cell(i, 6, "Принят")
            await context.bot.send_message(
                r["telegram_id"],
                f"✅ Ваш чек {check_id} ПРИНЯТ"
            )
            return

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    check_id = context.args[0]
    rows = checks_sheet.get_all_records()

    for i, r in enumerate(rows, start=2):
        if str(r["id"]) == check_id:
            checks_sheet.update_cell(i, 6, "Отклонён")
            await context.bot.send_message(
                r["telegram_id"],
                f"❌ Ваш чек {check_id} ОТКЛОНЁН"
            )
            return

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("accept", accept))
    app.add_handler(CommandHandler("reject", reject))
    app.add_handler(MessageHandler(filters.TEXT, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    app.run_polling()

if __name__ == "__main__":
    main()
