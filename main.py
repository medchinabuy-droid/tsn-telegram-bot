import os
import json
import logging
import datetime
from io import BytesIO

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# -------------------- НАСТРОЙКИ --------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))

FOLDER_NAME = "TSN_CHECKS"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-platform",
]

logging.basicConfig(level=logging.INFO)

# -------------------- GOOGLE INIT --------------------

creds = Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
gc = gspread.authorize(creds)

spreadsheet = gc.open_by_key(SPREADSHEET_ID)
sheet_users = spreadsheet.worksheet("Лист 1")
sheet_checks = spreadsheet.worksheet("Лист 2")
sheet_req = spreadsheet.worksheet("Реквизиты")

drive_service = build("drive", "v3", credentials=creds)
vision_service = build("vision", "v1", credentials=creds)

# -------------------- UI --------------------

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📎 Загрузить чек"), KeyboardButton("💳 Реквизиты")],
    ],
    resize_keyboard=True,
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📊 Статистика"), KeyboardButton("💰 Долги")],
    ],
    resize_keyboard=True,
)

# -------------------- HELPERS --------------------

def find_user_row(telegram_id):
    records = sheet_users.get_all_records()
    for i, r in enumerate(records, start=2):
        if str(r.get("Telegram_ID")) == str(telegram_id):
            return i, r
    return None, None


def get_drive_folder_id():
    q = f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder'"
    res = drive_service.files().list(q=q).execute()
    if res["files"]:
        return res["files"][0]["id"]

    folder = drive_service.files().create(
        body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
    ).execute()
    return folder["id"]


def upload_to_drive(filename, file_bytes):
    folder_id = get_drive_folder_id()
    media = MediaIoBaseUpload(file_bytes, resumable=True)
    file = drive_service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return file["webViewLink"]


def is_duplicate(file_unique_id):
    records = sheet_checks.get_all_records()
    for r in records:
        if r.get("File_Unique_ID") == file_unique_id:
            return True
    return False


def ocr_from_drive_link(link):
    image = {
        "source": {"imageUri": link}
    }
    response = vision_service.images().annotate(
        body={
            "requests": [{
                "image": image,
                "features": [{"type": "TEXT_DETECTION"}]
            }]
        }
    ).execute()

    texts = response["responses"][0].get("textAnnotations", [])
    if not texts:
        return ""

    return texts[0]["description"]


# -------------------- HANDLERS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    row, user = find_user_row(tg_id)

    if row:
        await update.message.reply_text(
            f"👋 Добро пожаловать, {user.get('ФИО','')}!\n"
            "Используйте меню ⬇️\n"
            "📎 — загрузка чека\n"
            "💳 — реквизиты",
            reply_markup=MAIN_MENU,
        )
        return

    context.user_data["reg"] = {}
    await update.message.reply_text("👋 Добро пожаловать в ТСН «Искона-Парк»\n\nВведите ФИО:")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    tg_id = update.effective_user.id

    if tg_id in ADMIN_IDS:
        if text == "📊 Статистика":
            rows = sheet_checks.get_all_records()
            await update.message.reply_text(
                f"📊 Всего чеков: {len(rows)}",
                reply_markup=ADMIN_MENU,
            )
            return

        if text == "💰 Долги":
            rows = sheet_users.get_all_records()
            msg = "💰 Долги:\n\n"
            for r in rows:
                if r.get("Сумма"):
                    msg += f"🏠 {r.get('Участок')} — {r.get('ФИО')} — {r.get('Сумма')} ₽\n"
            await update.message.reply_text(msg or "Нет долгов", reply_markup=ADMIN_MENU)
            return

    if "reg" in context.user_data:
        reg = context.user_data["reg"]

        if "fio" not in reg:
            reg["fio"] = text
            await update.message.reply_text(
                "📞 Введите телефон\n👉 пример: +79261234567"
            )
            return

        if "phone" not in reg:
            reg["phone"] = text
            await update.message.reply_text("🏠 Номер участка:")
            return

        reg["house"] = text

        sheet_users.append_row([
            reg["house"],
            reg["fio"],
            tg_id,
            reg["phone"],
            "", "", "", "", "", "user", "", "", "", ""
        ])

        context.user_data.pop("reg")

        await update.message.reply_text(
            "✅ Регистрация завершена\n\n"
            "ℹ️ Внизу меню ⬇️\n"
            "📎 Нажмите скрепку для загрузки чека",
            reply_markup=MAIN_MENU,
        )


async def requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = sheet_req.get_all_records()[0]
    await update.message.reply_text(
        "💳 Реквизиты:\n\n"
        f"Банк: {r.get('Банк')}\n"
        f"БИК: {r.get('БИК')}\n"
        f"Счёт: {r.get('Счёт получателя')}\n"
        f"Получатель: {r.get('Получатель')}\n"
        f"ИНН: {r.get('ИНН')}\n"
        f"QR: {r.get('QR_оплата')}",
        reply_markup=MAIN_MENU,
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document or update.message.photo[-1]
    file = await doc.get_file()

    if is_duplicate(doc.file_unique_id):
        await update.message.reply_text("⚠️ Этот чек уже был загружен ранее.")
        return

    data = BytesIO()
    await file.download_to_memory(data)
    data.seek(0)

    link = upload_to_drive(doc.file_unique_id, data)

    ocr_text = ocr_from_drive_link(link)

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        "",
        "",
        "",
        link,
        "",
        datetime.date.today().isoformat(),
        ocr_text,
        "NO",
        doc.file_unique_id,
        datetime.date.today().isoformat(),
        "AUTO",
    ])

    await update.message.reply_text(
        "✅ Чек принят и сохранён\n"
        "📄 OCR выполнен\n"
        "⏸ Напоминания приостановлены на месяц",
        reply_markup=MAIN_MENU,
    )


# -------------------- MAIN --------------------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_handler(MessageHandler(filters.Regex("^💳 Реквизиты$"), requisites))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=WEBHOOK_URL,
    )


if __name__ == "__main__":
    main()
