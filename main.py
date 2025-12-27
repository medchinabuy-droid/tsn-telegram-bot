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
    CallbackQueryHandler,
    ContextTypes,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"

SHEET_MAIN = "Лист 1"
SHEET_USERS = "Лист 2"
SHEET_REKV = "Реквизиты"

PORT = 10000

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

if not all([BOT_TOKEN, BASE_URL, WEBHOOK_SECRET, GOOGLE_CREDS_JSON]):
    raise RuntimeError("❌ Не заданы переменные окружения")

# ================= ЛОГИ =================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ================= GOOGLE =================

creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDS_JSON),
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_main = sh.worksheet(SHEET_MAIN)
sheet_users = sh.worksheet(SHEET_USERS)
sheet_rekv = sh.worksheet(SHEET_REKV)

drive = build("drive", "v3", credentials=creds)

# ================= ВСПОМОГАТЕЛЬНОЕ =================

def get_user_from_sheet2(tg_id):
    col = sheet_users.col_values(1)  # telegram_id
    headers = sheet_users.row_values(1)
    for i, v in enumerate(col[1:], start=2):
        if str(v) == str(tg_id):
            row = sheet_users.row_values(i)
            return i, dict(zip(headers, row))
    return None, None

def get_user_from_sheet1(tg_id):
    col = sheet_main.col_values(3)  # Telegram_ID
    headers = sheet_main.row_values(1)
    for i, v in enumerate(col[1:], start=2):
        if str(v) == str(tg_id):
            row = sheet_main.row_values(i)
            return i, dict(zip(headers, row))
    return None, None

def is_admin(tg_id):
    _, row = get_user_from_sheet1(tg_id)
    return row and str(row.get("Роль", "")).lower() == "админ"

def main_keyboard(admin=False):
    buttons = [
        [InlineKeyboardButton("💳 Реквизиты", callback_data="rekv")],
        [InlineKeyboardButton("📤 Загрузить чек", callback_data="upload")],
        [InlineKeyboardButton("📊 Статус оплаты", callback_data="status")]
    ]
    if admin:
        buttons.append([InlineKeyboardButton("🛠 Админ", callback_data="admin")])
    return InlineKeyboardMarkup(buttons)

def get_or_create_folder(tg_id):
    query = f"name='checks_{tg_id}' and mimeType='application/vnd.google-apps.folder'"
    res = drive.files().list(q=query).execute()
    if res.get("files"):
        return res["files"][0]["id"]

    folder = drive.files().create(body={
        "name": f"checks_{tg_id}",
        "mimeType": "application/vnd.google-apps.folder"
    }).execute()
    return folder["id"]

# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user

    row_i, row = get_user_from_sheet2(tg.id)

    if not row:
        context.user_data["reg"] = True
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\n"
            "Отправьте ОДНИМ сообщением:\n"
            "ФИО\nНомер участка\nТелефон"
        )
        return

    await update.message.reply_text(
        "✅ Бот готов к работе",
        reply_markup=main_keyboard(is_admin(tg.id))
    )

# ================= РЕГИСТРАЦИЯ =================

async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("reg") is not True:
        return

    parts = update.message.text.strip().split("\n")
    if len(parts) < 3:
        await update.message.reply_text("❌ Нужно 3 строки")
        return

    sheet_users.append_row([
        update.effective_user.id,
        update.effective_user.username or "",
        parts[0],
        parts[1],
        parts[2],
        ""
    ])

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Регистрация завершена",
        reply_markup=main_keyboard(is_admin(update.effective_user.id))
    )

# ================= КНОПКИ =================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tg_id = q.from_user.id

    if q.data == "rekv":
        rows = sheet_rekv.get_all_records()
        if not rows:
            await q.message.reply_text("❌ Реквизиты не заполнены")
            return

        text = ""
        for r in rows:
            for k, v in r.items():
                if v:
                    text += f"{k}: {v}\n"
        await q.message.reply_text(text)

    elif q.data == "upload":
        context.user_data["wait_check"] = True
        await q.message.reply_text("📎 Отправьте фото или PDF чека")

    elif q.data == "status":
        _, row = get_user_from_sheet1(tg_id)
        if not row:
            await q.message.reply_text("❌ Нет данных")
            return
        await q.message.reply_text(
            f"📊 Статус: {row.get('Статус')}\n"
            f"💰 Сумма: {row.get('Сумма')}"
        )

    elif q.data == "admin" and is_admin(tg_id):
        await q.message.reply_text("🛠 Админ-панель\n/report — отчёт")

# ================= ЧЕКИ =================

async def save_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("wait_check") is not True:
        return

    tg = update.effective_user
    row_i, _ = get_user_from_sheet2(tg.id)
    if not row_i:
        await update.message.reply_text("❌ Сначала /start")
        return

    file = update.message.document or update.message.photo[-1]
    f = await file.get_file()
    data = await f.download_as_bytearray()

    folder_id = get_or_create_folder(tg.id)

    media = MediaIoBaseUpload(BytesIO(data), resumable=True)
    uploaded = drive.files().create(
        body={"name": file.file_unique_id, "parents": [folder_id]},
        media_body=media
    ).execute()

    link = f"https://drive.google.com/file/d/{uploaded['id']}"
    sheet_users.update_cell(row_i, 6, link)

    context.user_data.clear()
    await update.message.reply_text("✅ Чек принят и сохранён")

# ================= НАПОМИНАНИЯ =================

async def reminders(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.date.today().day
    rows = sheet_main.get_all_records()

    for i, r in enumerate(rows, start=2):
        try:
            tg_id = r.get("Telegram_ID")
            pay_day = int(r.get("День_оплаты", 0))

            if not tg_id or not pay_day:
                continue

            if pay_day - today in (5, 3, 0):
                await context.bot.send_message(
                    tg_id,
                    f"🔔 Напоминание об оплате\nСумма: {r.get('Сумма')} ₽"
                )
                sheet_main.update_cell(i, 11, f"Отправлено {datetime.date.today()}")

        except Exception as e:
            sheet_main.update_cell(i, 10, str(e))

# ================= ЗАПУСК =================

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration, block=False))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, save_check))

    app.job_queue.run_daily(reminders, time=datetime.time(9, 0))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=f"webhook/{WEBHOOK_SECRET}",
        webhook_url=f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"
    )

if __name__ == "__main__":
    main()
