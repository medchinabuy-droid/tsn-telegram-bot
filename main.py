import os
import json
import logging
import datetime
import re
from io import BytesIO

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ---------------- НАСТРОЙКИ ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-platform",
]

logging.basicConfig(level=logging.INFO)

# ---------------- GOOGLE ----------------

creds = Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)
gc = gspread.authorize(creds)

sh = gc.open_by_key(SPREADSHEET_ID)
sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_req = sh.worksheet("Реквизиты")

drive = build("drive", "v3", credentials=creds)
vision = build("vision", "v1", credentials=creds)

# ---------------- UI ----------------

MAIN_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton("📎 Загрузить чек"), KeyboardButton("💳 Реквизиты")]],
    resize_keyboard=True
)

# ---------------- HELPERS ----------------

def find_user_row(tg_id):
    for i, r in enumerate(sheet_users.get_all_records(), start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    return None, None


def extract_amount(text):
    if not text:
        return None
    patterns = [
        r"(\d+[.,]\d{2})\s*₽",
        r"(\d+[.,]\d{2})\s*руб",
        r"ИТОГО[:\s]*([\d.,]+)"
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ".")
    return None


async def safe_send(bot, chat_id, text):
    try:
        await bot.send_message(chat_id, text)
        return True
    except:
        row, _ = find_user_row(chat_id)
        if row:
            sheet_users.update_cell(row, 9, "BLOCKED")
        return False

# ---------------- START ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row, user = find_user_row(update.effective_user.id)
    if row:
        await update.message.reply_text(
            "👋 Вы уже зарегистрированы\n\n"
            "Используйте меню ⬇️\n"
            "📎 — загрузка чека\n"
            "💳 — реквизиты",
            reply_markup=MAIN_MENU
        )
        return

    context.user_data["reg"] = {}
    await update.message.reply_text("Введите ФИО:")

# ---------------- REG ----------------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    tg_id = update.effective_user.id

    if "reg" in context.user_data:
        r = context.user_data["reg"]

        if "fio" not in r:
            r["fio"] = text
            await update.message.reply_text("📞 Телефон (+7...)")
            return

        if "phone" not in r:
            r["phone"] = text
            await update.message.reply_text("🏠 Номер участка:")
            return

        r["house"] = text

        sheet_users.append_row([
            r["house"], r["fio"], tg_id, r["phone"],
            "", "", "Не оплачено", "", ""
        ])

        context.user_data.pop("reg")
        await update.message.reply_text(
            "✅ Регистрация завершена\n"
            "📎 Нажмите скрепку для загрузки чека",
            reply_markup=MAIN_MENU
        )

# ---------------- REKV ----------------

async def requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = sheet_req.get_all_records()[0]
    await update.message.reply_text(
        f"💳 Реквизиты:\n\n"
        f"Банк: {r.get('Банк')}\n"
        f"БИК: {r.get('БИК')}\n"
        f"Счёт: {r.get('Счёт получателя')}\n"
        f"Получатель: {r.get('Получатель')}\n"
        f"ИНН: {r.get('ИНН')}\n"
        f"QR: {r.get('QR_оплата')}",
        reply_markup=MAIN_MENU
    )

# ---------------- CHECK ----------------

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document or update.message.photo[-1]
    file = await doc.get_file()

    for r in sheet_checks.get_all_records():
        if r.get("File_Unique_ID") == doc.file_unique_id:
            await update.message.reply_text("⚠️ Чек уже был загружен")
            return

    buf = BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)

    media = MediaIoBaseUpload(buf)
    f = drive.files().create(
        body={"name": doc.file_unique_id},
        media_body=media,
        fields="webViewLink"
    ).execute()

    link = f["webViewLink"]

    vision_res = vision.images().annotate(
        body={"requests": [{"image": {"source": {"imageUri": link}},
                            "features": [{"type": "TEXT_DETECTION"}]}]}
    ).execute()

    ocr = vision_res["responses"][0].get("textAnnotations", [{}])[0].get("description", "")
    amount = extract_amount(ocr)

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        "", "", link, amount,
        datetime.date.today().isoformat(),
        ocr,
        datetime.date.today().isoformat(),
        "NO",
        doc.file_unique_id
    ])

    row, _ = find_user_row(update.effective_user.id)
    if row and amount:
        sheet_users.update_cell(row, 6, "0")
        sheet_users.update_cell(row, 7, "Оплачено")
        sheet_users.update_cell(row, 8,
            (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        )

    await update.message.reply_text(
        "✅ Чек принят\n"
        "⏸ Напоминания остановлены на месяц",
        reply_markup=MAIN_MENU
    )

# ---------------- REMINDERS ----------------

async def reminders(app):
    today = datetime.date.today()

    for i, r in enumerate(sheet_users.get_all_records(), start=2):
        try:
            if not r.get("Telegram_ID") or float(r.get("Сумма") or 0) <= 0:
                continue

            pay_day = int(r.get("День_оплаты") or 0)
            if not pay_day:
                continue

            pay_date = today.replace(day=pay_day)
            delta = (pay_date - today).days

            if delta in [5, 3, 1]:
                await safe_send(
                    app.bot,
                    int(r["Telegram_ID"]),
                    f"⚠️ Напоминание: оплата через {delta} дн."
                )
                sheet_users.update_cell(i, 8, today.isoformat())
        except:
            pass

# ---------------- MAIN ----------------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Regex("^💳 Реквизиты$"), requisites))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(reminders, "cron", hour=10, args=[app])
    scheduler.start()

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
