import os
import json
import re
import logging
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

# ---------------- GOOGLE ----------------
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_info(creds_info, scopes=scopes)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)
sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_reqs = sh.worksheet("Реквизиты")

# ---------------- KEYBOARD ----------------
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🚀 Начать")],
        [KeyboardButton("📎 Загрузить чек")],
        [KeyboardButton("💳 Реквизиты")]
    ],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def find_user_row(tg_id):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(tg_id):
            return i
    return None

def valid_fio(text): return len(text.split()) >= 2
def valid_phone(text): return bool(re.fullmatch(r"\+7\d{10}", text))
def valid_house(text): return text.isdigit()

def check_duplicate(file_unique_id):
    ids = sheet_checks.col_values(11)
    return file_unique_id in ids

def upload_to_drive(file_bytes, filename, mime):
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime)
    file = drive.files().create(
        body={"name": filename, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{file['id']}"

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    row = find_user_row(uid)

    if row:
        fio = sheet_users.cell(row, 2).value
        await update.message.reply_text(
            f"👋 С возвращением, {fio}",
            reply_markup=MAIN_MENU
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать в ТСН «Искона-Парк»\n\nВведите ФИО:",
            reply_markup=MAIN_MENU
        )

# ---------------- DEBT (ADMIN) ----------------
async def debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text("❌ Использование: /debt 81")
        return

    house = context.args[0]
    rows = sheet_users.get_all_records()

    for r in rows:
        if r.get("Участок") == house:
            await update.message.reply_text(
                f"🏠 Участок {house}\n"
                f"ФИО: {r.get('ФИО')}\n"
                f"Телефон: {r.get('Телефон')}\n"
                f"Сумма: {r.get('Сумма')}\n"
                f"Статус: {r.get('Статус')}\n"
                f"Дата напоминания: {r.get('Дата_напоминания')}"
            )
            return

    await update.message.reply_text("❌ Участок не найден")

# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    step = context.user_data.get("step")

    if text == "🚀 Начать":
        await start(update, context)
        return

    if text == "💳 Реквизиты":
        r = sheet_reqs.get_all_records()[0]
        await update.message.reply_text(
            f"💳 Реквизиты:\n\n"
            f"Банк: {r['Банк']}\n"
            f"БИК: {r['БИК']}\n"
            f"Получатель: {r['Получатель']}\n"
            f"Счёт: {r['Счёт получателя']}\n"
            f"ИНН: {r['ИНН']}\n\n"
            f"🔗 QR:\n{r['QR_оплата']}",
            reply_markup=MAIN_MENU
        )
        return

    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text(
            "📎 Нажмите на скрепку 📎 и отправьте фото или PDF",
            reply_markup=MAIN_MENU
        )
        return

    if step == "fio":
        if not valid_fio(text):
            await update.message.reply_text("❌ Минимум имя и фамилия")
            return
        sheet_users.append_row(["", text, str(update.effective_user.id)])
        context.user_data["step"] = "phone"
        await update.message.reply_text("📞 Телефон: +7926XXXXXXXX")
        return

    if step == "phone":
        if not valid_phone(text):
            await update.message.reply_text("❌ Неверный формат телефона")
            return
        row = find_user_row(update.effective_user.id)
        sheet_users.update_cell(row, 4, text)
        context.user_data["step"] = "house"
        await update.message.reply_text("🏠 Номер участка:")
        return

    if step == "house":
        if not valid_house(text):
            await update.message.reply_text("❌ Только цифры")
            return
        row = find_user_row(update.effective_user.id)
        sheet_users.update_cell(row, 1, text)
        context.user_data.clear()
        await update.message.reply_text("✅ Регистрация завершена", reply_markup=MAIN_MENU)

# ---------------- FILE ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document
    file_unique_id = file.file_unique_id

    if check_duplicate(file_unique_id):
        await msg.reply_text("❌ Этот чек уже был загружен ранее", reply_markup=MAIN_MENU)
        return

    tg_file = await file.get_file()
    file_bytes = await tg_file.download_as_bytearray()

    link = upload_to_drive(
        file_bytes,
        f"check_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    uid = update.effective_user.id
    row = find_user_row(uid)

    fio = house = phone = ""
    if row:
        fio = sheet_users.cell(row, 2).value
        house = sheet_users.cell(row, 1).value
        phone = sheet_users.cell(row, 4).value

    sheet_checks.append_row([
        uid,
        update.effective_user.username or "",
        fio,
        house,
        phone,
        link,
        "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "",
        file_unique_id
    ])

    context.user_data.pop("wait_check", None)
    await msg.reply_text("✅ Чек сохранён", reply_markup=MAIN_MENU)

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("debt", debt))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
