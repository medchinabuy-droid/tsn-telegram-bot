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

SPREADSHEET_ID = "ВАШ_ID_ТАБЛИЦЫ"

SHEET_MAIN = "Лист 1"   # рассылки
SHEET_USERS = "Лист 2"  # пользователи + чеки
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

def today():
    return datetime.date.today()

def get_user_from_sheet2(tg_id):
    rows = sheet_users.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("telegram_id")) == str(tg_id):
            return i, r
    return None, None

def get_user_from_sheet1(tg_id):
    rows = sheet_main.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    return None, None

def is_admin(tg_id):
    _, row = get_user_from_sheet1(tg_id)
    return row and str(row.get("Роль", "")).lower() == "админ"

def main_keyboard(is_admin_user=False):
    buttons = [
        [InlineKeyboardButton("💳 Реквизиты", callback_data="rekv")],
        [InlineKeyboardButton("📤 Загрузить чек", callback_data="upload")],
        [InlineKeyboardButton("📊 Статус оплаты", callback_data="status")]
    ]
    if is_admin_user:
        buttons.append([InlineKeyboardButton("🛠 Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(buttons)

# ================= START / РЕГИСТРАЦИЯ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    row_i, row = get_user_from_sheet2(tg.id)

    if not row:
        context.user_data["reg"] = True
        await update.message.reply_text(
            "👋 Пожалуйста, отправьте одним сообщением:\n\n"
            "ФИО\nНомер участка\nТелефон"
        )
        return

    await update.message.reply_text(
        "✅ Бот готов к работе",
        reply_markup=main_keyboard(is_admin(tg.id))
    )

async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("reg"):
        return

    text = update.message.text.strip()
    parts = text.split("\n")

    fio = parts[0] if len(parts) > 0 else ""
    house = parts[1] if len(parts) > 1 else ""
    phone = parts[2] if len(parts) > 2 else ""

    sheet_users.append_row([
        update.effective_user.id,
        update.effective_user.username or "",
        fio,
        house,
        phone,
        ""
    ])

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Регистрация завершена",
        reply_markup=main_keyboard()
    )

# ================= КНОПКИ =================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    tg_id = q.from_user.id

    if q.data == "rekv":
        data = sheet_rekv.get_all_records()
        if not data:
            await q.message.reply_text("❌ Реквизиты не заполнены")
            return

        text = "\n".join([f"{k}: {v}" for r in data for k, v in r.items() if v])
        await q.message.reply_text(text)

    elif q.data == "status":
        _, row = get_user_from_sheet1(tg_id)
        if not row:
            await q.message.reply_text("❌ Нет данных")
            return
        await q.message.reply_text(
            f"📊 Статус: {row.get('Статус')}\n"
            f"💰 Сумма: {row.get('Сумма')}"
        )

    elif q.data == "upload":
        context.user_data["wait_check"] = True
        await q.message.reply_text("📎 Отправьте фото или PDF чека")

    elif q.data == "admin" and is_admin(tg_id):
        await q.message.reply_text(
            "🛠 Админ-панель\n\n"
            "/report — долги\n"
            "/soon — 3 дня до оплаты"
        )

# ================= ЧЕКИ + OCR =================

async def save_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    tg = update.effective_user
    file = update.message.document or update.message.photo[-1]

    tg_row_i, tg_row = get_user_from_sheet2(tg.id)
    if not tg_row:
        await update.message.reply_text("❌ Сначала /start")
        return

    f = await file.get_file()
    data = await f.download_as_bytearray()

    folder = drive.files().create(body={
        "name": f"checks_{tg.id}",
        "mimeType": "application/vnd.google-apps.folder"
    }).execute()

    media = MediaIoBaseUpload(BytesIO(data), resumable=True)
    uploaded = drive.files().create(
        body={"name": file.file_unique_id, "parents": [folder["id"]]},
        media_body=media
    ).execute()

    link = f"https://drive.google.com/file/d/{uploaded['id']}"

    sheet_users.update_cell(tg_row_i, 6, link)

    context.user_data.clear()
    await update.message.reply_text("✅ Чек сохранён и отправлен на проверку")

# ================= НАПОМИНАНИЯ (CRON) =================

async def reminders(context: ContextTypes.DEFAULT_TYPE):
    rows = sheet_main.get_all_records()
    today_day = today().day

    for i, r in enumerate(rows, start=2):
        try:
            pay_day = int(r.get("День_оплаты", 0))
            tg_id = r.get("Telegram_ID")

            if not tg_id or not pay_day:
                continue

            delta = pay_day - today_day

            if delta in (5, 3, 0):
                text = (
                    "🔔 Напоминание об оплате\n\n"
                    f"Сумма: {r.get('Сумма')} ₽"
                )
                await context.bot.send_message(tg_id, text)

                sheet_main.update_cell(i, 11, f"Отправлено {today()}")

        except Exception as e:
            sheet_main.update_cell(i, 10, str(e))

# ================= ЗАПУСК =================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration))
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
