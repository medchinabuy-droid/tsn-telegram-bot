import os
import json
import re
import io
import logging
import hashlib
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from telegram.error import Forbidden

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-vision"
]
creds = Credentials.from_service_account_info(creds_info, scopes=scopes)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)
vision = build("vision", "v1", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)
sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_reqs = sh.worksheet("Реквизиты")

# ---------------- MENUS ----------------
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)
ADMIN_MENU = ReplyKeyboardMarkup(
    [["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)
ADMIN_PANEL = ReplyKeyboardMarkup(
    [["🔍 Долг по участку"], ["📊 Статистика"], ["⬅️ Назад"]],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def is_admin(uid): return uid in ADMIN_IDS

def find_user_row(uid):
    for i, v in enumerate(sheet_users.col_values(3), start=1):
        if v == str(uid):
            return i
    return None

def valid_fio(t): return len(t.split()) >= 2
def valid_phone(t): return bool(re.fullmatch(r"\+7\d{10}", t))
def valid_house(t): return t.isdigit()

def sha256(data): return hashlib.sha256(data).hexdigest()

def is_duplicate(file_uid, file_hash):
    uids = sheet_checks.col_values(11)
    hashes = sheet_checks.col_values(12)
    return file_uid in uids or file_hash in hashes

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{f['id']}"

# === NEW: OCR ===
def ocr_check(image_bytes):
    try:
        body = {
            "requests": [{
                "image": {"content": image_bytes.decode("latin1")},
                "features": [{"type": "TEXT_DETECTION"}]
            }]
        }
        res = vision.images().annotate(body=body).execute()
        text = res.get("responses", [{}])[0].get("fullTextAnnotation", {}).get("text", "")
        amount = re.search(r"(\d+[.,]\d{2})", text)
        date = re.search(r"(\d{2}[./]\d{2}[./]\d{4})", text)
        return text, amount.group(1) if amount else "", date.group(1) if date else ""
    except Exception as e:
        logger.error(f"OCR error: {e}")
        return "", "", ""

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    row = find_user_row(uid)
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    if row:
        fio = sheet_users.cell(row, 2).value
        await update.message.reply_text(
            f"👋 С возвращением, {fio}\n\n⬇️ Используйте меню",
            reply_markup=menu
        )
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text(
            "👋 Добро пожаловать\nВведите ФИО:",
            reply_markup=menu
        )

# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text == "🚀 Начать":
        await start(update, context); return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель:", reply_markup=ADMIN_PANEL); return

    if text == "⬅️ Назад":
        await update.message.reply_text("Меню:", reply_markup=ADMIN_MENU); return

    if text == "📊 Статистика" and is_admin(uid):
        users = sheet_users.get_all_records()
        checks = sheet_checks.get_all_records()
        today = datetime.now().strftime("%Y-%m-%d")
        blocked = sum(1 for u in users if u.get("BOT_BLOCKED") == "TRUE")
        checks_today = sum(1 for c in checks if today in c.get("Дата", ""))
        await update.message.reply_text(
            f"📊 Статистика:\n"
            f"👥 Пользователей: {len(users)}\n"
            f"❌ Бот заблокирован: {blocked}\n"
            f"📎 Чеков сегодня: {checks_today}",
            reply_markup=ADMIN_PANEL
        )
        return

    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"💳 Реквизиты:\n{r}",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return

    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text(
            "📎 Отправьте фото или PDF чека\n"
            "ℹ️ Один чек = один платёж\n"
            "⏸ Чек останавливает напоминания на 30 дней"
        )
        return

    # ---- REGISTRATION ----
    step = context.user_data.get("step")

    if step == "fio":
        if not valid_fio(text):
            await update.message.reply_text("Введите ФИО (2 слова)"); return
        sheet_users.append_row(["", text, str(uid)])
        context.user_data["step"] = "phone"
        await update.message.reply_text("Телефон +7XXXXXXXXXX"); return

    if step == "phone":
        if not valid_phone(text):
            await update.message.reply_text("Формат +7XXXXXXXXXX"); return
        row = find_user_row(uid)
        sheet_users.update_cell(row, 4, text)
        context.user_data["step"] = "house"
        await update.message.reply_text("Номер участка:"); return

    if step == "house":
        if not valid_house(text):
            await update.message.reply_text("Только цифры"); return
        row = find_user_row(uid)
        sheet_users.update_cell(row, 1, text)
        context.user_data.clear()
        await update.message.reply_text("✅ Готово", reply_markup=USER_MENU)

# ---------------- FILE ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    file_hash = sha256(data)
    if is_duplicate(file.file_unique_id, file_hash):
        await msg.reply_text("❌ Этот чек уже был загружен"); return

    link = upload_to_drive(data, "check_"+datetime.now().strftime("%Y%m%d_%H%M%S"), file.mime_type)

    ocr_text, ocr_sum, ocr_date = ocr_check(data)

    uid = update.effective_user.id
    row = find_user_row(uid)

    sheet_checks.append_row([
        uid,
        update.effective_user.username or "",
        sheet_users.cell(row, 2).value if row else "",
        sheet_users.cell(row, 1).value if row else "",
        sheet_users.cell(row, 4).value if row else "",
        link,
        ocr_sum,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ocr_date,
        ocr_text[:500],
        file.file_unique_id,
        file_hash
    ])

    if row:
        pause_until = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        sheet_users.update_cell(row, 6, pause_until)

    context.user_data.pop("wait_check", None)
    await msg.reply_text("✅ Чек сохранён. Напоминания остановлены на 30 дней.",
                         reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU)

# ---------------- REMINDERS ----------------
async def reminders(app):
    users = sheet_users.get_all_records()
    today = datetime.now().date()

    for idx, u in enumerate(users, start=2):
        try:
            if u.get("BOT_BLOCKED") == "TRUE":
                continue
            pause = u.get("REMIND_PAUSE_UNTIL")
            if pause and today <= datetime.strptime(pause, "%Y-%m-%d").date():
                continue
            pay_date = u.get("PAY_DATE")
            if not pay_date:
                continue
            diff = (datetime.strptime(pay_date, "%Y-%m-%d").date() - today).days
            if diff in (5, 3, 1):
                try:
                    await app.bot.send_message(
                        chat_id=int(u.get("TG_ID")),
                        text=f"🔔 Напоминание: до оплаты {diff} дней"
                    )
                    sheet_users.update_cell(idx, 7, today.strftime("%Y-%m-%d"))
                except Forbidden:
                    sheet_users.update_cell(idx, 8, "TRUE")
        except Exception as e:
            logger.error(e)

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminders, "cron", hour=9, args=[app])
    scheduler.start()

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
