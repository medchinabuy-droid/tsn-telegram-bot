import os
import json
import logging
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, CallbackQueryHandler, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ["BASE_URL"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"

SHEET_USERS = "Лист 1"
SHEET_CHECKS = "Лист 2"
SHEET_REKV = "Реквизиты"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ================= ЛОГИ =================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ================= GOOGLE =================

creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDS_JSON), scopes=SCOPES
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

users_sheet = sh.worksheet(SHEET_USERS)
checks_sheet = sh.worksheet(SHEET_CHECKS)
rekv_sheet = sh.worksheet(SHEET_REKV)

drive = build("drive", "v3", credentials=creds)

# ================= ВСПОМОГАТЕЛЬНОЕ =================

def find_user(tg_id):
    rows = users_sheet.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    return None, None


def is_admin(row):
    return str(row.get("Роль", "")).lower() == "админ"


def keyboard(is_admin=False):
    kb = [
        [InlineKeyboardButton("💳 Реквизиты", callback_data="rekv")],
        [InlineKeyboardButton("📤 Загрузить чек", callback_data="upload")],
        [InlineKeyboardButton("📊 Статус", callback_data="status")]
    ]
    if is_admin:
        kb.append([InlineKeyboardButton("🛠 Админ", callback_data="admin")])
    return InlineKeyboardMarkup(kb)

# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    row_i, row = find_user(tg_id)

    if not row:
        context.user_data["reg"] = True
        await update.message.reply_text(
            "👋 Вас нет в базе.\n"
            "Введите: ФИО, номер дома, телефон\n"
            "одним сообщением."
        )
        return

    await update.message.reply_text(
        "✅ Добро пожаловать",
        reply_markup=keyboard(is_admin(row))
    )

# ================= РЕГИСТРАЦИЯ =================

async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("reg"):
        return

    text = update.message.text
    tg = update.effective_user

    users_sheet.append_row([
        "", text, tg.id, "", "", "", "", "", "Активен", "", ""
    ])

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Данные сохранены",
        reply_markup=keyboard()
    )

# ================= КНОПКИ =================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    row_i, row = find_user(q.from_user.id)

    if q.data == "rekv":
        r = rekv_sheet.get_all_records()[0]
        text = (
            f"💳 *Реквизиты*\n\n"
            f"🏦 Банк: {r['Банк']}\n"
            f"🔢 БИК: {r['БИК']}\n"
            f"💼 Счёт: {r['Счёт получателя']}\n"
            f"👤 Получатель: {r['Получатель']}\n"
            f"🧾 ИНН: {r['ИНН']}"
        )
        await q.message.reply_text(text, parse_mode="Markdown")

    elif q.data == "status":
        await q.message.reply_text(
            f"📊 Статус: {row.get('Статус', '—')}"
        )

    elif q.data == "upload":
        context.user_data["wait_check"] = True
        await q.message.reply_text("📤 Пришлите фото или PDF чека")

# ================= ЧЕК =================

async def save_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    tg = update.effective_user
    row_i, row = find_user(tg.id)

    file = update.message.document or update.message.photo[-1]
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    folder = drive.files().create(
        body={"name": f"Чеки_{tg.id}", "mimeType": "application/vnd.google-apps.folder"}
    ).execute()

    media = MediaIoBaseUpload(BytesIO(data), resumable=True)

    uploaded = drive.files().create(
        body={"name": "check", "parents": [folder["id"]]},
        media_body=media
    ).execute()

    link = f"https://drive.google.com/file/d/{uploaded['id']}"

    checks_sheet.append_row([
        tg.id,
        tg.username,
        row.get("ФИО"),
        row.get("Участок"),
        row.get("Телефон"),
        link
    ])

    users_sheet.update_cell(row_i, 9, "На проверке")
    context.user_data.clear()

    await update.message.reply_text("✅ Чек принят на проверку")

# ================= ЗАПУСК =================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, save_check))

    app.run_webhook(
        listen="0.0.0.0",
        port=10000,
        url_path="webhook",
        webhook_url=f"{BASE_URL}/webhook"
    )

if __name__ == "__main__":
    main()
