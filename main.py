import os
import json
import logging
from datetime import datetime, date
from io import BytesIO

import gspread
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

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

# ---------------- НАСТРОЙКИ ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

ADMIN_USERNAMES = [
    u.strip().lower()
    for u in os.getenv("ADMIN_IDS", "").split(",")
    if u.strip()
]

# ---------------- ЛОГИ ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- GOOGLE ----------------
creds = Credentials.from_service_account_info(
    json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ],
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_req = sh.worksheet("Реквизиты")

drive = build("drive", "v3", credentials=creds)

# ---------------- DRIVE ----------------
def get_folder(name):
    res = drive.files().list(
        q=f"name='{name}' and mimeType='application/vnd.google-apps.folder'",
        fields="files(id)"
    ).execute()["files"]
    if res:
        return res[0]["id"]
    folder = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"}
    ).execute()
    return folder["id"]

FOLDER_ID = get_folder("TSN_CHECKS")

# ---------------- КЛАВИАТУРА ----------------
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📎 Загрузить чек")],
        [KeyboardButton("📄 Мои платежи")],
        [KeyboardButton("💳 Реквизиты")]
    ],
    resize_keyboard=True
)

# ---------------- ПОИСК ПОЛЬЗОВАТЕЛЯ ----------------
def find_user(tg_id, phone=None):
    rows = sheet_users.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return r, i
        if phone and phone == str(r.get("Телефон")):
            sheet_users.update_cell(i, 3, tg_id)
            return r, i
    return None, None

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать!\nВведите номер телефона\n\n"
        "👉 Пример: +7 926 123-45-67"
    )
    context.user_data["wait_phone"] = True

# ---------------- ТЕЛЕФОН ----------------
async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_phone"):
        return

    phone = update.message.text.strip()
    user = update.effective_user

    data, _ = find_user(user.id, phone)
    context.user_data["wait_phone"] = False

    if not data:
        await update.message.reply_text("❌ Телефон не найден.")
        return

    context.user_data["fio"] = data["ФИО"]
    context.user_data["phone"] = phone
    context.user_data["house"] = data["Участок"]

    await update.message.reply_text(
        f"👋 {data['ФИО']}\n🏠 Дом: {data['Участок']}",
        reply_markup=MAIN_KB
    )

# ---------------- МОИ ПЛАТЕЖИ ----------------
async def my_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = sheet_checks.get_all_records()
    items = [r for r in rows if str(r["telegram_id"]) == str(uid)]

    if not items:
        await update.message.reply_text("📭 Платежей пока нет.")
        return

    text = "📄 Ваши платежи:\n\n"
    for r in items[-5:]:
        text += f"📅 {r['Дата_чека']} — {r['Сумма_по_чеку']}\n{r['Ссылка_на_чек']}\n\n"

    await update.message.reply_text(text)

# ---------------- РЕКВИЗИТЫ ----------------
async def requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = sheet_req.get_all_records()[0]
    await update.message.reply_text(
        f"💳 Реквизиты:\n\n"
        f"Банк: {r['Банк']}\n"
        f"ИНН: {r['ИНН']}\n"
        f"Счёт: {r['Счёт получателя']}\n\n"
        f"QR:\n{r['QR_оплата']}"
    )

# ---------------- ЗАГРУЗКА ЧЕКА ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user

    file = msg.photo[-1] if msg.photo else msg.document
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    media = MediaIoBaseUpload(BytesIO(data), mimetype="application/octet-stream")

    uploaded = drive.files().create(
        media_body=media,
        body={
            "name": f"{user.id}_{datetime.now().isoformat()}",
            "parents": [FOLDER_ID]
        }
    ).execute()

    link = f"https://drive.google.com/file/d/{uploaded['id']}"

    sheet_checks.append_row([
        user.id,
        user.username,
        context.user_data.get("fio"),
        context.user_data.get("house"),
        context.user_data.get("phone"),
        link,
        "",
        datetime.now().strftime("%Y-%m-%d"),
        "PHOTO",
        "",
        file.file_unique_id
    ])

    await update.message.reply_text("✅ Чек сохранён.", reply_markup=MAIN_KB)

# ---------------- НАПОМИНАНИЯ ----------------
async def reminders(app: Application):
    today = date.today().strftime("%Y-%m-%d")
    rows = sheet_users.get_all_records()

    for r in rows:
        if r.get("Статус") != "Оплачено" and r.get("Дата_напоминания") == today:
            try:
                await app.bot.send_message(
                    chat_id=r["Telegram_ID"],
                    text=f"⏰ Напоминание об оплате\nСумма: {r['Сумма']}"
                )
            except:
                pass

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminders, "cron", hour=10, args=[app])
    scheduler.start()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("📄"), my_payments))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("💳"), requisites))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=10000,
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
