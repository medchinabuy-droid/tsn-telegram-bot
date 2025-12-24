import os
import json
import logging
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ================= ЛОГИ =================

logging.basicConfig(level=logging.INFO)

# ================= GOOGLE =================

creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDS_JSON),
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

users_sheet = sh.worksheet("Лист 1")
checks_sheet = sh.worksheet("Чеки")

drive = build("drive", "v3", credentials=creds)

# ================= DRIVE =================

ROOT_FOLDER_NAME = "TSN_CHECKS"

def get_or_create_folder(name, parent=None):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
    if parent:
        q += f" and '{parent}' in parents"

    res = drive.files().list(q=q, fields="files(id)").execute()
    if res["files"]:
        return res["files"][0]["id"]

    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent] if parent else []
    }
    return drive.files().create(body=body, fields="id").execute()["id"]

ROOT_FOLDER_ID = get_or_create_folder(ROOT_FOLDER_NAME)

# ================= КНОПКИ =================

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Реквизиты", callback_data="reqs")],
        [InlineKeyboardButton("📤 Загрузить чек", callback_data="upload")],
        [InlineKeyboardButton("📊 Мой статус", callback_data="status")]
    ])

# ================= НАПОМИНАНИЯ =================

def check_payments():
    today = datetime.now().date()
    users = users_sheet.get_all_records()
    checks = checks_sheet.get_all_records()

    for u in users:
        if not u["telegram_id"]:
            continue

        pay_day = int(u["День_оплаты"])
        user_id = int(u["telegram_id"])

        pay_date = today.replace(day=pay_day)
        reminder_date = pay_date - timedelta(days=5)
        debt_date = pay_date + timedelta(days=1)

        paid = any(
            str(r["telegram_id"]) == str(user_id) and
            r["Дата"].startswith(today.strftime("%Y-%m"))
            for r in checks
        )

        if today == reminder_date:
            yield user_id, "🔔 Напоминание: скоро срок оплаты взноса"

        if today == debt_date and not paid:
            yield user_id, "❗ Образовался долг. Пожалуйста, оплатите взнос."

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в бот ТСН",
        reply_markup=main_keyboard()
    )

    for uid, text in check_payments():
        await context.bot.send_message(uid, text)

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    users = users_sheet.get_all_records()
    user = next(u for u in users if str(u["telegram_id"]) == str(user_id))

    if query.data == "reqs":
        await query.message.reply_text("💳 Реквизиты ТСН:\nXXXX XXXX XXXX")

    if query.data == "status":
        checks = checks_sheet.get_all_records()
        last = next((c for c in reversed(checks)
                    if str(c["telegram_id"]) == str(user_id)), None)

        if not last:
            await query.message.reply_text("❌ Чеков нет")
        else:
            await query.message.reply_text(f"📄 Статус: {last['Статус']}")

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    user_id = update.effective_user.id

    user = next(u for u in users_sheet.get_all_records()
                if str(u["telegram_id"]) == str(user_id))

    now = datetime.now()
    month = now.strftime("%Y-%m")

    house_folder = get_or_create_folder(f"Дом_{user['Дом']}", ROOT_FOLDER_ID)
    month_folder = get_or_create_folder(month, house_folder)

    file = await photo.get_file()
    data = await file.download_as_bytearray()

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg")
    name = f"check_{user_id}_{int(now.timestamp())}.jpg"

    uploaded = drive.files().create(
        body={"name": name, "parents": [month_folder]},
        media_body=media,
        fields="id"
    ).execute()

    amount = int(user["Сумма"]) - int(user.get("Скидка", 0) or 0)

    checks_sheet.append_row([
        int(now.timestamp()),
        user_id,
        user["Дом"],
        now.strftime("%Y-%m-%d %H:%M"),
        amount,
        "На проверке",
        f"https://drive.google.com/file/d/{uploaded['id']}"
    ])

    await update.message.reply_text("✅ Чек загружен", reply_markup=main_keyboard())

# ================= ЗАПУСК =================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.COMMAND, start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.PHOTO, upload_check))

    app.run_polling()

if __name__ == "__main__":
    main()
