import os
import re
import io
import uuid
import logging
import asyncio
from datetime import datetime, timedelta
from urllib.parse import quote

from dotenv import load_dotenv
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# OCR
from google.cloud import vision

# ========== CONFIG ==========
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDS_FILE = "creds.json"
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# ========== LOGGING ==========
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tsn-bot")

# ========== GOOGLE SHEETS ==========
creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

SHEET_USERS = sh.worksheet("Пользователи")
SHEET_PAYMENTS = sh.worksheet("Платежи")
SHEET_REKV = sh.worksheet("Реквизиты")
SHEET_ANALYTICS = sh.worksheet("Аналитика")

# ========== OCR ==========
vision_client = vision.ImageAnnotatorClient()

# ========== EMOJI BANKS ==========
BANK_EMOJI = {
    "sbp": "🔳 СБП",
    "vtb": "🏦 ВТБ",
    "tinkoff": "🟡 Т-Банк",
    "alfa": "🟣 Альфа"
}

# ========== UTILS ==========
def find_user_by_tg(tg_id):
    rows = SHEET_USERS.get_all_records()
    for i, r in enumerate(rows, start=2):
        if str(r.get("tg_id")) == str(tg_id):
            r["_row"] = i
            return r
    return None

def update_user(row, data: dict):
    for k, v in data.items():
        col = SHEET_USERS.find(k).col
        SHEET_USERS.update_cell(row, col, v)

def build_payment_links(amount, plot, fio):
    rows = SHEET_REKV.get_all_records()
    data = {r["Ключ"]: r["Значение"] for r in rows}

    purpose = quote(f"Поселковый взнос, участок {plot}, {fio}")

    def tpl(key):
        t = data.get(key)
        return t.format(amount=amount, purpose=purpose) if t else None

    return {
        "sbp": data.get("sbp_qr_url"),
        "qr_image": data.get("qr_image"),
        "vtb": tpl("vtb_template"),
        "tinkoff": tpl("tinkoff_template"),
        "alfa": tpl("alfa_template"),
    }

def payment_kb(links):
    buttons = []
    for key in ["sbp", "vtb", "tinkoff", "alfa"]:
        if links.get(key):
            buttons.append([
                InlineKeyboardButton(
                    BANK_EMOJI[key],
                    url=links[key]
                )
            ])

    buttons.append([
        InlineKeyboardButton("👉 Оплатил", callback_data="paid"),
        InlineKeyboardButton("✏️ Другая сумма", callback_data="pay_custom")
    ])
    buttons.append([
        InlineKeyboardButton("📎 Показать QR", callback_data="pay_qr")
    ])
    return InlineKeyboardMarkup(buttons)

# ========== START ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = find_user_by_tg(update.effective_user.id)
    if user:
        await update.message.reply_text(
            f"С возвращением, {user.get('ФИО','')} 👋",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📊 Ваш статус"), KeyboardButton("💳 Реквизиты")],
                 [KeyboardButton("📎 Загрузить чек")]],
                resize_keyboard=True
            )
        )
    else:
        await update.message.reply_text(
            "🚀 Нажмите «Поехали», чтобы начать регистрацию.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🚀 Поехали")]], resize_keyboard=True
            )
        )

# ========== STATUS ==========
async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = find_user_by_tg(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Вы не зарегистрированы.")

    text = (
        f"📊 Ваш статус:\n\n"
        f"ФИО: {user.get('ФИО','')}\n"
        f"Участок: {user.get('Участок','')}\n"
        f"Сумма: {user.get('Сумма','')}\n"
        f"День оплаты: {user.get('День оплаты','')}\n"
        f"Статус: {user.get('Статус','не оплачено')}"
    )
    await update.message.reply_text(text)

# ========== PAYMENT ==========
async def rekv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = find_user_by_tg(update.effective_user.id)
    amount = int(user.get("Сумма") or 0) or 6000
    plot = user.get("Участок") or "—"
    fio = user.get("ФИО") or update.effective_user.full_name

    links = build_payment_links(amount, plot, fio)
    context.user_data["expected_amount"] = amount

    await update.message.reply_text(
        f"💳 Оплата взноса\n\n"
        f"ФИО: {fio}\n"
        f"Участок: {plot}\n"
        f"Сумма к оплате: {amount} ₽",
        reply_markup=payment_kb(links)
    )

# ========== CALLBACKS ==========
async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "paid":
        context.user_data["await_receipt"] = True
        await q.message.reply_text("📎 Пришлите фото/скан чека.")

    elif q.data == "pay_custom":
        context.user_data["await_custom_sum"] = True
        await q.message.reply_text("✏️ Введите сумму:")

    elif q.data == "pay_qr":
        rows = SHEET_REKV.get_all_records()
        qr_img = next((r["Значение"] for r in rows if r["Ключ"] == "qr_image"), None)
        if qr_img:
            await q.message.reply_photo(qr_img, caption="📎 QR для оплаты")

# ========== OCR чек ==========
def ocr_receipt(file_bytes: bytes) -> dict:
    image = vision.Image(content=file_bytes)
    resp = vision_client.text_detection(image=image)
    text = resp.full_text_annotation.text.lower()

    amount = None
    m = re.findall(r"(\d+[.,]\d{2})", text)
    if m:
        amount = float(m[-1].replace(",", "."))

    bank = None
    if "vtb" in text or "втб" in text:
        bank = "vtb"
    elif "tinkoff" in text or "тиньк" in text:
        bank = "tinkoff"
    elif "альфа" in text:
        bank = "alfa"
    elif "сбп" in text or "qr" in text:
        bank = "sbp"

    return {"amount": amount, "bank": bank, "raw": text}

# ========== HANDLE чек ==========
async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_receipt"):
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)

    ocr = ocr_receipt(buf.getvalue())
    expected = context.user_data.get("expected_amount")

    diff_ok = ocr["amount"] and abs(ocr["amount"] - expected) <= 5

    status = "OK" if diff_ok else "❌ НЕСОВПАДЕНИЕ"

    SHEET_PAYMENTS.append_row([
        datetime.now().isoformat(),
        update.effective_user.id,
        ocr["amount"],
        ocr["bank"],
        status
    ])

    # аналитика
    SHEET_ANALYTICS.append_row([
        datetime.now().date().isoformat(),
        ocr["bank"] or "unknown"
    ])

    await update.message.reply_text(
        f"🧾 Чек распознан\n\n"
        f"Сумма в чеке: {ocr['amount']} ₽\n"
        f"Ожидалось: {expected} ₽\n"
        f"Банк: {BANK_EMOJI.get(ocr['bank'],'❓')}\n"
        f"Статус проверки: {status}"
    )

    context.user_data["await_receipt"] = False

# ========== MAIN ==========
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("📊 Ваш статус"), status_handler))
    app.add_handler(MessageHandler(filters.Regex("💳 Реквизиты"), rekv_handler))
    app.add_handler(CallbackQueryHandler(payment_callback))
    app.add_handler(MessageHandler(filters.PHOTO, receipt_handler))

    log.info("🚀 Bot started")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
# ===================== АНТИДУБЛИКАТЫ ЧЕКОВ =====================
import hashlib

def calc_image_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()

def is_duplicate_receipt(img_hash: str) -> bool:
    rows = SHEET_PAYMENTS.get_all_records()
    return any(r.get("hash") == img_hash for r in rows)


# ===================== МЕСЯЦА (GPT-подобная логика) =====================
MONTHS_MAP = {
    "янв": 1, "январ": 1, "january": 1,
    "фев": 2, "феврал": 2, "feb": 2,
    "мар": 3, "март": 3,
    "апр": 4, "апрел": 4,
    "май": 5,
    "июн": 6, "июнь": 6,
    "июл": 7, "июль": 7,
    "авг": 8, "август": 8,
    "сен": 9, "сент": 9,
    "окт": 10, "нояб": 11, "дек": 12
}

def parse_months(text: str):
    text = text.lower()
    found = set()
    for key, num in MONTHS_MAP.items():
        if key in text:
            found.add(num)
    return sorted(found)


# ===================== GPT ДЛЯ АДМИНА =====================
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

async def admin_gpt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    question = update.message.text.replace("🤖 GPT", "").strip()
    await update.message.reply_text("🤖 Думаю...")

    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты помощник администратора ТСН. Помогаешь разбирать чеки, платежи, долги."},
                {"role": "user", "content": question}
            ],
            temperature=0.2
        )
        answer = resp.choices[0].message["content"]
        await update.message.reply_text(answer)
    except Exception as e:
        await update.message.reply_text("Ошибка GPT: " + str(e))


# ===================== ОБНОВЛЁННЫЙ HANDLER ЧЕКА =====================
async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_receipt"):
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    img_bytes = buf.getvalue()

    img_hash = calc_image_hash(img_bytes)

    if is_duplicate_receipt(img_hash):
        await update.message.reply_text("⚠️ Этот чек уже был загружен ранее (дубликат).")
        return

    ocr = ocr_receipt(img_bytes)
    expected = context.user_data.get("expected_amount")
    months = context.user_data.get("months", [])

    diff_ok = ocr["amount"] and abs(ocr["amount"] - expected) <= 5
    status = "OK" if diff_ok else "❌ НЕСОВПАДЕНИЕ"

    SHEET_PAYMENTS.append_row([
        datetime.now().isoformat(),
        update.effective_user.id,
        ocr["amount"],
        ocr["bank"],
        ",".join(map(str, months)),
        status,
        img_hash
    ])

    SHEET_ANALYTICS.append_row([
        datetime.now().date().isoformat(),
        ocr["bank"] or "unknown"
    ])

    await update.message.reply_text(
        f"🧾 Чек принят\n\n"
        f"Сумма: {ocr['amount']} ₽\n"
        f"Ожидалось: {expected} ₽\n"
        f"Месяцы: {months}\n"
        f"Банк: {BANK_EMOJI.get(ocr['bank'],'❓')}\n"
        f"Статус: {status}"
    )

    context.user_data["await_receipt"] = False
    context.user_data["months"] = []


# ===================== СПРОСИТЬ МЕСЯЦА =====================
async def paid_callback_ask_months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["await_months"] = True
    await q.message.reply_text("✍️ За какие месяцы оплата? (например: янв февраль март)")

async def months_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_months"):
        return

    months = parse_months(update.message.text)
    context.user_data["months"] = months
    context.user_data["await_months"] = False
    context.user_data["await_receipt"] = True

    await update.message.reply_text(f"📅 Принято. Месяцы: {months}\nТеперь пришлите чек 📎")


# ===================== ВЕБ-ДАШБОРД С ГРАФИКАМИ =====================
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app_web = FastAPI()

@app_web.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard():
    rows = SHEET_ANALYTICS.get_all_records()
    stats = {}
    for r in rows:
        b = r.get("bank") or "unknown"
        stats[b] = stats.get(b, 0) + 1

    return HTMLResponse(f"""
    <html>
    <head>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
      <h2>📊 Оплаты по банкам</h2>
      <canvas id="chart"></canvas>
      <script>
        new Chart(document.getElementById('chart'), {{
          type: 'pie',
          data: {{
            labels: {list(stats.keys())},
            datasets: [{{
              data: {list(stats.values())}
            }}]
          }}
        }});
      </script>
    </body>
    </html>
    """)


# ===================== ДОБАВЬ В РЕГИСТРАЦИЮ HANDLERS =====================
application.add_handler(MessageHandler(filters.Regex("^🤖 GPT"), admin_gpt_handler))
application.add_handler(CallbackQueryHandler(paid_callback_ask_months, pattern="paid"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, months_input_handler))
