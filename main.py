import os
import json
import logging
import datetime
import re

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"

SHEET_MAIN = "Лист 1"
SHEET_REKV = "Лист 2"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ================= ЛОГИ =================

logging.basicConfig(level=logging.INFO)

# ================= GOOGLE =================

creds_dict = json.loads(GOOGLE_CREDS_JSON)

creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)

sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_MAIN)
rekv = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_REKV)

drive = build("drive", "v3", credentials=creds)

# ================= КНОПКИ =================

def main_keyboard(is_admin=False):
    kb = [
        [InlineKeyboardButton("💳 Оплатить", callback_data="pay")],
        [InlineKeyboardButton("📎 Загрузить чек", callback_data="upload")]
    ]
    if is_admin:
        kb.append([InlineKeyboardButton("📊 Долги и 3 дня", callback_data="admin_report")])
    return InlineKeyboardMarkup(kb)


# ================= ПОИСК ПОЛЬЗОВАТЕЛЯ =================

def find_user(tg_id):
    rows = sheet.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    return None, None


# ================= /start =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    row, user = find_user(tg_id)

    if not user:
        await update.message.reply_text(
            "ФИО не найдено.\nВведите ФИО:"
        )
        context.user_data["reg_step"] = "fio"
        return

    is_admin = user.get("Роль") == "админ"

    await update.message.reply_text(
        "Добро пожаловать!",
        reply_markup=main_keyboard(is_admin)
    )


# ================= РЕГИСТРАЦИЯ =================

async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("reg_step")
    text = update.message.text

    if step == "fio":
        context.user_data["fio"] = text
        context.user_data["reg_step"] = "phone"
        await update.message.reply_text("Введите телефон:")
    elif step == "phone":
        context.user_data["phone"] = text
        context.user_data["reg_step"] = "plot"
        await update.message.reply_text("Введите номер участка:")
    elif step == "plot":
        sheet.append_row([
            text,
            context.user_data["fio"],
            update.effective_user.id,
            context.user_data["phone"],
            "", "", "", "", "Новый", "", ""
        ])
        context.user_data.clear()
        await update.message.reply_text("✅ Данные сохранены")


# ================= КНОПКИ =================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    row, user = find_user(q.from_user.id)

    if q.data == "pay":
        rekv_data = rekv.row_values(2)
        text = (
            f"🏦 {rekv_data[0]}\n"
            f"Получатель: {rekv_data[1]}\n"
            f"Счёт: {rekv_data[2]}\n"
            f"Назначение: {rekv_data[3]}"
        )
        await q.message.reply_text(text)

    elif q.data == "upload":
        await q.message.reply_text("Отправьте фото или PDF чека")

    elif q.data == "admin_report" and user.get("Роль") == "админ":
        today = datetime.date.today().day
        rows = sheet.get_all_records()
        msg = "📊 Отчёт:\n"
        for r in rows:
            d = r.get("День_оплаты")
            if d and (int(d) - today in (3, -1)):
                msg += f"{r['ФИО']} | долг/3 дня\n"
        await q.message.reply_text(msg)


# ================= ЧЕКИ =================

async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row, user = find_user(update.effective_user.id)

    file = update.message.document or update.message.photo[-1]
    f = await context.bot.get_file(file.file_id)

    folder_name = f"Участок_{user['Участок']}"
    media = MediaFileUpload(await f.download_as_bytearray(), resumable=False)

    drive.files().create(
        body={"name": f"чек_{update.effective_user.id}"},
        media_body=media
    ).execute()

    sheet.update_cell(row, 9, "На проверке")
    await update.message.reply_text("✅ Чек принят")


# ================= ЗАПУСК =================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_check))

    app.run_polling()


if __name__ == "__main__":
    main()
