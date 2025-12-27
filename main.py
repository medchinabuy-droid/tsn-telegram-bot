import os
import json
import logging
import datetime
import re
from io import BytesIO

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ===================== НАСТРОЙКИ =====================

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

SPREADSHEET_ID = "1JNf6fRup9bS_Bi_05XzBDbU3aqDhq6Dtt2rxlOp1EPE"

SHEET_MAIN = "Лист 1"      # начисления / статусы
SHEET_USERS = "Лист 2"     # пользователи + чеки
SHEET_REKV = "Реквизиты"

PORT = 10000

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-vision"
]

if not all([BOT_TOKEN, BASE_URL, WEBHOOK_SECRET, GOOGLE_CREDS_JSON]):
    raise RuntimeError("❌ Не заданы переменные окружения")

# ===================== ЛОГИ =====================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ===================== GOOGLE =====================

creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDS_JSON),
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_main = sh.worksheet(SHEET_MAIN)
sheet_users = sh.worksheet(SHEET_USERS)
sheet_rekv = sh.worksheet(SHEET_REKV)

drive = build("drive", "v3", credentials=creds)
vision = build("vision", "v1", credentials=creds)

# ===================== ВСПОМОГАТЕЛЬНОЕ =====================

def today():
    return datetime.date.today().isoformat()

def get_user_sheet2(tg_id):
    rows = sheet_users.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("telegram_id")) == str(tg_id):
            return i, r
    return None, None

def get_user_sheet1(tg_id):
    rows = sheet_main.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("Telegram_ID")) == str(tg_id):
            return i, r
    return None, None

def is_admin(tg_id):
    _, r = get_user_sheet1(tg_id)
    return r and str(r.get("Роль", "")).lower() == "админ"

def main_keyboard(admin=False):
    kb = [
        [InlineKeyboardButton("💳 Реквизиты", callback_data="rekv")],
        [InlineKeyboardButton("📤 Загрузить чек", callback_data="upload")],
        [InlineKeyboardButton("📊 Статус оплаты", callback_data="status")]
    ]
    if admin:
        kb.append([InlineKeyboardButton("🛠 Админ", callback_data="admin")])
    return InlineKeyboardMarkup(kb)

# ===================== OCR =====================

def ocr_sum(image_bytes: bytes):
    body = {
        "requests": [{
            "image": {"content": image_bytes.decode("latin1")},
            "features": [{"type": "TEXT_DETECTION"}]
        }]
    }
    res = vision.images().annotate(body=body).execute()
    text = res["responses"][0].get("fullTextAnnotation", {}).get("text", "")
    nums = re.findall(r"\d+[.,]\d{2}", text.replace(" ", ""))
    if not nums:
        return None
    return float(nums[-1].replace(",", "."))

# ===================== START / РЕГИСТРАЦИЯ =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    idx, row = get_user_sheet2(tg.id)

    if not row:
        context.user_data["reg"] = True
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\n"
            "Отправьте ОДНИМ сообщением:\n"
            "ФИО\n"
            "Номер участка\n"
            "Телефон"
        )
        return

    await update.message.reply_text(
        "✅ Бот готов к работе",
        reply_markup=main_keyboard(is_admin(tg.id))
    )

async def registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("reg"):
        return

    parts = update.message.text.split("\n")
    fio = parts[0] if len(parts) > 0 else ""
    house = parts[1] if len(parts) > 1 else ""
    phone = parts[2] if len(parts) > 2 else ""

    sheet_users.append_row([
        update.effective_user.id,
        update.effective_user.username or "",
        fio,
        house,
        phone,
        "", "", "", "", ""
    ])

    context.user_data.clear()
    await update.message.reply_text(
        "✅ Регистрация завершена",
        reply_markup=main_keyboard(is_admin(update.effective_user.id))
    )

# ===================== КНОПКИ =====================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tg_id = q.from_user.id

    if q.data == "rekv":
        data = sheet_rekv.get_all_records()
        if not data:
            await q.message.reply_text("❌ Реквизиты не заполнены")
            return

        row = data[0]
        text = "\n".join(f"{k}: {v}" for k, v in row.items() if v and k != "QR_оплата")
        await q.message.reply_text(text)

        if row.get("QR_оплата"):
            await q.message.reply_photo(row["QR_оплата"])

    elif q.data == "status":
        _, r = get_user_sheet1(tg_id)
        if not r:
            await q.message.reply_text("❌ Начислений нет")
            return
        await q.message.reply_text(
            f"💰 Сумма: {r.get('Сумма')}\n"
            f"📌 Статус: {r.get('Статус')}"
        )

    elif q.data == "upload":
        context.user_data["wait_check"] = True
        await q.message.reply_text("📎 Пришлите фото или PDF чека")

    elif q.data == "admin" and is_admin(tg_id):
        await q.message.reply_text(
            "🛠 Админ-команды:\n"
            "/report — долги\n"
            "/payments — оплаты"
        )

# ===================== ЧЕКИ =====================

async def save_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    tg = update.effective_user
    file = update.message.document or update.message.photo[-1]

    idx, row = get_user_sheet2(tg.id)
    if not row:
        await update.message.reply_text("❌ Сначала /start")
        return

    file_id = file.file_unique_id
    existing = sheet_users.get_all_records()
    for r in existing:
        if r.get("File_Unique_ID") == file_id:
            await update.message.reply_text("⚠️ Этот чек уже загружен")
            return

    f = await file.get_file()
    data = await f.download_as_bytearray()

    media = MediaIoBaseUpload(BytesIO(data), resumable=True)
    uploaded = drive.files().create(
        body={"name": file_id},
        media_body=media
    ).execute()

    link = f"https://drive.google.com/file/d/{uploaded['id']}"

    amount = None
    try:
        amount = ocr_sum(data)
    except Exception:
        pass

    sheet_users.update_row(idx, [
        row["telegram_id"],
        row["username"],
        row["ФИО"],
        row["Дом"],
        row["Телефон"],
        link,
        amount or "",
        today(),
        "Да" if amount else "Нет",
        file_id
    ])

    # автозакрытие
    i1, r1 = get_user_sheet1(tg.id)
    if amount and r1 and float(amount) >= float(r1.get("Сумма", 0)):
        sheet_main.update_cell(i1, 9, "Оплачено")
        sheet_main.update_cell(i1, 10, today())

    context.user_data.clear()
    await update.message.reply_text("✅ Чек принят")

# ===================== НАПОМИНАНИЯ =====================

async def reminders(context: ContextTypes.DEFAULT_TYPE):
    rows = sheet_main.get_all_records()
    today_d = datetime.date.today().day

    for i, r in enumerate(rows, start=2):
        try:
            pay_day = int(r.get("День_оплаты", 0))
            tg_id = r.get("Telegram_ID")
            if not tg_id or r.get("Статус") == "Оплачено":
                continue

            if pay_day - today_d in (5, 3, 0):
                await context.bot.send_message(
                    tg_id,
                    f"🔔 Напоминание об оплате\nСумма: {r.get('Сумма')} ₽"
                )
                sheet_main.update_cell(i, 11, today())
        except Exception as e:
            sheet_main.update_cell(i, 12, str(e))

# ===================== ЗАПУСК =====================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registration))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, save_check))

    app.job_queue.run_daily(reminders, time=datetime.time(9, 0))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=f"webhook/{WEBHOOK_SECRET}",
        webhook_url=f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"
    )

if __name__ == "__main__":
    main()
