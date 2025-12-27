import os
import json
import datetime
import re
import pytz

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials


# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

TIMEZONE = pytz.timezone("Europe/Moscow")

# ================== GOOGLE SHEETS ==================
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))

creds = Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 2")     # пользователи + чеки
sheet_notify = sh.worksheet("Лист 1")    # рассылки / статусы
sheet_rekv = sh.worksheet("Реквизиты")   # реквизиты


# ================== HELPERS ==================
def find_user_row(telegram_id):
    ids = sheet_users.col_values(1)
    if str(telegram_id) in ids:
        return ids.index(str(telegram_id)) + 1
    return None


def extract_amount(text: str):
    matches = re.findall(r"\b\d{2,6}[.,]\d{2}\b", text.replace(" ", ""))
    if matches:
        return matches[0].replace(",", ".")
    return ""


# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    row = find_user_row(user.id)
    if not row:
        sheet_users.append_row([
            user.id,
            user.username or "",
            "",
            "",
            "",
            "",
        ])

    keyboard = [
        [InlineKeyboardButton("💳 Реквизиты", callback_data="rekv")],
        [InlineKeyboardButton("📤 Отправить чек", callback_data="send_check")],
    ]

    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Используйте кнопки ниже:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ================== BUTTONS ==================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "rekv":
        rows = sheet_rekv.get_all_values()
        text = "💳 *Реквизиты:*\n\n"
        for r in rows[1:]:
            text += f"{r[0]}: {r[1]}\n"
        await query.message.reply_text(text, parse_mode="Markdown")

    elif query.data == "send_check":
        await query.message.reply_text(
            "📸 Отправьте фото или PDF чека"
        )


# ================== REGISTRATION ==================
async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    row = find_user_row(user_id)
    if not row:
        return

    current = sheet_users.row_values(row)
    while len(current) < 6:
        current.append("")

    if not current[2]:
        current[2] = text
        sheet_users.update(f"C{row}", text)
        await update.message.reply_text("Введите номер дома:")
        return

    if not current[3]:
        current[3] = text
        sheet_users.update(f"D{row}", text)
        await update.message.reply_text("Введите телефон:")
        return

    if not current[4]:
        current[4] = text
        sheet_users.update(f"E{row}", text)
        await update.message.reply_text("Спасибо! Данные сохранены ✅")
        return


# ================== SAVE CHECK ==================
async def save_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = find_user_row(user.id)
    if not row:
        return

    link = ""
    text_ocr = ""

    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        link = file.file_path

    if update.message.document:
        file = await update.message.document.get_file()
        link = file.file_path

    amount = extract_amount(update.message.caption or "")

    sheet_users.update(f"F{row}", link)

    sheet_notify.append_row([
        user.username,
        user.id,
        amount,
        datetime.datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M"),
        "На проверке",
        "",
        "",
    ])

    await update.message.reply_text(
        "✅ Чек получен и отправлен на проверку"
    )


# ================== REMINDERS ==================
async def reminders(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.datetime.now(TIMEZONE).day
    rows = sheet_notify.get_all_values()

    for i, r in enumerate(rows[1:], start=2):
        try:
            day_pay = int(r[4])
        except:
            continue

        if day_pay == today and r[-1] != "Отправлено":
            try:
                await context.bot.send_message(
                    chat_id=r[1],
                    text="🔔 Напоминание об оплате"
                )
                sheet_notify.update(f"H{i}", "Отправлено")
            except:
                sheet_notify.update(f"H{i}", "Ошибка")


# ================== MAIN ==================
def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, save_check))

    app.job_queue.run_daily(
        reminders,
        time=datetime.time(hour=9, minute=0)
    )

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=BASE_URL,
        secret_token=WEBHOOK_SECRET,
    )


if __name__ == "__main__":
    main()
