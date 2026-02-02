# =========================
# 0. ИМПОРТЫ И НАСТРОЙКИ
# =========================
import os
import io
import json
import re
import time
import hashlib
import asyncio
import logging
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Any, List

import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel

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

from google.cloud import vision
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import qrcode
from PIL import Image

# openai SDK (опционально — если ключ не задан, модуль не падает)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except Exception:
    OPENAI_AVAILABLE = False

# =========================
# 1. ENV И КОНФИГ
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", 10000))

ADMIN_IDS = set(int(x) for x in (os.getenv("ADMIN_IDS") or "").split(",") if x.strip().isdigit())
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")  # папка для чеков
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
MONTHLY_FEE_DEFAULT = int(os.getenv("MONTHLY_FEE", "6000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Папка с картами посёлка (Google Drive)
TSN_MAPS_FOLDER_ID = "17bulx860YtFtTqW7cexESTSTKOdTE7Pf"

# =========================
# 2. ЛОГГИНГ
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# =========================
# 3. GOOGLE AUTH (SHEETS + DRIVE + VISION)
# =========================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_google_creds():
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return creds

creds = get_google_creds()
gc = gspread.authorize(creds)
sheets_service = build("sheets", "v4", credentials=creds)
drive_service = build("drive", "v3", credentials=creds)

vision_client = vision.ImageAnnotatorClient(credentials=creds)

# =========================
# 4. OPENAI (GPT для нестандартных чеков)
# =========================
openai_client = None
if OPENAI_AVAILABLE and OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# 5. FASTAPI + TELEGRAM APP
# =========================
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()

# =========================
# 6. УТИЛИТЫ
# =========================
BANK_EMOJI = {
    "ВТБ": "🔵",
    "АЛЬФА": "🔴",
    "Т-БАНК": "🟡",
    "СБП": "🟢",
}

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def parse_amount_from_text(text: str) -> Optional[int]:
    # ищем сумму вида 6 000, 6000.00, 6000 ₽
    m = re.findall(r"(\d[\d\s]{2,}\d)", text)
    if not m:
        return None
    val = m[0].replace(" ", "")
    try:
        return int(float(val))
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

def normalize_months(user_text: str) -> List[str]:
    # GPT-парсер месяцев (янв февраль → ["2026-01", "2026-02"])
    months = []
    now = datetime.now()
    mapping = {
        "янв": 1, "январ": 1,
        "фев": 2, "феврал": 2,
        "мар": 3, "март": 3,
        "апр": 4, "апрел": 4,
        "май": 5,
        "июн": 6,
        "июль": 7,
        "авг": 8,
        "сен": 9, "сентябр": 9,
        "окт": 10,
        "ноя": 11, "ноябр": 11,
        "дек": 12, "декабр": 12,
    }
    text = user_text.lower()
    for k, v in mapping.items():
        if k in text:
            months.append(f"{now.year}-{v:02d}")
    return months

# =========================
# 7. GOOGLE SHEETS (ЛИСТЫ)
# =========================
def get_sheet(name: str):
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=name, rows=1000, cols=30)

sheet_main = get_sheet("Лист 1")      # участки
sheet_checks = get_sheet("Лист 2")    # чеки
sheet_houses = get_sheet("Дома")      # на будущее

# =========================
# 8. ОПЛАТА (QR + Deeplink)
# =========================
def generate_qr(data: str) -> bytes:
    img = qrcode.make(data)
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio.read()

def make_deeplink(bank: str, amount: int) -> str:
    if bank == "СБП":
        return f"https://qr.nspk.ru/AS1A0000000000000000000000000000000000?amount={amount}"
    if bank == "ВТБ":
        return f"https://online.vtb.ru/pay?amount={amount}"
    if bank == "АЛЬФА":
        return f"https://alfabank.ru/pay/?amount={amount}"
    if bank == "Т-БАНК":
        return f"https://www.tbank.ru/pay/?amount={amount}"
    return ""

# =========================
# 9. OCR + АНТИДУБЛИКАТЫ
# =========================
async def ocr_image_bytes(image_bytes: bytes) -> Dict[str, Any]:
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    text = response.full_text_annotation.text if response.full_text_annotation else ""
    amount = parse_amount_from_text(text)
    d = parse_date_from_text(text)
    return {"text": text, "amount": amount, "date": d}

def is_duplicate_check(file_hash: str) -> bool:
    records = sheet_checks.get_all_records()
    for r in records:
        if r.get("File_Unique_ID") == file_hash:
            return True
    return False

# =========================
# 10. КНОПКИ
# =========================
def user_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("👉 Оплатил")], [KeyboardButton("ℹ️ Информация"), KeyboardButton("💳 Оплата")]],
        resize_keyboard=True
    )

def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📊 Админ-дашборд"), KeyboardButton("📣 Массовые уведомления")],
            [KeyboardButton("🗺 Карта посёлка")],
        ],
        resize_keyboard=True
    )

# =========================
# 11. ХЕНДЛЕРЫ БОТА
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (
        "Информация о взносах:\n\n"
        "• Взнос ежемесячный 6000 руб.\n"
        "• Сумма может отличаться при наличии льгот\n"
        "• День оплаты указан индивидуально\n"
        "• Чеки проверяются автоматически\n"
        "• При просрочке формируется задолженность\n\n"
        "📧 Контакты: propusk@tsn-iskona-park.ru\n"
        "📧 Связь с ТСН: info@iskonapark.ru"
    )
    await update.message.reply_text(text, reply_markup=user_keyboard())
    if uid in ADMIN_IDS:
        await update.message.reply_text("👑 Админ-панель активна", reply_markup=admin_keyboard())

async def handle_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Пришлите, пожалуйста, чек (фото/скрин). Я проверю автоматически.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    h = sha256_bytes(data)

    if is_duplicate_check(h):
        await update.message.reply_text("⚠️ Этот чек уже был загружен ранее (дубликат).")
        return

    ocr = await ocr_image_bytes(bytes(data))
    amount = ocr["amount"]
    d = ocr["date"]

    # TODO: сверка суммы с ожидаемой из таблицы
    await update.message.reply_text(
        f"🧾 Чек получен!\n"
        f"Сумма: {amount or 'не распознано'}\n"
        f"Дата: {d or 'не распознано'}\n"
        f"Статус: оплачено (предварительно)"
    )

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        "",
        "",
        "",
        "",
        amount or "",
        datetime.now().strftime("%d.%m.%Y"),
        d.strftime("%d.%m.%Y") if d else "",
        "",
        "",
        "",
        h,
        "оплачено"
    ])

async def handle_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ Ваш статус будет тут (участок, сумма, день оплаты, статус).")

async def handle_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 СБП", url=make_deeplink("СБП", MONTHLY_FEE_DEFAULT))],
        [InlineKeyboardButton("🔵 ВТБ", url=make_deeplink("ВТБ", MONTHLY_FEE_DEFAULT))],
        [InlineKeyboardButton("🔴 Альфа", url=make_deeplink("АЛЬФА", MONTHLY_FEE_DEFAULT))],
        [InlineKeyboardButton("🟡 Т-Банк", url=make_deeplink("Т-БАНК", MONTHLY_FEE_DEFAULT))],
    ])
    await update.message.reply_text("💳 Выберите способ оплаты:", reply_markup=kb)

async def admin_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return

    # берём первый файл из папки TSN_MAPS_FOLDER_ID
    res = drive_service.files().list(
        q=f"'{TSN_MAPS_FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name)"
    ).execute()
    files = res.get("files", [])
    if not files:
        await update.message.reply_text("❌ В папке TSN_MAPS нет файлов.")
        return

    file_id = files[0]["id"]
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()

    fh.seek(0)
    await update.message.reply_document(fh, filename=files[0]["name"], caption="🗺 Карта посёлка")

# =========================
# 12. APSCHEDULER (напоминания, ДР)
# =========================
scheduler = AsyncIOScheduler()

async def job_reminders():
    logger.info("⏰ Проверка напоминаний 5–3–1 и просрочек")

async def job_birthdays():
    logger.info("🎂 Проверка ДР")

# =========================
# 13. РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
# =========================
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.Regex("👉 Оплатил"), handle_paid))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
application.add_handler(MessageHandler(filters.Regex("ℹ️ Информация"), handle_info))
application.add_handler(MessageHandler(filters.Regex("💳 Оплата"), handle_pay))
application.add_handler(MessageHandler(filters.Regex("🗺 Карта посёлка"), admin_map))

# =========================
# 14. WEBHOOK
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
    scheduler.add_job(job_reminders, "interval", hours=24)
    scheduler.add_job(job_birthdays, "interval", hours=24)
    scheduler.start()
    logger.info("✅ Bot + Scheduler started")

@app.on_event("shutdown")
async def on_shutdown():
    scheduler.shutdown()
    await application.stop()
    await application.shutdown()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
# =========================================================
# 15. ДОП. УТИЛИТЫ ДЛЯ GOOGLE SHEETS (СУММА ПО УЧАСТКУ)
# =========================================================

def get_user_row_by_tg_id(tg_id: int) -> Optional[Dict[str, Any]]:
    rows = sheet_main.get_all_records()
    for r in rows:
        if str(r.get("tg_id", "")).strip() == str(tg_id):
            return r
    return None

def get_expected_amount_for_user(tg_id: int) -> int:
    row = get_user_row_by_tg_id(tg_id)
    if not row:
        return MONTHLY_FEE_DEFAULT
    try:
        return int(row.get("Сумма", MONTHLY_FEE_DEFAULT))
    except:
        return MONTHLY_FEE_DEFAULT

def get_plot_number_for_user(tg_id: int) -> str:
    row = get_user_row_by_tg_id(tg_id)
    return str(row.get("Участок", "")) if row else "—"

# =========================================================
# 16. ПРОВЕРКА СУММЫ ЧЕКА VS ОЖИДАЕМОЙ
# =========================================================

def compare_amounts(expected: int, actual: Optional[int]) -> bool:
    if actual is None:
        return False
    delta = abs(expected - actual)
    return delta <= 5  # допускаем погрешность 5 руб

# =========================================================
# 17. GPT-ФОЛБЭК ДЛЯ НЕСТАНДАРТНЫХ ЧЕКОВ
# =========================================================

async def gpt_parse_receipt(text: str) -> Dict[str, Any]:
    if not openai_client:
        return {"amount": None, "date": None, "bank": None}

    prompt = f"""
    Ты помощник для разбора банковских чеков.
    Извлеки:
    - сумму платежа
    - дату платежа
    - банк (ВТБ, Альфа, Т-Банк, СБП)
    Верни JSON:
    {{ "amount": 6000, "date": "2026-02-01", "bank": "ВТБ" }}
    Текст чека:
    {text}
    """

    resp = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    content = resp.choices[0].message.content
    try:
        data = json.loads(content)
        return data
    except:
        return {"amount": None, "date": None, "bank": None}

# =========================================================
# 18. ДОПОЛНЕННЫЙ ХЕНДЛЕР ЧЕКА (С ПРОВЕРКОЙ СУММЫ)
# =========================================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    h = sha256_bytes(data)

    if is_duplicate_check(h):
        await update.message.reply_text("⚠️ Этот чек уже был загружен ранее (дубликат).")
        return

    ocr = await ocr_image_bytes(bytes(data))
    text = ocr["text"]
    amount = ocr["amount"]
    d = ocr["date"]

    # GPT fallback
    bank = None
    if amount is None or d is None:
        gpt_data = await gpt_parse_receipt(text)
        amount = amount or gpt_data.get("amount")
        if gpt_data.get("date"):
            try:
                d = datetime.fromisoformat(gpt_data["date"]).date()
            except:
                pass
        bank = gpt_data.get("bank")

    expected = get_expected_amount_for_user(update.effective_user.id)
    ok_amount = compare_amounts(expected, amount)

    status = "оплачено" if ok_amount else "на проверке"

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        get_plot_number_for_user(update.effective_user.id),
        expected,
        amount or "",
        bank or "",
        datetime.now().strftime("%d.%m.%Y"),
        d.strftime("%d.%m.%Y") if d else "",
        h,
        status
    ])

    msg = (
        f"🧾 Чек обработан:\n\n"
        f"Участок: {get_plot_number_for_user(update.effective_user.id)}\n"
        f"Ожидалось: {expected} ₽\n"
        f"В чеке: {amount or 'не распознано'} ₽\n"
        f"Статус: {'✅ принято' if ok_amount else '⚠️ на проверке админом'}"
    )
    await update.message.reply_text(msg)

# =========================================================
# 19. APSCHEDULER: 5–3–1 + ПРОСРОЧКА
# =========================================================

async def job_reminders():
    rows = sheet_main.get_all_records()
    today = date.today().day

    for r in rows:
        tg_id = r.get("tg_id")
        due_day = int(r.get("День оплаты", 0))
        name = r.get("ФИО", "")
        plot = r.get("Участок", "")
        if not tg_id or not due_day:
            continue

        delta = due_day - today

        if delta in (5, 3, 1):
            await application.bot.send_message(
                chat_id=int(tg_id),
                text=f"🔔 Напоминание об оплате взноса.\nУчасток {plot}, срок через {delta} дн."
            )

        if today > due_day:
            await application.bot.send_message(
                chat_id=int(tg_id),
                text=f"⚠️ Просрочка оплаты по участку {plot}. Пожалуйста, оплатите взнос."
            )

# =========================================================
# 20. МАССОВЫЕ УВЕДОМЛЕНИЯ ПО УЧАСТКАМ (АДМИН)
# =========================================================

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return

    rows = sheet_main.get_all_records()
    sent = 0
    for r in rows:
        tg_id = r.get("tg_id")
        plot = r.get("Участок")
        if tg_id:
            await application.bot.send_message(
                chat_id=int(tg_id),
                text=f"📣 Уведомление ТСН:\nПросьба оплатить взнос по участку {plot}."
            )
            sent += 1

    await update.message.reply_text(f"✅ Массовая рассылка выполнена. Отправлено: {sent}")

application.add_handler(MessageHandler(filters.Regex("📣 Массовые уведомления"), admin_broadcast))

# =========================================================
# 21. ВЕБ-АДМИНКА + ГРАФИКИ (FASTAPI + CHART.JS)
# =========================================================

@app.get("/admin")
async def admin_dashboard():
    return """
    <html>
    <head>
        <title>TSN Admin Dashboard</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
        <h1>📊 Дашборд ТСН</h1>
        <canvas id="debts"></canvas>
        <canvas id="banks"></canvas>

        <script>
        fetch('/api/stats').then(r=>r.json()).then(data=>{
            new Chart(document.getElementById('debts'), {
                type: 'bar',
                data: { labels: data.months, datasets: [{ label: 'Долги', data: data.debts }] }
            });
            new Chart(document.getElementById('banks'), {
                type: 'pie',
                data: { labels: data.banks, datasets: [{ data: data.bank_counts }] }
            });
        });
        </script>
    </body>
    </html>
    """

@app.get("/api/stats")
async def api_stats():
    checks = sheet_checks.get_all_records()
    banks = {}
    months = {}
    for c in checks:
        b = c.get("bank", "—")
        banks[b] = banks.get(b, 0) + 1
        m = c.get("Дата платежа", "—")[:7]
        months[m] = months.get(m, 0) + 1

    return {
        "banks": list(banks.keys()),
        "bank_counts": list(banks.values()),
        "months": list(months.keys()),
        "debts": list(months.values())
    }

# =========================================================
# 22. GPT-ЧАТ ДЛЯ АДМИНА (КОНСУЛЬТАЦИИ)
# =========================================================

async def admin_gpt_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    question = update.message.text
    if not openai_client:
        await update.message.reply_text("❌ GPT недоступен (нет OPENAI_API_KEY)")
        return

    resp = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": question}],
        temperature=0.3
    )
    await update.message.reply_text("🤖 GPT:\n" + resp.choices[0].message.content)

# можно повесить на префикс "GPT:"
application.add_handler(MessageHandler(filters.Regex("^GPT:"), admin_gpt_chat))
