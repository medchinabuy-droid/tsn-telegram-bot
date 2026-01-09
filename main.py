import os, json, io, re, logging
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.error import Forbidden

import pytesseract
from PIL import Image

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# ---------------- GOOGLE ----------------
creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)

sh = gc.open_by_key(SPREADSHEET_ID)
users = sh.worksheet("Пользователи")
checks = sh.worksheet("Чеки")

# ---------------- HELPERS ----------------
def is_admin(uid): return uid in ADMIN_IDS

def extract_sum_and_date(text):
    sum_match = re.search(r'(\d+[.,]\d{2})', text.replace(',', '.'))
    date_match = re.search(r'(\d{2}[./]\d{2}[./]\d{4})', text)
    return (
        float(sum_match.group(1)) if sum_match else None,
        date_match.group(1) if date_match else None
    )

def upload_drive(data, name):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg")
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{f['id']}"

def find_user_row(uid):
    ids = users.col_values(3)
    for i, v in enumerate(ids, start=1):
        if v == str(uid):
            return i
    return None

# ---------------- FILE HANDLER ----------------
async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row = find_user_row(uid)
    if not row:
        await update.message.reply_text("❌ Сначала нажмите 🚀 Начать")
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()

    img = Image.open(io.BytesIO(data))
    text = pytesseract.image_to_string(img, lang="rus+eng")

    ocr_sum, ocr_date = extract_sum_and_date(text)
    link = upload_drive(data, f"check_{uid}_{datetime.now().isoformat()}")

    checks.append_row([
        uid,
        photo.file_unique_id,
        link,
        ocr_sum,
        ocr_date,
        "AUTO",
        datetime.now().strftime("%Y-%m-%d")
    ])

    if ocr_sum:
        users.update_cell(row, 6, 0)
        users.update_cell(row, 7, "Оплачено")
        users.update_cell(row, 8, datetime.now().strftime("%Y-%m-%d"))

    await update.message.reply_text(
        "✅ Чек принят.\n"
        "📄 Оплата распознана автоматически.\n"
        "🔕 Напоминания приостановлены на 30 дней."
    )

# ---------------- REMINDERS ----------------
async def reminders(app: Application):
    today = datetime.now().date()
    report = []

    for i, r in enumerate(users.get_all_records(), start=2):
        try:
            debt = float(r["Сумма"])
            if debt <= 0:
                continue

            last_check = r.get("Дата_последнего_чека")
            if last_check:
                if today - datetime.strptime(last_check, "%Y-%m-%d").date() < timedelta(days=30):
                    continue

            due = today + timedelta(days=5)
            msg = f"💰 Долг {debt} ₽\nОплатите до {due}"

            try:
                await app.bot.send_message(int(r["Telegram_ID"]), msg)
                users.update_cell(i, 9, today.strftime("%Y-%m-%d"))
                report.append(r["Участок"])
            except Forbidden:
                users.update_cell(i, 10, "BLOCKED")

        except Exception:
            continue

    for admin in ADMIN_IDS:
        await app.bot.send_message(
            admin,
            f"📊 Отчёт:\n"
            f"Напоминаний: {len(report)}\n"
            f"Участки: {', '.join(report)}"
        )

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("⬇️ Используйте меню")))
    app.add_handler(MessageHandler(filters.PHOTO, handle_check))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminders, "interval", days=1, args=[app])
    scheduler.start()

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
