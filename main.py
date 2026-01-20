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
sheet_logs = sh.worksheet("Лист 3")
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
    [
        ["🔍 Долг по участку"],
        ["📊 Статистика"],
        ["📣 Уведомление"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def log_event(event_type, uid="", username="", house="", details="", error=""):
    sheet_logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event_type,
        uid,
        username,
        house,
        event_type,
        details,
        error
    ])

def is_admin(uid): 
    return uid in ADMIN_IDS

def find_user(uid):
    for r in sheet_users.get_all_records():
        if str(r.get("Telegram_ID")) == str(uid):
            return r
    return None

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/file/d/{f['id']}"

# ---------------- AUTO NOTIFY ----------------
async def auto_notify(app: Application):
    today = datetime.now().day

    for r in sheet_users.get_all_records():
        try:
            pay_day = int(r.get("День_оплаты", 0))
            if pay_day == 0 or today < pay_day:
                continue

            await app.bot.send_message(
                int(r["Telegram_ID"]),
                "⏰ Напоминание ТСН\n\nПросим оплатить задолженность."
            )

            log_event("auto_notify", r["Telegram_ID"], r.get("username"), r.get("Участок"))

        except Exception as e:
            log_event("blocked", r.get("Telegram_ID"), r.get("username"),
                      r.get("Участок"), error=str(e))

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    await update.message.reply_text("👋 Добро пожаловать", reply_markup=menu)

# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    user = find_user(uid)

    if text == "🚀 Начать":
        await start(update, context)
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text("Меню", reply_markup=ADMIN_MENU)
        return

    # -------- STAT --------
    if text == "📊 Статистика" and is_admin(uid):
        msg = "📊 Статистика:\n\n"
        for r in sheet_users.get_all_records():
            blocked = "Нет"
            try:
                await context.bot.send_chat_action(int(r["Telegram_ID"]), "typing")
            except:
                blocked = "ДА"

            msg += (
                f"🏠 {r.get('Участок')}\n"
                f"👤 {r.get('ФИО')}\n"
                f"📞 {r.get('Телефон')}\n"
                f"💰 {r.get('Сумма')}\n"
                f"🚫 Блок: {blocked}\n\n"
            )
        await update.message.reply_text(msg)
        return

    # -------- NOTIFY --------
    if text == "📣 Уведомление" and is_admin(uid):
        sent = 0
        for r in sheet_users.get_all_records():
            try:
                await context.bot.send_message(
                    int(r["Telegram_ID"]),
                    "📣 Проверка бота. Уведомление доставлено."
                )
                sent += 1
            except:
                log_event("blocked", r.get("Telegram_ID"), r.get("username"))
        await update.message.reply_text(f"Отправлено: {sent}")
        return

    # -------- REQS --------
    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"Банк: {r[0]}\nБИК: {r[1]}\nСчёт: {r[2]}\n"
            f"Получатель: {r[3]}\nИНН: {r[4]}"
        )
        await update.message.reply_photo(r[5])
        return

    # -------- CHECK --------
    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text("Отправьте фото или PDF чека")
        return

# ---------------- FILE ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    if is_duplicate(file.file_unique_id):
        await msg.reply_text("❌ Дубликат чека")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()
    link = upload_to_drive(data, "check", file.mime_type)

    u = find_user(update.effective_user.id)

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        u.get("ФИО"),
        u.get("Участок"),
        u.get("Телефон"),
        link,
        "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "",
        "",
        "",
        file.file_unique_id,
        "ЗАГРУЖЕН"
    ])

    log_event("check_upload", update.effective_user.id,
              update.effective_user.username, u.get("Участок"))

    context.user_data.clear()
    await msg.reply_text("✅ Чек сохранён")

# ---------------- MAIN ----------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.post_init = auto_notify

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
