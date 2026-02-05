# =========================
# main.py — TSN Payment Bot (Part 1/2)
# Python 3.11 | FastAPI + python-telegram-bot v21
# =========================

import os
import io
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

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
from googleapiclient.http import MediaIoBaseDownload

# =========================
# 1. ENV И КОНФИГ
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", "10000"))

ADMIN_IDS = set(int(x) for x in (os.getenv("ADMIN_IDS") or "").split(",") if x.strip().isdigit())
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Папка Google Drive с картами посёлка
TSN_MAPS_FOLDER_ID = os.getenv("TSN_MAPS_FOLDER_ID", "17bulx860YtFtTqW7cexESTSTKOdTE7Pf")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not SPREADSHEET_ID:
    raise RuntimeError("SPREADSHEET_ID is not set")
if not GOOGLE_CREDENTIALS_JSON:
    raise RuntimeError("GOOGLE_CREDENTIALS_JSON is not set")

# =========================
# 2. ЛОГГИНГ
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# =========================
# 3. GOOGLE AUTH (Sheets + Drive)
# =========================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

def get_google_creds():
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    return Credentials.from_service_account_info(info, scopes=SCOPES)

creds = get_google_creds()
gc = gspread.authorize(creds)
drive_service = build("drive", "v3", credentials=creds)

def get_sheet(name: str):
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=name, rows=1000, cols=30)

sheet_main = get_sheet("Лист 1")        # tg_id, ФИО, Участок, Сумма, День оплаты, Статус
sheet_reqs = get_sheet("Реквизиты")    # Ключ, Значение, Счёт получателя, Получатель, ИНН, Назначение платежа, QR_оплата

# =========================
# 4. FASTAPI + TELEGRAM
# =========================
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()

# =========================
# 5. УТИЛИТЫ (Sheets)
# =========================
def get_user_row_by_tg_id(tg_id: int) -> Optional[Dict[str, Any]]:
    rows = sheet_main.get_all_records()
    for r in rows:
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
    except Exception:
        return 0

def get_user_fio(tg_id: int) -> str:
    row = get_user_row_by_tg_id(tg_id)
    return str(row.get("ФИО", "")) if row else ""

def get_requisites() -> Dict[str, str]:
    rows = sheet_reqs.get_all_records()
    req = {}

    for r in rows:
        key = r.get("Ключ")
        val = r.get("Значение")
        if key:
            req[key] = val

        if r.get("QR_оплата"):
            req["QR_оплата"] = r.get("QR_оплата")

    return req
    # QR_оплата может быть отдельным столбцом
    for r in rows:
        if r.get("QR_оплата"):
            req["QR_оплата"] = r.get("QR_оплата")
            break
    return req

# =========================
# 6. ОПЛАТА (Deeplink)
# =========================
BANKS = {
    "sbp": {
        "name": "🟢 СБП",
        "deeplink": "https://qr.nspk.ru/AS1A0000000000000000000000000000000000?amount={amount}"
    },
    "vtb": {
        "name": "🔵 ВТБ",
        "deeplink": "https://online.vtb.ru/payments?amount={amount}"
    },
    "alpha": {
        "name": "🔴 Альфа-Банк",
        "deeplink": "https://alfabank.ru/payments/transfer?amount={amount}"
    },
    "tbank": {
        "name": "🟡 Т-Банк",
        "deeplink": "https://www.tinkoff.ru/payments/form?amount={amount}"
    },
}

def build_payment_keyboard(amount: int) -> InlineKeyboardMarkup:
    kb = []
    for k, v in BANKS.items():
        kb.append([InlineKeyboardButton(v["name"], url=v["deeplink"].format(amount=amount))])
    return InlineKeyboardMarkup(kb)

# =========================
# 7. КНОПКИ
# =========================
def user_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("💳 Оплата"), KeyboardButton("📄 Реквизиты")],
        [KeyboardButton("👉 Оплатил"), KeyboardButton("ℹ️ Информация")],
    ]
    if is_admin:
        rows.append([KeyboardButton("🛠 Админ-панель")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def admin_panel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📨 Уведомить собственника")],
            [KeyboardButton("🗺 Карта посёлка")],
            [KeyboardButton("⬅️ Назад в меню")],
        ],
        resize_keyboard=True,
    )

# =========================
# 8. ХЕНДЛЕРЫ
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_admin = uid in ADMIN_IDS
    text = (
        "Привет! 👋\n\n"
        "Здесь можно оплатить взносы ТСН, посмотреть реквизиты и отправить чек.\n\n"
        "• Сумма берётся автоматически по вашему участку\n"
        "• Напоминания приходят автоматически\n"
    )
    await update.message.reply_text(text, reply_markup=user_keyboard(is_admin=is_admin))

async def handle_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    fio = get_user_fio(uid)
    plot = get_plot_number_for_user(uid)
    amount = get_expected_amount_for_user(uid)
    text = (
        f"ℹ️ Ваша информация:\n\n"
        f"ФИО: {fio or '—'}\n"
        f"Участок: {plot}\n"
        f"Сумма к оплате: {amount} ₽\n"
    )
    await update.message.reply_text(text)

async def handle_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    amount = get_expected_amount_for_user(uid)
    if not amount:
        await update.message.reply_text("❌ Не удалось определить сумму по участку. Обратитесь к администратору.")
        return
    kb = build_payment_keyboard(amount)
    await update.message.reply_text(
        f"💳 К оплате: <b>{amount} ₽</b>\nВыберите способ оплаты:",
        reply_markup=kb,
        parse_mode="HTML",
    )

async def handle_reqs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = get_requisites()
    text = (
        "📄 <b>Реквизиты для оплаты</b>\n\n"
        f"🏦 Получатель: {req.get('Получатель','')}\n"
        f"📄 ИНН: {req.get('ИНН','')}\n"
        f"💼 Счёт: {req.get('Счёт получателя','')}\n"
        f"📝 Назначение: {req.get('Назначение платежа','')}\n"
    )
    await update.message.reply_text(text, parse_mode="HTML")
    if req.get("QR_оплата"):
        await update.message.reply_text(f"📎 QR для оплаты:\n{req['QR_оплата']}")

async def handle_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Пришлите, пожалуйста, чек (фото/скрин). Я проверю автоматически.")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    await update.message.reply_text("🛠 Админ-панель:", reply_markup=admin_panel_keyboard())

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_admin = uid in ADMIN_IDS
    await update.message.reply_text("⬅️ Возврат в меню", reply_markup=user_keyboard(is_admin=is_admin))

async def admin_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return

    res = drive_service.files().list(
        q=f"'{TSN_MAPS_FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=1,
    ).execute()
    files = res.get("files", [])
    if not files:
        await update.message.reply_text("❌ В папке с картами нет файлов.")
        return

    file_id = files[0]["id"]
    name = files[0]["name"]

    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()

    fh.seek(0)
    await update.message.reply_photo(photo=fh, caption="🗺 Карта посёлка")

# =========================
# 9. WEBHOOK
# =========================
@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}

@app.get("/")
async def health():
    return {"status": "ok", "service": "TSN bot running"}

@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    logger.info("✅ Bot started + webhook set")

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()

# =========================
# 10. РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.Regex("^ℹ️ Информация$"), handle_info))
application.add_handler(MessageHandler(filters.Regex("^💳 Оплата$"), handle_pay))
application.add_handler(MessageHandler(filters.Regex("^📄 Реквизиты$"), handle_reqs))
application.add_handler(MessageHandler(filters.Regex("^👉 Оплатил$"), handle_paid))
application.add_handler(MessageHandler(filters.Regex("^🛠 Админ-панель$"), admin_panel))
application.add_handler(MessageHandler(filters.Regex("^🗺 Карта посёлка$"), admin_map))
application.add_handler(MessageHandler(filters.Regex("^⬅️ Назад в меню$"), admin_back))

# =========================
# 11. ENTRYPOINT
# =========================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
# =========================
# main.py — TSN Payment Bot (Part 2/2)
# OCR + Anti-duplicate + Reminders + Admin notify
# =========================

import re
import hashlib
from datetime import date, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from google.cloud import vision

# =========================
# 12. GOOGLE VISION OCR
# =========================
vision_client = vision.ImageAnnotatorClient(credentials=creds)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def parse_amount_from_text(text: str):
    m = re.findall(r"(\d[\d\s]{2,}\d)", text)
    if not m:
        return None
    try:
        return int(float(m[0].replace(" ", "")))
    except Exception:
        return None

def parse_date_from_text(text: str):
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d.%m.%Y").date()
    except Exception:
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
def drive_get_or_create_plot_folder(plot: str) -> str:
    q = f"mimeType='application/vnd.google-apps.folder' and name='{plot}' and trashed=false"
    res = drive_service.files().list(q=q, fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    meta = {
        "name": plot,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = drive_service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def drive_upload_check(plot: str, filename: str, data: bytes):
    folder_id = drive_get_or_create_plot_folder(plot)

    media = MediaIoBaseDownload(io.BytesIO(data), None)  # заглушка для типа
    drive_service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=io.BytesIO(data)
    ).execute()

# =========================
# 13. ЛИСТ ДЛЯ ЧЕКОВ + АНТИДУБЛИКАТЫ
# =========================
sheet_checks = get_sheet("Лист 2")  # tg_id, ФИО, Участок, Ожидалось, В чеке, Месяц, Хэш, Статус, Дата

def is_duplicate_check(file_hash: str) -> bool:
    rows = sheet_checks.get_all_records()
    for r in rows:
        if r.get("Хэш") == file_hash:
            return True
    return False

def current_month_key(d: date | None) -> str:
    d = d or date.today()
    return f"{d.year}-{d.month:02d}"

def compare_amounts(expected: int, actual: int | None) -> bool:
    if actual is None:
        return False
    return abs(expected - actual) <= 5  # допускаем погрешность

# =========================
# 14. ОБРАБОТКА ЧЕКА (👉 Оплатил → фото)
# =========================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    fio = get_user_fio(uid)
    plot = get_plot_number_for_user(uid)
    expected = get_expected_amount_for_user(uid)

    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    h = sha256_bytes(bytes(data))

    if is_duplicate_check(h):
        await update.message.reply_text("⚠️ Этот чек уже был загружен ранее (дубликат).")
        return

    ocr = await ocr_image_bytes(bytes(data))
    amount = ocr["amount"]
    d = ocr["date"]

# загрузка чека на Google Drive в папку участка
try:
    drive_upload_check(plot, f"check_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg", bytes(data))
except Exception as e:
    logger.error(f"Ошибка загрузки чека в Drive: {e}")

    ok_amount = compare_amounts(expected, amount)
    status = "принято" if ok_amount else "на проверке"
    month_key = current_month_key(d)

    sheet_checks.append_row([
        uid,
        fio,
        plot,
        expected,
        amount or "",
        month_key,
        h,
        status,
        datetime.now().strftime("%d.%m.%Y %H:%M"),
    ])

    msg = (
        f"🧾 Чек обработан:\n\n"
        f"ФИО: {fio}\n"
        f"Участок: {plot}\n"
        f"Ожидалось: {expected} ₽\n"
        f"В чеке: {amount or 'не распознано'} ₽\n"
        f"Месяц: {month_key}\n"
        f"Статус: {'✅ принято' if ok_amount else '⚠️ отправлено на проверку администратору'}"
    )
    await update.message.reply_text(msg)

# Регистрируем новый обработчик фото (перекрывает старый из Части 1)
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# =========================
# 15. APSCHEDULER: 5–3–1 + ПРОСРОЧКА
# =========================
scheduler = AsyncIOScheduler()

async def job_reminders():
    rows = sheet_main.get_all_records()
    today = date.today().day

    for r in rows:
        try:
            tg_id = int(r.get("tg_id"))
        except Exception:
            continue

        fio = r.get("ФИО", "")
        plot = r.get("Участок", "")
        expected = r.get("Сумма", "")
        due_day = int(r.get("День оплаты", 0))
        status = str(r.get("Статус", "")).lower()

        if not due_day:
            continue

        delta = due_day - today

        if delta == 5:
            text = f"👋 {fio}, через 5 дней срок оплаты взноса по участку №{plot}."
        elif delta == 3:
            text = f"🔔 {fio}, напоминаем об оплате взноса по участку №{plot}. Осталось 3 дня."
        elif delta == 1:
            text = f"⏰ {fio}, завтра день оплаты взноса по участку №{plot}."
        elif delta < 0:
            text = f"⚠️ {fio}, по участку №{plot} зафиксирована просрочка оплаты. Просим погасить задолженность."
        else:
            continue

        try:
            await application.bot.send_message(chat_id=tg_id, text=text)
        except Exception as e:
            logger.warning(f"Не удалось отправить напоминание {tg_id}: {e}")

# Запускаем планировщик раз в день
scheduler.add_job(job_reminders, "interval", hours=24)

@app.on_event("startup")
async def start_scheduler():
    scheduler.start()
    logger.info("⏰ APScheduler запущен (напоминания 5–3–1 + просрочки)")

@app.on_event("shutdown")
async def stop_scheduler():
    scheduler.shutdown()

# =========================
# 16. АДМИН: УВЕДОМИТЬ СОБСТВЕННИКА ПО УЧАСТКУ
# =========================
ADMIN_NOTIFY_STATE: Dict[int, str] = {}

async def admin_notify_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    rows = sheet_main.get_all_records()
    plots = sorted({str(r.get("Участок", "")).strip() for r in rows if r.get("Участок")})

    keyboard = [[KeyboardButton(p)] for p in plots]
    keyboard.append([KeyboardButton("❌ Отмена")])

    ADMIN_NOTIFY_STATE[update.effective_user.id] = "await_plot"
    await update.message.reply_text(
        "Выберите участок для отправки уведомления:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def admin_notify_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return

    if update.message.text == "❌ Отмена":
        ADMIN_NOTIFY_STATE.pop(uid, None)
        await update.message.reply_text("❌ Отменено.", reply_markup=admin_panel_keyboard())
        return

    if ADMIN_NOTIFY_STATE.get(uid) != "await_plot":
        return

    plot = update.message.text.strip()
    rows = sheet_main.get_all_records()
    target = None
    for r in rows:
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
        f"Напоминаем о необходимости оплаты взноса по участку №{plot}.\n"
        f"Сумма к оплате: {amount} ₽.\n\n"
        f"Если вы уже оплатили — отправьте, пожалуйста, чек через бота 🙏"
    )

    await application.bot.send_message(chat_id=tg_id, text=text)
    await update.message.reply_text(f"✅ Уведомление отправлено собственнику участка {plot}.")
    ADMIN_NOTIFY_STATE.pop(uid, None)

application.add_handler(MessageHandler(filters.Regex("^📨 Уведомить собственника$"), admin_notify_start))
application.add_handler(MessageHandler(filters.TEXT & filters.User(list(ADMIN_IDS)), admin_notify_flow))
