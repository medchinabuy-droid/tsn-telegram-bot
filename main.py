import os
import json
import logging
import datetime
from io import BytesIO

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
BASE_URL = os.environ.get("BASE_URL")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"
SHEET_USERS = "Лист 1"
SHEET_REKV = "Лист 2"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

if not BOT_TOKEN or not BASE_URL or not GOOGLE_CREDS_JSON:
    raise RuntimeError("❌ Не заданы переменные окружения")

# ================= ЛОГИ =================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ================= GOOGLE =================

creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
users_sheet = sh.worksheet(SHEET_USERS)
rekv_sheet = sh.worksheet(SHEET_REKV)

drive = build("drive", "v3", credentials=creds)

# ================= ВСПОМОГАТЕЛЬНОЕ =================

def get_user_row(tg_id):
    rows = users_sheet.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    return None, None

def is_admin(row):
    return str(row.get("Роль", "")).lower() == "админ"

def main_keyboard(is_admin=False):
    buttons = [
        [InlineKeyboardButton("💳 Реквизиты", callback_data="rekv")],
        [InlineKeyboardButton("📤 Загрузить чек", callback_data="upload")],
        [InlineKeyboardButton("📊 Статус оплаты", callback_data="status")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("🛠 Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(buttons)

# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    row_i, row = get_user_row(tg_id)

    if not row:
        context.user_data["reg"] = True
        await update.message.reply_text(
            "👋 Добрый день!\nВведите ФИО, номер участка и телефон одним сообщением."
        )
        return

    await update.message.reply_text(
        "✅ Бот готов к работе",
        reply_markup=main_keyboard(is_admin(row))
    )

# ================= РЕГИСТРАЦИЯ =================

async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("reg"):
        return

    text = update.message.text
    users_sheet.append_row([
        "", text, update.effective_user.id, "", "", "", "", "На проверке", ""
    ])
    context.user_data.clear()

    await update.message.reply_text(
        "✅ Данные сохранены",
        reply_markup=main_keyboard()
    )

# ================= КНОПКИ =================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    row_i, row = get_user_row(q.from_user.id)

    if q.data == "rekv":
        data = rekv_sheet.get_all_records()
        text = "\n".join([f"{r['Название']}: {r['Значение']}" for r in data])
        await q.message.reply_text(text)

    elif q.data == "status":
        await q.message.reply_text(f"📊 Статус: {row.get('Статус')}")

    elif q.data == "upload":
        context.user_data["wait_check"] = True
        await q.message.reply_text("📤 Отправьте фото или PDF чека")

    elif q.data == "admin" and is_admin(row):
        await q.message.reply_text("🛠 Используйте /accept ID или /reject ID")

# ================= ЧЕКИ =================

async def save_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    file = update.message.document or update.message.photo[-1]
    tg_id = update.effective_user.id

    f = await file.get_file()
    data = await f.download_as_bytearray()

    now = datetime.datetime.now().strftime("%Y-%m")
    folder_name = f"Чеки/{tg_id}/{now}"

    folder_id = None
    q = drive.files().list(q=f"name='{folder_name}'").execute()
    if q["files"]:
        folder_id = q["files"][0]["id"]
    else:
        folder = drive.files().create(body={
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder"
        }).execute()
        folder_id = folder["id"]

    media = MediaIoBaseUpload(BytesIO(data), resumable=True)
    drive.files().create(
        body={"name": f"check_{tg_id}.jpg", "parents": [folder_id]},
        media_body=media
    ).execute()

    users_sheet.update_cell(get_user_row(tg_id)[0], 8, "На проверке")

    context.user_data.clear()
    await update.message.reply_text("✅ Чек отправлен на проверку")

# ================= АДМИН =================

async def accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row_i = int(context.args[0])
    users_sheet.update_cell(row_i, 8, "Принят")
    await update.message.reply_text("✅ Принято")

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row_i = int(context.args[0])
    users_sheet.update_cell(row_i, 8, "Отклонён")
    await update.message.reply_text("❌ Отклонено")

# ================= WEBHOOK =================

async def on_startup(app):
    await app.bot.set_webhook(f"{BASE_URL}/webhook")

# ================= ЗАПУСК =================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("accept", accept))
    app.add_handler(CommandHandler("reject", reject))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, save_check))

    app.run_webhook(
        listen="0.0.0.0",
        port=10000,
        webhook_url=f"{BASE_URL}/webhook",
        on_startup=on_startup
    )

if __name__ == "__main__":
    main()
