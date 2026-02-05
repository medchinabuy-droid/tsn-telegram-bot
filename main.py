import os
import io
import json
import re
import hashlib
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, date

import uvicorn
from fastapi import FastAPI, Request

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.cloud import vision

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# =========================
# 1. ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "10000"))
ADMIN_IDS = set(int(x) for x in (os.getenv("ADMIN_IDS") or "").split(",") if x.strip().isdigit())
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
TSN_MAPS_FOLDER_ID = os.getenv("TSN_MAPS_FOLDER_ID")

if not BOT_TOKEN or not SPREADSHEET_ID or not GOOGLE_CREDENTIALS_JSON:
    raise RuntimeError("❌ Не заданы ENV переменные")

# =========================
# 2. ЛОГИ
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# =========================
# 3. GOOGLE AUTH
# =========================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_google_creds():
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    return Credentials.from_service_account_info(info, scopes=SCOPES)

creds = get_google_creds()
gc = gspread.authorize(creds)
drive_service = build("drive", "v3", credentials=creds)
vision_client = vision.ImageAnnotatorClient(credentials=creds)

# =========================
# 4. SHEETS (автосоздание)
# =========================

REQUIRED_MAIN_HEADERS = [
    "Участок","ФИО","Telegram_ID","username","Телефон","День_оплаты","Электро",
    "Сумма","Дата","Статус","Роль","Дата_напоминания","Дата_рождения",
    "Дата_регистрации","Последняя_оплата","Комментарий_админа","Активен"
]

REQUIRED_REQ_HEADERS = [
    "Банк","БИК","Счёт получателя","Получатель","ИНН","Назначение платежа","QR_оплата"
]

def get_or_create_sheet(title: str, headers: List[str]):
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
        ws.append_row(headers)
        return ws

    existing_headers = ws.row_values(1)
    if existing_headers != headers:
        ws.resize(rows=1)
        ws.update("A1", [headers])
    return ws

sheet_main = get_or_create_sheet("Лист 1", REQUIRED_MAIN_HEADERS)
sheet_reqs = get_or_create_sheet("Реквизиты", REQUIRED_REQ_HEADERS)
sheet_logs = get_or_create_sheet("Логи", ["Дата","Тип","Telegram_ID","Описание"])
sheet_checks = get_or_create_sheet("Чеки", ["Telegram_ID","ФИО","Участок","Ожидалось","В чеке","Месяц","Хэш","Статус","Дата"])
sheet_months = get_or_create_sheet("Платежи_по_месяцам", ["Telegram_ID","ФИО","Участок","Месяц","Сумма","Дата"])

# =========================
# 5. FASTAPI + TG
# =========================
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()

# =========================
# 6. УТИЛИТЫ ПАРСИНГА ЛИСТ 1
# =========================

def get_user_row(tg_id: int) -> Optional[Dict[str, Any]]:
    for r in sheet_main.get_all_records():
        if str(r.get("Telegram_ID", "")).strip() == str(tg_id):
            return r
    return None

def ensure_user_registered(update: Update):
    uid = update.effective_user.id
    username = update.effective_user.username or ""
    fio = update.effective_user.full_name

    if get_user_row(uid):
        return

    sheet_main.append_row([
        "", fio, uid, username, "", "", "", "", "",
        "новый", "user", "", "", datetime.now().strftime("%d.%m.%Y"), "", "", "TRUE"
    ])
    sheet_logs.append_row([datetime.now().isoformat(), "register", uid, f"Новая регистрация: {fio}"])

def get_user_fio(tg_id: int) -> str:
    r = get_user_row(tg_id)
    return r.get("ФИО","") if r else ""

def get_plot(tg_id: int) -> str:
    r = get_user_row(tg_id)
    return str(r.get("Участок","")) if r else ""

def get_amount(tg_id: int) -> int:
    r = get_user_row(tg_id)
    try:
        return int(r.get("Сумма",0))
    except:
        return 0

def get_due_day(tg_id: int) -> int:
    r = get_user_row(tg_id)
    try:
        return int(r.get("День_оплаты",0))
    except:
        return 0

# =========================
# 7. РЕКВИЗИТЫ + DEEPLINK
# =========================

BANK_DEEPLINKS = {
    "СБП": "https://qr.nspk.ru/?amount={amount}",
    "ВТБ": "https://online.vtb.ru/payments?amount={amount}",
    "Альфа": "https://alfabank.ru/payments/transfer?amount={amount}",
    "Т-Банк": "https://www.tinkoff.ru/payments/form?amount={amount}",
}

def get_requisites() -> List[Dict[str, Any]]:
    return sheet_reqs.get_all_records()

def build_payment_keyboard(amount: int) -> InlineKeyboardMarkup:
    kb = []
    for r in get_requisites():
        bank = r.get("Банк")
        deeplink_tpl = BANK_DEEPLINKS.get(bank)
        if deeplink_tpl:
            kb.append([InlineKeyboardButton(f"💳 {bank}", url=deeplink_tpl.format(amount=amount))])
    return InlineKeyboardMarkup(kb)

# =========================
# 8. КНОПКИ
# =========================

def user_keyboard(is_admin=False):
    rows = [
        [KeyboardButton("💳 Оплатить"), KeyboardButton("📄 Реквизиты")],
        [KeyboardButton("👉 Я оплатил"), KeyboardButton("ℹ️ Мои данные")],
    ]
    if is_admin:
        rows.append([KeyboardButton("🛠 Админ-панель")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

# =========================
# 9. ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЯ
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user_registered(update)
    uid = update.effective_user.id
    await update.message.reply_text(
        f"👋 Здравствуйте, {get_user_fio(uid)}!\n\n"
        f"Это бот ТСН. Здесь вы можете оплатить взносы, получить реквизиты и отправить чек.",
        reply_markup=user_keyboard(uid in ADMIN_IDS)
    )

async def handle_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"📊 Ваши данные:\n\n"
        f"ФИО: {get_user_fio(uid)}\n"
        f"Участок: {get_plot(uid)}\n"
        f"Сумма: {get_amount(uid)} ₽\n"
        f"День оплаты: {get_due_day(uid)}"
    )

async def handle_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    amount = get_amount(uid)
    if not amount:
        await update.message.reply_text("❌ Сумма не указана в таблице. Обратитесь к администратору.")
        return

    await update.message.reply_text(
        f"💳 К оплате: {amount} ₽\nВыберите банк:",
        reply_markup=build_payment_keyboard(amount)
    )

async def handle_reqs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_requisites()
    text = "📄 Реквизиты:\n\n"
    for r in rows:
        text += (
            f"🏦 Банк: {r.get('Банк')}\n"
            f"Получатель: {r.get('Получатель')}\n"
            f"ИНН: {r.get('ИНН')}\n"
            f"Счёт: {r.get('Счёт получателя')}\n"
            f"Назначение: {r.get('Назначение платежа')}\n\n"
        )
        if r.get("QR_оплата"):
            await update.message.reply_photo(photo=r["QR_оплата"], caption=f"QR для {r.get('Банк')}")
    await update.message.reply_text(text)

async def handle_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Пришлите фото чека для проверки.")

# =========================
# 10. РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.Regex("^ℹ️ Мои данные$"), handle_my_data))
application.add_handler(MessageHandler(filters.Regex("^💳 Оплатить$"), handle_pay))
application.add_handler(MessageHandler(filters.Regex("^📄 Реквизиты$"), handle_reqs))
application.add_handler(MessageHandler(filters.Regex("^👉 Я оплатил$"), handle_paid))
# =========================
# 11. OCR + АНТИДУБЛИКАТЫ
# =========================

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def parse_amount_from_text(text: str) -> Optional[int]:
    matches = re.findall(r"(\d[\d\s]{2,}\d)", text)
    if not matches:
        return None
    try:
        return int(matches[0].replace(" ", ""))
    except:
        return None

def parse_date_from_text(text: str) -> Optional[date]:
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d.%m.%Y").date()
    except:
        return None

async def ocr_image_bytes(image_bytes: bytes) -> Dict[str, Any]:
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    text = response.full_text_annotation.text if response.full_text_annotation else ""
    return {
        "text": text,
        "amount": parse_amount_from_text(text),
        "date": parse_date_from_text(text),
    }

def is_duplicate_check(file_hash: str) -> bool:
    for r in sheet_checks.get_all_records():
        if str(r.get("Хэш","")) == file_hash:
            return True
    return False

def current_month_key(d: Optional[date]) -> str:
    d = d or date.today()
    return f"{d.year}-{d.month:02d}"

# =========================
# 12. GOOGLE DRIVE — ПАПКИ ПО УЧАСТКАМ
# =========================

def drive_get_or_create_plot_folder(plot: str) -> str:
    if not plot:
        plot = "unknown"

    res = drive_service.files().list(
        q=f"mimeType='application/vnd.google-apps.folder' and name='{plot}' and trashed=false",
        fields="files(id, name)"
    ).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    meta = {"name": plot, "mimeType": "application/vnd.google-apps.folder"}
    folder = drive_service.files().create(body=meta, fields="id").execute()
    return folder["id"]

def drive_upload_check(plot: str, filename: str, data: bytes):
    folder_id = drive_get_or_create_plot_folder(plot)
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg", resumable=False)
    drive_service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id"
    ).execute()

# =========================
# 13. ОБРАБОТКА ФОТО ЧЕКА
# =========================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    fio = get_user_fio(uid)
    plot = get_plot(uid)
    expected = get_amount(uid)

    photo = update.message.photo[-1]
    file = await photo.get_file()
    data_bytes = bytes(await file.download_as_bytearray())

    file_hash = sha256_bytes(data_bytes)
    if is_duplicate_check(file_hash):
        await update.message.reply_text("⚠️ Этот чек уже был загружен ранее.")
        return

    ocr = await ocr_image_bytes(data_bytes)
    amount = ocr["amount"]
    check_date = ocr["date"]

    try:
        drive_upload_check(plot, f"check_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg", data_bytes)
    except Exception as e:
        logger.error(f"Ошибка загрузки в Drive: {e}")

    ok_amount = amount is not None and abs(expected - amount) <= 5
    status = "принято" if ok_amount else "на проверке"
    month_key = current_month_key(check_date)

    sheet_checks.append_row([
        uid, fio, plot, expected, amount or "", month_key,
        file_hash, status, datetime.now().strftime("%d.%m.%Y %H:%M")
    ])

    sheet_months.append_row([
        uid, fio, plot, month_key, amount or "", datetime.now().strftime("%d.%m.%Y")
    ])

    if not ok_amount:
        await update.message.reply_text(
            f"⚠️ {fio}, сумма в чеке отличается от ожидаемой.\n"
            f"Ожидалось: {expected} ₽\n"
            f"В чеке: {amount or 'не распознано'} ₽\n"
            f"Администратор получит уведомление."
        )
    else:
        await update.message.reply_text(
            f"✅ Спасибо, {fio}!\n"
            f"Чек принят за {month_key}.\n"
            f"Сумма: {amount} ₽"
        )

application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# =========================
# 14. APSCHEDULER — НАПОМИНАНИЯ + ДР
# =========================

scheduler = AsyncIOScheduler()

async def job_reminders():
    today = date.today()
    today_day = today.day

    for r in sheet_main.get_all_records():
        try:
            tg_id = int(r.get("Telegram_ID"))
        except:
            continue

        fio = r.get("ФИО","")
        plot = r.get("Участок","")
        amount = r.get("Сумма","")
        due_day = int(r.get("День_оплаты") or 0)

        delta = due_day - today_day
        text = None

        if delta == 5:
            text = f"👋 {fio}, через 5 дней срок оплаты взноса по участку №{plot} ({amount} ₽)."
        elif delta == 3:
            text = f"🔔 {fio}, напоминаем об оплате взноса по участку №{plot}. Осталось 3 дня."
        elif delta == 1:
            text = f"⏰ {fio}, завтра день оплаты взноса по участку №{plot}."
        elif delta < 0:
            text = f"⚠️ {fio}, по участку №{plot} зафиксирована просрочка оплаты."

        if text:
            try:
                await application.bot.send_message(chat_id=tg_id, text=text)
            except Exception as e:
                logger.warning(f"Не удалось отправить напоминание {tg_id}: {e}")

        # 🎂 Поздравление с ДР
        bday = r.get("Дата_рождения")
        if bday:
            try:
                bd = datetime.strptime(bday, "%d.%m.%Y").date()
                if bd.day == today.day and bd.month == today.month:
                    await application.bot.send_message(
                        chat_id=tg_id,
                        text=f"🎉 {fio}, с днём рождения! Здоровья и отличного дня! 🎂"
                    )
            except:
                pass

scheduler.add_job(job_reminders, "interval", hours=24)

# =========================
# 15. АДМИН: УВЕДОМИТЬ СОБСТВЕННИКА
# =========================

ADMIN_NOTIFY_STATE: Dict[int, str] = {}

async def admin_notify_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    plots = sorted({str(r.get("Участок","")).strip() for r in sheet_main.get_all_records() if r.get("Участок")})
    keyboard = [[KeyboardButton(p)] for p in plots]
    keyboard.append([KeyboardButton("❌ Отмена")])

    ADMIN_NOTIFY_STATE[update.effective_user.id] = "await_plot"
    await update.message.reply_text(
        "Выберите участок для уведомления:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def admin_notify_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return

    if update.message.text == "❌ Отмена":
        ADMIN_NOTIFY_STATE.pop(uid, None)
        await update.message.reply_text("Отменено.", reply_markup=user_keyboard(True))
        return

    if ADMIN_NOTIFY_STATE.get(uid) != "await_plot":
        return

    plot = update.message.text.strip()
    target = None
    for r in sheet_main.get_all_records():
        if str(r.get("Участок","")).strip() == plot:
            target = r
            break

    if not target:
        await update.message.reply_text("❌ Участок не найден.")
        return

    tg_id = int(target.get("Telegram_ID"))
    fio = target.get("ФИО","")
    amount = target.get("Сумма","")

    text = (
        f"👋 {fio}, добрый день!\n\n"
        f"Напоминаем об оплате взноса по участку №{plot}.\n"
        f"Сумма: {amount} ₽.\n\n"
        f"Если вы уже оплатили — отправьте чек через бота 🙏"
    )

    await application.bot.send_message(chat_id=tg_id, text=text)
    await update.message.reply_text(f"✅ Уведомление отправлено для участка {plot}.")
    ADMIN_NOTIFY_STATE.pop(uid, None)

application.add_handler(MessageHandler(filters.Regex("^📨 Уведомить собственника$"), admin_notify_start))
application.add_handler(MessageHandler(filters.TEXT & filters.User(list(ADMIN_IDS)), admin_notify_flow))

# =========================
# 16. WEBHOOK + STARTUP
# =========================

@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

@app.get("/")
async def health():
    return {"status": "ok"}

@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    scheduler.start()
    logger.info("✅ Bot + Webhook + Scheduler запущены")

@app.on_event("shutdown")
async def on_shutdown():
    scheduler.shutdown()
    await application.stop()
    await application.shutdown()

# =========================
# 17. ENTRYPOINT
# =========================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
