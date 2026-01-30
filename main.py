import os
import logging
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from google.oauth2.service_account import Credentials
import qrcode
from io import BytesIO

# -------------------- CONFIG --------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = set(map(int, os.getenv("ADMIN_IDS", "").split(",")))

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDS = Credentials.from_service_account_file(
    "service_account.json", scopes=SCOPES
)
GC = gspread.authorize(CREDS)

SPREAD = GC.open(os.getenv("SPREAD_NAME"))

SHEET_USERS = SPREAD.worksheet("Лист 1")
SHEET_CHECKS = SPREAD.worksheet("Лист 2")
SHEET_LOGS = SPREAD.worksheet("Лист 3")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- HELPERS --------------------

def log_event(event_type, uid=None, username=None, plot=None, details="", error=""):
    SHEET_LOGS.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        event_type,
        uid,
        username,
        plot,
        details,
        error
    ])

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def get_all_users():
    return SHEET_USERS.get_all_records()

def find_user(uid):
    for row, u in enumerate(get_all_users(), start=2):
        if str(u.get("Telegram_ID")) == str(uid):
            return row, u
    return None, None

# -------------------- QR --------------------

def generate_qr(text: str) -> BytesIO:
    qr = qrcode.make(text)
    bio = BytesIO()
    qr.save(bio, format="PNG")
    bio.seek(0)
    return bio

# -------------------- START / REG --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    row, user = find_user(uid)

    if not user:
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\n"
            "Вы ещё не зарегистрированы.\n"
            "Обратитесь к администратору."
        )
        return

    kb = [
        ["💳 Реквизиты", "📊 Статус"],
        ["ℹ️ Информация"],
    ]

    if is_admin(uid):
        kb.append(["🛠 Админ панель"])

    await update.message.reply_text(
        f"Здравствуйте, {user['ФИО']}!",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

# -------------------- INFO --------------------

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ Поселковые взносы\n\n"
        "• Оплата ежемесячная\n"
        "• День оплаты указан индивидуально\n"
        "• Льготы учитываются автоматически\n"
        "• После просрочки формируется задолженность"
    )

# -------------------- STATUS --------------------

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row, user = find_user(update.effective_user.id)
    if not user:
        return

    await update.message.reply_text(
        f"📊 Ваш статус:\n\n"
        f"Участок: {user['Участок']}\n"
        f"Сумма: {user['Сумма']}\n"
        f"День оплаты: {user['День_оплаты']}\n"
        f"Статус: {user['Статус']}"
    )

# -------------------- REKV --------------------

async def rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Получатель: ТСН\n"
        "ИНН: 0000000000\n"
        "Счёт: 0000000000000000\n"
        "Банк: Банк\n"
        "БИК: 000000000"
    )
    qr = generate_qr(text)
    await update.message.reply_photo(qr, caption=text)

# -------------------- ADMIN --------------------

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    kb = [
        ["📣 Напоминание по дому"],
        ["⬅️ Назад"],
    ]
    await update.message.reply_text(
        "🛠 Админ панель",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def admin_remind_house(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["wait_house"] = True
    await update.message.reply_text("Введите номер дома:")

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("wait_house"):
        house = update.message.text
        sent = 0

        for u in get_all_users():
            if str(u.get("Участок")) == house and u.get("Telegram_ID"):
                try:
                    await context.bot.send_message(
                        int(u["Telegram_ID"]),
                        f"🔔 Напоминание об оплате.\nУчасток: {house}"
                    )
                    sent += 1
                except:
                    pass

        context.user_data.clear()
        await update.message.reply_text(f"✅ Отправлено: {sent}")

# -------------------- REMIND JOB --------------------

async def payment_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().day
    users = get_all_users()

    for idx, u in enumerate(users, start=2):
        try:
            pay_day = int(u.get("День_оплаты", 0))
            if not u.get("Telegram_ID"):
                continue

            delta = pay_day - today

            if delta in (5, 3, 1):
                text = (
                    f"Уважаемый(ая) {u['ФИО']},\n"
                    f"Напоминаем об оплате поселковых взносов."
                )
            elif delta < 0:
                text = (
                    f"{u['ФИО']},\n"
                    f"У вас образовалась задолженность."
                )
            else:
                continue

            await context.bot.send_message(int(u["Telegram_ID"]), text)
            SHEET_USERS.update_cell(idx, 12, datetime.now().strftime("%Y-%m-%d"))

        except Exception as e:
            log_event("error", error=str(e))

# -------------------- ROUTER --------------------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "ℹ️ Информация":
        await info(update, context)
    elif text == "📊 Статус":
        await status(update, context)
    elif text == "💳 Реквизиты":
        await rekv(update, context)
    elif text == "🛠 Админ панель":
        await admin_panel(update, context)
    elif text == "📣 Напоминание по дому":
        await admin_remind_house(update, context)
    elif text == "⬅️ Назад":
        await start(update, context)
    else:
        await handle_admin_text(update, context)

# -------------------- MAIN --------------------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.job_queue.run_daily(payment_reminder_job, time=datetime.now().time())

    app.run_polling()

if __name__ == "__main__":
    main()

