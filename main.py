import os
import json
import re
import logging
from datetime import datetime
import io

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

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

# ---------------- MENUS ----------------
USER_MENU = ReplyKeyboardMarkup(
    [
        ["🚀 Начать"],
        ["📎 Загрузить чек", "💳 Реквизиты"]
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["🚀 Начать"],
        ["🛠 Админ-панель"],
        ["📎 Загрузить чек", "💳 Реквизиты"]
    ],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["🔍 Долг по участку"],
        ["📊 Статистика"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None

def valid_fio(t): return len(t.split()) >= 2
def valid_phone(t): return bool(re.fullmatch(r"\+7\d{10}", t))
def valid_house(t): return t.isdigit()

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(11)

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{f['id']}"

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    row = find_user_row(uid)

    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    if row:
        fio = sheet_users.cell(row, 2).value or ""
        await update.message.reply_text(
            f"👋 С возвращением, {fio}\n\n⬇️ Используйте меню",
            reply_markup=menu
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать в ТСН «Искона-Парк»\n\nВведите ФИО:",
            reply_markup=menu
        )

# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    # ВСЕГДА логируем
    logger.info(f"TEXT from {uid}: {text}")

    # START
    if text == "🚀 Начать":
        await start(update, context)
        return

    # ADMIN PANEL
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text(
            "🛠 Админ-панель\n\nВыберите действие:",
            reply_markup=ADMIN_PANEL
        )
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "⬇️ Главное меню",
            reply_markup=ADMIN_MENU
        )
        return

    # ADMIN DEBT
    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["admin_wait_house"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("admin_wait_house") and is_admin(uid):
        context.user_data.pop("admin_wait_house", None)
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                await update.message.reply_text(
                    f"🏠 Участок: {text}\n"
                    f"ФИО: {r.get('ФИО')}\n"
                    f"Телефон: {r.get('Телефон')}\n"
                    f"Сумма долга: {r.get('Сумма')}\n"
                    f"Статус: {r.get('Статус')}\n"
                    f"Дата напоминания: {r.get('Дата_напоминания')}",
                    reply_markup=ADMIN_PANEL
                )
                return
        await update.message.reply_text("❌ Участок не найден", reply_markup=ADMIN_PANEL)
        return

    # REQUISITES
    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"💳 Реквизиты:\n\n"
            f"Банк: {r[0]}\n"
            f"БИК: {r[1]}\n"
            f"Счёт: {r[2]}\n"
            f"Получатель: {r[3]}\n"
            f"ИНН: {r[4]}\n\n"
            f"QR:\n{r[5]}",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return

    # UPLOAD CHECK
    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text(
            "📎 Отправьте фото или PDF чека\n\n"
            "👉 Нажмите на 📎 (скрепку) внизу экрана"
        )
        return

    # REGISTRATION FLOW
    step = context.user_data.get("step")

    if step == "fio":
        if not valid_fio(text):
            await update.message.reply_text("Введите ФИО (минимум 2 слова)")
            return
        sheet_users.append_row(["", text, str(uid)])
        context.user_data["step"] = "phone"
        await update.message.reply_text("📞 Телефон в формате +7926XXXXXXXX")
        return

    if step == "phone":
        if not valid_phone(text):
            await update.message.reply_text("❌ Формат: +7926XXXXXXXX")
            return
        row = find_user_row(uid)
        sheet_users.update_cell(row, 4, text)
        context.user_data["step"] = "house"
        await update.message.reply_text("🏠 Номер участка:")
        return

    if step == "house":
        if not valid_house(text):
            await update.message.reply_text("❌ Только цифры")
            return
        row = find_user_row(uid)
        sheet_users.update_cell(row, 1, text)
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Регистрация завершена",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return

    # 🔴 FALLBACK — НИКОГДА НЕ МОЛЧИМ
    await update.message.reply_text(
        "ℹ️ Используйте кнопки меню ⬇️",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# ---------------- FILE ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    if is_duplicate(file.file_unique_id):
        await msg.reply_text("❌ Этот чек уже был загружен")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    link = upload_to_drive(
        data,
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
        file.file_unique_id
    ])

    context.user_data.pop("wait_check", None)
    await msg.reply_text(
        "✅ Чек сохранён",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
