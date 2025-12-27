import os
import json
import logging
import datetime
import re
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
from telegram.error import Forbidden

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"

SHEET_MAIN = "Лист 1"
SHEET_USERS = "Лист 2"
SHEET_REKV = "Реквизиты"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

if not all([BOT_TOKEN, BASE_URL, WEBHOOK_SECRET, GOOGLE_CREDS_JSON]):
    raise RuntimeError("❌ Не заданы переменные окружения")

# ================== ЛОГИ ==================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ================== GOOGLE ==================

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

# ================== ВСПОМОГАТЕЛЬНОЕ ==================

def get_user_main(tg_id):
    rows = sheet_main.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    return None, None


def get_user_users(tg_id):
    rows = sheet_users.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("telegram_id")) == str(tg_id):
            return i, r
    return None, None


def is_admin(row):
    return str(row.get("Роль", "")).lower() == "админ"


def main_keyboard(admin=False):
    kb = [
        [InlineKeyboardButton("💳 Реквизиты", callback_data="rekv")],
        [InlineKeyboardButton("📤 Загрузить чек", callback_data="upload")],
        [InlineKeyboardButton("📊 Статус оплаты", callback_data="status")]
    ]
    if admin:
        kb.append([InlineKeyboardButton("🛠 Админ", callback_data="admin")])
    return InlineKeyboardMarkup(kb)

# ================== START / РЕГИСТРАЦИЯ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    row_i, row = get_user_users(tg.id)

    if not row:
        context.user_data["reg"] = True
        await update.message.reply_text(
            "👋 Введите одним сообщением:\nФИО, номер участка, телефон"
        )
        return

    _, main_row = get_user_main(tg.id)
    await update.message.reply_text(
        "✅ Бот готов к работе",
        reply_markup=main_keyboard(is_admin(main_row) if main_row else False)
    )


async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("reg"):
        return

    tg = update.effective_user
    text = update.message.text

    sheet_users.append_row([
        tg.id,
        tg.username or "",
        text,
        "",
        "",
        "",
        "",
        ""
    ])

    context.user_data.clear()
    await update.message.reply_text("✅ Данные сохранены")

# ================== КНОПКИ ==================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "rekv":
        data = sheet_rekv.get_all_records()
        if not data:
            await q.message.reply_text("❌ Реквизиты не заполнены")
            return

        r = data[0]
        text = (
            f"🏦 Банк: {r['Банк']}\n"
            f"🔢 БИК: {r['БИК']}\n"
            f"💳 Счёт: {r['Счёт получателя']}\n"
            f"👤 Получатель: {r['Получатель']}\n"
            f"🧾 ИНН: {r['ИНН']}"
        )
        await q.message.reply_text(text)

    elif q.data == "upload":
        context.user_data["wait_check"] = True
        await q.message.reply_text("📎 Отправьте фото или PDF чека")

    elif q.data == "status":
        _, row = get_user_main(q.from_user.id)
        await q.message.reply_text(f"📊 Статус: {row.get('Статус','—')}")

    elif q.data == "admin":
        await q.message.reply_text("📊 Отчёт формируется… (в разработке)")

# ================== OCR ЧЕКОВ ==================

def extract_sum(text):
    m = re.search(r"(\d{1,5}[.,]\d{2})", text)
    return m.group(1) if m else ""


async def save_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    tg = update.effective_user
    file = update.message.document or update.message.photo[-1]
    f = await file.get_file()
    data = await f.download_as_bytearray()

    folder = drive.files().create(body={
        "name": f"checks_{tg.id}",
        "mimeType": "application/vnd.google-apps.folder"
    }).execute()

    media = MediaIoBaseUpload(BytesIO(data), resumable=True)
    uploaded = drive.files().create(
        body={"name": f"check_{tg.id}.pdf", "parents": [folder["id"]]},
        media_body=media
    ).execute()

    link = f"https://drive.google.com/file/d/{uploaded['id']}"

    row_i, _ = get_user_users(tg.id)
    sheet_users.update(row_i, [
        [tg.id, tg.username or "", "", "", "", link, "", ""]
    ])

    context.user_data.clear()
    await update.message.reply_text("✅ Чек получен и отправлен на проверку")

# ================== НАПОМИНАНИЯ ==================

async def check_payments(app: Application):
    today = datetime.date.today().day
    rows = sheet_main.get_all_records()

    for i, r in enumerate(rows, start=2):
        try:
            pay_day = int(r.get("День_оплаты", 0))
            if pay_day == today and r.get("Напоминание_отправлено") != "Да":
                try:
                    await app.bot.send_message(
                        r["Telegram_ID"],
                        "🔔 Сегодня день оплаты взноса"
                    )
                    sheet_main.update_cell(i, 12, "Да")
                    sheet_main.update_cell(i, 13, str(datetime.datetime.now()))
                    sheet_main.update_cell(i, 14, "Доставлено")
                except Forbidden:
                    sheet_main.update_cell(i, 14, "Бот заблокирован")
        except Exception as e:
            sheet_main.update_cell(i, 11, str(e))

# ================== WEBHOOK ==================

async def on_startup(app: Application):
    await app.bot.set_webhook(
        url=f"{BASE_URL}/webhook",
        secret_token=WEBHOOK_SECRET
    )
    await check_payments(app)

# ================== ЗАПУСК ==================

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
        webhook_url=f"{BASE_URL}/webhook",
        secret_token=WEBHOOK_SECRET,
        on_startup=on_startup
    )

if __name__ == "__main__":
    main()
