import os
import json
import re
import logging
from io import BytesIO
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional, List, Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import Application, ContextTypes, MessageHandler, CommandHandler, filters

import gspread
from google.oauth2.service_account import Credentials
from google.cloud import vision

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook/secure"
PORT = int(os.getenv("PORT", "1000"))

ADMINS = set(map(int, os.getenv("ADMINS", "").split(","))) if os.getenv("ADMINS") else set()
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

BASE_CHECKS_DIR = Path("Чеки ТСН")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tsn-bot")

# ================== GOOGLE ==================
gc = gspread.authorize(creds)
SPREAD = gc.open_by_key(SPREADSHEET_ID)

def get_or_create_sheet(title: str, headers: List[str]):
    try:
        ws = SPREAD.worksheet(title)
        if ws.row_values(1) != headers:
            ws.update("A1", [headers])
        return ws
    except:
        ws = SPREAD.add_worksheet(title=title, rows=2000, cols=30)
        ws.update("A1", [headers])
        return ws

SHEET_USERS = get_or_create_sheet("Пользователи", [
    "ФИО", "Участок", "Сумма", "День_оплаты", "Статус", "ДР",
    "Telegram_ID", "username"
])
SHEET_CHECKS = get_or_create_sheet("Чеки", [
    "Дата_загрузки", "Telegram_ID", "ФИО", "Участок",
    "Сумма_по_чеку", "Дата_по_чеку", "Путь_к_файлу", "Статус"
])
SHEET_REKV = get_or_create_sheet("Реквизиты", ["Ключ", "Значение"])

vision_client = vision.ImageAnnotatorClient(credentials=creds)

# ================== FASTAPI ==================
app = FastAPI()
application = Application.builder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

# ================== KEYBOARDS ==================
def user_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("ℹ️ Информация"), KeyboardButton("💳 Реквизиты")],
        [KeyboardButton("📊 Ваш статус"), KeyboardButton("📎 Загрузить чек")]
    ], resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🛠 Админ-панель"), KeyboardButton("📈 Админ-статистика")],
        [KeyboardButton("📣 Уведомить участок")]
    ], resize_keyboard=True)

# ================== HELPERS ==================
def is_admin(uid: int) -> bool:
    return uid in ADMINS

def get_users() -> List[Dict]:
    return SHEET_USERS.get_all_records()

def find_user_by_tg(tg_id: int) -> Optional[Dict]:
    for row in get_users():
        if str(row.get("Telegram_ID")) == str(tg_id):
            return row
    return None

def update_user_row(tg_id: int, updates: Dict):
    rows = SHEET_USERS.get_all_records()
    for i, row in enumerate(rows, start=2):
        if str(row.get("Telegram_ID")) == str(tg_id):
            for col, val in updates.items():
                col_idx = SHEET_USERS.find(col).col
                SHEET_USERS.update_cell(i, col_idx, val)
            return

def parse_sum_and_date(text: str):
    sum_match = re.search(r"(\d{2,6})\s?руб", text.lower())
    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    return (
        int(sum_match.group(1)) if sum_match else None,
        date_match.group(1) if date_match else None
    )

# ================== BOT HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = find_user_by_tg(user.id)
    if not existing:
        SHEET_USERS.append_row([
            "", "", "", "", "не оплачено", "", user.id, user.username
        ])
    await update.message.reply_text(
        "👋 Добро пожаловать в бот ТСН.\nВыберите действие:",
        reply_markup=user_kb()
    )

async def info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Информация о взносах:\n\n"
        "• Взнос ежемесячный (индивидуально)\n"
        "• День оплаты указан индивидуально\n"
        "• Чеки проверяются автоматически\n"
        "• При просрочке формируется задолженность\n\n"
        "📧 propusk@tsn-iskona-park.ru\n"
        "📧 info@iskonapark.ru",
        reply_markup=user_kb()
    )

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = find_user_by_tg(update.effective_user.id)
    if not user:
        return await update.message.reply_text("❌ Вы не зарегистрированы")
    text = (
        "📊 Ваш статус:\n\n"
        f"Участок: {user.get('Участок')}\n"
        f"Сумма: {user.get('Сумма')}\n"
        f"День оплаты: {user.get('День_оплаты')}\n"
        f"Статус: {user.get('Статус')}"
    )
    await update.message.reply_text(text, reply_markup=user_kb())

async def rekv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = SHEET_REKV.get_all_records()
    text_lines = []
    qr_url = None
    for r in rows:
        k, v = r.get("Ключ"), r.get("Значение")
        if k and "qr" in k.lower():
            qr_url = v
        else:
            text_lines.append(f"{k}: {v}")
    await update.message.reply_text("💳 Реквизиты:\n\n" + "\n".join(text_lines))
    if qr_url:
        await update.message.reply_photo(qr_url, caption="📎 QR для оплаты")

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_row = find_user_by_tg(update.effective_user.id)
    if not user_row:
        return await update.message.reply_text("❌ Вы не зарегистрированы")

    photo = update.message.photo[-1]
    file = await photo.get_file()

    plot = user_row.get("Участок") or "Неизвестно"
    folder = BASE_CHECKS_DIR / f"Участок_{plot}"
    folder.mkdir(parents=True, exist_ok=True)

    fname = folder / f"чек_{date.today()}_{update.effective_user.id}.jpg"
    await file.download_to_drive(str(fname))

    with open(fname, "rb") as f:
        image = vision.Image(content=f.read())
    response = vision_client.text_detection(image=image)
    text = response.full_text_annotation.text if response.text_annotations else ""

    sum_rub, date_str = parse_sum_and_date(text)

    SHEET_CHECKS.append_row([
        str(datetime.now()),
        update.effective_user.id,
        user_row.get("ФИО"),
        plot,
        sum_rub,
        date_str,
        str(fname),
        "оплачено"
    ])

    update_user_row(update.effective_user.id, {"Статус": "оплачено"})
    await update.message.reply_text("✅ Чек принят. Статус обновлён.")

# ================== APSCHEDULER JOBS ==================
async def notify_payments():
    today = date.today().day
    for row in get_users():
        try:
            pay_day = int(row.get("День_оплаты"))
            tg_id = int(row.get("Telegram_ID"))
            fio = row.get("ФИО")
            delta = pay_day - today

            if delta in (5, 3, 1):
                await application.bot.send_message(
                    tg_id,
                    f"Здравствуйте, {fio}! Напоминаем об оплате поселкового взноса через {delta} дн."
                )
            elif delta < 0 and row.get("Статус") != "оплачено":
                await application.bot.send_message(
                    tg_id,
                    f"{fio}, у вас задолженность по поселковому взносу. Просьба срочно оплатить."
                )
        except:
            continue

async def notify_birthdays():
    today = date.today().strftime("%d.%m")
    for row in get_users():
        if row.get("ДР") == today:
            await application.bot.send_message(
                int(row["Telegram_ID"]),
                f"🎉 С Днём Рождения, {row.get('ФИО')}! Желаем уюта, благополучия и отличного настроения!"
            )

def schedule_jobs():
    scheduler.add_job(lambda: notify_payments(), CronTrigger(hour=9, minute=0))
    scheduler.add_job(lambda: notify_birthdays(), CronTrigger(hour=10, minute=0))

# ================== WEB ==================
@app.get("/", response_class=HTMLResponse)
async def admin_dashboard():
    return "<h2>Админ-дашборд (часть 2 — графики + живые данные)</h2>"

@app.post(WEBHOOK_PATH)
async def telegram_webhook(req: Request):
    data = await req.json()
    await application.update_queue.put(Update.de_json(data, application.bot))
    return {"ok": True}

@app.on_event("startup")
async def startup():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)
    schedule_jobs()
    scheduler.start()
    logger.info("🚀 Bot started with APScheduler")

# ================== HANDLERS ==================
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^ℹ️ Информация$"), info_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📊 Ваш статус$"), status_handler))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^💳 Реквизиты$"), rekv_handler))
application.add_handler(MessageHandler(filters.PHOTO, upload_check))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
# ================== ADMIN HELPERS ==================
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")

def get_users_stats():
    users = get_users()
    total = len(users)
    paid = len([u for u in users if u.get("Статус") == "оплачено"])
    debt = total - paid
    by_plot = {}
    for u in users:
        plot = u.get("Участок") or "?"
        by_plot[plot] = by_plot.get(plot, 0) + 1
    return {
        "total": total,
        "paid": paid,
        "debt": debt,
        "by_plot": by_plot
    }

# ================== ADMIN BOT HANDLERS ==================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "🛠 Админ-панель:\nВыберите действие:",
        reply_markup=admin_kb()
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    stats = get_users_stats()
    text = (
        "📈 Статистика ТСН:\n\n"
        f"Всего собственников: {stats['total']}\n"
        f"Оплатили: {stats['paid']}\n"
        f"С задолженностью: {stats['debt']}\n\n"
        "По участкам:\n"
    )
    for plot, cnt in stats["by_plot"].items():
        text += f"• Участок {plot}: {cnt}\n"

    await update.message.reply_text(text, reply_markup=admin_kb())

async def admin_notify_plot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_plot"] = True
    await update.message.reply_text("📣 Введите номер участка для уведомления:")

async def admin_notify_plot_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("await_plot"):
        return

    plot = update.message.text.strip()
    users = get_users()
    sent = 0

    for u in users:
        if str(u.get("Участок")) == plot:
            tg_id = u.get("Telegram_ID")
            fio = u.get("ФИО")
            if tg_id:
                try:
                    await application.bot.send_message(
                        int(tg_id),
                        f"Здравствуйте, {fio}! Напоминаем об необходимости оплаты поселкового взноса. Благодарим за понимание 🙏"
                    )
                    sent += 1
                except:
                    pass

    context.user_data["await_plot"] = False
    await update.message.reply_text(f"✅ Уведомления отправлены по участку {plot}. Отправлено: {sent}")

# ================== DASHBOARD ==================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    stats = get_users_stats()
    labels = list(stats["by_plot"].keys())
    values = list(stats["by_plot"].values())

    html = f"""
    <html>
    <head>
        <title>Дашборд ТСН</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{ font-family: Arial; padding: 20px; background: #f5f7fa; }}
            .card {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
        </style>
    </head>
    <body>
        <h1>📊 Админ-дашборд ТСН</h1>

        <div class="card">
            <h3>Общая статистика</h3>
            <p>Всего собственников: {stats['total']}</p>
            <p>Оплатили: {stats['paid']}</p>
            <p>С задолженностью: {stats['debt']}</p>
        </div>

        <div class="card">
            <h3>Собственники по участкам</h3>
            <canvas id="plotsChart"></canvas>
        </div>

        <script>
            const ctx = document.getElementById('plotsChart').getContext('2d');
            new Chart(ctx, {{
                type: 'bar',
                data: {{
                    labels: {labels},
                    datasets: [{{
                        label: 'Кол-во собственников',
                        data: {values},
                        backgroundColor: 'rgba(54, 162, 235, 0.6)'
                    }}]
                }},
                options: {{
                    responsive: true,
                    scales: {{
                        y: {{ beginAtZero: true }}
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ================== BUTTON VISIBILITY ==================
async def middleware_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.effective_user.id
    if is_admin(uid):
        await update.message.reply_text("🔐 Админ-доступ активен", reply_markup=admin_kb())
    else:
        await update.message.reply_text("Меню:", reply_markup=user_kb())

# ================== REGISTER ADMIN HANDLERS ==================
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^🛠 Админ-панель$"), admin_panel))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📈 Админ-статистика$"), admin_stats))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📣 Уведомить участок$"), admin_notify_plot_start))
application.add_handler(MessageHandler(filters.TEXT & filters.TEXT, admin_notify_plot_process))
# ================== ADMIN HELPERS ==================
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")

def get_users_stats():
    users = get_users()
    total = len(users)
    paid = len([u for u in users if u.get("Статус") == "оплачено"])
    debt = total - paid
    by_plot = {}
    for u in users:
        plot = u.get("Участок") or "?"
        by_plot[plot] = by_plot.get(plot, 0) + 1
    return {
        "total": total,
        "paid": paid,
        "debt": debt,
        "by_plot": by_plot
    }

# ================== ADMIN BOT HANDLERS ==================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "🛠 Админ-панель:\nВыберите действие:",
        reply_markup=admin_kb()
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    stats = get_users_stats()
    text = (
        "📈 Статистика ТСН:\n\n"
        f"Всего собственников: {stats['total']}\n"
        f"Оплатили: {stats['paid']}\n"
        f"С задолженностью: {stats['debt']}\n\n"
        "По участкам:\n"
    )
    for plot, cnt in stats["by_plot"].items():
        text += f"• Участок {plot}: {cnt}\n"

    await update.message.reply_text(text, reply_markup=admin_kb())

async def admin_notify_plot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["await_plot"] = True
    await update.message.reply_text("📣 Введите номер участка для уведомления:")

async def admin_notify_plot_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("await_plot"):
        return

    plot = update.message.text.strip()
    users = get_users()
    sent = 0

    for u in users:
        if str(u.get("Участок")) == plot:
            tg_id = u.get("Telegram_ID")
            fio = u.get("ФИО")
            if tg_id:
                try:
                    await application.bot.send_message(
                        int(tg_id),
                        f"Здравствуйте, {fio}! Напоминаем об необходимости оплаты поселкового взноса. Благодарим за понимание 🙏"
                    )
                    sent += 1
                except:
                    pass

    context.user_data["await_plot"] = False
    await update.message.reply_text(f"✅ Уведомления отправлены по участку {plot}. Отправлено: {sent}")

# ================== DASHBOARD ==================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    stats = get_users_stats()
    labels = list(stats["by_plot"].keys())
    values = list(stats["by_plot"].values())

    html = f"""
    <html>
    <head>
        <title>Дашборд ТСН</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{ font-family: Arial; padding: 20px; background: #f5f7fa; }}
            .card {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
        </style>
    </head>
    <body>
        <h1>📊 Админ-дашборд ТСН</h1>

        <div class="card">
            <h3>Общая статистика</h3>
            <p>Всего собственников: {stats['total']}</p>
            <p>Оплатили: {stats['paid']}</p>
            <p>С задолженностью: {stats['debt']}</p>
        </div>

        <div class="card">
            <h3>Собственники по участкам</h3>
            <canvas id="plotsChart"></canvas>
        </div>

        <script>
            const ctx = document.getElementById('plotsChart').getContext('2d');
            new Chart(ctx, {{
                type: 'bar',
                data: {{
                    labels: {labels},
                    datasets: [{{
                        label: 'Кол-во собственников',
                        data: {values},
                        backgroundColor: 'rgba(54, 162, 235, 0.6)'
                    }}]
                }},
                options: {{
                    responsive: true,
                    scales: {{
                        y: {{ beginAtZero: true }}
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)

# ================== BUTTON VISIBILITY ==================
async def middleware_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.effective_user.id
    if is_admin(uid):
        await update.message.reply_text("🔐 Админ-доступ активен", reply_markup=admin_kb())
    else:
        await update.message.reply_text("Меню:", reply_markup=user_kb())

# ================== REGISTER ADMIN HANDLERS ==================
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^🛠 Админ-панель$"), admin_panel))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📈 Админ-статистика$"), admin_stats))
application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📣 Уведомить участок$"), admin_notify_plot_start))
application.add_handler(MessageHandler(filters.TEXT & filters.TEXT, admin_notify_plot_process))
