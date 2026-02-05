import os
import io
import json
import hashlib
import asyncio
import datetime as dt
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, InputFile
)
from telegram.ext import (
    Application, ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, CallbackQueryHandler, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from google.cloud import vision
import qrcode
from openai import OpenAI

# ===================== ENV =====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else set()
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# ===================== GOOGLE INIT =====================
creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-vision",
]
creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)
ws_main = sh.sheet1
ws_req = sh.worksheet("Реквизиты")

drive_service = build("drive", "v3", credentials=creds)
vision_client = vision.ImageAnnotatorClient(credentials=creds)

# ===================== FASTAPI =====================
app = FastAPI()

# ===================== TELEGRAM =====================
tg_app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

# ===================== GPT =====================
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ===================== HELPERS =====================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def get_all_rows():
    return ws_main.get_all_records()

def get_owner_by_tg(user_id: int) -> Optional[Dict[str, Any]]:
    for r in get_all_rows():
        if str(r.get("telegram_id")) == str(user_id):
            return r
    return None

def get_owner_by_plot(plot: str) -> Optional[Dict[str, Any]]:
    for r in get_all_rows():
        if str(r.get("Участок")) == str(plot):
            return r
    return None

def update_owner_field(plot: str, field: str, value: Any):
    cell = ws_main.find(str(plot))
    col = ws_main.find(field).col
    ws_main.update_cell(cell.row, col, value)

def get_requisites() -> Dict[str, str]:
    data = ws_req.get_all_records()
    return {r["Ключ"]: r["Значение"] for r in data if r.get("Ключ")}

def get_due_info(owner: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "plot": owner.get("Участок"),
        "fio": owner.get("ФИО"),
        "amount": owner.get("Сумма"),
        "due_day": owner.get("День оплаты"),
        "status": owner.get("Статус"),
    }

# ===================== QR + BANK =====================

def make_spb_qr(amount: float) -> bytes:
    payload = f"ST00012|Name=ТСН Посёлок|BankName=СБП|Sum={int(amount*100)}"
    qr = qrcode.make(payload)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    return buf.getvalue()

def bank_deeplink(bank: str, amount: float) -> str:
    links = {
        "sbp": f"https://qr.nspk.ru/AS1A000000000000000000000000000?amount={amount}",
        "vtb": f"vtbapp://pay?amount={amount}",
        "alfa": f"alfabank://pay?amount={amount}",
        "tbank": f"tinkoff://pay?amount={amount}",
    }
    return links.get(bank, "")

def detect_bank_from_text(text: str) -> str:
    t = text.lower()
    if "втб" in t:
        return "vtb"
    if "альфа" in t:
        return "alfa"
    if "тиньк" in t or "тбанк" in t:
        return "tbank"
    if "сбп" in t:
        return "sbp"
    return "other"

# ===================== OCR =====================

def ocr_text(image_bytes: bytes) -> str:
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    if response.text_annotations:
        return response.text_annotations[0].description
    return ""

def parse_receipt_amount(text: str) -> Optional[float]:
    import re
    matches = re.findall(r"(\d+[.,]\d{2})", text.replace(",", "."))
    if matches:
        return float(matches[-1])
    return None

# ===================== DRIVE =====================

def ensure_plot_folder(plot: str) -> str:
    q = f"'{DRIVE_FOLDER_ID}' in parents and name='{plot}' and mimeType='application/vnd.google-apps.folder'"
    res = drive_service.files().list(q=q).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    folder = drive_service.files().create(body={
        "name": str(plot),
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [DRIVE_FOLDER_ID],
    }, fields="id").execute()
    return folder["id"]

def upload_receipt(plot: str, filename: str, data: bytes) -> str:
    folder_id = ensure_plot_folder(plot)
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg")
    file = drive_service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="webViewLink"
    ).execute()
    return file["webViewLink"]

# ===================== GPT =====================

async def gpt_analyze_receipt(text: str) -> str:
    prompt = f"""
Ты — помощник администратора ТСН.
Текст чека:
{text}

Определи банк, сумму, дату и есть ли признаки подделки.
Ответ в JSON.
"""
    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content

# ===================== UI =====================

def owner_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Мой статус", callback_data="status")],
        [InlineKeyboardButton("💳 Оплатить (QR)", callback_data="pay")],
        [InlineKeyboardButton("👉 Я оплатил", callback_data="paid")],
        [InlineKeyboardButton("📄 Реквизиты", callback_data="reqs")],
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Уведомить участок", callback_data="notify")],
        [InlineKeyboardButton("📊 Веб-дашборд", url="/admin")],
    ])
# ===================== BOT HANDLERS =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("▶️ Старт")]]
    await update.message.reply_text(
        "Добро пожаловать! Нажмите кнопку «Старт» для входа 👇",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def start_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    owner = get_owner_by_tg(user.id)

    if not owner:
        context.user_data["await_plot"] = True
        await update.message.reply_text("Введите номер участка для привязки 👇")
        return

    await update.message.reply_text(
        f"Привет, {owner.get('ФИО')} 👋",
        reply_markup=owner_menu()
    )

    if is_admin(user.id):
        await update.message.reply_text("🛠 Админ-панель:", reply_markup=admin_menu())

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("await_plot"):
        plot = update.message.text.strip()
        owner = get_owner_by_plot(plot)

        if not owner:
            await update.message.reply_text("❌ Участок не найден. Попробуй ещё раз.")
            return

        update_owner_field(plot, "telegram_id", update.effective_user.id)
        context.user_data.clear()

        await update.message.reply_text(
            "✅ Вы успешно зарегистрированы!",
            reply_markup=owner_menu()
        )

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = q.from_user.id
    owner = get_owner_by_tg(user_id)

    if data == "status":
        info = get_due_info(owner)
        await q.message.reply_text(
            f"📊 Ваш статус:\n\n"
            f"Участок: {info['plot']}\n"
            f"Сумма: {info['amount']} ₽\n"
            f"День оплаты: {info['due_day']}\n"
            f"Статус: {info['status']}"
        )

    elif data == "pay":
        amount = float(owner.get("Сумма"))
        qr_bytes = make_spb_qr(amount)

        await q.message.reply_photo(
            InputFile(io.BytesIO(qr_bytes), filename="qr.png"),
            caption="💳 Оплата по СБП или выберите банк:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💚 СБП", url=bank_deeplink("sbp", amount))],
                [InlineKeyboardButton("🔵 ВТБ", url=bank_deeplink("vtb", amount))],
                [InlineKeyboardButton("🔴 Альфа", url=bank_deeplink("alfa", amount))],
                [InlineKeyboardButton("⚫ Т-Банк", url=bank_deeplink("tbank", amount))],
            ])
        )

    elif data == "paid":
        context.user_data["await_receipt"] = True
        await q.message.reply_text("📸 Пришлите фото чека")

    elif data == "reqs":
        reqs = get_requisites()
        text = "\n".join([f"{k}: {v}" for k, v in reqs.items()])
        await q.message.reply_text("📄 Реквизиты для оплаты:\n\n" + text)

    elif data == "notify" and is_admin(user_id):
        await q.message.reply_text("Введите номер участка для уведомления:")

# ===================== PHOTO HANDLER =====================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_receipt"):
        return

    owner = get_owner_by_tg(update.effective_user.id)
    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()

    receipt_hash = hash_bytes(data)

    for r in get_all_rows():
        if r.get("receipt_hash") == receipt_hash:
            await update.message.reply_text("⚠️ Такой чек уже был загружен.")
            return

    text = ocr_text(bytes(data))
    amount = parse_receipt_amount(text)
    expected = float(owner.get("Сумма"))
    bank = detect_bank_from_text(text)

    update_owner_field(owner["Участок"], "Банк", bank)

    if amount and abs(amount - expected) > 1:
        gpt_result = await gpt_analyze_receipt(text)
        await update.message.reply_text(
            f"⚠️ Сумма в чеке не совпадает с ожидаемой.\n"
            f"Отправлено администратору.\n\nGPT-анализ:\n{gpt_result}"
        )

    link = upload_receipt(owner["Участок"], f"receipt_{dt.datetime.now().isoformat()}.jpg", bytes(data))

    update_owner_field(owner["Участок"], "receipt_hash", receipt_hash)
    update_owner_field(owner["Участок"], "Статус", "Оплачено")

    await update.message.reply_text("✅ Чек принят! Спасибо за оплату 🙏")
    context.user_data["await_receipt"] = False

# ===================== SCHEDULER =====================

scheduler = AsyncIOScheduler()

async def notify_job():
    today = dt.date.today().day
    for r in get_all_rows():
        try:
            due = int(r.get("День оплаты"))
            tg_id = int(r.get("telegram_id", 0))
            fio = r.get("ФИО")
            amount = r.get("Сумма")

            if not tg_id:
                continue

            if today in [due - 5, due - 3, due - 1]:
                await tg_app.bot.send_message(
                    tg_id,
                    f"Уважаемый(ая) {fio}, напоминаем об оплате взноса {amount} ₽ 🙏"
                )

            if today > due:
                await tg_app.bot.send_message(
                    tg_id,
                    f"{fio}, у вас задолженность по взносу {amount} ₽. "
                    f"Просим срочно оплатить."
                )
        except:
            pass

scheduler.add_job(notify_job, "cron", hour=10, minute=0)

# ===================== TELEGRAM INIT =====================

tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(MessageHandler(filters.Regex("^▶️ Старт$"), start_btn))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
tg_app.add_handler(CallbackQueryHandler(callbacks))
tg_app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

# ===================== FASTAPI =====================

@app.on_event("startup")
async def on_startup():
    scheduler.start()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return JSONResponse({"ok": True})

@app.get("/api/stats")
async def api_stats():
    rows = get_all_rows()
    banks = {"sbp": 0, "vtb": 0, "alfa": 0, "tbank": 0, "other": 0}
    for r in rows:
        b = r.get("Банк", "other")
        banks[b] = banks.get(b, 0) + 1

    return {"banks": banks, "rows": rows}

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Админ ТСН</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
<h1>📊 Админ-дашборд</h1>
<canvas id="chart"></canvas>
<script>
fetch('/api/stats').then(r=>r.json()).then(data=>{
  new Chart(document.getElementById('chart'),{
    type:'pie',
    data:{
      labels:['СБП','ВТБ','Альфа','Т-Банк','Другое'],
      datasets:[{data:[
        data.banks.sbp,
        data.banks.vtb,
        data.banks.alfa,
        data.banks.tbank,
        data.banks.other
      ]}]
    }
  })
})
</script>
</body>
</html>
""")

# ===================== MAIN =====================

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
