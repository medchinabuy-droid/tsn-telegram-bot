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
sheet_stats = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# ---------------- TEXTS ----------------
BATTLE_TEXT = (
    "⚠️ Уведомление ТСН\n\n"
    "У вас имеется задолженность по взносам.\n"
    "Просим срочно погасить долг.\n\n"
    "Если оплата произведена — загрузите чек в бота."
)

# ---------------- MENUS ----------------
USER_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["🔍 Долг по участку"], ["📣 Боевое уведомление"], ["📊 Статистика"], ["⬅️ Назад"]],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------
def is_admin(uid): 
    return uid in ADMIN_IDS

def log_stat(event, uid="", username="", house="", comment=""):
    sheet_stats.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event, uid, username, house, comment
    ])

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
    uid = update.effective_user.id
    context.user_data.clear()

    log_stat("start", uid, update.effective_user.username)

    row = find_user_row(uid)
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    if row:
        fio = sheet_users.cell(row, 2).value
        await update.message.reply_text(f"👋 С возвращением, {fio}", reply_markup=menu)
    else:
        context.user_data["step"] = "fio"
        await update.message.reply_text("Введите ФИО:", reply_markup=menu)

# ---------------- TEXT ----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    username = update.effective_user.username or ""

    if text == "🚀 Начать":
        await start(update, context)
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-меню", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text("Главное меню", reply_markup=ADMIN_MENU)
        return

    # ---- ДОЛГ ----
    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["wait_debt"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("wait_debt") and is_admin(uid):
        context.user_data.pop("wait_debt")
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                await update.message.reply_text(
                    f"🏠 Участок {text}\n"
                    f"ФИО: {r.get('ФИО')}\n"
                    f"Телефон: {r.get('Телефон')}\n"
                    f"Долг: {r.get('Сумма')}\n"
                    f"Статус: {r.get('Статус')}",
                    reply_markup=ADMIN_PANEL
                )
                return
        await update.message.reply_text("❌ Участок не найден", reply_markup=ADMIN_PANEL)
        return

    # ---- БОЕВОЕ ----
    if text == "📣 Боевое уведомление" and is_admin(uid):
        context.user_data["wait_battle"] = True
        await update.message.reply_text("Введите участок или ALL:")
        return

    if context.user_data.get("wait_battle") and is_admin(uid):
        context.user_data.pop("wait_battle")
        sent = 0
        for r in sheet_users.get_all_records():
            if text == "ALL" or str(r.get("Участок")) == text:
                try:
                    await context.bot.send_message(int(r["TelegramID"]), BATTLE_TEXT)
                    sent += 1
                except Exception as e:
                    logger.error(e)
        await update.message.reply_text(f"✅ Отправлено: {sent}", reply_markup=ADMIN_PANEL)
        return

    # ---- СТАТИСТИКА ----
    if text == "📊 Статистика" and is_admin(uid):
        rows = sheet_stats.get_all_records()
        await update.message.reply_text(
            f"📊 Всего событий: {len(rows)}\n"
            f"Регистраций: {sum(1 for r in rows if r['event']=='регистрация')}\n"
            f"Чеков: {sum(1 for r in rows if r['event']=='загрузка_чека')}",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---- РЕКВИЗИТЫ ----
    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"🏦 Банк: {r[0]}\nБИК: {r[1]}\nСчёт: {r[2]}\n"
            f"Получатель: {r[3]}\nИНН: {r[4]}\nQR: {r[5]}",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return

    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text("Пришлите фото или PDF чека")
        return

# ---------------- FILE ----------------
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    file = update.message.photo[-1] if update.message.photo else update.message.document

    if is_duplicate(file.file_unique_id):
        await update.message.reply_text("❌ Этот чек уже был загружен")
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()
    link = upload_to_drive(data, "check", file.mime_type)

    uid = update.effective_user.id
    row = find_user_row(uid)

    fio = sheet_users.cell(row, 2).value
    house = sheet_users.cell(row, 1).value
    phone = sheet_users.cell(row, 4).value

    sheet_checks.append_row([
        uid, "", fio, house, phone, link,
        "", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "", "", file.file_unique_id
    ])

    log_stat("загрузка_чека", uid, update.effective_user.username, house)

    context.user_data.pop("wait_check")
    await update.message.reply_text(
        "✅ Чек сохранён\nЕсли у вас был долг — он будет проверен",
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
