import os
import json
import logging
import asyncio
from datetime import datetime

from aiohttp import web
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
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

# -------------------- НАСТРОЙКИ --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- GOOGLE --------------------
creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
if not creds_json:
    raise RuntimeError("❌ GOOGLE_CREDENTIALS_JSON отсутствует")

creds_dict = json.loads(creds_json)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)

gc = gspread.authorize(credentials)
spreadsheet = gc.open(SPREADSHEET_NAME)

sheet_users = spreadsheet.worksheet("Реестр")
sheet_checks = spreadsheet.worksheet("Чеки")
sheet_req = spreadsheet.worksheet("Реквизиты")

drive_service = build("drive", "v3", credentials=credentials)

# -------------------- UI --------------------
START_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("🚀 Начать")]],
    resize_keyboard=True
)

REQ_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📄 Реквизиты")]],
    resize_keyboard=True
)

# -------------------- ВСПОМОГАТЕЛЬНЫЕ --------------------
def find_user_row(tg_id):
    rows = sheet_users.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    return None, None

def upload_to_drive(file_bytes, filename, mime):
    folder_name = "TSN_CHECKS"

    folders = drive_service.files().list(
        q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
    ).execute().get("files", [])

    if folders:
        folder_id = folders[0]["id"]
    else:
        folder = drive_service.files().create(
            body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        ).execute()
        folder_id = folder["id"]

    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime)
    file = drive_service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    return file["webViewLink"]

# -------------------- ХЭНДЛЕРЫ --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать!\nНажмите «🚀 Начать»",
        reply_markup=START_KB
    )

async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    row, data = find_user_row(tg_id)

    if data:
        await update.message.reply_text(
            f"👋 Здравствуйте, {data.get('ФИО')}!\n\n"
            "📎 Прикрепите фото или PDF чека.\n"
            "👉 Нажмите на значок 📎",
            reply_markup=REQ_KB
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text("✍️ Укажите ФИО:")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    tg_id = update.effective_user.id

    # ---- Админ ----
    if tg_id in ADMIN_IDS and text.isdigit():
        rows = sheet_users.get_all_records()
        for r in rows:
            if str(r.get("Участок")) == text:
                if r.get("Статус") != "Оплачено":
                    await update.message.reply_text(
                        f"🏠 Участок {text}\n"
                        f"👤 {r.get('ФИО')}\n"
                        f"💰 Долг: {r.get('Сумма')}\n"
                        f"📅 Дата: {r.get('Дата')}\n"
                        f"🔔 Напоминание: {r.get('Дата_напоминания')}\n"
                        f"📌 Статус: {r.get('Статус')}"
                    )
                else:
                    await update.message.reply_text("✅ Задолженностей нет")
                return
        await update.message.reply_text("❌ Участок не найден")
        return

    step = context.user_data.get("step")

    if step == "fio":
        context.user_data["fio"] = text
        context.user_data["step"] = "plot"
        await update.message.reply_text("🏠 Укажите номер участка:")
    elif step == "plot":
        context.user_data["plot"] = text
        context.user_data["step"] = "phone"
        await update.message.reply_text(
            "📞 Укажите номер телефона\nПример: +79261234567"
        )
    elif step == "phone":
        if not text.startswith("+7") or len(text) < 12:
            await update.message.reply_text(
                "❌ Неверный формат\nПример: +79261234567"
            )
            return

        sheet_users.append_row([
            context.user_data["plot"],
            context.user_data["fio"],
            tg_id,
            text,
            "", "", "", datetime.now().strftime("%d.%m.%Y"),
            "Ожидает",
            "Пользователь",
            "", "", "", "", ""
        ])

        await update.message.reply_text(
            "✅ Данные сохранены.\nСпасибо!\n\n"
            "📎 Теперь прикрепите чек.\n"
            "👉 Нажмите на 📎",
            reply_markup=REQ_KB
        )
        context.user_data.clear()

    elif text == "📄 Реквизиты":
        data = sheet_req.get_all_records()[0]
        await update.message.reply_text(data["Текст"])
        await update.message.reply_text(data["QR_URL"])
    else:
        await update.message.reply_text(
            "🤖 Я не понял сообщение.\nНажмите «🚀 Начать»",
            reply_markup=START_KB
        )

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document or update.message.photo[-1]
    file = await doc.get_file()

    # проверка дубля
    rows = sheet_checks.col_values(2)
    if doc.file_unique_id in rows:
        await update.message.reply_text("⚠️ Этот чек уже был загружен")
        return

    file_bytes = await file.download_as_bytearray()
    link = upload_to_drive(
        file_bytes,
        doc.file_name or "check.jpg",
        doc.mime_type or "image/jpeg"
    )

    sheet_checks.append_row([
        update.effective_user.id,
        doc.file_unique_id,
        link,
        datetime.now().strftime("%d.%m.%Y %H:%M")
    ])

    await update.message.reply_text(
        "✅ Чек принят!\nСпасибо за оплату 🙌"
    )

# -------------------- WEBHOOK SERVER --------------------
async def health(request):
    return web.Response(text="OK")

async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Начать$"), begin))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, file_handler))

    await app.initialize()
    await app.bot.set_webhook(WEBHOOK_URL)
    await app.start()

    web_app = web.Application()
    web_app.router.add_get("/", health)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"🚀 HTTP сервер запущен на порту {PORT}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
