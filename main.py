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
TSN_MAPS_FOLDER_ID = os.getenv("TSN_MAPS_FOLDER_ID", "17bulx860YtFtTqW7cexESTSTKOdTE7Pf")

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

def get_sheet(name: str):
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet(name)
    except Exception:
        return sh.add_worksheet(title=name, rows=1000, cols=30)

sheet_main = get_sheet("Лист 1")
sheet_reqs = get_sheet("Реквизиты")
sheet_checks = get_sheet("Лист 2")

# =========================
# 4. FASTAPI + TG
# =========================
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()

# =========================
# 5. УТИЛИТЫ
# =========================
def get_user_row_by_tg_id(tg_id: int) -> Optional[Dict[str, Any]]:
    for r in sheet_main.get_all_records():
        if str(r.get("tg_id", "")).strip() == str(tg_id):
            return r
    return None

def get_plot_number_for_user(tg_id: int) -> str:
    row = get_user_row_by_tg_id(tg_id)
    return str(row.get("Участок", "—")) if row else "—"

def get_expected_amount_for_user(tg_id: int) -> int:
    row = get_user_row_by_tg_id(tg_id)
    try:
        return int(row.get("Сумма", 0)) if row else 0
    except:
        return 0

def get_user_fio(tg_id: int) -> str:
    row = get_user_row_by_tg_id(tg_id)
    return str(row.get("ФИО", "")) if row else ""

def get_requisites() -> Dict[str, str]:
    req = {}
    for r in sheet_reqs.get_all_records():
        for k, v in r.items():
            if k and v:
                req[k] = v
    return req

# =========================
# 6. ОПЛАТА (Deeplink банки)
# =========================
BANKS = {
    "sbp": {"name": "🟢 СБП", "deeplink": "https://qr.nspk.ru/?amount={amount}"},
    "vtb": {"name": "🔵 ВТБ", "deeplink": "https://online.vtb.ru/payments?amount={amount}"},
    "alpha": {"name": "🔴 Альфа-Банк", "deeplink": "https://alfabank.ru/payments/transfer?amount={amount}"},
    "tbank": {"name": "🟡 Т-Банк", "deeplink": "https://www.tinkoff.ru/payments/form?amount={amount}"},
}

def build_payment_keyboard(amount: int) -> InlineKeyboardMarkup:
    kb = []
    for v in BANKS.values():
        kb.append([InlineKeyboardButton(v["name"], url=v["deeplink"].format(amount=amount))])
    return InlineKeyboardMarkup(kb)

# =========================
# 7. КЛАВИАТУРЫ
# =========================
def user_keyboard(is_admin=False):
    rows = [
        [KeyboardButton("💳 Оплатить"), KeyboardButton("📄 Реквизиты")],
        [KeyboardButton("👉 Я оплатил"), KeyboardButton("ℹ️ Мои данные")],
    ]
    if is_admin:
        rows.append([KeyboardButton("🛠 Админ-панель")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def admin_panel_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📨 Уведомить собственника")],
            [KeyboardButton("🗺 Карта посёлка")],
            [KeyboardButton("⬅️ Назад")],
        ],
        resize_keyboard=True
    )

# =========================
# 8. ХЕНДЛЕРЫ
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_admin = uid in ADMIN_IDS
    await update.message.reply_text(
        "👋 Добро пожаловать в бот ТСН!\n\n"
        "Здесь вы можете оплатить взносы, получить реквизиты и отправить чек.",
        reply_markup=user_keyboard(is_admin)
    )

async def handle_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"ℹ️ Ваши данные:\n"
        f"ФИО: {get_user_fio(uid)}\n"
        f"Участок: {get_plot_number_for_user(uid)}\n"
        f"Сумма: {get_expected_amount_for_user(uid)} ₽"
    )

async def handle_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    amount = get_expected_amount_for_user(uid)
    if not amount:
        await update.message.reply_text("❌ Сумма не найдена, обратитесь к администратору.")
        return
    await update.message.reply_text(
        f"💳 К оплате: {amount} ₽",
        reply_markup=build_payment_keyboard(amount)
    )

async def handle_reqs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = get_requisites()
    text = (
        f"📄 Реквизиты:\n\n"
        f"Получатель: {req.get('Получатель','')}\n"
        f"ИНН: {req.get('ИНН','')}\n"
        f"Счёт: {req.get('Счёт получателя','')}\n"
        f"Назначение: {req.get('Назначение платежа','')}"
    )
    await update.message.reply_text(text)
    if req.get("QR_оплата"):
        await update.message.reply_photo(photo=req["QR_оплата"], caption="📎 QR для оплаты")

async def handle_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Пришлите фото чека.")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛠 Админ-панель", reply_markup=admin_panel_keyboard())

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⬅️ Возврат в меню", reply_markup=user_keyboard(True))

async def admin_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = drive_service.files().list(
        q=f"'{TSN_MAPS_FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name)",
        pageSize=1
    ).execute()
    files = res.get("files", [])
    if not files:
        await update.message.reply_text("❌ Карта не найдена.")
        return
    file_id = files[0]["id"]
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, drive_service.files().get_media(fileId=file_id))
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    await update.message.reply_photo(photo=fh, caption="🗺 Карта посёлка")

# =========================
# 9. РЕГИСТРАЦИЯ
# =========================
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.Regex("^ℹ️ Мои данные$"), handle_info))
application.add_handler(MessageHandler(filters.Regex("^💳 Оплатить$"), handle_pay))
application.add_handler(MessageHandler(filters.Regex("^📄 Реквизиты$"), handle_reqs))
application.add_handler(MessageHandler(filters.Regex("^👉 Я оплатил$"), handle_paid))
application.add_handler(MessageHandler(filters.Regex("^🛠 Админ-панель$"), admin_panel))
application.add_handler(MessageHandler(filters.Regex("^🗺 Карта посёлка$"), admin_map))
application.add_handler(MessageHandler(filters.Regex("^⬅️ Назад$"), admin_back))
# =========================
# 10. OCR + АНТИДУБЛИКАТЫ
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
        if str(r.get("Хэш", "")) == file_hash:
            return True
    return False

def current_month_key(d: Optional[date]) -> str:
    d = d or date.today()
    return f"{d.year}-{d.month:02d}"

# =========================
# 11. GOOGLE DRIVE (папки по участкам)
# =========================

def drive_get_or_create_plot_folder(plot: str) -> str:
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
# 12. ОБРАБОТКА ФОТО ЧЕКА
# =========================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    fio = get_user_fio(uid)
    plot = get_plot_number_for_user(uid)
    expected = get_expected_amount_for_user(uid)

    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    data_bytes = bytes(data)

    file_hash = sha256_bytes(data_bytes)

    if is_duplicate_check(file_hash):
        await update.message.reply_text("⚠️ Этот чек уже был загружен ранее (дубликат).")
        return

    ocr = await ocr_image_bytes(data_bytes)
    amount = ocr["amount"]
    check_date = ocr["date"]

    try:
        drive_upload_check(plot, f"check_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg", data_bytes)
    except Exception as e:
        logger.error(f"Ошибка загрузки в Drive: {e}")

    ok_amount = (amount is not None and abs(expected - amount) <= 5)
    status = "принято" if ok_amount else "на проверке"
    month_key = current_month_key(check_date)

    sheet_checks.append_row([
        uid,
        fio,
        plot,
        expected,
        amount or "",
        month_key,
        file_hash,
        status,
        datetime.now().strftime("%d.%m.%Y %H:%M"),
    ])

    msg = (
        f"🧾 Чек принят:\n\n"
        f"ФИО: {fio}\n"
        f"Участок: {plot}\n"
        f"Ожидалось: {expected} ₽\n"
        f"В чеке: {amount or 'не распознано'} ₽\n"
        f"Месяц: {month_key}\n"
        f"Статус: {'✅ принято' if ok_amount else '⚠️ отправлено на проверку администратору'}"
    )
    await update.message.reply_text(msg)

application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# =========================
# 13. APSCHEDULER — НАПОМИНАНИЯ 5–3–1 + ПРОСРОЧКА
# =========================

scheduler = AsyncIOScheduler()

async def job_reminders():
    today = date.today().day
    for r in sheet_main.get_all_records():
        try:
            tg_id = int(r.get("tg_id"))
        except:
            continue

        fio = r.get("ФИО", "")
        plot = r.get("Участок", "")
        amount = r.get("Сумма", "")
        due_day = int(r.get("День оплаты", 0))

        delta = due_day - today

        if delta == 5:
            text = f"👋 {fio}, через 5 дней срок оплаты взноса по участку №{plot} ({amount} ₽)."
        elif delta == 3:
            text = f"🔔 {fio}, напоминаем об оплате взноса по участку №{plot}. Осталось 3 дня."
        elif delta == 1:
            text = f"⏰ {fio}, завтра день оплаты взноса по участку №{plot}."
        elif delta < 0:
            text = f"⚠️ {fio}, по участку №{plot} есть просрочка оплаты. Просим погасить задолженность."
        else:
            continue

        try:
            await application.bot.send_message(chat_id=tg_id, text=text)
        except Exception as e:
            logger.warning(f"Не удалось отправить напоминание {tg_id}: {e}")

scheduler.add_job(job_reminders, "interval", hours=24)

# =========================
# 14. АДМИН: МАССОВОЕ УВЕДОМЛЕНИЕ ПО УЧАСТКУ
# =========================

ADMIN_NOTIFY_STATE: Dict[int, str] = {}

async def admin_notify_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    plots = sorted({str(r.get("Участок", "")).strip() for r in sheet_main.get_all_records() if r.get("Участок")})
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
        await update.message.reply_text("Отменено.", reply_markup=admin_panel_keyboard())
        return

    if ADMIN_NOTIFY_STATE.get(uid) != "await_plot":
        return

    plot = update.message.text.strip()
    target = None
    for r in sheet_main.get_all_records():
        if str(r.get("Участок", "")).strip() == plot:
            target = r
            break

    if not target:
        await update.message.reply_text("❌ Участок не найден.")
        return

    tg_id = int(target.get("tg_id"))
    fio = target.get("ФИО", "")
    amount = target.get("Сумма", "")

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
# 15. WEBHOOK
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
# 16. ENTRYPOINT
# =========================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
